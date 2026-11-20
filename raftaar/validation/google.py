"""Ask Google Maps for the same named pairs, and keep only the durations.

    uv run python -m raftaar.validation.google            # needs GOOGLE_ROUTES_API_KEY in .env

For every pair in validation/od_pairs.csv and every time slot of the validation table, two questions go to the
Routes API (computeRoutes):

  transit   bus only, leaving at the slot's time on the next day of that kind. Two figures are kept: Google's
            whole journey, and the time on the bus alone (the part comparable with a bus's own GPS).
  driving   a car with Google's traffic forecast for that departure time: how Google sees the road itself.

Results are appended to validation/google_durations.csv (minutes only; no routes or other Google content are
stored). Pairs and slots already in the file are skipped, so the run can be repeated.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request

from ..calendar import IST_OFFSET_S, daytype_of_date
from ..config import ROOT
from .od_pairs import SLOTS

URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
KEY_ENV = "GOOGLE_ROUTES_API_KEY"
PAIRS = ROOT / "validation" / "od_pairs.csv"
OUT = ROOT / "validation" / "google_durations.csv"
FIELDS = ["route", "from", "to", "day", "at", "departure_utc", "transit_total_min", "transit_on_bus_min", "transit_lines",
          "driving_min", "queried_utc"]


def next_departure(kind: str, clock: str, now: dt.datetime | None = None) -> dt.datetime:
    """The next date of that kind (weekday = a Tuesday to Thursday that is not a holiday), as UTC."""
    now = now or dt.datetime.utcnow()
    today = (now + dt.timedelta(seconds=IST_OFFSET_S)).date()
    want = {"weekday": 0, "saturday": 1, "sunday": 2}[kind]
    hh, mm = map(int, clock.split(":"))
    for ahead in range(1, 15):
        d = today + dt.timedelta(days=ahead)
        if daytype_of_date(d) == want and (want != 0 or d.weekday() in (1, 2, 3)):
            return dt.datetime.combine(d, dt.time(hh, mm)) - dt.timedelta(seconds=IST_OFFSET_S)
    raise RuntimeError("no matching day in the next two weeks")


def _seconds(text: str | None) -> float | None:
    return float(text[:-1]) if text and text.endswith("s") else None


def ask(key: str, body: dict, mask: str) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": mask})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read() or b"{}")


def point(lat: str, lng: str) -> dict:
    return {"location": {"latLng": {"latitude": float(lat), "longitude": float(lng)}}}


def query(key: str, pair: dict, when_utc: dt.datetime) -> dict:
    stamp = when_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"origin": point(pair["from_lat"], pair["from_lng"]), "destination": point(pair["to_lat"], pair["to_lng"]), "departureTime": stamp}
    out = {"transit_total_min": "", "transit_on_bus_min": "", "transit_lines": "", "driving_min": ""}
    transit = ask(key, {**base, "travelMode": "TRANSIT", "transitPreferences": {"allowedTravelModes": ["BUS"]}},
                  "routes.duration,routes.legs.steps.travelMode,routes.legs.steps.staticDuration,routes.legs.steps.transitDetails.transitLine.nameShort")
    if transit.get("routes"):
        r = transit["routes"][0]
        steps = [s for leg in r.get("legs", []) for s in leg.get("steps", [])]
        on_bus = [_seconds(s.get("staticDuration")) or 0.0 for s in steps if s.get("travelMode") == "TRANSIT"]
        total = _seconds(r.get("duration"))
        out["transit_total_min"] = round(total / 60, 1) if total else ""
        out["transit_on_bus_min"] = round(sum(on_bus) / 60, 1) if on_bus else ""
        out["transit_lines"] = " > ".join(s.get("transitDetails", {}).get("transitLine", {}).get("nameShort", "?") for s in steps if s.get("travelMode") == "TRANSIT")
    drive = ask(key, {**base, "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE_OPTIMAL"}, "routes.duration")
    if drive.get("routes"):
        d = _seconds(drive["routes"][0].get("duration"))
        out["driving_min"] = round(d / 60, 1) if d else ""
    return out


def main() -> None:
    key = os.environ.get(KEY_ENV, "")
    if not key:
        raise SystemExit(f"{KEY_ENV} is not set in .env")
    pairs = list(csv.DictReader(open(PAIRS)))
    done = set()
    if OUT.exists():
        done = {(r["route"], r["from"], r["to"], r["day"], r["at"]) for r in csv.DictReader(open(OUT))}
    new_file = not OUT.exists()
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for pair in pairs:
            for kind, clock in SLOTS:
                if (pair["route"], pair["from"], pair["to"], kind, clock) in done:
                    continue
                when = next_departure(kind, clock)
                try:
                    res = query(key, pair, when)
                except urllib.error.HTTPError as exc:
                    raise SystemExit(f"Google answered {exc.code}: {exc.read()[:300].decode(errors='replace')}") from exc
                w.writerow({"route": pair["route"], "from": pair["from"], "to": pair["to"], "day": kind, "at": clock,
                            "departure_utc": when.isoformat() + "Z", **res, "queried_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
                f.flush()
                print(f"{pair['route']:>14} {kind:8} {clock}  bus {res['transit_on_bus_min'] or '-':>5}  whole trip {res['transit_total_min'] or '-':>5}  driving {res['driving_min'] or '-':>5}  [{res['transit_lines']}]")
                time.sleep(0.2)


if __name__ == "__main__":
    main()
