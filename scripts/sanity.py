"""Quick look at one matched day: are the trips and speeds believable?

    uv run python scripts/sanity.py 27
"""
import sys

import duckdb

from raftaar.config import ART, GTFS_ART

day = int(sys.argv[1]) if len(sys.argv) > 1 else 27
ev = f"read_parquet('{ART}/events/events_2026_10_{day:02d}.parquet')"
tr = f"read_parquet('{ART}/events/trips_2026_10_{day:02d}.parquet')"
pat = f"read_parquet('{GTFS_ART}/patterns.parquet')"
con = duckdb.connect()
con.execute(f"""
    CREATE VIEW runs AS
    WITH e AS (
        SELECT *, lead(seq) OVER w AS seq_n, lead(t_arr) OVER w AS arr_n, lead(chain_m - zb) OVER w AS in_n
        FROM {ev} WINDOW w AS (PARTITION BY trip ORDER BY seq)
    )
    SELECT trip, seq, seg_idx, t_dep AS t, arr_n - t_dep AS run_s, in_n - (chain_m + za) AS run_m,
           CAST(floor(((t_dep + 19800) % 86400) / 3600) AS INTEGER) AS hour
    FROM e WHERE seq_n = seq + 1 AND arr_n IS NOT NULL AND t_dep IS NOT NULL
""")


def show(title, sql):
    print(f"\n== {title}")
    con.sql(sql).show(max_rows=40, max_width=200)


show("trips", f"""
    SELECT count(*) AS trips, count(DISTINCT vehicle) AS buses, count(DISTINCT t.pattern) AS patterns,
           round(median(t_end - t_start) / 60, 1) AS med_min, round(median(s_end - s_start) / 1000, 1) AS med_km,
           round(median(stops * 1.0 / p.n_stops), 2) AS med_cover,
           round(avg(CASE WHEN stops >= 0.8 * p.n_stops THEN 1 ELSE 0 END), 3) AS share_cover_80,
           round(median(on_share), 3) AS med_on, round(median(label_share), 3) AS med_label,
           round(avg(CASE WHEN label_share < 0.5 THEN 1 ELSE 0 END), 3) AS share_label_disagrees
    FROM {tr} t JOIN {pat} p ON p.pattern_idx = t.pattern""")
show("stop events", f"""
    SELECT count(*) AS events, round(avg(CASE WHEN t_arr IS NULL THEN 1 ELSE 0 END), 3) AS no_arr,
           round(avg(CASE WHEN t_dep IS NULL THEN 1 ELSE 0 END), 3) AS no_dep,
           round(median(t_dep - t_arr), 1) AS med_zone_s, round(quantile_cont(t_dep - t_arr, 0.9), 1) AS p90_zone_s
    FROM {ev}""")
show("stop-to-stop runs", """
    SELECT count(*) AS runs, count(DISTINCT seg_idx) AS segments,
           round(avg(CASE WHEN run_s <= 0 OR run_m <= 0 THEN 1 ELSE 0 END), 4) AS non_positive,
           round(quantile_cont(run_m / run_s * 3.6, 0.05) FILTER (WHERE run_s > 0), 1) AS kmh_p5,
           round(median(run_m / run_s * 3.6) FILTER (WHERE run_s > 0), 1) AS kmh_p50,
           round(quantile_cont(run_m / run_s * 3.6, 0.95) FILTER (WHERE run_s > 0), 1) AS kmh_p95,
           round(avg(CASE WHEN run_s > 0 AND run_m / run_s * 3.6 > 70 THEN 1 ELSE 0 END), 4) AS over_70,
           round(avg(CASE WHEN run_s > 0 AND run_m / run_s * 3.6 < 2 THEN 1 ELSE 0 END), 4) AS under_2
    FROM runs""")
show("citywide running speed by hour (IST)", """
    SELECT hour, count(*) AS runs, round(sum(run_m) / sum(run_s) * 3.6, 1) AS kmh_dist_weighted,
           round(median(run_m / run_s * 3.6), 1) AS kmh_median
    FROM runs WHERE run_s > 0 AND run_m > 0 AND run_m / run_s * 3.6 BETWEEN 2 AND 70 GROUP BY hour ORDER BY hour""")
show("runs per segment", """
    SELECT round(quantile_cont(n, 0.1)) AS p10, round(median(n)) AS p50, round(quantile_cont(n, 0.9)) AS p90, count(*) AS segments_seen
    FROM (SELECT seg_idx, count(*) AS n FROM runs GROUP BY seg_idx)""")
show("route 2262 (timetable: 116 min at every hour): whole-pattern trips", f"""
    SELECT CAST(floor(((t_start + 19800) % 86400) / 3600) AS INTEGER) AS hour, count(*) AS trips,
           round(median(t_end - t_start) / 60) AS med_min, round(min(t_end - t_start) / 60) AS min_min, round(max(t_end - t_start) / 60) AS max_min
    FROM {tr} t JOIN {pat} p ON p.pattern_idx = t.pattern
    WHERE t.route_id = 2262 AND stops >= 0.9 * p.n_stops GROUP BY 1 ORDER BY 1""")
