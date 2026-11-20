"""Score a file of test trips: for each (from, to, departure time) write the model's travel time.

    uv run python -m raftaar.validation.predict cases.csv              # writes cases.predictions.csv
    uv run python -m raftaar.validation.predict cases.csv -o out.csv
    make predict CASES=cases.csv

The input is a CSV (or a JSON list of objects). Column names are matched loosely, so most layouts work as they are:

* where from / where to, any one of:
    a stop name or 'lat,lng'      from, origin, source, start, from_stop, from_stop_name ...   (to, destination, dest, end ...)
    a GTFS stop_id                from_stop_id, origin_stop_id ...                              (to_stop_id ...)
    coordinates                   from_lat + from_lng (or _lon / _long)                         (to_lat + to_lng)
* when: depart, departure_time, time, start_time, datetime or timestamp, as
    '2026-10-28 08:30', '2026-10-28T08:30:00+05:30', epoch seconds, or '08:30' with either a date column
    or a day column (weekday / saturday / sunday, or a day name). A bare clock time means a typical weekday.
* optional route (route, route_name, route_short_name; or route_id): the bus ride is then priced on that route only.
* optional observed time (actual_min, observed_min, actual_s, travel_time_s, duration_min ...): the file is scored.

Every row gets two answers. door_to_door_min is the whole journey from the journey planner, with its range and
its parts (walk_min, wait_min, ride_min, number of buses). bus_ride_min is the time on one bus from the boarding
stop to the alighting stop (with a 10th-90th percentile range), which is what the backtest measures.
An observed-time column is taken as door to door unless --actual-is ride is given. All clock times are Indian Standard Time.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

import numpy as np

from ..api.places import Places
from ..calendar import DAYTYPE_NAMES, IST_OFFSET_S, daytype
from ..config import LAT_MAX, LAT_MIN, LNG_MAX, LNG_MIN
from ..geo.planar import to_xy
from ..model.traveltime import TravelTimeModel
from ..planner.journeys import Planner
from ..store import Network

FROM = ("from", "origin", "source", "start", "src", "o", "boarding", "board")
TO = ("to", "destination", "dest", "end", "dst", "d", "alighting", "alight")
WHEN = ("depart", "departure", "departure_time", "depart_time", "time", "start_time", "datetime", "date_time", "timestamp", "depart_at")
DATE = ("date", "service_date", "trip_date", "travel_date")
DAY = ("day", "day_type", "daytype", "day_of_week", "dow", "weekday")
ROUTE_NAME = ("route", "route_name", "route_short_name", "route_no", "route_number", "bus_route", "bus", "line")
ROUTE_ID = ("route_id",)
ACTUAL = ("actual", "observed", "true", "truth", "ground_truth", "travel_time", "duration", "expected", "actual_travel_time",
          "observed_travel_time", "actual_duration", "observed_duration", "trip_time")
MINUTE_ENDS, SECOND_ENDS = ("_min", "_mins", "_minutes"), ("_s", "_sec", "_secs", "_seconds")

# A clock time with no date is priced on a typical day of its kind; these dates only select that kind.
TYPICAL = {0: dt.date(2026, 11, 18), 1: dt.date(2026, 11, 21), 2: dt.date(2026, 11, 22)}
DAY_WORDS = {"weekday": 0, "wd": 0, "working": 0, "monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0, "friday": 0,
             "mon": 0, "tue": 0, "wed": 0, "thu": 0, "fri": 0, "saturday": 1, "sat": 1,
             "sunday": 2, "sun": 2, "holiday": 2, "sunday_holiday": 2}
COORD = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")
NEAR_M = (250.0, 500.0)          # a named place or a coordinate boards within this distance (the second if the first finds no bus)
SAME_STOP_M = 150.0              # a stop_id with no direct bus may use the stop across the road

NEW_COLUMNS = ["matched_from", "matched_to", "depart_ist", "day_type",
               "door_to_door_min", "door_to_door_low_min", "door_to_door_high_min", "walk_min", "wait_min", "ride_min",
               "buses", "journey", "timetable_door_to_door_min",
               "route_used", "stops", "distance_km", "bus_ride_min", "bus_ride_p10_min", "bus_ride_p90_min", "timetable_min", "note"]
BUS_ONLY = ("route_used", "stops", "distance_km", "bus_ride_min", "bus_ride_p10_min", "bus_ride_p90_min", "timetable_min")


def header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h).strip().lower()).strip("_")


def pick(row: dict, names) -> str | None:
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def route_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(s).upper())


def to_epoch(local: dt.datetime) -> float:
    return (local - dt.datetime(1970, 1, 1)).total_seconds() - IST_OFFSET_S


def parse_date(text: str) -> dt.date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%Y%m%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"date '{text}' not understood (use YYYY-MM-DD)")


def parse_clock(text: str) -> dt.time:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?\s*$", text)
    if not m:
        raise ValueError(f"time '{text}' not understood (use HH:MM or YYYY-MM-DD HH:MM)")
    hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if m.group(4):
        hh = hh % 12 + (12 if m.group(4).lower() == "pm" else 0)
    return dt.time(hh % 24, mm, ss)


def parse_when(row: dict) -> tuple[float, str]:
    """Departure as an epoch, and a note when something was assumed."""
    text, date, day = pick(row, WHEN), pick(row, DATE), pick(row, DAY)
    if text is None:
        raise ValueError("no departure time column (depart, departure_time, time, datetime or timestamp)")
    if re.fullmatch(r"\d+(\.\d+)?", text) and float(text) >= 1e8:                       # epoch seconds or milliseconds
        v = float(text)
        return (v / 1000 if v > 1e11 else v), ""
    if re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}", text):                # a full date and time
        iso = text.replace("Z", "+00:00").replace(" ", "T", 1)
        try:
            when = dt.datetime.fromisoformat(iso)
        except ValueError:
            d, _, c = text.replace("T", " ").partition(" ")
            when = dt.datetime.combine(parse_date(d), parse_clock(c or "00:00"))
        if when.tzinfo is not None:                                                    # any zone -> Indian Standard Time
            when = (when.astimezone(dt.timezone.utc) + dt.timedelta(seconds=IST_OFFSET_S)).replace(tzinfo=None)
        return to_epoch(when), ""
    clock = parse_clock(text)
    if date:
        return to_epoch(dt.datetime.combine(parse_date(date), clock)), ""
    if day:
        kind = DAY_WORDS.get(header(day))
        if kind is None:
            raise ValueError(f"day '{day}' not understood (weekday, saturday or sunday)")
        return to_epoch(dt.datetime.combine(TYPICAL[kind], clock)), ""
    return to_epoch(dt.datetime.combine(TYPICAL[0], clock)), "no date or day given: a typical weekday assumed"


def actual_column(headers: list[str], rows: list[dict]) -> tuple[str, float] | None:
    """The column holding observed travel time, and the factor that turns it into minutes."""
    for h in headers:
        for base in ACTUAL:
            if h == base or (h.startswith(base + "_") and h[len(base):] in MINUTE_ENDS + SECOND_ENDS):
                if h.endswith(MINUTE_ENDS):
                    return h, 1.0
                if h.endswith(SECOND_ENDS):
                    return h, 1 / 60
                vals = [float(r[h]) for r in rows if re.fullmatch(r"-?\d+(\.\d+)?", str(r.get(h, "")).strip())]
                return h, (1 / 60 if vals and float(np.median(vals)) > 240 else 1.0)      # no unit in the name: guess from the size
    return None


class Scorer:
    def __init__(self, journeys: bool = True):
        self.net = Network()
        self.model = TravelTimeModel.load(self.net)
        self.planner = Planner(self.net, self.model)
        self.places = Places(self.net, np.diff(self.planner.sp_off))
        self.by_id = {str(s).strip(): i for i, s in enumerate(self.net.stop_id)}
        self.pat_key = np.array([route_key(n) for n in self.net.pat_name])
        self.pat_base = np.array([re.sub(r"(UP|DOWN)$", "", k) for k in self.pat_key])
        self.journeys = journeys

    # ---- places ---------------------------------------------------------------------------------
    def near(self, lat: float, lng: float, radius: float) -> dict[int, float]:
        x, y = to_xy(lat, lng)
        d = np.hypot(self.net.stop_x - x, self.net.stop_y - y)
        return {int(i): float(d[i]) for i in np.flatnonzero(d <= radius)}

    def place(self, row: dict, side) -> dict:
        """{'label', 'coords', 'stop' (an exact stop index, or None)} for the from or the to side of a row."""
        n = self.net
        sid = pick(row, [f"{p}_{s}" for p in side for s in ("stop_id", "id", "stop_code")])
        lat = pick(row, [f"{p}_{s}" for p in side for s in ("lat", "latitude", "stop_lat")])
        lng = pick(row, [f"{p}_{s}" for p in side for s in ("lng", "lon", "long", "longitude", "stop_lon", "stop_lng")])
        text = pick(row, [x for p in side for x in (p, f"{p}_stop", f"{p}_stop_name", f"{p}_name", f"{p}_station", f"{p}_place")])
        if sid is not None:
            key = sid[:-2] if sid.endswith(".0") else sid
            if key not in self.by_id:
                raise ValueError(f"stop_id '{sid}' is not in the GTFS")
            s = self.by_id[key]
            return {"label": f"{n.stop_name[s]} (stop {n.stop_id[s]})", "coords": (float(n.stop_lat[s]), float(n.stop_lng[s])), "stop": s}
        if lat is not None and lng is not None:
            text = f"{lat},{lng}"
        if text is None:
            raise ValueError(f"no '{side[0]}' column (a stop name, {side[0]}_stop_id, or {side[0]}_lat and {side[0]}_lng)")
        m = COORD.match(text)
        if m:
            la, lo = float(m.group(1)), float(m.group(2))
            if not (LAT_MIN <= la <= LAT_MAX and LNG_MIN <= lo <= LNG_MAX):
                if LAT_MIN <= lo <= LAT_MAX and LNG_MIN <= la <= LNG_MAX:
                    la, lo = lo, la                                                    # given as lng,lat
                else:
                    raise ValueError(f"'{text}' is outside Delhi NCR")
            return {"label": f"{la:.5f}, {lo:.5f}", "coords": (la, lo), "stop": None}
        if text in self.by_id:
            s = self.by_id[text]
            return {"label": f"{n.stop_name[s]} (stop {n.stop_id[s]})", "coords": (float(n.stop_lat[s]), float(n.stop_lng[s])), "stop": s}
        hits = self.places.search(text, 1)
        if not hits:
            raise ValueError(f"no bus stop matches '{text}'")
        return {"label": hits[0]["name"], "coords": (hits[0]["lat"], hits[0]["lng"]), "stop": None}

    # ---- the ride on one bus -----------------------------------------------------------------------
    def route_mask(self, row: dict) -> tuple[np.ndarray | None, str]:
        name, rid = pick(row, ROUTE_NAME), pick(row, ROUTE_ID)
        if name is None and rid is None:
            return None, ""
        mask = np.zeros(self.net.n_patterns, dtype=bool)
        if name is not None:
            key = route_key(name)
            mask = self.pat_key == key
            if not mask.any():
                mask = self.pat_base == key                                            # '425' means 425UP and 425DOWN
        if not mask.any() and (rid or name or "").replace(".0", "").isdigit():
            mask = self.net.pat_route == int((rid or name).replace(".0", ""))
        if not mask.any():
            return None, f"route '{name or rid}' is not in the GTFS: any route used"
        return mask, ""

    def direct(self, a_stops: dict[int, float], b_stops: dict[int, float], when: float, mask):
        """The best single bus from any of a_stops to any of b_stops: (pattern, board seq, alight seq, a, b) or None.
        Best = the route seen running most often at that hour, then the least walking."""
        pl, n = self.planner, self.net
        dtp = int(daytype(when))
        hour = int(((when + IST_OFFSET_S) % 86400) // 3600)
        b_arr = np.fromiter(b_stops, dtype=np.int64)
        best = None
        for a, da in a_stops.items():
            for q in range(pl.sp_off[a], pl.sp_off[a + 1]):
                p, i = int(pl.sp_pat[q]), int(pl.sp_seq[q])
                if mask is not None and not mask[p]:
                    continue
                lo, hi = int(n.ps_off[p]), int(n.ps_off[p + 1])
                later = pl.ps_stop[lo + i + 1 : hi]
                hit = np.flatnonzero(np.isin(later, b_arr))
                if len(hit) == 0:
                    continue
                j = i + 1 + int(hit[0])
                b = int(later[hit[0]])
                key = (float(pl.freq[p, dtp, hour]), -(da + b_stops[b]), -(j - i))
                if best is None or key > best[0]:
                    best = (key, p, i, j, a, b)
        return best

    def ride(self, o: dict, d: dict, when: float, mask) -> tuple[dict, str]:
        n = self.net
        note = ""
        tries = []
        for radius in NEAR_M:
            a = {o["stop"]: 0.0} if o["stop"] is not None else self.near(*o["coords"], radius)
            b = {d["stop"]: 0.0} if d["stop"] is not None else self.near(*d["coords"], radius)
            tries.append((a, b, ""))
            if o["stop"] is not None and d["stop"] is not None:
                break
        if o["stop"] is not None or d["stop"] is not None:                          # same stop name across the road
            a = self.near(*o["coords"], SAME_STOP_M) if o["stop"] is not None else tries[-1][0]
            b = self.near(*d["coords"], SAME_STOP_M) if d["stop"] is not None else tries[-1][1]
            tries.append((a, b, "no direct bus between the exact stops: a stop within 150 m was used"))
        best = None
        for a, b, why in tries:
            if a and b:
                best = self.direct(a, b, when, mask)
                if best:
                    note = why
                    break
        if best is None:
            return {}, "no direct bus between these two places" + (" on this route" if mask is not None else "")
        key, p, i, j, a, b = best
        q = self.model.predict_quantiles(p, i, j, when)[0]
        lo = int(n.ps_off[p])
        if key[0] <= 0:
            note = (note + "; " if note else "") + "this route was not seen running at this hour"
        return {
            "matched_from": f"{n.stop_name[a]} (stop {n.stop_id[a]})", "matched_to": f"{n.stop_name[b]} (stop {n.stop_id[b]})",
            "route_used": n.pat_name[p], "stops": j - i,
            "distance_km": round(float(n.ps_chain[lo + j] - n.ps_chain[lo + i]) / 1000, 2),
            "bus_ride_min": round(float(q[2]) / 60, 1), "bus_ride_p10_min": round(float(q[0]) / 60, 1),
            "bus_ride_p90_min": round(float(q[4]) / 60, 1),
            "timetable_min": round(float(n.ps_sched[lo + j] - n.ps_sched[lo + i]) / 60, 1),
        }, note

    # ---- one row ---------------------------------------------------------------------------------
    def score(self, row: dict) -> dict:
        out = dict.fromkeys(NEW_COLUMNS, "")
        notes = []
        try:
            o, d = self.place(row, FROM), self.place(row, TO)
            when, assumed = parse_when(row)
            mask, route_note = self.route_mask(row)
        except ValueError as exc:
            out["note"] = str(exc)
            return out
        notes += [x for x in (assumed, route_note) if x]
        places = (o["label"], d["label"])
        out["depart_ist"] = (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=when + IST_OFFSET_S)).strftime("%Y-%m-%d %H:%M")
        out["day_type"] = DAYTYPE_NAMES[int(daytype(when))]
        ride, ride_note = self.ride(o, d, when, mask)
        out.update({k: v for k, v in ride.items() if k in BUS_ONLY})
        out["matched_from"], out["matched_to"] = ride.get("matched_from", places[0]), ride.get("matched_to", places[1])
        if ride_note:
            notes.append(ride_note)
        if self.journeys:
            res = self.planner.plan(o["coords"], d["coords"], when, 1)
            if res["options"]:
                best = res["options"][0]
                out.update(door_to_door_min=best["duration_min"], door_to_door_low_min=best["range_min"][0],
                           door_to_door_high_min=best["range_min"][1], walk_min=best["walk_min"], wait_min=best["wait_min"],
                           ride_min=best["ride_min"], buses=best["buses"], journey=best["summary"],
                           timetable_door_to_door_min=best.get("timetable_duration_min", ""))
            else:
                notes.append("no journey found")
        out["note"] = "; ".join(notes)
        return out


def read_cases(path: Path) -> tuple[list[str], list[dict]]:
    """Original column names, and the rows keyed by them."""
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text())
        rows = rows.get("cases", rows) if isinstance(rows, dict) else rows
        names = list(dict.fromkeys(k for r in rows for k in r))
        return names, rows
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        return list(reader.fieldnames or []), list(reader)


def summary(rows: list[dict], actual_min: list[float | None], which: str) -> list[str]:
    col = "bus_ride_min" if which == "ride" else "door_to_door_min"
    pairs = [(float(r[col]), a, r) for r, a in zip(rows, actual_min) if a is not None and r.get(col) not in ("", None)]
    if not pairs:
        return []
    pred, act = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
    err = pred - act
    lines = [f"scored {len(pairs)} cases against the observed time ({'time on the bus' if which == 'ride' else 'door to door'}):",
             f"  average error       {np.abs(err).mean():.2f} min",
             f"  median error        {np.median(np.abs(err)):.2f} min",
             f"  error / trip time   {100 * np.abs(err).sum() / act.sum():.1f}%",
             f"  within 10%          {100 * (np.abs(err) <= 0.1 * act).mean():.1f}% of cases",
             f"  bias                {err.mean():+.2f} min (plus = we say longer)"]
    tt_col = "timetable_min" if which == "ride" else "timetable_door_to_door_min"
    tt = np.array([float(p[2][tt_col]) if p[2].get(tt_col) not in ("", None) else np.nan for p in pairs])
    if np.isfinite(tt).any():
        lines.append(f"  published timetable {np.nanmean(np.abs(tt - act)):.2f} min average error on the same cases")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", type=Path, help="CSV or JSON file of test trips")
    ap.add_argument("-o", "--out", type=Path, default=None, help="where to write predictions (default: <cases>.predictions.csv)")
    ap.add_argument("--actual-is", choices=("door", "ride"), default="door",
                    help="what an observed-time column measures: the whole door-to-door journey (default) or time on one bus")
    ap.add_argument("--no-journeys", action="store_true", help="skip the door-to-door planner: bus ride only (implies --actual-is ride)")
    args = ap.parse_args(argv)
    if args.no_journeys:
        args.actual_is = "ride"

    names, raw = read_cases(args.cases)
    keyed = [{header(k): v for k, v in r.items() if k is not None} for r in raw]
    scorer = Scorer(journeys=not args.no_journeys)
    act = actual_column([header(n) for n in names], keyed)
    results, actual_min = [], []
    for r in keyed:
        res = scorer.score(r)
        a = None
        if act:
            text = str(r.get(act[0], "")).strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", text):
                a = float(text) * act[1]
                col = "bus_ride_min" if args.actual_is == "ride" else "door_to_door_min"
                res["actual_min"] = round(a, 1)
                res["error_min"] = round(float(res[col]) - a, 1) if res.get(col) not in ("", None) else ""
        results.append(res)
        actual_min.append(a)

    extra = NEW_COLUMNS[:-1] + (["actual_min", "error_min"] if act else []) + ["note"]
    taken = set(names)
    out_names = {c: ("pred_" + c if c in taken else c) for c in extra}
    out = args.out or args.cases.with_suffix(".predictions.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(names + [out_names[c] for c in extra])
        for r0, res in zip(raw, results):
            w.writerow([r0.get(n, "") for n in names] + [res.get(c, "") for c in extra])

    rode = sum(1 for r in results if r["bus_ride_min"] != "")
    door = sum(1 for r in results if r["door_to_door_min"] != "")
    failed = sum(1 for r in results if r["matched_from"] == "")
    print(f"{len(results)} cases: {rode} with a direct bus ride, {door} with a door-to-door journey, {failed} not understood -> {out}")
    if act:
        print(f"observed time read from column '{act[0]}' as {'minutes' if act[1] == 1.0 else 'seconds'}")
        print("\n".join(summary(results, actual_min, args.actual_is)))
    for r in results:
        if r["matched_from"] == "":
            print("  not understood:", r["note"], file=sys.stderr)
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
