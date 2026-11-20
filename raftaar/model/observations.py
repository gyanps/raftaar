"""Stop events -> what the model learns from.

A *run* is the time on open road between two consecutive stops: from leaving
one stop's zone to entering the next one's. Signals and congestion are inside
it. A *dwell* is the time inside a stop's zone. A trip from stop a to stop b
is the runs between them plus the dwells at the stops in between, so the two
add up to what a passenger experiences and can be learned separately.
"""
from __future__ import annotations

from typing import Iterable

import duckdb
import numpy as np

from ..config import ART, GTFS_ART

EVENTS = ART / "events"
MIN_KMH, MAX_KMH = 2.0, 70.0   # outside this a "run" is a parked bus or a GPS artefact
MIN_RUN_M = 5.0
MAX_DWELL_S = 600.0            # longer is a layover or a breakdown, not a stop


def _files(kind: str, days: Iterable[int]) -> str:
    return "[" + ", ".join(f"'{EVENTS}/{kind}_2026_10_{d:02d}.parquet'" for d in days) + "]"


def _day(col: str = "filename") -> str:
    return f"CAST(regexp_extract({col}, '_(\\d\\d)\\.parquet$', 1) AS TINYINT)"


def load_runs(days: Iterable[int]) -> dict[str, np.ndarray]:
    """One row per observed stop-to-stop run: day, trip, seq, seg, t (leaving the first stop), run_s, run_m."""
    days = list(days)
    con = duckdb.connect()
    return con.execute(f"""
        WITH e AS (
            SELECT {_day()} AS day, trip, seq, seg_idx, t_dep, chain_m + za AS out_m,
                   lead(seq) OVER w AS seq_n, lead(t_arr) OVER w AS arr_n, lead(chain_m - zb) OVER w AS in_n
            FROM read_parquet({_files('events', days)}, filename = true)
            WINDOW w AS (PARTITION BY filename, trip ORDER BY seq)
        )
        SELECT day, trip, seq, seg_idx AS seg, t_dep AS t, CAST(arr_n - t_dep AS FLOAT) AS run_s, CAST(in_n - out_m AS FLOAT) AS run_m
        FROM e
        WHERE seq_n = seq + 1 AND t_dep IS NOT NULL AND arr_n IS NOT NULL AND arr_n > t_dep AND in_n - out_m >= {MIN_RUN_M}
          AND (in_n - out_m) / (arr_n - t_dep) BETWEEN {MIN_KMH / 3.6} AND {MAX_KMH / 3.6}
    """).fetchnumpy()


def load_dwells(days: Iterable[int]) -> dict[str, np.ndarray]:
    """One row per observed stop zone: day, trip, seq, stop, t (entering), dwell_s. Pattern terminals are left out."""
    days = list(days)
    con = duckdb.connect()
    return con.execute(f"""
        SELECT e.day, e.trip, e.seq, e.stop_idx AS stop, e.t_arr AS t, CAST(least(e.t_dep - e.t_arr, {MAX_DWELL_S}) AS FLOAT) AS dwell_s
        FROM (SELECT {_day()} AS day, * FROM read_parquet({_files('events', days)}, filename = true)) e
        JOIN (SELECT {_day()} AS day, trip, pattern FROM read_parquet({_files('trips', days)}, filename = true)) t
          ON t.day = e.day AND t.trip = e.trip
        JOIN read_parquet('{GTFS_ART}/patterns.parquet') p ON p.pattern_idx = t.pattern
        WHERE e.t_arr IS NOT NULL AND e.t_dep IS NOT NULL AND e.seq > 0 AND e.seq < p.n_stops - 1
    """).fetchnumpy()


def load_spans(days: Iterable[int], per_trip: int = 4, min_stops: int = 3, seed: int = 7) -> dict[str, np.ndarray]:
    """Board -> alight samples on the same bus: what a rider would have experienced.

    For each trip, up to per_trip random pairs (a, b) at least min_stops apart where both the departure from a
    and the arrival at b were observed. Returns day, trip, pattern, a, b, t0 (leaving a), actual_s.
    """
    days = list(days)
    con = duckdb.connect()
    ev = con.execute(f"""
        SELECT e.day, e.trip, t.pattern, e.seq, e.t_arr, e.t_dep
        FROM (SELECT {_day()} AS day, * FROM read_parquet({_files('events', days)}, filename = true)) e
        JOIN (SELECT {_day()} AS day, trip, pattern FROM read_parquet({_files('trips', days)}, filename = true)) t
          ON t.day = e.day AND t.trip = e.trip
        ORDER BY e.day, e.trip, e.seq
    """).fetchnumpy()
    day, trip, pattern, seq = (np.asarray(ev[k]) for k in ("day", "trip", "pattern", "seq"))
    arr = np.ma.filled(np.ma.asarray(ev["t_arr"], dtype=np.float64), np.nan)
    dep = np.ma.filled(np.ma.asarray(ev["t_dep"], dtype=np.float64), np.nan)
    n = len(seq)
    key = day.astype(np.int64) * 10_000_000 + trip
    bounds = np.r_[0, np.flatnonzero(key[1:] != key[:-1]) + 1, n]
    start = np.repeat(bounds[:-1], np.diff(bounds))
    end = np.repeat(bounds[1:], np.diff(bounds))
    idx = np.arange(n)
    room = end - idx - 1                                    # rows after this one in the same trip
    rng = np.random.default_rng(seed)
    out = {k: [] for k in ("day", "trip", "pattern", "a", "b", "t0", "actual_s")}
    for _ in range(per_trip):
        j = np.minimum(idx + 1 + (rng.random(n) * room).astype(np.int64), end - 1)
        actual = arr[j] - dep
        valid = (room >= 1) & ~np.isnan(actual) & (actual > 0) & (seq[j] - seq >= min_stops)
        rank = rng.random(n)
        rank[~valid] = 2.0
        order = np.lexsort((rank, key))                     # within each trip, a random valid row comes first
        pick = order[bounds[:-1]]
        pick = pick[valid[pick]]
        for k, v in zip(out, (day[pick], trip[pick], pattern[pick], seq[pick], seq[j[pick]], dep[pick], actual[pick])):
            out[k].append(v)
    res = {k: np.concatenate(v) for k, v in out.items()}
    _, first = np.unique(np.stack([res["day"].astype(np.int64) * 10_000_000 + res["trip"], res["a"], res["b"]]), axis=1, return_index=True)
    return {k: v[np.sort(first)] for k, v in res.items()}
