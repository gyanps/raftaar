import { decodePolyline } from './MapUtils.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const mins = (m) => (m >= 60 ? Math.floor(m / 60) + ' h ' + Math.round(m % 60) + ' min' : Math.round(m) + ' min');
const api = async (path, params = {}) => {
  const url = new URL(path, location.origin);
  Object.entries(params).forEach(([k, v]) => v != null && url.searchParams.set(k, v));
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
};
const STYLE = { light: 'https://tiles.openfreemap.org/styles/positron', dark: 'https://tiles.openfreemap.org/styles/dark' };
const PRESETS = [
  ['ISBT Kashmere Gate', 'Nehru Place Terminal'],
  ['Anand Vihar ISBT', 'Shivaji Stadium'],
  ['Uttam Nagar Terminal', 'AIIMS'],
  ['Badarpur Border', 'Old Delhi Railway Station'],
];
const state = { dark: matchMedia('(prefers-color-scheme: dark)').matches, day: 'weekday', picked: { from: null, to: null },
  plan: null, selected: 0, markers: [], traffic: {}, tday: 'weekday', tab: 'plan', playing: null, segments: null,
  buses: new Map(), busTimer: null, busAnim: null };

// ---------------------------------------------------------------- map
document.body.classList.toggle('dark', state.dark);
const map = new maplibregl.Map({ container: 'map', style: STYLE[state.dark ? 'dark' : 'light'], center: [77.209, 28.6139], zoom: 10.6,
  attributionControl: { compact: true } });
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
map.on('error', (e) => window.__errors?.push('map: ' + (e.error?.message || e.type)));
window.__map = map;
const pad = () => (innerWidth > 760 ? { left: 450, top: 60, right: 60, bottom: 60 } : { left: 30, top: 30, right: 30, bottom: innerHeight * 0.64 });
const line = (enc) => decodePolyline(enc).map(([lat, lng]) => [lng, lat]);

function addLayers() {
  const firstLabel = map.getStyle().layers.find((l) => l.type === 'symbol')?.id;
  map.addSource('seg', { type: 'geojson', data: state.segments || { type: 'FeatureCollection', features: [] }, promoteId: 'i' });
  const v = ['coalesce', ['feature-state', 'v'], 0];
  const wide = ['case', ['<', v, 10], 1.9, ['<', v, 15], 1.35, 1];
  map.addLayer({ id: 'seg', type: 'line', source: 'seg', layout: { 'line-cap': 'round', visibility: state.tab === 'traffic' ? 'visible' : 'none' },
    paint: {
      'line-color': ['case', ['==', v, 0], 'rgba(0,0,0,0)', ['interpolate', ['linear'], v, 5, '#7f1d1d', 10, '#dc2626', 15, '#f59e0b', 22, '#84cc16', 30, '#16a34a']],
      // slower stretches are drawn wider, so a jam stays visible when zoomed out
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, ['*', 0.9, wide], 12, ['*', 2, wide], 15, ['*', 5, wide]],
      'line-opacity': 0.9 } }, firstLabel);
  for (const kind of ['walk', 'ride']) map.addSource(kind, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'ride-case', type: 'line', source: 'ride', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#fff', 'line-width': 9, 'line-opacity': 0.9 } });
  map.addLayer({ id: 'ride', type: 'line', source: 'ride', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': ['get', 'color'], 'line-width': 5 } });
  map.addLayer({ id: 'walk', type: 'line', source: 'walk', paint: { 'line-color': '#64748b', 'line-width': 3, 'line-dasharray': [0.6, 1.6] }, layout: { 'line-cap': 'round' } });
  map.addSource('buses', { type: 'geojson', data: busData(1) });
  map.addLayer({ id: 'buses', type: 'circle', source: 'buses', layout: { visibility: state.tab === 'live' ? 'visible' : 'none' },
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.7, 12, 3.2, 15, 6.5], 'circle-color': ['case', ['get', 't'], '#2563eb', '#94a3b8'],
      'circle-stroke-color': '#fff', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 0.3, 14, 1.2] } }, firstLabel);
  paintTraffic();
  drawOption(true);
}
map.on('style.load', addLayers);
map.on('click', (e) => {
  if (state.tab !== 'plan') return;
  const { lat, lng } = e.lngLat;
  const el = document.createElement('div');
  el.className = 'pick';
  el.innerHTML = '<button data-k="from">From here</button><button data-k="to">To here</button>';
  const pop = new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
  el.onclick = (ev) => {
    const k = ev.target.dataset.k;
    if (!k) return;
    state.picked[k] = { lat, lng };
    $(k).value = lat.toFixed(5) + ', ' + lng.toFixed(5);
    pop.remove();
    if ($('from').value && $('to').value) plan();
  };
});
$('theme').onclick = () => {
  state.dark = !state.dark;
  document.body.classList.toggle('dark', state.dark);
  map.setStyle(STYLE[state.dark ? 'dark' : 'light']);
};

// ---------------------------------------------------------------- tabs
document.querySelectorAll('.tabs button').forEach((b) => (b.onclick = () => {
  state.tab = b.dataset.tab;
  document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('on', x === b));
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('on', t.id === 'tab-' + state.tab));
  const planView = state.tab === 'plan' || state.tab === 'accuracy';
  if (map.getLayer('seg')) map.setLayoutProperty('seg', 'visibility', state.tab === 'traffic' ? 'visible' : 'none');
  if (map.getLayer('buses')) map.setLayoutProperty('buses', 'visibility', state.tab === 'live' ? 'visible' : 'none');
  for (const id of ['ride', 'ride-case', 'walk']) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', planView ? 'visible' : 'none');
  state.markers.forEach((m) => (m.getElement().style.display = planView ? '' : 'none'));
  clearInterval(state.busTimer);
  if (state.tab === 'live') { loadLive(); state.busTimer = setInterval(loadLive, 30000); }
  if (state.tab === 'traffic') loadTraffic();
  if (state.tab === 'accuracy') loadAccuracy();
}));

// ---------------------------------------------------------------- place inputs
function suggest(input, list, key) {
  let timer, items = [], hi = -1;
  const close = () => list.classList.remove('on');
  const choose = (it) => { input.value = it.name; state.picked[key] = it; close(); };
  const render = () => {
    list.innerHTML = items.map((it, i) => '<li class="' + (i === hi ? 'hi' : '') + '" data-i="' + i + '"><span>' + esc(it.name) + '</span><small>' + it.routes + ' routes</small></li>').join('');
    list.classList.toggle('on', items.length > 0);
  };
  input.addEventListener('input', () => {
    state.picked[key] = null;
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2 || /^-?\d/.test(q)) return close();
    timer = setTimeout(async () => { items = await api('/api/places', { q }).catch(() => []); hi = -1; render(); }, 140);
  });
  input.addEventListener('keydown', (e) => {
    if (!list.classList.contains('on')) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { hi = (hi + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; render(); e.preventDefault(); }
    else if (e.key === 'Enter' && hi >= 0) { choose(items[hi]); e.preventDefault(); }
    else if (e.key === 'Escape') close();
  });
  list.addEventListener('mousedown', (e) => { const li = e.target.closest('li'); if (li) choose(items[+li.dataset.i]); });
  input.addEventListener('blur', () => setTimeout(close, 120));
}
suggest($('from'), $('from-list'), 'from');
suggest($('to'), $('to-list'), 'to');
$('swap').onclick = () => {
  [$('from').value, $('to').value] = [$('to').value, $('from').value];
  [state.picked.from, state.picked.to] = [state.picked.to, state.picked.from];
};
$('day').onclick = (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  const was = state.day, t = $('time');
  state.day = b.dataset.day;
  [...$('day').children].forEach((x) => x.classList.toggle('on', x === b));
  // "Now" shows the present time (Indian Standard Time), greyed out; going back restores what was typed.
  if (state.day === 'now' && was !== 'now') {
    state.timeWas = t.value;
    t.value = new Date().toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' });
  } else if (state.day !== 'now' && was === 'now' && state.timeWas) t.value = state.timeWas;
  t.disabled = state.day === 'now';
};
$('presets').innerHTML = PRESETS.map(([a, b], i) => '<button type="button" data-i="' + i + '">' + esc(a) + ' → ' + esc(b) + '</button>').join('');
$('presets').onclick = (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  [$('from').value, $('to').value] = PRESETS[+b.dataset.i];
  state.picked = { from: null, to: null };
  plan();
};
$('form').onsubmit = (e) => { e.preventDefault(); plan(); };

// A link can carry a whole question: ?from=...&to=...&time=18:30&day=weekday  or  ?tab=traffic&time=18:30
function fromLink() {
  const q = new URLSearchParams(location.search);
  if (q.get('day')) document.querySelector('#day [data-day="' + q.get('day') + '"]')?.click();
  if (q.get('time')) {
    if (state.day === 'now') state.timeWas = q.get('time'); else $('time').value = q.get('time');
    const [h, m] = q.get('time').split(':').map(Number);
    $('tslider').value = h * 2 + (m >= 30 ? 1 : 0);
  }
  if (q.get('from') && q.get('to')) { $('from').value = q.get('from'); $('to').value = q.get('to'); plan(); }
  if (q.get('tab')) document.querySelector('.tabs [data-tab="' + q.get('tab') + '"]')?.click();
}

// ---------------------------------------------------------------- planning
const place = (k) => (state.picked[k] ? state.picked[k].lat + ',' + state.picked[k].lng : $(k).value.trim());
const when = (time) => (state.day === 'now' ? { depart: 'now' } : { depart: time || $('time').value, day: state.day });

async function plan() {
  if (!$('from').value.trim() || !$('to').value.trim()) return;
  $('status').className = 'status';
  $('status').textContent = 'Working out the buses…';
  $('options').innerHTML = $('otherTimes').innerHTML = '';
  try {
    state.plan = await api('/api/journeys', { from: place('from'), to: place('to'), ...when() });
    state.selected = 0;
    const p = state.plan;
    $('tab-plan').classList.toggle('has-plan', p.options.length > 0);
    const liveNote = p.mode === 'live'
      ? '<br><span class="live-dot"></span>Live: buses are running ' + Math.abs(p.live.city_vs_usual_pct) + '% ' + (p.live.city_vs_usual_pct >= 0 ? 'slower' : 'faster')
        + ' than usual right now (' + p.live.runs_last_20_min.toLocaleString() + ' stop-to-stop runs in the last 20 min)'
      : (state.day === 'now' ? '<br><span class="live-dot off"></span>Typical-day estimate (live data not available yet)' : '');
    $('status').innerHTML = p.options.length
      ? esc(p.origin.name) + ' → ' + esc(p.destination.name) + ' · leaving ' + p.depart + ' on a ' + p.day_type.replace('_', ' / ') + ' · ' + p.straight_line_km + ' km apart' + liveNote
      : 'No bus journey found between these two places.';
    renderOptions();
    drawOption(true);
    if (p.options.length && state.day !== 'now') otherTimes();
  } catch (err) {
    $('status').className = 'status err';
    $('status').textContent = err.message;
  }
}

function renderOptions() {
  const p = state.plan;
  $('options').innerHTML = p.options.map((o, i) => {
    const buses = o.legs.filter((l) => l.mode === 'bus');
    const strip = o.legs.map((l) => l.mode === 'walk'
      ? '<i class="walk" style="flex:' + l.minutes + '"></i>'
      : '<i class="wait" style="flex:' + l.wait_min + '"></i><i class="ride" style="flex:' + l.ride_min + '"></i>').join('');
    const pills = buses.map((l) => '<span class="pill">' + esc(l.lines[0].route) + '</span>' + (l.lines.length > 1 ? '<span class="pill more">+' + (l.lines.length - 1) + '</span>' : '')).join('<span class="arrow">›</span>') || '<span class="pill more">Walk</span>';
    const delay = o.traffic_delay_min >= 3
      ? '<span class="chip slow">+' + Math.round(o.traffic_delay_min) + ' min from traffic</span>'
      : (o.buses ? '<span class="chip ok">roads clear</span>' : '');
    const tt = o.buses ? '<span class="chip">timetable says ' + mins(o.timetable_duration_min) + '</span>' : '';
    const steps = o.legs.map((l) => l.mode === 'walk'
      ? '<div class="step"><span class="ic">🚶</span><div>Walk ' + l.distance_m + ' m to ' + esc(l.to.name) + '<small>' + mins(l.minutes) + '</small></div></div>'
      : '<div class="step bus"><span class="ic">🚌</span><div>Board at <b>' + esc(l.board.name) + '</b> around ' + l.board_at
        + '<small>Take ' + l.lines.slice(0, 5).map((x) => '<b>' + esc(x.route) + '</b>').join(', ') + (l.lines.length > 5 ? ' or ' + (l.lines.length - 5) + ' more' : '')
        + ', whichever comes first · a bus about every ' + Math.max(1, Math.round(60 / l.lines.reduce((s, x) => s + x.buses_per_hour, 0))) + ' min · wait ~' + mins(l.wait_min) + '</small>'
        + '<small>Ride ' + l.stops + ' stops, ' + l.distance_km + ' km: <b>' + mins(l.ride_min) + '</b> (usually ' + Math.round(l.ride_range_min[0]) + '–' + Math.round(l.ride_range_min[1]) + '). At ' + l.quiet_at + ' the same ride takes ' + mins(l.ride_quiet_min) + '; the timetable says ' + mins(l.ride_timetable_min) + '.</small>'
        + 'Get off at <b>' + esc(l.alight.name) + '</b> around ' + l.alight_at + '</div></div>').join('');
    return '<div class="opt ' + (i === state.selected ? 'on' : '') + '" data-i="' + i + '">'
      + '<div class="opt-head"><b>' + mins(o.duration_min) + '<small>' + Math.round(o.range_min[0]) + '–' + Math.round(o.range_min[1]) + '</small></b><span>arrive ' + o.arrive + '<br>' + (o.buses ? o.buses + (o.buses > 1 ? ' buses' : ' bus') : 'on foot') + '</span></div>'
      + '<div class="strip">' + strip + '</div><div class="pills">' + pills + '</div>'
      + '<div class="facts"><span class="chip">walk ' + mins(o.walk_min) + '</span>' + (o.buses ? '<span class="chip">wait ' + mins(o.wait_min) + '</span><span class="chip">ride ' + mins(o.ride_min) + '</span>' : '') + delay + tt + '</div>'
      + '<div class="steps">' + steps + '</div></div>';
  }).join('');
}
$('options').onclick = (e) => {
  const card = e.target.closest('.opt');
  if (!card) return;
  state.selected = +card.dataset.i;
  renderOptions();
  drawOption(true);
};

function marker(cls, text, lat, lng, title) {
  const el = document.createElement('div');
  el.className = 'mk ' + cls;
  el.textContent = text;
  el.title = title || '';
  state.markers.push(new maplibregl.Marker({ element: el }).setLngLat([lng, lat]).addTo(map));
}

function drawOption(fit) {
  state.markers.forEach((m) => m.remove());
  state.markers = [];
  const o = state.plan?.options[state.selected];
  if (!map.getSource('ride')) return;
  const ride = [], walk = [], colors = ['#2563eb', '#7c3aed', '#0891b2'];
  let n = 0;
  const bounds = new maplibregl.LngLatBounds();
  for (const l of o?.legs || []) {
    if (l.mode === 'bus') {
      const coords = line(l.polyline);
      coords.forEach((c) => bounds.extend(c));
      ride.push({ type: 'Feature', properties: { color: colors[n++ % 3] }, geometry: { type: 'LineString', coordinates: coords } });
      marker('stop', '', l.board.lat, l.board.lng, 'Board: ' + l.board.name);
      marker('stop', '', l.alight.lat, l.alight.lng, 'Get off: ' + l.alight.name);
    } else {
      const coords = [[l.from.lng, l.from.lat], [l.to.lng, l.to.lat]];
      coords.forEach((c) => bounds.extend(c));
      walk.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } });
    }
  }
  map.getSource('ride').setData({ type: 'FeatureCollection', features: ride });
  map.getSource('walk').setData({ type: 'FeatureCollection', features: walk });
  if (state.plan) {
    marker('a', 'A', state.plan.origin.lat, state.plan.origin.lng, state.plan.origin.name);
    marker('b', 'B', state.plan.destination.lat, state.plan.destination.lng, state.plan.destination.name);
  }
  if (fit && !bounds.isEmpty()) map.fitBounds(bounds, { padding: pad(), duration: 600, maxZoom: 15 });
}

async function otherTimes() {
  const times = ['06:00', '09:00', '13:00', '18:30', '22:00'];
  const mine = state.plan;
  const rows = await Promise.all(times.map((t) => api('/api/journeys', { from: place('from'), to: place('to'), ...when(t), options: 1 }).catch(() => null)));
  if (state.plan !== mine) return;
  const best = rows.map((r) => r?.options[0]).map((o) => (o ? { total: o.duration_min, ride: o.ride_min, tt: o.timetable_duration_min } : null));
  const max = Math.max(...best.filter(Boolean).map((b) => Math.max(b.total, b.tt)), 1);
  $('otherTimes').innerHTML = '<div class="other"><h3>The same trip at other times</h3><div class="bars">'
    + times.map((t, i) => (best[i] ? '<span>' + t + '</span><span class="bar" style="width:' + (best[i].total / max * 100).toFixed(1) + '%"></span><span>' + mins(best[i].total) + '</span>' : '')).join('')
    + '<span>timetable</span><span class="tt" style="width:' + ((best.find(Boolean)?.tt || 0) / max * 100).toFixed(1) + '%"></span><span>' + mins(best.find(Boolean)?.tt || 0) + '</span>'
    + '</div></div>';
}

// ---------------------------------------------------------------- traffic
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
async function loadTraffic() {
  if (!state.segments) {
    $('tnote').textContent = 'Loading 15,000 stretches of road…';
    const s = await api('/api/segments');
    state.segments = { type: 'FeatureCollection', features: s.polylines.map((enc, i) => (enc ? { type: 'Feature', properties: { i }, geometry: { type: 'LineString', coordinates: line(enc) } } : null)).filter(Boolean) };
    map.getSource('seg')?.setData(state.segments);
  }
  if (!state.traffic[state.tday]) {
    const t = await api('/api/traffic', { day: state.tday });
    state.traffic[state.tday] = { n: t.segments, bins: t.bins, speeds: b64(t.speeds), city: t.city_kmh_by_half_hour, seen: b64(t.observed).reduce((a, b) => a + b, 0) };
  }
  paintTraffic();
}
function paintTraffic() {
  const t = state.traffic[state.tday];
  if (!t || !map.getSource('seg')) return;
  const bin = +$('tslider').value;
  for (let i = 0; i < t.n; i++) map.setFeatureState({ source: 'seg', id: i }, { v: t.speeds[i * t.bins + bin] });
  $('tclock').textContent = String(Math.floor(bin / 2)).padStart(2, '0') + ':' + (bin % 2 ? '30' : '00');
  $('tcity').textContent = 'city buses moving at ' + t.city[bin].toFixed(1) + ' km/h';
  const w = 360, h = 74, lo = Math.min(...t.city) - 1, hi = Math.max(...t.city) + 1;
  const x = (i) => (i / 47) * w, y = (v) => h - 4 - ((v - lo) / (hi - lo)) * (h - 10);
  $('spark').innerHTML = '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><path d="M' + t.city.map((v, i) => x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' L') + '" fill="none" stroke="var(--accent-2)" stroke-width="2"/>'
    + '<line x1="' + x(bin) + '" x2="' + x(bin) + '" y1="0" y2="' + h + '" stroke="var(--ink-3)" stroke-dasharray="3 3"/><circle cx="' + x(bin) + '" cy="' + y(t.city[bin]) + '" r="4" fill="var(--accent)"/></svg>';
  $('tnote').textContent = t.seen.toLocaleString() + ' of ' + t.n.toLocaleString() + ' stretches have GPS readings. Speed is while moving between stops, averaged over October 2026 for this kind of day and half-hour.';
}
$('tslider').oninput = paintTraffic;
$('tday').onclick = (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  state.tday = b.dataset.day;
  [...$('tday').children].forEach((x) => x.classList.toggle('on', x === b));
  loadTraffic();
};
$('play').onclick = () => {
  if (state.playing) { clearInterval(state.playing); state.playing = null; $('play').textContent = '▶'; return; }
  $('play').textContent = '❚❚';
  state.playing = setInterval(() => { $('tslider').value = (+$('tslider').value + 1) % 48; paintTraffic(); }, 450);
};

// ---------------------------------------------------------------- live buses
// Between two polls a bus glides from where it was to where it is. Hops under 5 m are GPS noise and are
// ignored; hops over 2 km are not a drive, so the dot jumps.
const NOISE_M = 5, TELEPORT_M = 2000, GLIDE_MS = 1500;
const metres = (a, b) => Math.hypot((a[0] - b[0]) * 97700, (a[1] - b[1]) * 111195);
function busData(k) {
  const features = [];
  for (const [id, b] of state.buses) {
    const lng = b.from[0] + (b.to[0] - b.from[0]) * k, lat = b.from[1] + (b.to[1] - b.from[1]) * k;
    features.push({ type: 'Feature', properties: { id, r: b.route, a: b.age, t: b.onTrip }, geometry: { type: 'Point', coordinates: [lng, lat] } });
  }
  return { type: 'FeatureCollection', features };
}
async function loadLive() {
  const [v, s] = await Promise.all([api('/api/live/vehicles').catch(() => ({ rows: [] })), api('/api/live/status').catch(() => ({ usable: false }))]);
  const seen = new Set();
  for (const [id, lat, lng, route, age, onTrip] of v.rows) {
    seen.add(id);
    const old = state.buses.get(id), to = [lng, lat];
    const hop = old ? metres(old.to, to) : Infinity;
    state.buses.set(id, { from: !old || hop > TELEPORT_M ? to : old.to, to: old && hop < NOISE_M ? old.to : to, route, age, onTrip });
  }
  for (const id of [...state.buses.keys()]) if (!seen.has(id)) state.buses.delete(id);
  cancelAnimationFrame(state.busAnim);
  const t0 = performance.now();
  let lastDraw = 0;
  const frame = (now) => {
    const k = Math.min(1, (now - t0) / GLIDE_MS);
    if (now - lastDraw >= 100 || k === 1) { map.getSource('buses')?.setData(busData(k)); lastDraw = now; }   // 10 frames a second is enough
    if (k < 1) state.busAnim = requestAnimationFrame(frame);
  };
  state.busAnim = requestAnimationFrame(frame);
  const onTrip = v.rows.filter((r) => r[5]).length;
  $('live').innerHTML = '<div class="acc">'
    + '<p class="live-head"><span class="live-dot' + (v.rows.length ? '' : ' off') + '"></span>' + (v.rows.length ? v.rows.length.toLocaleString() + ' buses reporting' : 'No live feed') + '</p>'
    + '<p class="note">As of ' + esc(v.as_of || '') + ' IST · refreshed every 30 s from the Transport Stack real-time feed.</p>'
    + '<div class="kpis"><div class="kpi"><b>' + onTrip.toLocaleString() + '</b><span><i class="swatch" style="background:#2563eb"></i>followed along a route right now</span></div>'
    + '<div class="kpi"><b>' + (v.rows.length - onTrip).toLocaleString() + '</b><span><i class="swatch" style="background:#94a3b8"></i>parked, off route, or just seen</span></div>'
    + (s.usable
      ? '<div class="kpi"><b>' + (s.city_vs_usual_pct >= 0 ? '+' : '') + s.city_vs_usual_pct + '%</b><span>travel time against a usual ' + new Date().toLocaleDateString('en-IN', { weekday: 'long' }) + ' at this hour</span></div>'
        + '<div class="kpi"><b>' + s.runs_last_20_min.toLocaleString() + '</b><span>stop-to-stop runs timed in the last 20 min, on ' + s.stretches_with_live_data.toLocaleString() + ' stretches</span></div>'
      : '<div class="kpi" style="grid-column:1/-1"><b>Warming up' + (s.warm_up_min_left ? ': ' + Math.ceil(s.warm_up_min_left) + ' min left' : '') + '</b><span>Live estimates switch on after a full 20-minute window of timed runs (' + (s.runs_last_20_min || 0).toLocaleString() + ' runs so far). Until then trips leaving now use the typical-day model.</span></div>')
    + '</div><p class="note">Ask for a trip leaving <b>Now</b> on the first tab and the estimate uses these readings. Click a bus for its route.</p></div>';
}
map.on('click', 'buses', (e) => {
  const p = e.features[0].properties;
  new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat)
    .setHTML('<b>' + esc(p.r || 'No route') + '</b><br>' + esc(p.id) + ' · ' + p.a + ' s ago').addTo(map);
});

// ---------------------------------------------------------------- accuracy
async function loadAccuracy() {
  if ($('accuracy').dataset.done) return;
  const [a, tt] = await Promise.all([api('/api/about'), api('/api/traveltime', { from: 'ISBT Kashmere Gate Terminal', to: 'Nehru Place Terminal' }).catch(() => null)]);
  const b = a.backtest;
  const all = b?.trips.all;
  const row = (name, r) => '<tr><td>' + name + '</td><td>' + r.model.mae_min.toFixed(1) + '</td><td>' + r.flat.mae_min.toFixed(1) + '</td><td>' + r.timetable.mae_min.toFixed(1) + '</td><td>' + r.skill_vs_timetable_pct + '%</td></tr>';
  let chart = '';
  if (tt) {
    const w = 360, h = 150, pts = tt.by_half_hour, hi = Math.max(...pts.map((p) => p.p90_min), tt.timetable_min) * 1.05;
    const x = (i) => 26 + (i / 47) * (w - 30), y = (v) => h - 18 - (v / hi) * (h - 26);
    chart = '<h3>' + esc(tt.from.name) + ' → ' + esc(tt.to.name) + ', route ' + esc(tt.route) + ' (' + tt.distance_km + ' km)</h3><div class="chart"><svg viewBox="0 0 ' + w + ' ' + h + '">'
      + '<path d="M' + pts.map((p, i) => x(i) + ' ' + y(p.p90_min)).join(' L') + ' L' + pts.map((p, i) => x(i) + ' ' + y(p.p10_min)).reverse().join(' L') + ' Z" fill="var(--accent-2)" opacity="0.18"/>'
      + '<path d="M' + pts.map((p, i) => x(i) + ' ' + y(p.ride_min)).join(' L') + '" fill="none" stroke="var(--accent-2)" stroke-width="2.2"/>'
      + '<line x1="26" x2="' + w + '" y1="' + y(tt.timetable_min) + '" y2="' + y(tt.timetable_min) + '" stroke="var(--ink-3)" stroke-width="1.6" stroke-dasharray="5 4"/>'
      + [0, 12, 24, 36, 47].map((i) => '<text x="' + x(i) + '" y="' + (h - 4) + '" font-size="10" text-anchor="middle" fill="var(--ink-2)">' + pts[i].at + '</text>').join('')
      + [0.5, 1].map((f) => '<text x="0" y="' + (y(hi * f * 0.95) + 3) + '" font-size="10" fill="var(--ink-2)">' + Math.round(hi * f * 0.95) + '</text>').join('')
      + '</svg></div><p class="note">Blue: minutes on the bus by time of day on a weekday, with the range that 8 in 10 real trips fell in. Dashed: the timetable, ' + tt.timetable_min + ' min at every hour.</p>';
  }
  $('accuracy').innerHTML = '<div class="acc">'
    + '<div class="kpis"><div class="kpi"><b>' + (a.data.raw_rows / 1e6).toFixed(0) + ' M</b><span>GPS rows read, ' + a.data.days + ' days</span></div>'
    + '<div class="kpi"><b>' + (a.data.trips_rebuilt / 1e6).toFixed(2) + ' M</b><span>bus trips rebuilt from them</span></div>'
    + (all ? '<div class="kpi"><b>' + all.model.mae_min.toFixed(1) + ' min</b><span>average error on unseen days</span></div><div class="kpi"><b>' + all.timetable.mae_min.toFixed(1) + ' min</b><span>the timetable, same trips</span></div>' : '') + '</div>'
    + (b ? '<h3>Tested on ' + b.test_cases.toLocaleString() + ' real trips, ' + b.test_days[0] + '–' + b.test_days.at(-1) + ' Oct (trained on ' + b.train_days[0] + '–' + b.train_days.at(-1) + ' Oct)</h3>'
      + '<table><tr><th>Average error, min</th><th>Raftaar</th><th>No time of day</th><th>Timetable</th><th>Error removed</th></tr>'
      + Object.entries(b.trips).filter(([k, r]) => r.model.n).map(([k, r]) => row(esc(k.replace(/^(distance|time|day): /, '')), r)).join('') + '</table>'
      + '<p class="note">' + b.range_coverage.inside_10_to_90_pct + '% of real trips landed inside the range shown to the rider.</p>' : '')
    + chart + '</div>';
  $('accuracy').dataset.done = '1';
}

fromLink();   // last, once everything above exists
