"""Fit the travel-time model that the API serves.

    uv run python -m raftaar.model.fit              # every matched day
    uv run python -m raftaar.model.fit --days 1-24
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from ..config import ART
from ..store import Network
from .traveltime import MODEL_DIR, fit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", default=None, help="e.g. 1-24 or 1,2,5-9 (default: every matched day)")
    args = ap.parse_args()
    if args.days:
        from ..validation.backtest import parse_days
        days = parse_days(args.days)
    else:
        days = sorted(int(p.stem[-2:]) for p in (ART / "events").glob("events_2026_10_*.parquet"))
    t = time.time()
    model = fit(Network(), days)
    model.save()
    # How a surprise seen now carries forward: each stretch's usual log-ratio, then the look-ahead weights.
    from . import live
    centre = live.fit_centre(model, days)
    weights = live.fit_weights(model, days[-10:], centre)
    np.save(MODEL_DIR / "live_centre.npy", centre)
    np.save(MODEL_DIR / "live_weights.npy", weights)
    print("live weights (city, own stretch) by look-ahead band:", weights.round(3).tolist())
    seen = int((model.runs.n_key > 0).sum())
    print(f"fitted on {len(days)} days in {time.time() - t:.0f} s: {seen:,} of {len(model.runs.n_key):,} stretches have GPS runs; saved to {MODEL_DIR}")


if __name__ == "__main__":
    main()
