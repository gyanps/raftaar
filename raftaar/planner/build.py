"""Everything the journey planner needs besides the travel-time model.

    uv run python -m raftaar.planner.build

* How often each pattern really runs: buses per hour by day type, counted from
  the matched GPS trips (the timetable's trip counts are used only for
  patterns the GPS never saw).
* How irregular headways are, which decides how long a rider waits.
* Which stops are within walking distance of each other.
* Which patterns serve each stop.
"""
from __future__ import annotations

import datetime as dt
import json

import duckdb
import numpy as np

from ..calendar import N_DAYTYPES, daytype_of_date
from ..config import ART, GTFS_ART
from ..store import Network

OUT = ART / "planner.npz"
WALK_DETOUR = 1.3          # streets are longer than the straight line
WALK_MS = 1.2              # 4.3 km/h
TRANSFER_M = 400.0
TRANSFER_PENALTY_S = 60.0  # getting off, finding the next stop


def walk_seconds(metres):
    return np.asarray(metres) * WALK_DETOUR / WALK_MS


def observed_frequency(net: Network) -> tuple[np.ndarray, np.ndarray, float]:
    """buses/hour [pattern, day type, hour], trips/day seen per pattern, and the wait factor alpha."""
    con = duckdb.connect()
    day = "CAST(regexp_extract(filename, '_(\\d\\d)\\.parquet$', 1) AS INTEGER)"
    rows = con.execute(f"""
        SELECT e.day, t.pattern, e.seq, CAST(floor(((coalesce(e.t_dep, e.t_arr) + 19800) % 86400) / 3600) AS INTEGER) AS hour, count(*) AS n
        FROM (SELECT {day} AS day, * FROM read_parquet('{ART}/events/events_2026_10_*.parquet', filename = true)) e
        JOIN (SELECT {day} AS day, trip, pattern FROM read_parquet('{ART}/events/trips_2026_10_*.parquet', filename = true)) t
          ON t.day = e.day AND t.trip = e.trip
        GROUP BY ALL
    """).fetchnumpy()
    days = np.unique(rows["day"])
    dtype_of_day = np.zeros(32, dtype=np.int64)
    n_days = np.zeros(N_DAYTYPES)
    for d in days:
        dtype_of_day[d] = daytype_of_date(dt.date(2026, 10, int(d)))
        n_days[dtype_of_day[d]] += 1
    row = net.ps_off[rows["pattern"]] + rows["seq"]
    flat = (row * N_DAYTYPES + dtype_of_day[rows["day"]]) * 24 + rows["hour"]
    per_stop = np.bincount(flat, weights=rows["n"], minlength=len(net.ps_stop) * N_DAYTYPES * 24).reshape(-1, N_DAYTYPES, 24)
    per_stop /= np.maximum(n_days, 1)[None, :, None]
    freq = np.zeros((net.n_patterns, N_DAYTYPES, 24), dtype=np.float32)
    seen = np.zeros(net.n_patterns)
    for p in range(net.n_patterns):
        sl = net.stops(p)
        freq[p] = np.median(per_stop[sl], axis=0)   # the middle stop's count: robust to short-turns and to broken trips
        seen[p] = freq[p, 0].sum()

    # Headway irregularity at each pattern's middle stop on weekdays: a rider arriving at random
    # waits E[H^2] / (2 E[H]), which is alpha x the average headway. Even headways give alpha = 0.5.
    h = con.execute(f"""
        WITH mid AS (
            SELECT e.day, t.pattern, coalesce(e.t_dep, e.t_arr) AS t
            FROM (SELECT {day} AS day, * FROM read_parquet('{ART}/events/events_2026_10_*.parquet', filename = true)) e
            JOIN (SELECT {day} AS day, trip, pattern FROM read_parquet('{ART}/events/trips_2026_10_*.parquet', filename = true)) t
              ON t.day = e.day AND t.trip = e.trip
            JOIN read_parquet('{GTFS_ART}/patterns.parquet') p ON p.pattern_idx = t.pattern
            WHERE e.seq = p.n_stops // 2 AND floor(((coalesce(e.t_dep, e.t_arr) + 19800) % 86400) / 3600) BETWEEN 7 AND 20
        ), gaps AS (
            SELECT t - lag(t) OVER (PARTITION BY day, pattern ORDER BY t) AS gap FROM mid
        )
        SELECT sum(gap * gap) / (2 * sum(gap)) / avg(gap) FROM gaps WHERE gap BETWEEN 60 AND 5400
    """).fetchone()[0]
    return freq, seen, float(h)


def scheduled_frequency(net: Network) -> np.ndarray:
    con = duckdb.connect()
    r = con.execute(f"""SELECT pattern_idx, CAST(floor(start_s / 3600) AS INTEGER) % 24 AS hour, count(*) AS n
                        FROM read_parquet('{GTFS_ART}/trips.parquet') GROUP BY ALL""").fetchnumpy()
    out = np.zeros((net.n_patterns, 24), dtype=np.float32)
    out[r["pattern_idx"], r["hour"]] = r["n"]
    return out


def footpaths(net: Network, radius: float = TRANSFER_M):
    """Stop pairs within walking range, as CSR (offsets, neighbour stop, seconds)."""
    x, y = net.stop_x, net.stop_y
    cell = 500.0
    gx, gy = np.floor(x / cell).astype(np.int64), np.floor(y / cell).astype(np.int64)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, k in enumerate(zip(gx, gy)):
        grid.setdefault(k, []).append(i)
    off, to, sec = [0], [], []
    for i in range(len(x)):
        near = [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in grid.get((gx[i] + dx, gy[i] + dy), ())]
        near = np.array(near)
        d = np.hypot(x[near] - x[i], y[near] - y[i])
        keep = (d <= radius) & (near != i)
        to += near[keep].tolist()
        sec += (walk_seconds(d[keep]) + TRANSFER_PENALTY_S).tolist()
        off.append(len(to))
    return np.array(off, dtype=np.int64), np.array(to, dtype=np.int32), np.array(sec, dtype=np.float32)


def build() -> dict:
    net = Network()
    freq_obs, seen, alpha = observed_frequency(net)
    sched = scheduled_frequency(net)
    use_obs = seen >= 2.0                     # the GPS saw this pattern run at least twice on a typical weekday
    # On patterns the GPS did see, it saw about half the trips the timetable promises. Patterns it never
    # saw keep the timetable's shape but at that same discount, and are flagged in every answer.
    ratio = float(freq_obs[use_obs, 0, :].sum() / max(sched[use_obs].sum(), 1.0))
    freq = freq_obs.copy()
    freq[~use_obs] = sched[~use_obs][:, None, :] * ratio
    usable = net.pat_monotone & (freq.sum(axis=(1, 2)) > 0)

    # stop -> (pattern, position) for every stop a rider can board at
    pairs = [(int(net.ps_stop[r]), p, r - int(net.ps_off[p]))
             for p in np.flatnonzero(usable) for r in range(int(net.ps_off[p]), int(net.ps_off[p + 1]) - 1)]
    pairs.sort()
    sp_stop = np.array([s for s, _, _ in pairs], dtype=np.int64)
    sp_off = np.searchsorted(sp_stop, np.arange(len(net.stop_id) + 1)).astype(np.int64)
    fp_off, fp_to, fp_s = footpaths(net)
    np.savez_compressed(OUT, freq=freq, freq_from_gps=use_obs, usable=usable, alpha=alpha,
                        sp_off=sp_off, sp_pat=np.array([p for _, p, _ in pairs], dtype=np.int32),
                        sp_seq=np.array([q for _, _, q in pairs], dtype=np.int32), fp_off=fp_off, fp_to=fp_to, fp_s=fp_s)
    wk = freq[:, 0, :]
    summary = {
        "patterns": int(net.n_patterns), "usable_patterns": int(usable.sum()),
        "patterns_with_gps_frequency": int((use_obs & usable).sum()), "patterns_on_timetable_frequency": int((~use_obs & usable).sum()),
        "weekday_bus_trips_per_day_gps": round(float(freq_obs[:, 0, :].sum()), 0), "timetable_trips_per_day": round(float(sched.sum()), 0),
        "wait_factor_alpha": round(alpha, 3), "gps_trips_per_timetable_trip": round(ratio, 3),
        "weekday_peak_buses_per_hour_per_pattern": {q: round(float(np.percentile(wk[usable, 8:11].mean(axis=1), q)), 2) for q in (25, 50, 75, 95)},
        "boardable_stop_pattern_pairs": len(pairs), "walk_links": int(len(fp_to)),
    }
    (ART / "planner_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
