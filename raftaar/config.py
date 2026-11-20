"""Paths and the few constants shared by every stage."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Secrets live in .env (git-ignored), one KEY=VALUE per line. Anything already in the environment wins.
if (ROOT / ".env").exists():
    for _line in (ROOT / ".env").read_text().splitlines():
        _k, _sep, _v = _line.strip().partition("=")
        if _sep and _k and not _k.startswith("#"):
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))
DATA = Path(os.environ.get("RAFTAAR_DATA", ROOT / "data"))
ART = Path(os.environ.get("RAFTAAR_ARTIFACTS", ROOT / "artifacts"))

GTFS_DIR = DATA / "gtfs"
GPS_DIR = DATA / "gps"
ARCHIVE = DATA / "delhi-dtc-bus-gtfs-rt-2026-10.7z"

GTFS_ART = ART / "gtfs"      # patterns, stop chainages, segments
FIXES_DIR = ART / "fixes"    # one de-duplicated Parquet file per day

# Every GTFS stop lies inside 28.19..29.69 N, 76.60..77.43 E; this is that box with a margin.
# The raw feed has fixes at (200, 200) and in the southern hemisphere.
LAT_MIN, LAT_MAX = 28.1, 29.8
LNG_MIN, LNG_MAX = 76.5, 77.6

# Origin of the flat local grid (Connaught Place). See geo/planar.py.
LAT0, LNG0 = 28.6139, 77.2090
