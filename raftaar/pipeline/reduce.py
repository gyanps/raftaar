"""One day of the raw feed -> one small Parquet file of real fixes.

    uv run python -m raftaar.pipeline.reduce --days 27 28 29
    uv run python -m raftaar.pipeline.reduce --all --delete-extracted

The feed re-logs a bus every ~10 s whether or not it has a new fix, so a day's
31M rows hold about 18M fixes. A fix is one (vehicle, device timestamp); the
first row seen for it wins. Fixes outside the NCR box are dropped. The `speed`
column is kept only for diagnostics: speeds are computed from positions.

Only one unpacked day (about 4.5 GB) is ever on disk at a time when running
from the archive with --delete-extracted. A .db that was already on disk is
never deleted.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import duckdb

from ..config import ARCHIVE, ART, FIXES_DIR, GPS_DIR, LAT_MAX, LAT_MIN, LNG_MAX, LNG_MIN

STATS = ART / "reduce_stats.json"


def db_name(day: int) -> str:
    return f"bus_movements_2026_10_{day:02d}.db"


def out_path(day: int) -> Path:
    return FIXES_DIR / f"fixes_2026_10_{day:02d}.parquet"


def reduce_day(db: Path, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    spill = ART / "tmp"
    spill.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit='7GB'")
    con.execute("SET threads=8")
    con.execute(f"SET temp_directory='{spill}'")
    con.execute("INSTALL sqlite")
    con.execute("LOAD sqlite")
    con.execute("SET sqlite_all_varchar=true")  # lat, lng and timestamp are stored as text
    con.execute(f"ATTACH '{db}' AS src (TYPE sqlite, READ_ONLY)")
    con.execute(f"""
        COPY (
            WITH f AS (
                SELECT vehicle_id,
                       TRY_CAST(timestamp AS BIGINT) AS ts,
                       TRY_CAST(lat AS DOUBLE) AS lat,
                       TRY_CAST(lng AS DOUBLE) AS lng,
                       TRY_CAST(speed AS FLOAT) AS speed,
                       CAST(TRY_CAST(route_id AS DOUBLE) AS INTEGER) AS route_id,
                       trip_id, time
                FROM src.vehicle_feed
            )
            SELECT vehicle_id, ts, CAST(lat AS FLOAT) AS lat, CAST(lng AS FLOAT) AS lng, speed, route_id, trip_id
            FROM f
            WHERE vehicle_id IS NOT NULL AND ts IS NOT NULL
              AND lat BETWEEN {LAT_MIN} AND {LAT_MAX} AND lng BETWEEN {LNG_MIN} AND {LNG_MAX}
            QUALIFY row_number() OVER (PARTITION BY vehicle_id, ts ORDER BY time) = 1
            ORDER BY vehicle_id, ts
        ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1000000)
    """)
    raw, bad_box = con.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE NOT (TRY_CAST(lat AS DOUBLE) BETWEEN {LAT_MIN} AND {LAT_MAX}
                                           AND TRY_CAST(lng AS DOUBLE) BETWEEN {LNG_MIN} AND {LNG_MAX}))
        FROM src.vehicle_feed""").fetchone()
    con.execute("DETACH src")
    fixes, vehicles, routes, t0, t1 = con.execute(
        f"SELECT count(*), count(DISTINCT vehicle_id), count(DISTINCT route_id), min(ts), max(ts) FROM read_parquet('{tmp}')"
    ).fetchone()
    con.close()
    if not fixes:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{db.name}: no fixes survived")
    tmp.replace(out)
    return {"raw_rows": raw, "rows_outside_box": bad_box, "fixes": fixes, "vehicles": vehicles, "routes": routes,
            "first_ts": t0, "last_ts": t1, "parquet_mb": round(out.stat().st_size / 1e6, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, nargs="*", default=[])
    ap.add_argument("--all", action="store_true", help="every day of October")
    ap.add_argument("--delete-extracted", action="store_true",
                    help="delete a .db this run unpacked from the archive, once its Parquet is written")
    args = ap.parse_args()
    days = list(range(1, 32)) if args.all else args.days
    stats = json.loads(STATS.read_text()) if STATS.exists() else {}
    for day in days:
        out = out_path(day)
        if out.exists():
            print(f"day {day:02d}: already reduced", flush=True)
            continue
        db = GPS_DIR / db_name(day)
        extracted = False
        t = time.time()
        if not db.exists():
            GPS_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(["7zz", "x", "-y", f"-o{GPS_DIR}", str(ARCHIVE), db_name(day)], check=True,
                           stdout=subprocess.DEVNULL)
            extracted = True
        t_extract = time.time() - t
        s = reduce_day(db, out)
        s["seconds"] = {"extract": round(t_extract), "reduce": round(time.time() - t - t_extract)}
        stats[f"2026-10-{day:02d}"] = s
        STATS.write_text(json.dumps(stats, indent=2, sort_keys=True))
        if extracted and args.delete_extracted:
            db.unlink()
            for side in ("-shm", "-wal"):  # left behind by the read-only SQLite attach
                Path(str(db) + side).unlink(missing_ok=True)
        print(f"day {day:02d}: {s['raw_rows']:,} rows -> {s['fixes']:,} fixes, {s['vehicles']:,} buses, "
              f"{s['parquet_mb']} MB, {s['seconds']}", flush=True)


if __name__ == "__main__":
    main()
