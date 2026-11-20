"""Error measures for travel-time estimates. Times in seconds in, minutes out."""
from __future__ import annotations

import numpy as np


def summarise(actual: np.ndarray, pred: np.ndarray) -> dict:
    actual = np.asarray(actual, dtype=np.float64)
    pred = np.asarray(pred, dtype=np.float64)
    if len(actual) == 0:
        return {"n": 0}
    err = pred - actual
    ape = np.abs(err) / actual
    return {
        "n": int(len(actual)),
        "mean_actual_min": round(float(actual.mean() / 60), 2),
        "mae_min": round(float(np.abs(err).mean() / 60), 2),
        "wmape_pct": round(float(np.abs(err).sum() / actual.sum() * 100), 1),
        "median_ape_pct": round(float(np.median(ape) * 100), 1),
        "bias_pct": round(float(err.sum() / actual.sum() * 100), 1),
        "p90_abs_err_min": round(float(np.percentile(np.abs(err), 90) / 60), 2),
        "within_10pct": round(float((ape <= 0.10).mean() * 100), 1),
        "within_20pct": round(float((ape <= 0.20).mean() * 100), 1),
        "within_2min": round(float((np.abs(err) <= 120).mean() * 100), 1),
        "within_5min": round(float((np.abs(err) <= 300).mean() * 100), 1),
    }


def skill(model: dict, baseline: dict) -> float:
    """Share of the baseline's absolute error removed by the model, in percent."""
    return round((1 - model["mae_min"] / baseline["mae_min"]) * 100, 1) if baseline.get("mae_min") else float("nan")
