// The demo video, recorded from the running app by a headless Chrome of its own: every scene is scripted,
// so the video can be re-made after any change.
//
//   node scripts/demo_video.mjs [base URL] [out dir]      default: the public demo, into dist/video
//   then: see the ffmpeg line printed at the end (adds the music bed if dist/video/music.mp3 exists)
//
// Needs Node 22+, Google Chrome and ffmpeg.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'https://gyanps-raftaar.hf.space';
const OUT = process.argv[3] || 'dist/video';
const W = 1280, H = 720, SCALE = 2;                         // the page is laid out at 1280 x 720 and drawn at 2560 x 1440
// The graphics card draws the map far faster and cleaner than software rendering. RAFTAAR_GL=software goes back to that.
const GL = process.env.RAFTAAR_GL === 'software' ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] : ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// With a voiceover (scripts/voiceover.py), each scene is held for as long as its line takes to say.
const VO = existsSync(join(OUT, 'vo', 'durations.json')) ? JSON.parse(readFileSync(join(OUT, 'vo', 'durations.json'), 'utf8')) : {};
const marks = {};
const scene = (key) => { marks[key] = Date.now(); };
const hold = (key, minMs) => sleep(Math.max(0, Math.max(minMs, (VO[key] || 0) * 1000 + 1100) - (Date.now() - marks[key])));
rmSync(join(OUT, 'frames'), { recursive: true, force: true });
mkdirSync(join(OUT, 'frames'), { recursive: true });

const port = 9300 + Math.floor(Math.random() * 400);
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', ['--headless=new', '--remote-debugging-port=' + port,
  '--user-data-dir=' + mkdtempSync(join(tmpdir(), 'raftaar-video-')), ...GL, '--hide-scrollbars',
  '--no-first-run', '--window-size=' + W + ',' + H, 'about:blank'], { stdio: 'ignore' });

let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  target = await fetch('http://127.0.0.1:' + port + '/json/list').then((r) => r.json()).then((l) => l.find((t) => t.type === 'page')).catch(() => null);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let id = 0;
const pending = new Map();
// Chrome pushes a frame whenever the picture changes, stamped with its real time.
const frames = [];
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  else if (d.method === 'Page.screencastFrame') {
    const name = 'f' + String(frames.length).padStart(5, '0') + '.jpg';
    writeFileSync(join(OUT, 'frames', name), Buffer.from(d.params.data, 'base64'));
    frames.push({ name, t: d.params.metadata.timestamp * 1000 });
    ws.send(JSON.stringify({ id: ++id, method: 'Page.screencastFrameAck', params: { sessionId: d.params.sessionId } }));
  }
};
const send = (method, params = {}) => new Promise((res) => { pending.set(++id, res); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expression) => (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.result?.value;
const until = async (expression, ms = 30000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await js('!!(' + expression + ')')) return true; await sleep(200); } return false; };

const cap = (text) => js('window.__cap(' + JSON.stringify(text) + ')');
const card = (html) => js('window.__card(' + JSON.stringify(html) + ')');
const click = (sel) => js('document.querySelector(' + JSON.stringify(sel) + ').click()');

await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: SCALE, mobile: false });
await send('Page.navigate', { url: BASE + '/?from=ISBT%20Kashmere%20Gate&to=Nehru%20Place%20Terminal&time=08:30&day=weekday' });
await until("document.readyState === 'complete' && document.body");
await js(`(() => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;left:calc(50% + 208px);bottom:30px;transform:translateX(-50%);width:min(700px,52vw);padding:13px 20px;border-radius:14px;background:rgba(30,64,175,.95);color:#fff;font:600 18px/1.4 -apple-system,BlinkMacSystemFont,Inter,sans-serif;text-align:center;z-index:99999;opacity:0;transition:opacity .45s;box-shadow:0 10px 36px rgba(30,58,138,.38)';
  document.body.appendChild(c);
  const k = document.createElement('div');
  k.style.cssText = 'position:fixed;inset:0;display:grid;place-items:center;background:linear-gradient(135deg,#1e3a8a 0%,#1e40af 55%,#2563eb 100%);color:#fff;z-index:100000;opacity:1;transition:opacity .7s;pointer-events:none;text-align:center;font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif';
  document.body.appendChild(k);
  // Chrome sends a frame only when the picture changes, and can drop the last change before a still scene.
  // A two-pixel element that never stops moving keeps the frames coming, so nothing is lost or late.
  const beat = document.createElement('div');
  beat.style.cssText = 'position:fixed;left:0;bottom:0;width:2px;height:2px;z-index:100001;background:rgba(0,0,0,.02);animation:__beat .16s steps(2) infinite';
  const kf = document.createElement('style');
  kf.textContent = '@keyframes __beat { from { transform: translateX(0) } to { transform: translateX(2px) } }';
  document.head.appendChild(kf);
  document.body.appendChild(beat);
  window.__cap = (t) => { if (t) c.textContent = t; c.style.opacity = t ? 1 : 0; };
  window.__card = (h) => { if (h) k.innerHTML = h; k.style.opacity = h ? 1 : 0; };
})()`);

// 1. title
await card('<div><div style="font-size:64px;font-weight:800;letter-spacing:-.03em;color:#fff">Raftaar</div>'
  + '<div style="font-size:26px;margin-top:10px">Delhi bus times, learned from the buses&rsquo; own GPS</div>'
  + '<div style="font-size:16px;margin-top:26px;color:#bfdbfe">Transport Stack Hackathon 2026 &middot; Problem 1 &middot; Team Raftaar, ABC Express</div></div>');
await until("document.querySelector('.other')", 40000);
// Behind the title card: open the Speeds and Accuracy tabs once so their data is already loaded when their scenes come.
await click('.tabs [data-tab="traffic"]');
await until("window.__map.getSource('seg') && window.__map.querySourceFeatures('seg').length > 100 && document.getElementById('tcity').textContent.length > 5", 90000);
await click('.tabs [data-tab="accuracy"]');
await until("document.getElementById('accuracy').dataset.done", 40000);
await click('.tabs [data-tab="plan"]');
await sleep(1500);
await send('Page.startScreencast', { format: 'jpeg', quality: 95, maxWidth: W * SCALE, maxHeight: H * SCALE, everyNthFrame: 1 });
await sleep(700);
scene('s1');
await hold('s1', 3800);
await card('');
await sleep(900);

// 2. the brief's own example, morning
scene('s2');
await cap('Kashmere Gate to Nehru Place, leaving 8:30 am on a weekday: where to walk, which bus to board, where to change, and how long it takes.');
await hold('s2', 6500);
scene('s3');
await js("document.getElementById('tab-plan').scrollTo({ top: 330, behavior: 'smooth' })");
await cap('At each stop it lists every route worth taking. A rider takes whichever comes first.');
await hold('s3', 6000);

// 3. the same trip in the evening peak
await js("document.getElementById('tab-plan').scrollTo({ top: 0, behavior: 'smooth' })");
await js("document.getElementById('time').value = '18:30'; document.getElementById('form').requestSubmit()");
await sleep(600);
await until("document.querySelector('.other') && document.getElementById('status').textContent.includes('18:30')", 30000);
scene('s4');
await cap('The same trip at 6:30 pm takes half an hour longer. The published timetable gives one figure for the whole day.');
await hold('s4', 6000);
scene('s5');
await js("document.getElementById('tab-plan').scrollTo({ top: 9999, behavior: 'smooth' })");
await cap('Every answer shows the same trip at other times of day, next to the timetable.');
await hold('s5', 5500);

// 4. the city, hour by hour
await click('.tabs [data-tab="traffic"]');
await until("window.__map.getSource('seg') && window.__map.querySourceFeatures('seg').length > 100 && document.getElementById('tcity').textContent.length > 5", 60000);
await js("document.getElementById('tslider').value = 8; document.getElementById('tslider').dispatchEvent(new Event('input'))");
await sleep(1200);
scene('s6');
await cap('One month of bus GPS data, 913 million rows: how fast buses really move on 15,000 stretches of road.');
await hold('s6', 4500);
scene('s7');
await js("document.getElementById('tslider').value = 9; document.getElementById('tslider').dispatchEvent(new Event('input'))");
await click('#play');
await cap('Green at dawn, red by evening. This is the traffic model, by half-hour and by kind of day.');
await hold('s7', 14000);                                      // 5 am to about 8 pm at 450 ms a half-hour
await click('#play');

// 5. live
await click('.tabs [data-tab="live"]');
await until("document.querySelector('#live .kpi') && window.__map.querySourceFeatures('buses').length > 500", 40000);
scene('s8');
await cap('Right now: the live feed. Each bus is followed along its route, minute by minute.');
await hold('s8', 7500);
await click('.tabs [data-tab="plan"]');
await click('#day [data-day="now"]');
await js("document.getElementById('tab-plan').scrollTo({ top: 0 }); document.getElementById('form').requestSubmit()");
await sleep(600);
await until("document.querySelector('.opt') && document.getElementById('status').textContent.includes('estimate') || document.getElementById('status').textContent.includes('Live')", 30000);
scene('s9');
await cap('Leaving now, the estimate is adjusted by what buses did in the last 20 minutes.');
await hold('s9', 6500);

// 6. accuracy
await click('.tabs [data-tab="accuracy"]');
await until("document.getElementById('accuracy').dataset.done", 40000);
scene('s10');
await cap('Tested on 1.4 million real bus trips from days the model never saw: 3.0 minutes average error.');
await hold('s10', 6000);
scene('s11');
await cap('On the same trips the timetable is off by 5.4 minutes. Google Maps bus times were off by 9.7 minutes on the routes we checked.');
await js("document.getElementById('tab-accuracy').scrollTo({ top: 9999, behavior: 'smooth' })");
await hold('s11', 7000);

// 7. end card
await cap('');
await card('<div><div style="font-size:54px;font-weight:800;letter-spacing:-.03em;color:#fff">Raftaar</div>'
  + '<div style="font-size:24px;margin-top:14px">gyanps-raftaar.hf.space</div>'
  + '<div style="font-size:19px;margin-top:8px;color:#dbeafe">github.com/gyanps/raftaar</div>'
  + '<div style="font-size:17px;margin-top:22px;color:#dbeafe">Open source &middot; Python, FastAPI, MapLibre &middot; API reference at /docs</div>'
  + '<div style="font-size:15px;margin-top:10px;color:#bfdbfe">Data: Transport Stack / IIIT Delhi &middot; Map &copy; OpenStreetMap contributors</div></div>');
scene('s12');
await hold('s12', 5000);

const ended = (await send('Page.stopScreencast'), Date.now());
await sleep(300);
ws.close();

// ---- frames -> video ---------------------------------------------------------------------------------
// The last picture (the end card) stays up until recording stopped: Chrome only sends a frame when something changes.
const lines = frames.map((f, i) => "file 'frames/" + f.name + "'\nduration " + (Math.max(0.01, (frames[i + 1]?.t ?? Math.max(ended, f.t + 1500)) - f.t) / 1000).toFixed(3)).join('\n');
writeFileSync(join(OUT, 'frames.txt'), lines + "\nfile 'frames/" + frames.at(-1).name + "'\n");
const seconds = (Math.max(ended, frames.at(-1).t + 1500) - frames[0].t) / 1000;
const gpu = await fetch('http://127.0.0.1:' + port + '/json/version').then((r) => r.json()).then((v) => v.Browser).catch(() => '');
console.log(frames.length + ' frames over ' + seconds.toFixed(1) + ' s (' + (frames.length / seconds).toFixed(1) + ' per second), graphics: ' + (process.env.RAFTAAR_GL === 'software' ? 'software' : 'metal') + ' ' + gpu);
writeFileSync(join(OUT, 'scenes.json'), JSON.stringify(Object.fromEntries(Object.entries(marks).map(([k, t]) => [k, +((t - frames[0].t) / 1000).toFixed(2)])), null, 1));
const mix = spawnSync('node', ['scripts/mix_video.mjs', OUT], { encoding: 'utf8' });
console.log((mix.stdout + mix.stderr).trim());
chrome.kill();
if (mix.status) process.exit(1);
