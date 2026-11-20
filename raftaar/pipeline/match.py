"""Link each bus's fixes to a route pattern and read off when it passed each stop.

    uv run python -m raftaar.pipeline.match --days 27
    uv run python -m raftaar.pipeline.match --all --workers 6

The feed labels every fix with a route, but one fix in five sits more than
60 m from that route (stale label after arrival, wrong direction, a bus
running empty to the depot), and the feed's trip ids do not delimit trips.
So trips are rebuilt from geometry:

1. Candidates for a bus = every pattern of every route it was labelled with
   that day, plus the opposite direction of each.
2. On each candidate the bus is followed along the shape by distance
   ("chainage"), looking only a little behind and as far ahead as it could
   have driven since the last fix. That keeps a bus on the right pass of a
   loop or an out-and-back road.
3. A trip is a stretch where the bus keeps moving forward along the shape.
   It ends when the bus is off the shape or silent for 10 minutes, falls
   200 m behind its own furthest point, jumps impossibly far, or stands
   still for 10 minutes. (Leaving the shape for a few minutes and rejoining
   it further on is common, and does not end the trip.)
4. Where candidates overlap in time the longest forward run wins, with a
   bonus for agreeing with the feed's label. Buses that fit nothing are
   left out and counted; they are never forced onto a shape.
5. For each stop on a kept trip: the time the bus entered the stop zone and
   the time it left, interpolated between fixes. Nothing is interpolated
   across a hole of more than 60 s or 500 m.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numba import njit

from ..config import ART, FIXES_DIR
from ..geo.planar import to_xy
from ..store import Network

EVENTS_DIR = ART / "events"

ON_M = 45.0              # on the shape
WINDOW_BACK_M = 100.0    # how far behind the last position to look
WINDOW_FWD_M = 300.0     # how far ahead, at least
MAX_SPEED_MS = 25.0      # ... or this fast since the last fix (90 km/h)
REACQUIRE_S = 120        # after this long, search the whole shape again
OFF_BREAK_S = 600        # buses often leave the published shape for a few minutes and rejoin it further on
GAP_BREAK_S = 600
BACK_BREAK_M = 200.0
JUMP_SLACK_M = 200.0
STALL_S = 600
STALL_M = 50.0
BACK_STEP_M = 30.0       # a step back by more than this counts against the trip

MIN_FWD_M = 1000.0
MIN_STOPS = 5
MIN_ON_SHARE = 0.70
MAX_BACK_SHARE = 0.05
MIN_LABEL_FIXES = 6
MAX_LABELS = 16
OVERLAP_S = 60

BRIDGE_S = 60            # never interpolate a stop time across a bigger hole than this
BRIDGE_M = 500.0


HEADING_MIN_M = 15.0     # a hop at least this long tells which way the bus is going
HEADING_COS = -0.2       # a segment "agrees" with the hop unless it points more than ~100 degrees away


@njit(cache=True)
def _nearest(px, py, sx, sy, cum, lo, hi, hx, hy, hn):
    """Nearest point on segments lo..hi-1: (distance, chainage, segment, agrees with travel).

    With a direction of travel (hx, hy, length hn > 0) the nearest segment that
    runs the same way wins if it is on the shape; this is what keeps a bus on
    its own side of an out-and-back road. Otherwise the plain nearest.
    """
    best = np.inf
    best_s = 0.0
    best_i = lo
    fwd = np.inf
    fwd_s = 0.0
    fwd_i = lo
    for i in range(lo, hi):
        ax = sx[i]
        ay = sy[i]
        dx = sx[i + 1] - ax
        dy = sy[i + 1] - ay
        len2 = dx * dx + dy * dy
        t = 0.0
        if len2 > 0.0:
            t = ((px - ax) * dx + (py - ay) * dy) / len2
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
        cx = ax + t * dx - px
        cy = ay + t * dy - py
        d = cx * cx + cy * cy
        if d < best:
            best = d
            best_s = cum[i] + t * (cum[i + 1] - cum[i])
            best_i = i
        if hn > 0.0 and d < fwd and dx * hx + dy * hy >= HEADING_COS * np.sqrt(len2) * hn:
            fwd = d
            fwd_s = cum[i] + t * (cum[i + 1] - cum[i])
            fwd_i = i
    if hn > 0.0:
        if fwd <= ON_M * ON_M:
            return np.sqrt(fwd), fwd_s, fwd_i, True
        return np.sqrt(best), best_s, best_i, False
    return np.sqrt(best), best_s, best_i, True


@njit(cache=True)
def project_fixes(ts, px, py, sx, sy, cum, box, chain, off):
    """Follow one bus along one shape. Fills chain (m along the shape) and off (m from it; inf = nowhere near)."""
    n = len(ts)
    nseg = len(sx) - 1
    tracking = False
    s_prev = 0.0
    t_prev = 0
    seg_prev = 0
    pad = ON_M + 5.0
    for i in range(n):
        x = px[i]
        y = py[i]
        if nseg < 1 or x < box[0] - pad or x > box[1] + pad or y < box[2] - pad or y > box[3] + pad:
            off[i] = np.inf
            chain[i] = 0.0
            continue
        hx = 0.0
        hy = 0.0
        hn = 0.0
        if i > 0 and ts[i] - ts[i - 1] <= REACQUIRE_S:
            hx = x - px[i - 1]
            hy = y - py[i - 1]
            hn = np.sqrt(hx * hx + hy * hy)
            if hn < HEADING_MIN_M:
                hn = 0.0
        d = np.inf
        s = 0.0
        seg = 0
        agrees = False
        if tracking and ts[i] - t_prev <= REACQUIRE_S:
            dt = ts[i] - t_prev
            lo_s = s_prev - WINDOW_BACK_M
            hi_s = s_prev + max(WINDOW_FWD_M, MAX_SPEED_MS * dt)
            lo = seg_prev
            while lo > 0 and cum[lo] > lo_s:
                lo -= 1
            hi = seg_prev + 1
            while hi < nseg and cum[hi] < hi_s:
                hi += 1
            d, s, seg, agrees = _nearest(x, y, sx, sy, cum, lo, hi, hx, hy, hn)
            if d <= ON_M and not agrees:
                # On the shape but against its direction: we may be on the wrong pass of an
                # out-and-back road. Switch only to a pass that runs our way.
                d2, s2, seg2, agrees2 = _nearest(x, y, sx, sy, cum, 0, nseg, hx, hy, hn)
                if agrees2:
                    d, s, seg = d2, s2, seg2
            # Off the shape while still being followed: stay off. A far-away pass that happens to be
            # near is not where this bus is; the whole shape is searched again after REACQUIRE_S.
        else:
            d, s, seg, agrees = _nearest(x, y, sx, sy, cum, 0, nseg, hx, hy, hn)
        off[i] = d
        chain[i] = s
        if d <= ON_M:
            tracking = True
            s_prev = s
            t_prev = ts[i]
            seg_prev = seg
    return 0


@njit(cache=True)
def find_tracks(ts, chain, off, same_label, out):
    """Split one bus's day on one shape into forward runs.

    out[m] = (first fix, last fix, start chainage, furthest chainage, fixes on shape, fixes in span,
              backward steps, fixes whose label agrees, why it ended). Returns the number of runs written.
    Why: 1 left the shape, 2 feed went silent, 3 fell behind its furthest point, 4 impossible jump,
         5 stood still, 6 the day's data ended.
    """
    n = len(ts)
    cap = out.shape[0]
    m = 0
    is_open = False
    i0 = 0
    i_last = 0
    i_adv = 0
    s_start = 0.0
    s_max = 0.0
    s_adv = 0.0
    t_adv = 0
    n_on = 0
    n_back = 0
    n_lab = 0
    adv_on = 0      # the same three counts as they stood when the bus last moved forward
    adv_back = 0
    adv_lab = 0
    why = 0
    for i in range(n):
        on = off[i] <= ON_M
        restart = False
        close_at = -1
        if not is_open:
            if on:
                restart = True
        elif not on:
            if ts[i] - ts[i_last] > OFF_BREAK_S:
                close_at = i_last
                why = 1
        else:
            dt = ts[i] - ts[i_last]
            ds = chain[i] - chain[i_last]
            if dt > GAP_BREAK_S:
                close_at = i_last
                restart = True
                why = 2
            elif chain[i] < s_max - BACK_BREAK_M:
                close_at = i_last
                restart = True
                why = 3
            elif ds > max(WINDOW_FWD_M, MAX_SPEED_MS * dt) + JUMP_SLACK_M:
                close_at = i_last
                restart = True
                why = 4
            elif s_max - s_start < STALL_M and chain[i] < s_start:
                restart = True  # still sliding backwards before any progress: the run starts here instead
            else:
                if ds < -BACK_STEP_M:
                    n_back += 1
                n_on += 1
                n_lab += same_label[i]
                if chain[i] > s_max:
                    s_max = chain[i]
                if chain[i] > s_adv + STALL_M:
                    s_adv = chain[i]
                    t_adv = ts[i]
                    i_adv = i
                    i_last = i
                    adv_on = n_on
                    adv_back = n_back
                    adv_lab = n_lab
                elif ts[i] - t_adv > STALL_S:
                    close_at = i_adv  # stood still: the run ended where it last moved
                    restart = True
                    why = 5
                    n_on = adv_on
                    n_back = adv_back
                    n_lab = adv_lab
                else:
                    i_last = i
        if close_at >= 0:
            if m < cap and s_max - s_start >= MIN_FWD_M:
                out[m, 0] = i0
                out[m, 1] = close_at
                out[m, 2] = s_start
                out[m, 3] = s_max
                out[m, 4] = n_on
                out[m, 5] = close_at - i0 + 1
                out[m, 6] = n_back
                out[m, 7] = n_lab
                out[m, 8] = why
                m += 1
            is_open = False
        if restart and on:
            is_open = True
            i0 = i
            i_last = i
            i_adv = i
            s_start = chain[i]
            s_max = chain[i]
            s_adv = chain[i]
            t_adv = ts[i]
            n_on = 1
            n_back = 0
            n_lab = same_label[i]
            adv_on = 1
            adv_back = 0
            adv_lab = same_label[i]
    if is_open and m < cap and s_max - s_start >= MIN_FWD_M:
        out[m, 0] = i0
        out[m, 1] = i_last
        out[m, 2] = s_start
        out[m, 3] = s_max
        out[m, 4] = n_on
        out[m, 5] = i_last - i0 + 1
        out[m, 6] = n_back
        out[m, 7] = n_lab
        out[m, 8] = 6
        m += 1
    return m


@njit(cache=True)
def stop_times(t, c, stop_s, zb, za, arr, dep):
    """When the bus entered and left each stop's zone. t, c = times and chainages of the fixes on the shape.

    arr[k]: first moment at or past (stop - zb). dep[k]: last moment at or before (stop + za).
    NaN where the run does not span the zone edge, or the two fixes around it are too far apart.
    """
    n = len(t)
    k_n = len(stop_s)
    j = 0
    for k in range(k_n):
        arr[k] = np.nan
        thr = stop_s[k] - zb[k]
        while j < n and c[j] < thr:
            j += 1
        if 0 < j < n:
            dt = t[j] - t[j - 1]
            dc = c[j] - c[j - 1]
            if dt <= BRIDGE_S and dc <= BRIDGE_M and dc > 0.0:
                arr[k] = t[j - 1] + (thr - c[j - 1]) / dc * dt
    j = n - 1
    for k in range(k_n - 1, -1, -1):
        dep[k] = np.nan
        thr = stop_s[k] + za[k]
        while j >= 0 and c[j] > thr:
            j -= 1
        if 0 <= j < n - 1:
            dt = t[j + 1] - t[j]
            dc = c[j + 1] - c[j]
            if dt <= BRIDGE_S and dc <= BRIDGE_M and dc > 0.0:
                dep[k] = t[j] + (thr - c[j]) / dc * dt
    for k in range(k_n):
        if dep[k] < arr[k]:
            dep[k] = arr[k]
    return 0


def match_vehicle(net: Network, ts, x, y, label):
    """All kept trips of one bus for one day: [(pattern, track row, t_on, c_on)]."""
    n = len(ts)
    labels, counts = np.unique(label, return_counts=True)
    order = np.argsort(-counts)
    cands: list[int] = []
    for k in order[:MAX_LABELS]:
        if counts[k] < MIN_LABEL_FIXES:
            break
        r = int(labels[k])
        for route in (r, net.route_sibling.get(r, -1)):
            for p in net.route_patterns.get(route, ()):
                if p not in cands and net.pat_monotone[p]:
                    cands.append(p)
    found = []
    buf = np.empty((256, 9))
    proj = {}
    for p in cands:
        sx, sy, cum, box = net.shape(p)
        chain = np.empty(n)
        off = np.empty(n)
        project_fixes(ts, x, y, sx, sy, cum, box, chain, off)
        m = find_tracks(ts, chain, off, (label == net.pat_route[p]).astype(np.int64), buf)
        if not m:
            continue
        proj[p] = (chain, off)
        stop_s = net.ps_chain[net.stops(p)]
        for row in buf[:m].copy():
            i0, i1, s0, s1, n_on, n_tot, n_back, n_lab, _why = row
            covered = int(np.count_nonzero((stop_s >= s0) & (stop_s <= s1)))
            if covered < MIN_STOPS or n_on / n_tot < MIN_ON_SHARE or n_back / n_on > MAX_BACK_SHARE:
                continue
            score = (s1 - s0) * (1 + 0.5 * n_lab / n_on) * (1 + 0.1 * covered / len(stop_s))
            found.append((score, p, row, covered))
    found.sort(key=lambda f: -f[0])
    kept = []
    for score, p, row, covered in found:
        t0, t1 = ts[int(row[0])], ts[int(row[1])]
        if any(min(t1, k[1]) - max(t0, k[0]) > OVERLAP_S for k in kept):
            continue
        kept.append((t0, t1, p, row, covered))
    out = []
    for t0, t1, p, row, covered in sorted(kept, key=lambda k: k[0]):
        chain, off = proj[p]
        i0, i1 = int(row[0]), int(row[1])
        on = np.flatnonzero(off[i0 : i1 + 1] <= ON_M) + i0
        out.append((p, row, covered, ts[on].astype(np.float64), chain[on]))
    return out


def match_day(day: int, net: Network | None = None) -> dict:
    net = net or Network()
    t_start = time.time()
    src = FIXES_DIR / f"fixes_2026_10_{day:02d}.parquet"
    tab = pq.read_table(src, columns=["vehicle_id", "ts", "lat", "lng", "route_id"])
    veh = tab["vehicle_id"].combine_chunks().dictionary_encode()
    code = veh.indices.to_numpy()
    names = veh.dictionary.to_pylist()
    ts = tab["ts"].to_numpy()
    x, y = to_xy(tab["lat"].to_numpy(), tab["lng"].to_numpy())
    label = tab["route_id"].to_numpy(zero_copy_only=False)
    label = np.where(np.isnan(label), -1, label).astype(np.int32) if label.dtype.kind == "f" else label.astype(np.int32)
    del tab
    bounds = np.r_[0, np.flatnonzero(code[1:] != code[:-1]) + 1, len(code)]

    ev = {k: [] for k in ("trip", "seq", "stop_idx", "seg_idx", "t_arr", "t_dep", "chain_m", "zb", "za")}
    tr = {k: [] for k in ("trip", "vehicle", "pattern", "route_id", "t_start", "t_end", "s_start", "s_end", "fixes_on",
                          "on_share", "back_share", "label_share", "stops", "end_reason")}
    fixes_in_trips = 0
    trip = 0
    for a, b in zip(bounds[:-1], bounds[1:]):
        for p, row, covered, t_on, c_on in match_vehicle(net, ts[a:b], x[a:b], y[a:b], label[a:b]):
            sl = net.stops(p)
            k_n = sl.stop - sl.start
            arr = np.empty(k_n)
            dep = np.empty(k_n)
            stop_times(t_on, c_on, net.ps_chain[sl], net.ps_zb[sl], net.ps_za[sl], arr, dep)
            seen = ~(np.isnan(arr) & np.isnan(dep))
            ev["trip"].append(np.full(int(seen.sum()), trip, dtype=np.int32))
            ev["seq"].append(np.flatnonzero(seen).astype(np.int16))
            ev["stop_idx"].append(net.ps_stop[sl][seen])
            ev["seg_idx"].append(net.ps_seg[sl][seen])
            ev["t_arr"].append(arr[seen])
            ev["t_dep"].append(dep[seen])
            ev["chain_m"].append(net.ps_chain[sl][seen].astype(np.float32))
            ev["zb"].append(net.ps_zb[sl][seen].astype(np.float32))
            ev["za"].append(net.ps_za[sl][seen].astype(np.float32))
            _, _, s0, s1, n_on, n_tot, n_back, n_lab, why = row
            for key, val in zip(tr, (trip, names[code[a]], p, int(net.pat_route[p]), float(t_on[0]), float(t_on[-1]), float(s0),
                                     float(s1), int(n_on), n_on / n_tot, n_back / n_on, n_lab / n_on, covered, int(why))):
                tr[key].append(val)
            fixes_in_trips += int(n_on)
            trip += 1

    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    cols = {k: np.concatenate(v) if v else np.array([]) for k, v in ev.items()}
    for k in ("t_arr", "t_dep"):  # "not observed" is stored as null, never NaN
        cols[k] = pa.array(cols[k], mask=np.isnan(cols[k]))
    pq.write_table(pa.table(cols), EVENTS_DIR / f"events_2026_10_{day:02d}.parquet", compression="zstd")
    pq.write_table(pa.table(tr), EVENTS_DIR / f"trips_2026_10_{day:02d}.parquet", compression="zstd")
    n_events = int(sum(len(v) for v in ev["seq"]))
    return {"day": day, "fixes": int(len(ts)), "buses": int(len(bounds) - 1), "trips": trip,
            "fixes_in_trips": fixes_in_trips, "share_of_fixes_in_trips": round(fixes_in_trips / len(ts), 4),
            "stop_events": n_events, "seconds": round(time.time() - t_start, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, nargs="*", default=[])
    ap.add_argument("--all", action="store_true", help="every reduced day")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()
    days = sorted(int(p.stem[-2:]) for p in FIXES_DIR.glob("fixes_2026_10_*.parquet")) if args.all else args.days
    stats_path = ART / "match_stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    if args.workers > 1:
        with ProcessPoolExecutor(args.workers) as pool:
            results = pool.map(match_day, days)
            for s in results:
                stats[f"2026-10-{s['day']:02d}"] = s
                print(s, flush=True)
    else:
        net = Network()
        for day in days:
            s = match_day(day, net)
            stats[f"2026-10-{day:02d}"] = s
            print(s, flush=True)
    stats_path.write_text(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
