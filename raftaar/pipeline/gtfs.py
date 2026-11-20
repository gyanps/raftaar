"""Static GTFS -> the arrays every later stage runs on.

    uv run python -m raftaar.pipeline.gtfs

Writes to artifacts/gtfs/:
  routes.parquet         route_id, agency, name, description, base name, direction, sibling (UP <-> DOWN)
  stops.parquet          stop_idx, stop_id, name, lat, lng, x, y
  shapes.npz             every shape's vertices in one array, with offsets
  patterns.parquet       one row per (route, shape, stop sequence)
  pattern_stops.parquet  each pattern's stops with their distance along the shape
  segments.parquet       stop-to-stop stretches, shared by every pattern that runs them
  trips.parquet          scheduled trips: pattern + start time
  summary.json           counts and quality checks
"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import GTFS_ART, GTFS_DIR
from ..geo.planar import to_xy

# Where a shape passes a stop more than once (a loop, or out and back on the
# same road) each pass is a candidate; the stop order decides between them.
PASS_SLACK_M = 25.0
BACK_TOL_M = 1.0
INFEASIBLE = 1e6

# Two patterns share a stop-to-stop stretch only if they agree on its length.
SEG_SPLIT_ABS_M = 100.0
SEG_SPLIT_REL = 0.15


def stop_passes(sx, sy, cum, px, py, slack=PASS_SLACK_M):
    """Every pass of the shape near point P: [(chainage_m, offset_m)], one per run of nearby segments."""
    ax, ay = sx[:-1], sy[:-1]
    dx, dy = sx[1:] - ax, sy[1:] - ay
    len2 = dx * dx + dy * dy
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / np.where(len2 > 0, len2, 1.0), 0.0, 1.0)
    d = np.hypot(ax + t * dx - px, ay + t * dy - py)
    ch = cum[:-1] + t * (cum[1:] - cum[:-1])
    near = np.flatnonzero(d <= d.min() + slack)
    out = []
    start = 0
    for k in range(1, len(near) + 1):
        if k == len(near) or near[k] != near[k - 1] + 1:
            run = near[start:k]
            best = run[np.argmin(d[run])]
            out.append((float(ch[best]), float(d[best])))
            start = k
    return out


def stop_chainages(sx, sy, cum, px, py):
    """Distance along the shape of each stop, in stop order.

    Picks one pass per stop so that chainage never goes backwards and the
    total stop-to-shape offset is smallest. Returns (chainage, offset, ok);
    ok is False when no forward-only assignment exists.
    """
    if len(sx) < 2:  # a shape with a single vertex cannot carry stops
        return np.zeros(len(px)), np.hypot(px - sx[0], py - sy[0]) if len(sx) else np.full(len(px), np.inf), False
    cands = [stop_passes(sx, sy, cum, float(x), float(y)) for x, y in zip(px, py)]
    cost = [[c[1] for c in cands[0]]]
    back = [[-1] * len(cands[0])]
    for i in range(1, len(cands)):
        row_cost, row_back = [], []
        for ch, d in cands[i]:
            best, arg = np.inf, -1
            for k, (pch, _) in enumerate(cands[i - 1]):
                c = cost[i - 1][k] + (0.0 if pch <= ch + BACK_TOL_M else INFEASIBLE)
                if c < best:
                    best, arg = c, k
            row_cost.append(best + d)
            row_back.append(arg)
        cost.append(row_cost)
        back.append(row_back)
    j = int(np.argmin(cost[-1]))
    ok = cost[-1][j] < INFEASIBLE
    chain = np.zeros(len(cands))
    off = np.zeros(len(cands))
    for i in range(len(cands) - 1, -1, -1):
        chain[i], off[i] = cands[i][j]
        j = back[i][j]
    return chain, off, ok


def cluster_lengths(lengths):
    """Group sorted-able lengths; a new group starts when a length leaves the group's first by 100 m / 15%."""
    order = np.argsort(lengths)
    groups, ref = [], None
    for i in order:
        if ref is None or lengths[i] - ref > max(SEG_SPLIT_ABS_M, SEG_SPLIT_REL * ref):
            groups.append([])
            ref = lengths[i]
        groups[-1].append(int(i))
    return groups


def _secs(col: str) -> str:
    return (f"(CAST(split_part({col}, ':', 1) AS INTEGER) * 3600 + CAST(split_part({col}, ':', 2) AS INTEGER) * 60"
            f" + CAST(split_part({col}, ':', 3) AS INTEGER))")


def build() -> dict:
    GTFS_ART.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    def csv(name):
        return f"read_csv('{GTFS_DIR / name}', all_varchar=true, header=true)"

    # ---- routes: UP and DOWN are separate route_ids; pair them by name --------------------
    rows = con.execute(f"SELECT route_id, agency_id, route_short_name, route_desc FROM {csv('routes.txt')}").fetchall()
    routes = {}
    by_base = defaultdict(dict)
    for rid, agency, name, desc in rows:
        m = re.match(r"^(.*?)(UP|DOWN)$", name or "")
        base, direction = (m.group(1), m.group(2)) if m else (name, None)
        routes[int(rid)] = {"route_id": int(rid), "agency": agency, "name": name, "desc": desc, "base": base,
                            "direction": direction, "sibling": -1}
        if direction:
            by_base[(agency, base)][direction] = int(rid)
    for pair in by_base.values():
        if len(pair) == 2:
            routes[pair["UP"]]["sibling"] = pair["DOWN"]
            routes[pair["DOWN"]]["sibling"] = pair["UP"]

    # ---- stops -----------------------------------------------------------------------------
    rows = con.execute(f"""SELECT stop_id, stop_code, stop_name, CAST(stop_lat AS DOUBLE), CAST(stop_lon AS DOUBLE)
                           FROM {csv('stops.txt')} ORDER BY CAST(stop_id AS INTEGER)""").fetchall()
    stop_idx = {r[0]: i for i, r in enumerate(rows)}
    s_lat = np.array([r[3] for r in rows])
    s_lng = np.array([r[4] for r in rows])
    s_x, s_y = to_xy(s_lat, s_lng)

    # ---- shapes: one concatenated vertex array ----------------------------------------------
    sh = con.execute(f"""SELECT shape_id, CAST(shape_pt_lat AS DOUBLE) AS lat, CAST(shape_pt_lon AS DOUBLE) AS lng
                         FROM {csv('shapes.txt')} ORDER BY shape_id, CAST(shape_pt_sequence AS INTEGER)""").fetchnumpy()
    ids = np.asarray(sh["shape_id"]).astype(str)
    x, y = to_xy(sh["lat"], sh["lng"])
    first = np.r_[True, ids[1:] != ids[:-1]]
    keep = first | np.r_[True, (np.hypot(np.diff(x), np.diff(y)) > 0.01)]  # drop repeated vertices
    ids, x, y = ids[keep], x[keep], y[keep]
    lat, lng = np.asarray(sh["lat"])[keep], np.asarray(sh["lng"])[keep]
    first = np.r_[True, ids[1:] != ids[:-1]]
    offsets = np.r_[np.flatnonzero(first), len(ids)]
    shape_ids = ids[offsets[:-1]]
    shape_idx = {s: i for i, s in enumerate(shape_ids)}
    step = np.r_[0.0, np.hypot(np.diff(x), np.diff(y))]
    step[offsets[:-1]] = 0.0
    run = np.cumsum(step)
    cum = run - np.repeat(run[offsets[:-1]], np.diff(offsets))

    # ---- trips and their stop sequences -----------------------------------------------------
    trip_meta = {t: (int(r), s) for t, r, s in con.execute(
        f"SELECT trip_id, route_id, shape_id FROM {csv('trips.txt')}").fetchall()}
    st = con.execute(f"""SELECT trip_id,
                                list(stop_id ORDER BY CAST(stop_sequence AS INTEGER)) AS stops,
                                list({_secs('arrival_time')} ORDER BY CAST(stop_sequence AS INTEGER)) AS arr
                         FROM {csv('stop_times.txt')} GROUP BY trip_id""").fetchall()

    pat_key = {}
    patterns = []
    trips_out = []
    for trip_id, stops, arr in st:
        route_id, shape_id = trip_meta[trip_id]
        key = (route_id, shape_id, tuple(stops))
        p = pat_key.get(key)
        if p is None:
            p = pat_key[key] = len(patterns)
            patterns.append({"route_id": route_id, "shape_id": shape_id, "stops": stops, "sched": [a - arr[0] for a in arr],
                             "trips": 0, "full": 0})
        patterns[p]["trips"] += 1
        patterns[p]["full"] += "_FULL_" in trip_id
        trips_out.append((trip_id, route_id, p, arr[0]))

    # ---- each stop's distance along its pattern's shape ---------------------------------------
    cache = {}
    ps_rows = []           # pattern_idx, seq, stop_idx, chainage, offset, sched
    seg_obs = defaultdict(list)   # (from, to) -> [(length, pattern_idx, seq)]
    pat_rows = []
    not_monotone = 0
    for p, pat in enumerate(patterns):
        si = shape_idx[pat["shape_id"]]
        a, b = offsets[si], offsets[si + 1]
        idx = [stop_idx[s] for s in pat["stops"]]
        ck = (pat["shape_id"], tuple(idx))
        if ck not in cache:
            cache[ck] = stop_chainages(x[a:b], y[a:b], cum[a:b], s_x[idx], s_y[idx])
        chain, off, ok = cache[ck]
        not_monotone += not ok
        for seq, (s, c, o, t) in enumerate(zip(idx, chain, off, pat["sched"])):
            ps_rows.append((p, seq, s, float(c), float(o), int(t)))
        for seq in range(len(idx) - 1):
            seg_obs[(idx[seq], idx[seq + 1])].append((float(chain[seq + 1] - chain[seq]), p, seq))
        r = routes[pat["route_id"]]
        pat_rows.append((p, pat["route_id"], r["name"], r["desc"], r["direction"], r["sibling"], si, len(idx), pat["trips"],
                         "FULL" if pat["full"] * 2 >= pat["trips"] else "SHORT", float(cum[b - 1]), bool(ok),
                         float(off.max()), float(chain[0]), float(chain[-1])))

    # ---- stop-to-stop segments ------------------------------------------------------------------
    seg_rows = []
    seg_of = {}            # (pattern_idx, seq) -> seg_idx
    split_pairs = 0
    for (a_, b_), obs in seg_obs.items():
        lengths = np.array([o[0] for o in obs])
        groups = cluster_lengths(lengths)
        split_pairs += len(groups) > 1
        for g in groups:
            s = len(seg_rows)
            seg_rows.append((s, a_, b_, float(np.median(lengths[g])), len({obs[i][1] for i in g})))
            for i in g:
                seg_of[(obs[i][1], obs[i][2])] = s

    # ---- write ----------------------------------------------------------------------------------
    def table(rows_, names):
        return pa.table({n: [r[i] for r in rows_] for i, n in enumerate(names)})

    pq.write_table(pa.Table.from_pylist(list(routes.values())), GTFS_ART / "routes.parquet")
    pq.write_table(pa.table({
        "stop_idx": np.arange(len(rows)), "stop_id": [r[0] for r in rows], "stop_code": [r[1] for r in rows],
        "stop_name": [r[2] for r in rows], "lat": s_lat, "lng": s_lng, "x": s_x, "y": s_y,
    }), GTFS_ART / "stops.parquet")
    np.savez_compressed(GTFS_ART / "shapes.npz", shape_ids=shape_ids, offsets=offsets, lat=lat, lng=lng, x=x, y=y, cum=cum)
    pq.write_table(table(pat_rows, ["pattern_idx", "route_id", "route_name", "route_desc", "direction", "sibling_route_id",
                                    "shape_idx", "n_stops", "n_trips", "kind", "shape_len_m", "monotone", "max_stop_offset_m",
                                    "first_stop_m", "last_stop_m"]), GTFS_ART / "patterns.parquet")
    ps = table(ps_rows, ["pattern_idx", "seq", "stop_idx", "chainage_m", "offset_m", "sched_s"])
    ps = ps.append_column("seg_idx", pa.array([seg_of.get((r[0], r[1]), -1) for r in ps_rows], pa.int32()))
    pq.write_table(ps, GTFS_ART / "pattern_stops.parquet")
    pq.write_table(table(seg_rows, ["seg_idx", "from_stop_idx", "to_stop_idx", "length_m", "n_patterns"]),
                   GTFS_ART / "segments.parquet")
    pq.write_table(table(trips_out, ["trip_id", "route_id", "pattern_idx", "start_s"]), GTFS_ART / "trips.parquet")

    seg_len = np.array([r[3] for r in seg_rows])
    offs = np.array([r[4] for r in ps_rows])
    sched_kmh = [p_[10] / 1000 / (patterns[i]["sched"][-1] / 3600) for i, p_ in enumerate(pat_rows) if patterns[i]["sched"][-1] > 0]
    summary = {
        "routes": len(routes), "routes_with_sibling": sum(r["sibling"] >= 0 for r in routes.values()),
        "stops": len(rows), "shapes": len(shape_ids), "shape_vertices": int(len(x)),
        "patterns": len(patterns), "patterns_not_monotone": int(not_monotone), "trips": len(trips_out),
        "segments": len(seg_rows), "stop_pairs": len(seg_obs), "stop_pairs_split_by_length": int(split_pairs),
        "segment_len_m": {q: round(float(np.percentile(seg_len, q)), 1) for q in (5, 25, 50, 75, 95)},
        "segments_under_20m": int((seg_len < 20).sum()),
        "stop_offset_m": {q: round(float(np.percentile(offs, q)), 1) for q in (50, 90, 99)},
        "stops_over_50m_from_shape": int((offs > 50).sum()),
        "timetable_kmh_along_shape": {q: round(statistics.quantiles(sched_kmh, n=100)[q - 1], 1) for q in (5, 50, 95)},
        "stops_bbox": [float(s_lat.min()), float(s_lat.max()), float(s_lng.min()), float(s_lng.max())],
    }
    (GTFS_ART / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
