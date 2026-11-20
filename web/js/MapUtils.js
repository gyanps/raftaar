/**
 * MapUtils: geometry helpers for the map screen. All functions are pure and
 * have no map-SDK dependency. Points are {lat, lng}; distances are km unless
 * a name says otherwise.
 */

/**
 * Position an absolutely-placed map overlay at container-pixel (x, y) using a
 * GPU-composited `transform` instead of layout-triggering `left`/`top`.
 *
 * Writing left/top forces a style-recalc + reflow + paint on every marker every
 * frame during a pan/zoom gesture (move fires once per frame), which is the main
 * source of map stutter at scale. `transform: translate()` is composite-only and
 * skips layout entirely.
 *
 * `centering` is the per-layer anchor offset that used to live in each marker's
 * base cssText (most markers center on the point with 'translate(-50%, -50%)';
 * pin-style markers like branches anchor at the bottom with
 * 'translate(-50%, -100%)'). It is folded into the same transform so the anchor
 * is preserved. Pass the value matching that layer's original base transform.
 *
 * `scale` shrinks/grows the whole overlay (perspective size attenuation under
 * map pitch — see MarkerManager._cameraSnap). Appended AFTER the centering
 * translate, so with the default -50%,-50% centering the element's center
 * stays exactly on (x, y) at any scale. 1 keeps the legacy two-part transform
 * string byte-identical (flat-view parity).
 */
export function positionOverlay(el, x, y, centering = 'translate(-50%, -50%)', scale = 1) {
    el.style.transform = scale !== 1
        ? `translate(${x}px, ${y}px) ${centering} scale(${scale})`
        : `translate(${x}px, ${y}px) ${centering}`;
}

/**
 * True when (lat, lng) is a real plottable coordinate: finite numbers inside
 * world range and not the (0, 0) null-island sentinel. World-range only — the
 * India operating-envelope check is the backend's job; this guard exists so a
 * NaN/undefined that slips into marker state is hidden instead of being pinned
 * at whatever pixel project() falls back to.
 */
export function isPlottable(lat, lng) {
    return Number.isFinite(lat) && Number.isFinite(lng)
        && lat >= -90 && lat <= 90
        && lng >= -180 && lng <= 180
        && !(lat === 0 && lng === 0);
}

/**
 * Per-frame context for filtering garbage projections under map pitch.
 *
 * SVG-overlay polylines reproject every vertex through map.project() each
 * frame. With the camera pitched, ground points behind the camera plane
 * (w <= 0 in clip space) project MIRRORED through the viewport — often landing
 * inside it — so joining all vertices paints streaks across the screen. A
 * pixel-magnitude cap alone can't catch them (far-behind points converge to a
 * mirrored vanishing point near the viewport center).
 *
 * Geometry: MapLibre's default fov (≈36.87°) puts the camera ~1.5 viewport
 * heights from the map center, along the view ray. A ground point s metres
 * behind the center (opposite the bearing direction) has camera depth
 * w = camDist − s·sin(pitch), crossing the camera plane at
 * s = camDist / sin(pitch). The 0.8 factor drops vertices slightly before the
 * exact plane — those project far below the viewport bottom anyway.
 *
 * Returns null when pitch <= 2°: the projection is affine there, garbage is
 * impossible, and callers keep the cheap single-run path. pxPerMeter must be
 * probed PERPENDICULAR to the bearing (screen-horizontal at center), where
 * pitch foreshortening is zero — see RouteManager._groundFilterCtx().
 * The hard caps catch near-plane blowups (w → 0⁺ → huge coordinates); 20×
 * viewport is generous so legitimate far geometry at low pitch (horizon many
 * viewport-heights above the top edge) is never truncated.
 */
export function computeGroundFilterCtx({ pitch, bearing, centerLng, centerLat, pxPerMeter, width, height }) {
    if (!(pitch > 2) || !width || !height || !(pxPerMeter > 0)) return null;
    const camDistPx = 1.5 * height;
    const sinP = Math.sin(pitch * Math.PI / 180);
    const bRad = bearing * Math.PI / 180;
    return {
        centerLng,
        centerLat,
        cosLat: Math.cos(centerLat * Math.PI / 180),
        sinB: Math.sin(bRad),
        cosB: Math.cos(bRad),
        behindLimitM: 0.8 * (camDistPx / sinP) / pxPerMeter,
        hardCapX: 20 * width,
        hardCapY: 20 * height,
    };
}

/**
 * Probe the live camera and build the behind-camera filter context for this
 * frame (null at pitch <= 2 — affine projection, nothing to filter).
 * pxPerMeter is probed PERPENDICULAR to the bearing (screen-horizontal at the
 * view center), where pitch foreshortening doesn't shrink pixel distances.
 * Shared by RouteManager (route polylines) and MarkerManager (vehicle cull) —
 * call once per frame and pass the ctx down, never per element.
 */
export function buildGroundFilterCtx(mapManager) {
    try {
        const pitch = mapManager.getPitch ? mapManager.getPitch() : 0;
        if (pitch <= 2) return null;
        // Cached dims when the adapter provides them (forced-layout reads
        // otherwise — this runs up to 5× per pitched frame across layers).
        const vp = mapManager.getViewportSize?.();
        const container = vp ? null : mapManager.getContainer();
        const width = vp?.w ?? (container?.clientWidth || 0);
        const height = vp?.h ?? (container?.clientHeight || 0);
        const center = mapManager.getCenter?.();
        if (!width || !height || !center) return null;
        const bearing = mapManager.getBearing?.() || 0;
        const perpRad = (bearing + 90) * Math.PI / 180;
        const probeM = 1000;
        const cosLat = Math.cos(center.lat * Math.PI / 180) || 1e-9;
        const p0 = mapManager.project([center.lng, center.lat]);
        const p1 = mapManager.project([
            center.lng + (probeM * Math.sin(perpRad)) / (111320 * cosLat),
            center.lat + (probeM * Math.cos(perpRad)) / 110540,
        ]);
        if (!p0 || !p1) return null;
        return computeGroundFilterCtx({
            pitch,
            bearing,
            centerLng: center.lng,
            centerLat: center.lat,
            pxPerMeter: Math.hypot(p1.x - p0.x, p1.y - p0.y) / probeM,
            width,
            height,
        });
    } catch (e) {
        return null;
    }
}

/**
 * True when a [lng, lat] ground point sits behind the pitched camera's plane
 * (its projection is mirrored garbage). Always false with ctx == null.
 * Signed forward distance (m) from the map center along the bearing; planar
 * approximation is plenty at corridor scale.
 */
export function isCoordBehindCamera(coord, ctx) {
    if (!ctx) return false;
    const dxM = (coord[0] - ctx.centerLng) * ctx.cosLat * 111320;
    const dyM = (coord[1] - ctx.centerLat) * 110540;
    return (dxM * ctx.sinB + dyM * ctx.cosB) < -ctx.behindLimitM;
}

/**
 * Project [[lng, lat], ...] into an SVG path `d`, splitting into separate
 * `M…L…` runs wherever a vertex is invalid under the current camera (behind
 * the camera plane, non-finite, or beyond the blowup caps — see
 * computeGroundFilterCtx). With ctx == null the output is one continuous run,
 * equivalent to the old <polyline points> join. Single-vertex runs are
 * dropped so round line caps don't leave stray dots.
 *
 * @param {number[][]} coords - [[lng, lat], ...]
 * @param {function} project - ([lng,lat]) => {x, y}
 * @param {object|null} ctx - from computeGroundFilterCtx()
 */
export function projectCoordsToPathD(coords, project, ctx = null) {
    const parts = [];
    let run = [];
    const flush = () => {
        if (run.length >= 2) parts.push('M' + run.join(' L'));
        run = [];
    };
    for (const coord of coords) {
        let valid = !isCoordBehindCamera(coord, ctx);
        if (valid) {
            const p = project(coord);
            if (!p || !Number.isFinite(p.x) || !Number.isFinite(p.y)
                || (ctx && (Math.abs(p.x) > ctx.hardCapX || Math.abs(p.y) > ctx.hardCapY))) {
                valid = false;
            } else {
                run.push(`${p.x},${p.y}`);
            }
        }
        if (!valid) flush();
    }
    flush();
    return parts.join(' ');
}

/**
 * Sample a ground circle's circumference as a closed [[lng, lat], ...] ring
 * (last point repeats the first so a stroked path closes). Built once per
 * geofence — per-frame rendering just projects these through
 * projectCoordsToPathD, which is what makes circle geofences pixel-exact
 * under pitch/bearing: the projection itself produces the correct
 * lying-on-the-ground ellipse instead of a screen-space circle sized off a
 * single foreshortened north probe. Planar offsets (same 111320/110540
 * constants as the helpers below) are plenty at geofence scale; 48 segments
 * keep chord error ≈ 0.2% of radius.
 */
export function circleRingCoords(centerLat, centerLng, radiusMeters, segments = 48) {
    const coords = [];
    const latScale = radiusMeters / 110540;
    const lngScale = radiusMeters / (111320 * (Math.cos(centerLat * Math.PI / 180) || 1e-9));
    for (let i = 0; i <= segments; i++) {
        const t = (i % segments) * 2 * Math.PI / segments;
        coords.push([centerLng + Math.sin(t) * lngScale, centerLat + Math.cos(t) * latScale]);
    }
    return coords;
}

export function haversineDistance(lat1, lng1, lat2, lng2) {
    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLng = (lng2 - lng1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function isPointInCircle(lat, lng, centerLat, centerLng, radiusMeters) {
    const distance = haversineDistance(lat, lng, centerLat, centerLng) * 1000;
    return distance <= radiusMeters;
}

export function isPointInPolygon(lat, lng, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const xi = polygon[i][0], yi = polygon[i][1];
        const xj = polygon[j][0], yj = polygon[j][1];
        if (((yi > lng) !== (yj > lng)) &&
            (lat < (xj - xi) * (lng - yi) / (yj - yi) + xi)) {
            inside = !inside;
        }
    }
    return inside;
}

export function distanceToSegment(lat, lng, lat1, lng1, lat2, lng2) {
    const A = lat - lat1, B = lng - lng1;
    const C = lat2 - lat1, D = lng2 - lng1;
    const dot = A * C + B * D;
    const lenSq = C * C + D * D;
    let t = lenSq !== 0 ? Math.max(0, Math.min(1, dot / lenSq)) : 0;
    const closestLat = lat1 + t * C;
    const closestLng = lng1 + t * D;
    return haversineDistance(lat, lng, closestLat, closestLng);
}

/**
 * Build an arc-length cache for a path expressed as [[lng, lat], ...] points.
 * Returns {segLenKm, cumKm, bearingDeg, totalKm} where:
 *   - segLenKm[i] = haversine length of segment path[i]→path[i+1]
 *   - cumKm[i]    = arc length from path[0] to path[i]; cumKm[0] === 0
 *   - bearingDeg[i] = initial bearing of segment i in degrees, [0,360)
 * Lookup helpers downstream binary-search cumKm and linearly interpolate
 * inside the chosen segment. Zero-length segments are kept so indices align
 * with path indices, but pointAtArcLength clamps over them.
 */
export function buildArcCache(path) {
    const n = Array.isArray(path) ? path.length : 0;
    const segLenKm = new Array(Math.max(0, n - 1));
    const cumKm = new Array(n);
    const bearingDeg = new Array(Math.max(0, n - 1));
    cumKm[0] = 0;
    for (let i = 0; i < n - 1; i++) {
        const lng1 = path[i][0], lat1 = path[i][1];
        const lng2 = path[i + 1][0], lat2 = path[i + 1][1];
        const len = haversineDistance(lat1, lng1, lat2, lng2);
        segLenKm[i] = len;
        cumKm[i + 1] = cumKm[i] + len;
        bearingDeg[i] = initialBearing(lat1, lng1, lat2, lng2);
    }
    return {
        segLenKm,
        cumKm,
        bearingDeg,
        totalKm: n > 1 ? cumKm[n - 1] : 0,
    };
}

/**
 * Initial bearing (forward azimuth) from (lat1,lng1) to (lat2,lng2) in degrees [0,360).
 * Spherical formula — accurate enough for marker heading; planar approximation drifts
 * past ~10° at India-corridor scales.
 */
export function initialBearing(lat1, lng1, lat2, lng2) {
    const φ1 = lat1 * Math.PI / 180;
    const φ2 = lat2 * Math.PI / 180;
    const Δλ = (lng2 - lng1) * Math.PI / 180;
    const y = Math.sin(Δλ) * Math.cos(φ2);
    const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
    const θ = Math.atan2(y, x);
    return (θ * 180 / Math.PI + 360) % 360;
}

/**
 * Project a (lat,lng) point onto the closest segment of `path` ([[lng,lat],...]).
 * Returns {s, distKm, segIndex, t} where s is arc-length from path[0].
 * If path has <2 points, returns null.
 */
export function projectOnPath(lat, lng, path, arcCache) {
    if (!path || path.length < 2 || !arcCache) return null;
    let bestDist = Infinity;
    let bestT = 0;
    let bestIdx = 0;
    for (let i = 0; i < path.length - 1; i++) {
        const lng1 = path[i][0], lat1 = path[i][1];
        const lng2 = path[i + 1][0], lat2 = path[i + 1][1];
        const A = lat - lat1, B = lng - lng1;
        const C = lat2 - lat1, D = lng2 - lng1;
        const dot = A * C + B * D;
        const lenSq = C * C + D * D;
        const t = lenSq !== 0 ? Math.max(0, Math.min(1, dot / lenSq)) : 0;
        const closestLat = lat1 + t * C;
        const closestLng = lng1 + t * D;
        const d = haversineDistance(lat, lng, closestLat, closestLng);
        if (d < bestDist) {
            bestDist = d;
            bestT = t;
            bestIdx = i;
        }
    }
    const s = arcCache.cumKm[bestIdx] + bestT * arcCache.segLenKm[bestIdx];
    return { s, distKm: bestDist, segIndex: bestIdx, t: bestT };
}

/**
 * Look up the point on `path` at arc-length `s` (km from path[0]).
 * Returns {lat, lng, bearingDeg, segIndex}. Clamps `s` to [0, totalKm].
 */
export function pointAtArcLength(s, path, arcCache) {
    if (!path || path.length < 2 || !arcCache) return null;
    const total = arcCache.totalKm;
    let clamped = s;
    if (clamped <= 0) clamped = 0;
    else if (clamped >= total) clamped = total;

    // Binary search cumKm[] for the largest i with cumKm[i] <= clamped.
    const cum = arcCache.cumKm;
    let lo = 0, hi = cum.length - 1;
    while (lo < hi) {
        const mid = (lo + hi + 1) >>> 1;
        if (cum[mid] <= clamped) lo = mid;
        else hi = mid - 1;
    }
    let segIndex = lo;
    if (segIndex >= path.length - 1) segIndex = path.length - 2;
    const segLen = arcCache.segLenKm[segIndex] || 0;
    const t = segLen > 0 ? (clamped - cum[segIndex]) / segLen : 0;
    const lng1 = path[segIndex][0], lat1 = path[segIndex][1];
    const lng2 = path[segIndex + 1][0], lat2 = path[segIndex + 1][1];
    return {
        lat: lat1 + t * (lat2 - lat1),
        lng: lng1 + t * (lng2 - lng1),
        bearingDeg: arcCache.bearingDeg[segIndex],
        segIndex,
    };
}

export function decodePolyline(encoded) {
    const coords = [];
    let index = 0, lat = 0, lng = 0;

    while (index < encoded.length) {
        let b, shift = 0, result = 0;
        do {
            b = encoded.charCodeAt(index++) - 63;
            result |= (b & 0x1f) << shift;
            shift += 5;
        } while (b >= 0x20);
        const dlat = ((result & 1) ? ~(result >> 1) : (result >> 1));
        lat += dlat;

        shift = 0;
        result = 0;
        do {
            b = encoded.charCodeAt(index++) - 63;
            result |= (b & 0x1f) << shift;
            shift += 5;
        } while (b >= 0x20);
        const dlng = ((result & 1) ? ~(result >> 1) : (result >> 1));
        lng += dlng;

        coords.push([lat / 1e5, lng / 1e5]);
    }
    return coords;
}
