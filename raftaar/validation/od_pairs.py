"""Named origin-destination pairs: what buses really took on the test days, next to each estimate.

    uv run python -m raftaar.validation.od_pairs            # after the backtest (uses its model)

Pairs are picked by rule, not by hand: the busiest route patterns, spread across the city, from a stop about
a fifth of the way along to one about four fifths along. The brief's own example is added first.
Writes artifacts/od_pairs.json and validation/od_pairs.csv (the list the Google sampler reads).
"""
from __future__ import annotations

import csv
import json

import duckdb
import numpy as np

from ..calendar import IST_OFFSET_S, daytype_of_date, epoch_ist
from ..config import ART, ROOT
from ..model.traveltime import MODEL_DIR, TravelTimeModel
from ..store import Network

SLOTS = [("weekday", "08:30"), ("weekday", "13:00"), ("weekday", "18:30"), ("weekday", "22:00"), ("sunday", "13:00")]
HALF_WINDOW_S = 1800
N_PAIRS = 14
MIN_SPREAD_M = 4000.0


def pick_pairs(net: Network, freq: np.ndarray) -> list[tuple[int, int, int]]:
    """[(pattern, board position, alight position)]"""
    pairs: list[tuple[int, int, int]] = []
    kashmere = [i for i, n in enumerate(net.stop_name) if n.strip() == "ISBT Kashmere Gate Terminal"]
    nehru = [i for i, n in enumerate(net.stop_name) if n.strip() == "Nehru Place Terminal"]
    for p in np.argsort(-freq[:, 0, :].sum(axis=1)):
        stops = net.ps_stop[net.stops(int(p))]
        a = np.flatnonzero(np.isin(stops, kashmere))
        b = np.flatnonzero(np.isin(stops, nehru))
        if len(a) and len(b) and a[0] < b[-1]:
            # Buses stand at a terminal, so a departure from a pattern's very first stop is rarely observable:
            # the example is measured from the stop after the terminal to the stop before the last one.
            n = len(stops)
            pairs.append((int(p), max(int(a[0]), 1), min(int(b[-1]), n - 2)))
            break
    used_routes = {net.pat_name[p].replace("UP", "").replace("DOWN", "") for p, _, _ in pairs}
    anchors = [(net.stop_x[net.ps_stop[net.ps_off[p] + a]], net.stop_y[net.ps_stop[net.ps_off[p] + a]]) for p, a, _ in pairs]
    for p in np.argsort(-freq[:, 0, :].sum(axis=1)):
        p = int(p)
        n = int(net.ps_off[p + 1] - net.ps_off[p])
        base = net.pat_name[p].replace("UP", "").replace("DOWN", "")
        if n < 25 or not net.pat_monotone[p] or base in used_routes:
            continue
        a, b = n // 5, (4 * n) // 5
        sa = net.ps_stop[net.ps_off[p] + a]
        if any(np.hypot(net.stop_x[sa] - x, net.stop_y[sa] - y) < MIN_SPREAD_M for x, y in anchors):
            continue
        pairs.append((p, a, b))
        used_routes.add(base)
        anchors.append((net.stop_x[sa], net.stop_y[sa]))
        if len(pairs) >= N_PAIRS:
            break
    return pairs


def run() -> list[dict]:
    net = Network()
    model = TravelTimeModel.load(net, MODEL_DIR.with_name("model_backtest"))
    bt = json.loads((ART / "backtest.json").read_text())
    test_days = bt["test_days"]
    freq = np.load(ART / "planner.npz")["freq"]
    con = duckdb.connect()
    files = lambda kind: "[" + ", ".join(f"'{ART}/events/{kind}_2026_10_{d:02d}.parquet'" for d in test_days) + "]"
    day = "CAST(regexp_extract(filename, '_(\\d\\d)\\.parquet$', 1) AS INTEGER)"
    import datetime as dt
    out = []
    pairs = pick_pairs(net, freq)
    # The brief's example (Kashmere Gate to Nehru Place): buses are rarely on the published path at the first few
    # stops near the terminal, so measure from the first stop that has enough end-to-end observations.
    if pairs:
        p, a, b = pairs[0]
        counts = dict(con.execute(f"""
            SELECT e1.seq, count(*)
            FROM (SELECT {day} AS day, * FROM read_parquet({files('events')}, filename = true)) e1
            JOIN (SELECT {day} AS day, * FROM read_parquet({files('events')}, filename = true)) e2 ON e2.day = e1.day AND e2.trip = e1.trip
            JOIN (SELECT {day} AS day, trip, pattern FROM read_parquet({files('trips')}, filename = true)) t ON t.day = e1.day AND t.trip = e1.trip
            WHERE t.pattern = {p} AND e1.seq BETWEEN {a} AND {a + 10} AND e2.seq = {b} AND e1.t_dep IS NOT NULL AND e2.t_arr IS NOT NULL
            GROUP BY 1""").fetchall())
        usable = [s for s in range(a, a + 11) if counts.get(s, 0) >= 40]
        if usable:
            pairs[0] = (p, usable[0], b)
    for p, a, b in pairs:
        lo = int(net.ps_off[p])
        sa, sb = int(net.ps_stop[lo + a]), int(net.ps_stop[lo + b])
        obs = con.execute(f"""
            SELECT e1.day, e1.t_dep, e2.t_arr - e1.t_dep AS secs
            FROM (SELECT {day} AS day, * FROM read_parquet({files('events')}, filename = true)) e1
            JOIN (SELECT {day} AS day, * FROM read_parquet({files('events')}, filename = true)) e2 ON e2.day = e1.day AND e2.trip = e1.trip
            JOIN (SELECT {day} AS day, trip, pattern FROM read_parquet({files('trips')}, filename = true)) t ON t.day = e1.day AND t.trip = e1.trip
            WHERE t.pattern = {p} AND e1.seq = {a} AND e2.seq = {b} AND e1.t_dep IS NOT NULL AND e2.t_arr IS NOT NULL
        """).fetchnumpy()
        d_type = np.array([daytype_of_date(dt.date(2026, 10, int(d))) for d in obs["day"]])
        sod = np.mod(np.asarray(obs["t_dep"], dtype=np.float64) + IST_OFFSET_S, 86400.0)
        rows = []
        for kind, clock in SLOTS:
            hh, mm = map(int, clock.split(":"))
            want = {"weekday": 0, "saturday": 1, "sunday": 2}[kind]
            m = (d_type == want) & (np.abs(sod - (hh * 3600 + mm * 60)) <= HALF_WINDOW_S)
            secs = np.asarray(obs["secs"], dtype=np.float64)[m]
            ref_day = next(d for d in test_days if daytype_of_date(dt.date(2026, 10, d)) == want)
            t0 = epoch_ist(2026, 10, ref_day, hh, mm)
            q = model.predict_quantiles([p], [a], [b], [t0])[0]
            rows.append({
                "day": kind, "at": clock, "buses_observed": int(m.sum()),
                "actual_median_min": round(float(np.median(secs)) / 60, 1) if m.sum() >= 3 else None,
                "actual_p10_min": round(float(np.percentile(secs, 10)) / 60, 1) if m.sum() >= 5 else None,
                "actual_p90_min": round(float(np.percentile(secs, 90)) / 60, 1) if m.sum() >= 5 else None,
                "model_min": round(float(q[2]) / 60, 1), "model_p10_min": round(float(q[0]) / 60, 1), "model_p90_min": round(float(q[4]) / 60, 1),
                "timetable_min": round(float(net.ps_sched[lo + b] - net.ps_sched[lo + a]) / 60, 1),
                "google_transit_min": None, "google_driving_min": None,
            })
        out.append({
            "route": net.pat_name[p], "pattern": p, "from": net.stop_name[sa].strip(), "to": net.stop_name[sb].strip(),
            "from_lat": round(float(net.stop_lat[sa]), 6), "from_lng": round(float(net.stop_lng[sa]), 6),
            "to_lat": round(float(net.stop_lat[sb]), 6), "to_lng": round(float(net.stop_lng[sb]), 6),
            "stops": b - a, "distance_km": round(float(net.ps_chain[lo + b] - net.ps_chain[lo + a]) / 1000, 1), "slots": rows,
        })
    google = ROOT / "validation" / "google_durations.csv"
    if google.exists():       # written by validation/google.py; durations only
        seen = {(r["route"], r["from"], r["to"], r["day"], r["at"]): r for r in csv.DictReader(open(google))}
        for o in out:
            for s in o["slots"]:
                g = seen.get((o["route"], o["from"], o["to"], s["day"], s["at"]))
                if g:
                    s["google_transit_min"] = float(g["transit_on_bus_min"]) if g["transit_on_bus_min"] else None
                    s["google_driving_min"] = float(g["driving_min"]) if g["driving_min"] else None
    (ART / "od_pairs.json").write_text(json.dumps(out, indent=1))
    (ROOT / "validation").mkdir(exist_ok=True)
    with open(ROOT / "validation" / "od_pairs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["route", "from", "to", "from_lat", "from_lng", "to_lat", "to_lng", "stops", "distance_km"])
        for o in out:
            w.writerow([o[k] for k in ("route", "from", "to", "from_lat", "from_lng", "to_lat", "to_lng", "stops", "distance_km")])
    return out


if __name__ == "__main__":
    for o in run():
        print(f"{o['route']:>14}  {o['from'][:30]:30} -> {o['to'][:30]:30} {o['distance_km']:5.1f} km")
        for s in o["slots"]:
            print(f"      {s['day']:8} {s['at']}  n={s['buses_observed']:3}  actual {s['actual_median_min']}  model {s['model_min']}  timetable {s['timetable_min']}")
