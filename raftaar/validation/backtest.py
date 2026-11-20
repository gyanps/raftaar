"""Train on some days, predict trips on days the model never saw.

    uv run python -m raftaar.validation.backtest --train 1-24 --test 25-31

A test case is one rider's trip on one real bus: board at stop a, alight at
stop b, the bus's own GPS says how long it took. The model is asked the same
question knowing only the route, the two stops and the departure time.

Compared against:
  timetable   the published GTFS stop_times (the same duration at every hour)
  flat        each stretch's all-day average from the same GPS data: what you get
              from the data without modelling time of day
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from ..calendar import DAYTYPE_NAMES, daytype, second_of_day
from ..config import ART
from ..model import live
from ..model.observations import load_runs, load_spans
from ..model.traveltime import MODEL_DIR, _lookup, fit
from ..store import Network
from .metrics import skill, summarise

DIST_BANDS = [(0, 3, "under 3 km"), (3, 8, "3-8 km"), (8, 15, "8-15 km"), (15, 1e9, "over 15 km")]
TIME_BANDS = [(0, 6, "night 00-06"), (6, 8, "early 06-08"), (8, 11, "morning 08-11"), (11, 16, "midday 11-16"),
              (16, 21, "evening 16-21"), (21, 24, "late 21-24")]


def parse_days(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def run(train: list[int], test: list[int], per_trip: int = 4, half_life: float | None = None, calibrate: bool = True) -> dict:
    t_start = time.time()
    net = Network()
    model = fit(net, train, half_life_days=half_life, calibrate=calibrate)
    model.save(MODEL_DIR.with_name("model_backtest"))
    spans = load_spans(test, per_trip=per_trip)
    pattern, a, b, t0, actual = spans["pattern"], spans["a"], spans["b"], spans["t0"], spans["actual_s"]
    q = model.predict_quantiles(pattern, a, b, t0)
    # Live: the same trips, also knowing what buses did in the 20 minutes before each one left.
    centre = live.fit_centre(model, train)
    weights = live.fit_weights(model, train[-10:], centre)
    np.save(MODEL_DIR.with_name("model_backtest") / "live_weights.npy", weights)
    np.save(MODEL_DIR.with_name("model_backtest") / "live_centre.npy", centre)
    live_raw = np.empty(len(pattern))
    for d in np.unique(spans["day"]):
        m = spans["day"] == d
        live_raw[m] = live.predict_live(model, weights, live.day_state(model, int(d), centre), pattern[m], a[m], b[m], t0[m])
    preds = {
        "model": q[:, 2],
        "live": live_raw * model.calib.factors(b - a, t0)[:, 2],
        "flat": model.predict_flat(pattern, a, b),
        "timetable": model.timetable(pattern, a, b),
    }
    km = model.distance_m(pattern, a, b) / 1000
    hour = second_of_day(t0) / 3600
    dtp = daytype(t0)
    ok = (actual > 30) & (preds["timetable"] > 0) & (km / (actual / 3600) < 70)   # drop GPS artefacts in the truth
    groups = {"all": ok}
    groups |= {f"distance: {name}": ok & (km >= lo) & (km < hi) for lo, hi, name in DIST_BANDS}
    groups |= {f"time: {name}": ok & (hour >= lo) & (hour < hi) for lo, hi, name in TIME_BANDS}
    groups |= {f"day: {name}": ok & (dtp == i) for i, name in enumerate(DAYTYPE_NAMES)}
    table = {}
    for g, mask in groups.items():
        row = {name: summarise(actual[mask], p[mask]) for name, p in preds.items()}
        if row["model"]["n"]:
            row["skill_vs_timetable_pct"] = skill(row["model"], row["timetable"])
            row["skill_vs_flat_pct"] = skill(row["model"], row["flat"])
            row["live_skill_vs_timetable_pct"] = skill(row["live"], row["timetable"])
            row["live_gain_over_model_pct"] = skill(row["live"], row["model"])
        table[g] = row

    # One level down: single stop-to-stop runs on the test days.
    r = load_runs(test)
    d = daytype(r["t"]).astype(np.int64)
    sod = second_of_day(r["t"])
    pace = np.array([_lookup(model.runs.est, int(s), int(dd), float(x)) for s, dd, x in zip(r["seg"][:400000], d[:400000], sod[:400000])])
    seg_pred = pace * r["run_m"][:400000]
    seg_flat = model.runs.flat[r["seg"][:400000]] * r["run_m"][:400000]
    seen = model.runs.n_key[r["seg"]]
    tiers = {
        "runs_on_segments_never_seen_in_training_pct": round(float((seen == 0).mean() * 100), 2),
        "runs_on_segments_with_under_20_training_runs_pct": round(float((seen < 20).mean() * 100), 2),
        "segments_with_training_data": int((model.runs.n_key > 0).sum()),
        "segments_total": int(net.n_segments),
    }
    return {
        "train_days": train, "test_days": test, "test_cases": int(ok.sum()), "dropped_as_artefacts": int((~ok).sum()),
        "options": {"half_life_days": half_life, "calibrated": calibrate},
        "live_weights": {"look_ahead_min": ["0-10", "10-20", "20-40", "40-60", "60+"], "city": [round(float(x), 3) for x in weights[:, 0]],
                         "own_stretch": [round(float(x), 3) for x in weights[:, 1]]},
        "range_coverage": {
            "inside_10_to_90_pct": round(float(((actual >= q[:, 0]) & (actual <= q[:, 4]))[ok].mean() * 100), 1),
            "inside_25_to_75_pct": round(float(((actual >= q[:, 1]) & (actual <= q[:, 3]))[ok].mean() * 100), 1),
            "median_width_10_to_90_pct_of_estimate": round(float(np.median((q[:, 4] - q[:, 0]) / q[:, 2])[()] * 100), 1),
        },
        "trips": table,
        "single_runs": {"model": summarise(r["run_s"][:400000], seg_pred), "flat": summarise(r["run_s"][:400000], seg_flat)},
        "coverage": tiers,
        "seconds": round(time.time() - t_start, 1),
    }


def markdown(res: dict) -> str:
    cols = ["n", "mean_actual_min", "mae_min", "wmape_pct", "median_ape_pct", "bias_pct", "p90_abs_err_min", "within_10pct", "within_5min"]
    out = [f"Trained on {res['train_days'][0]}-{res['train_days'][-1]} Oct, tested on {res['test_days'][0]}-{res['test_days'][-1]} Oct: "
           f"{res['test_cases']:,} board-to-alight trips on real buses.", ""]
    for g, row in res["trips"].items():
        if not row["model"]["n"]:
            continue
        out += [f"**{g}**", "", "| estimate | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
        for name, label in (("live", "model + last 20 min of buses"), ("model", "model (typical day)"), ("flat", "no time of day"), ("timetable", "timetable")):
            out.append(f"| {label} | " + " | ".join(str(row[name].get(c, "")) for c in cols) + " |")
        out += ["", f"Error removed vs timetable: {row['skill_vs_timetable_pct']}% (typical day), {row['live_skill_vs_timetable_pct']}% (with live data). "
                    f"Live data removes a further {row['live_gain_over_model_pct']}% of the typical-day error.", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", default="1-24")
    ap.add_argument("--test", default="25-31")
    ap.add_argument("--per-trip", type=int, default=4)
    ap.add_argument("--half-life", type=float, default=None, help="days; recent training days count for more")
    ap.add_argument("--no-calibrate", action="store_true")
    ap.add_argument("--brief", action="store_true", help="print only the headline rows")
    args = ap.parse_args()
    res = run(parse_days(args.train), parse_days(args.test), args.per_trip, args.half_life, not args.no_calibrate)
    if args.brief:
        for g in ("all", "day: weekday", "day: saturday", "day: sunday_holiday", "time: evening 16-21", "distance: over 15 km"):
            m = res["trips"][g]["model"]
            lv = res["trips"][g]["live"]
            print(f"{g:24s} n={m['n']:>8,} typical: mae={m['mae_min']:5.2f} wmape={m['wmape_pct']:5.1f} bias={m['bias_pct']:5.1f} | "
                  f"live: mae={lv['mae_min']:5.2f} wmape={lv['wmape_pct']:5.1f} bias={lv['bias_pct']:5.1f} within10={lv['within_10pct']:5.1f} | "
                  f"timetable mae={res['trips'][g]['timetable']['mae_min']:5.2f}")
        print(res["live_weights"])
        print(res["range_coverage"], res["seconds"], "s")
        return
    (ART / "backtest.json").write_text(json.dumps(res, indent=2))
    (ART / "backtest.md").write_text(markdown(res))
    print(markdown(res))
    print(json.dumps({k: res[k] for k in ("range_coverage", "single_runs", "coverage", "seconds", "dropped_as_artefacts")}, indent=2))


if __name__ == "__main__":
    main()
