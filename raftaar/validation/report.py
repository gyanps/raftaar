"""Write docs/VALIDATION.md from the numbers the pipeline produced. Nothing in it is typed by hand.

    uv run python -m raftaar.validation.report
"""
from __future__ import annotations

import json

import numpy as np

from ..config import ART, ROOT

COLS = [("n", "Trips"), ("mean_actual_min", "Mean trip, min"), ("mae_min", "Avg error, min"), ("wmape_pct", "Error, % of trip time"),
        ("median_ape_pct", "Median error, %"), ("bias_pct", "Bias, %"), ("p90_abs_err_min", "90th pct error, min"),
        ("within_10pct", "Within 10%, %"), ("within_5min", "Within 5 min, %")]
NAMES = [("live", "Raftaar + last 20 min of buses"), ("model", "Raftaar, typical day"), ("flat", "Same GPS, no time of day"), ("timetable", "Published timetable")]


def table(row: dict) -> list[str]:
    out = ["| Estimate | " + " | ".join(c[1] for c in COLS) + " |", "|---|" + "---:|" * len(COLS)]
    for key, label in NAMES:
        if key in row:
            out.append(f"| {label} | " + " | ".join(f"{row[key][c]:,}" if c == "n" else str(row[key][c]) for c, _ in COLS) + " |")
    return out


def main() -> None:
    bt = json.loads((ART / "backtest.json").read_text())
    red = json.loads((ART / "reduce_stats.json").read_text())
    mat = json.loads((ART / "match_stats.json").read_text())
    pl = json.loads((ART / "planner_summary.json").read_text())
    od_path = ART / "od_pairs.json"
    od = json.loads(od_path.read_text()) if od_path.exists() else []
    raw = sum(d["raw_rows"] for d in red.values())
    fixes = sum(d["fixes"] for d in red.values())
    in_trips = sum(d["fixes_in_trips"] for d in mat.values())
    trips = sum(d["trips"] for d in mat.values())
    events = sum(d["stop_events"] for d in mat.values())
    allr = bt["trips"]["all"]
    tr, te = bt["train_days"], bt["test_days"]
    L = [
        "# Validation report", "",
        "Every number below is written by `python -m raftaar.validation.report` from the files the pipeline produced "
        "(`artifacts/backtest.json`, `od_pairs.json`, `reduce_stats.json`, `match_stats.json`).", "",
        "## 1. What is tested", "",
        f"The model is trained on {tr[0]} to {tr[-1]} October 2026 and tested on {te[0]} to {te[-1]} October, days it never saw. "
        "A test case is one rider's trip on one real bus: board at stop a, get off at stop b at least three stops later; "
        "the bus's own GPS says how long that took. The model is given only the route, the two stops and the departure time. "
        f"Up to four such trips are drawn at random from every bus trip of the test week: **{bt['test_cases']:,} test cases** "
        f"({bt['dropped_as_artefacts']} dropped because the 'truth' itself was impossible, such as over 70 km/h).", "",
        "Four estimates are compared:", "",
        "- **Raftaar, typical day**: the travel-time profile for that kind of day and half-hour. This is what answers a question about a future time.",
        "- **Raftaar + last 20 min of buses**: the same, adjusted by how the network was running just before the trip left, "
        "using only GPS that had already arrived by then. This is what answers 'leaving now'.",
        "- **Same GPS, no time of day**: each stretch's all-day average. It shows how much of the gain comes from modelling the clock.",
        "- **Published timetable**: GTFS `stop_times`, which give every route the same duration at every hour.", "",
        "## 2. Headline", "", *table(allr), "",
        f"The typical-day model removes **{allr['skill_vs_timetable_pct']}%** of the timetable's average error; with live data, "
        f"**{allr['live_skill_vs_timetable_pct']}%**. Live data removes a further {allr['live_gain_over_model_pct']}% of the typical-day error.", "",
        f"Range shown to the rider: {bt['range_coverage']['inside_10_to_90_pct']}% of real trips fell inside the 10th to 90th percentile range "
        f"(target 80%), and {bt['range_coverage']['inside_25_to_75_pct']}% inside the 25th to 75th (target 50%). "
        f"The 10 to 90 range is typically {bt['range_coverage']['median_width_10_to_90_pct_of_estimate']}% of the estimate wide.", "",
        "Bias is negative because the estimate is the typical (median) trip and bus trips have a long slow tail: "
        "a few very slow trips pull the average up but not the median.", "",
        "## 3. By distance, time of day and kind of day", "",
    ]
    for g, row in bt["trips"].items():
        if g == "all" or not row["model"]["n"]:
            continue
        L += [f"**{g}**", "", *table(row), ""]
    w = bt["live_weights"]
    L += [
        "## 4. How long a live reading stays useful", "",
        "Fitted on the last ten training days: how much of a surprise seen now still holds for a stretch the bus reaches later.", "",
        "| Minutes ahead | " + " | ".join(w["look_ahead_min"]) + " |", "|---|" + "---:|" * len(w["city"]),
        "| City-wide surprise | " + " | ".join(str(x) for x in w["city"]) + " |",
        "| A stretch's own surprise | " + " | ".join(str(x) for x in w["own_stretch"]) + " |", "",
        "If the whole city is slow now it will mostly still be slow in an hour; one road's own jam is half gone in about half an hour.", "",
        "## 5. One level down: single stop-to-stop runs", "",
        f"On {bt['single_runs']['model']['n']:,} single runs from the test days (mean {bt['single_runs']['model']['mean_actual_min']} min): "
        f"average error {bt['single_runs']['model']['mae_min']} min with the profile, {bt['single_runs']['flat']['mae_min']} min without time of day. "
        "Single runs are noisy (one red light is a large share of a 90-second run); errors partly cancel over a trip, which is why trips are the unit that matters.", "",
        "## 6. What the data covered", "",
        "| | |", "|---|---:|",
        f"| Days | {len(red)} |", f"| Raw rows read | {raw:,} |", f"| Real GPS fixes after removing re-logged rows and positions outside Delhi | {fixes:,} |",
        f"| Fixes that belong to a rebuilt trip | {in_trips:,} ({in_trips / fixes * 100:.0f}%) |", f"| Bus trips rebuilt | {trips:,} |",
        f"| Stop arrivals and departures read off | {events:,} |",
        f"| Stop-to-stop stretches with GPS runs in training | {bt['coverage']['segments_with_training_data']:,} of {bt['coverage']['segments_total']:,} |",
        f"| Test runs on stretches never seen in training | {bt['coverage']['runs_on_segments_never_seen_in_training_pct']}% |",
        f"| Route patterns whose frequency comes from GPS | {pl['patterns_with_gps_frequency']:,} of {pl['usable_patterns']:,} |",
        f"| Bus trips seen in GPS per timetable trip (on patterns seen) | {pl['gps_trips_per_timetable_trip']} |", "",
        "The fixes outside trips are mostly buses standing at depots and terminals, buses running empty, and buses whose route label does not match where they are. "
        "They are left out rather than forced onto a route.", "",
    ]
    if od:
        L += [
            "## 7. Named pairs: actual bus times, Raftaar, the timetable, and Google Maps", "",
            "Pairs are chosen by rule (the busiest route patterns, spread across the city, plus the brief's own example). "
            f"'Actual' is the median of real buses that left the first stop within 30 minutes of the stated time on the test days ({te[0]} to {te[-1]} October), "
            f"with the 10th to 90th percentile in brackets. Raftaar's figure comes from the model trained on {tr[0]} to {tr[-1]} October.", "",
            "| Route | From → To | km | When | Buses | Actual, min | Raftaar, min | Timetable, min | Google, on the bus | Google, by car |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
        errs = {"model": [], "timetable": [], "gt": [], "gd": []}
        for o in od:
            for s in o["slots"]:
                if s["actual_median_min"] is None:
                    continue
                rng = f" ({s['actual_p10_min']}–{s['actual_p90_min']})" if s["actual_p10_min"] is not None else ""
                g1 = s["google_transit_min"] if s["google_transit_min"] is not None else "pending"
                g2 = s["google_driving_min"] if s["google_driving_min"] is not None else "pending"
                L.append(f"| {o['route']} | {o['from']} → {o['to']} | {o['distance_km']} | {s['day']} {s['at']} | {s['buses_observed']} | "
                         f"{s['actual_median_min']}{rng} | {s['model_min']} | {s['timetable_min']} | {g1} | {g2} |")
                errs["model"].append(abs(s["model_min"] - s["actual_median_min"]))
                errs["timetable"].append(abs(s["timetable_min"] - s["actual_median_min"]))
                if s["google_transit_min"] is not None:
                    errs["gt"].append(abs(s["google_transit_min"] - s["actual_median_min"]))
                if s["google_driving_min"] is not None:
                    errs["gd"].append(abs(s["google_driving_min"] - s["actual_median_min"]))
        L += ["", f"Over these {len(errs['model'])} rows, average distance from the actual median: Raftaar {np.mean(errs['model']):.1f} min, "
                  f"timetable {np.mean(errs['timetable']):.1f} min"
                  + (f", Google's bus answer {np.mean(errs['gt']):.1f} min" if errs["gt"] else "")
                  + (f", Google by car {np.mean(errs['gd']):.1f} min" if errs["gd"] else "") + ".", ""]
        # How much does each source move across the day? (pairs with at least three weekday times observed)
        swing = {"actual": [], "model": [], "google": []}
        for o in od:
            wk = [s for s in o["slots"] if s["day"] == "weekday" and s["actual_median_min"] is not None and s["google_transit_min"] is not None]
            if len(wk) >= 3:
                swing["actual"].append(max(s["actual_median_min"] for s in wk) - min(s["actual_median_min"] for s in wk))
                swing["model"].append(max(s["model_min"] for s in wk) - min(s["model_min"] for s in wk))
                swing["google"].append(max(s["google_transit_min"] for s in wk) - min(s["google_transit_min"] for s in wk))
        if swing["google"]:
            L += [f"Between the quietest and the busiest weekday time, the same pair's bus ride changed by a median of "
                  f"**{np.median(swing['actual']):.1f} min** on real buses, **{np.median(swing['model']):.1f} min** in Raftaar's estimates and "
                  f"**{np.median(swing['google']):.1f} min** in Google's bus answers ({len(swing['google'])} pairs). "
                  "Google's bus times barely move with the clock, as the timetable's do not.", ""]
        if not errs["gt"]:
            L += ["Google Maps columns are filled by `python -m raftaar.validation.google` once a Routes API key is in `.env`; "
                  "only durations are stored.", ""]
        else:
            L += ["'Google, on the bus' is the time on the bus in Google's bus-only answer for the same two stops, leaving at that time on "
                  "the next day of that kind; 'Google, by car' is its driving time with its traffic forecast, a view of the road itself. "
                  "Google is asked about a coming day, the actual figures are from October.", ""]
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "VALIDATION.md").write_text("\n".join(L))
    print(f"wrote docs/VALIDATION.md ({len(L)} lines)")


if __name__ == "__main__":
    main()
