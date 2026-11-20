"""HTTP API and the map screen.

    uv run uvicorn raftaar.api.main:app --port 8000
    open http://localhost:8000        (map)      http://localhost:8000/docs   (API reference)
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ..calendar import DAYTYPE_NAMES, IST_OFFSET_S, N_BINS, daytype_of_date
from ..config import ART, LAT_MAX, LAT_MIN, LNG_MAX, LNG_MIN, ROOT
from ..geo import polyline
from ..live import feed as live_feed
from ..live.service import LiveService
from ..live.state import LiveState
from ..model import live as blend
from ..model.traveltime import MODEL_DIR, TravelTimeModel
from ..planner.journeys import Planner
from ..store import Network
from .places import Places

WEB = ROOT / "web"
STATE: dict = {}
COORD = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def now_ist() -> dt.datetime:
    return dt.datetime.utcnow() + dt.timedelta(seconds=IST_OFFSET_S)


def hhmm_now() -> str:
    return now_ist().strftime("%H:%M:%S")


def to_epoch(local: dt.datetime) -> float:
    return (local - dt.datetime(1970, 1, 1)).total_seconds() - IST_OFFSET_S


def parse_depart(depart: str, day: str | None) -> float:
    """'now', '2026-11-24T08:30', or '08:30' with day = weekday | saturday | sunday (a typical day of that kind)."""
    now = now_ist()
    if depart in ("", "now"):
        return to_epoch(now)
    try:
        if "T" in depart or " " in depart:
            return to_epoch(dt.datetime.fromisoformat(depart.replace(" ", "T")[:16]))
        hh, mm = depart.split(":")[:2]
        clock = dt.time(int(hh), int(mm))
    except ValueError as exc:
        raise HTTPException(422, "depart must be 'now', YYYY-MM-DDTHH:MM or HH:MM (Indian Standard Time)") from exc
    date = now.date()
    if day:
        want = {"weekday": 0, "saturday": 1, "sunday": 2, "sunday_holiday": 2}.get(day.lower())
        if want is None:
            raise HTTPException(422, "day must be weekday, saturday or sunday")
        for ahead in range(1, 15):
            date = now.date() + dt.timedelta(days=ahead)
            if daytype_of_date(date) == want and (want != 0 or date.weekday() in (1, 2, 3)):
                break
    return to_epoch(dt.datetime.combine(date, clock))


def resolve(text: str, what: str) -> tuple[tuple[float, float], str]:
    m = COORD.match(text)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
            raise HTTPException(422, f"{what} is outside Delhi NCR (expected 'lat,lng')")
        return (lat, lng), f"{lat:.5f}, {lng:.5f}"
    hits = STATE["places"].search(text, 1)
    if not hits:
        raise HTTPException(404, f"No bus stop matches {what} '{text}'. Try /api/places?q=...")
    return (hits[0]["lat"], hits[0]["lng"]), hits[0]["name"]


def build_segments(net: Network) -> dict:
    """One line per stop-to-stop stretch, for the traffic layer."""
    lines = [""] * net.n_segments
    for p in range(net.n_patterns):
        if not net.pat_monotone[p]:
            continue
        lo, hi = int(net.ps_off[p]), int(net.ps_off[p + 1])
        sh = net.pat_shape[p]
        i0, i1 = int(net.sh_off[sh]), int(net.sh_off[sh + 1])
        cum = net.sh_cum[i0:i1]
        for r in range(lo, hi - 1):
            s = int(net.ps_seg[r])
            if s < 0 or lines[s]:
                continue
            a, b = net.ps_chain[r], net.ps_chain[r + 1]
            mid = np.flatnonzero((cum > a) & (cum < b)) + i0
            pts = [(net.stop_lat[net.ps_stop[r]], net.stop_lng[net.ps_stop[r]])]
            pts += [(net.sh_lat[i], net.sh_lng[i]) for i in mid]
            pts.append((net.stop_lat[net.ps_stop[r + 1]], net.stop_lng[net.ps_stop[r + 1]]))
            lines[s] = polyline.encode(pts)
    return {"polylines": lines, "length_m": [int(x) for x in net.seg_len]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    net = Network()
    model = TravelTimeModel.load(net)
    planner = Planner(net, model)
    planner.plan((28.6674, 77.2297), (28.5483, 77.2563), to_epoch(now_ist()))   # compile the search once, before the first visitor
    STATE.update(net=net, model=model, planner=planner,
                 places=Places(net, np.diff(planner.sp_off)), segments=build_segments(net))
    for name in ("backtest.json", "match_stats.json", "reduce_stats.json", "planner_summary.json"):
        path = ART / name
        STATE[name] = json.loads(path.read_text()) if path.exists() else None
    # Live feed: only with a key in .env and the fitted look-ahead weights; otherwise every answer is "typical day".
    STATE["live"] = None
    if os.environ.get(live_feed.KEY_ENV) and (MODEL_DIR / "live_weights.npy").exists() and os.environ.get("RAFTAAR_LIVE", "1") != "0":
        state = LiveState(net, model, np.load(MODEL_DIR / "live_weights.npy"), np.load(MODEL_DIR / "live_centre.npy"))
        STATE["live"] = LiveService(state).start()
    yield
    if STATE["live"]:
        STATE["live"].stop()


app = FastAPI(
    title="Raftaar",
    version="0.1.0",
    lifespan=lifespan,
    description="Delhi bus travel times learned from the bus fleet's own GPS, and a journey planner on top. "
                "All clock times are Indian Standard Time. Built for the Transport Stack Hackathon 2026, Problem 1.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.get("/api/journeys", summary="Bus journeys between two places")
def journeys(
    frm: str = Query(..., alias="from", description="'lat,lng' or part of a bus stop name", examples=["ISBT Kashmere Gate"]),
    to: str = Query(..., description="'lat,lng' or part of a bus stop name", examples=["Nehru Place Terminal"]),
    depart: str = Query("now", description="'now', 'YYYY-MM-DDTHH:MM', or 'HH:MM' together with day", examples=["08:30"]),
    day: str | None = Query(None, description="with an HH:MM depart: weekday, saturday or sunday (a typical day of that kind)"),
    options: int = Query(3, ge=1, le=5),
):
    """Up to *options* journeys. Each lists the walk to the first stop, where to board, every route worth taking
    there (a rider takes whichever comes first), where to get off, any change, the walk to the destination,
    and the total time with a range.

    Time on the bus comes from how fast buses really moved on each stretch at that time of day and kind of
    day; it is compared with the same ride at the quietest half-hour (*ride_quiet_min*) and with the
    published timetable (*ride_timetable_min*). Waiting comes from how often buses were really seen.

    *mode* is "live" when the trip leaves within 10 minutes of now and the live feed is healthy: the estimate
    is then adjusted by how buses ran in the last 20 minutes (*live* gives the figures). Otherwise "typical"."""
    origin, o_name = resolve(frm, "from")
    dest, d_name = resolve(to, "to")
    when = parse_depart(depart, day)
    svc: LiveService | None = STATE["live"]
    use_live = svc is not None and svc.usable() and abs(when - time.time()) <= 600      # live data only describes "about now"
    res = STATE["planner"].plan(origin, dest, when, options, (svc.state.factors, blend.HORIZON_EDGES_S) if use_live else None)
    res["origin"]["name"], res["destination"]["name"] = o_name, d_name
    if use_live:
        res["live"] = {k: svc.state.summary[k] for k in ("runs_last_20_min", "city_vs_usual_pct", "stretches_with_live_data", "buses_on_a_trip")}
    return res


@app.get("/api/places", summary="Find bus stops by name")
def places(q: str = Query(..., min_length=2), limit: int = Query(8, ge=1, le=25)):
    return STATE["places"].search(q, limit)


@app.get("/api/traveltime", summary="Time on the bus between two stops, hour by hour")
def traveltime(
    frm: str = Query(..., alias="from", description="'lat,lng' or stop name: boarding within 250 m of it"),
    to: str = Query(..., description="'lat,lng' or stop name: alighting within 250 m of it"),
    day: str = Query("weekday", description="weekday, saturday or sunday"),
):
    """The direct bus ride between two places for every half-hour of a typical day: the model's estimate with its
    10th-90th percentile range, next to the timetable's single figure. No walking or waiting."""
    pl: Planner = STATE["planner"]
    net: Network = STATE["net"]
    origin, o_name = resolve(frm, "from")
    dest, d_name = resolve(to, "to")
    want = {"weekday": 0, "saturday": 1, "sunday": 2}.get(day.lower())
    if want is None:
        raise HTTPException(422, "day must be weekday, saturday or sunday")
    a_stops, _ = pl.stops_near(*origin, radius=250)
    b_stops, _ = pl.stops_near(*dest, radius=250)
    base = parse_depart("00:00", day)
    best = None
    for a in a_stops:
        for b in b_stops:
            chosen, _, _ = pl.lines_between(int(a), int(b), base + 9 * 3600, want, pl.no_ban)   # typical day
            if chosen and (best is None or len(chosen) > len(best[2])):
                best = (int(a), int(b), chosen)
    if best is None:
        raise HTTPException(404, "No direct bus between these two places")
    a, b, chosen = best
    _, _, p, i, j = max(chosen, key=lambda c: c[1])
    t = base + (np.arange(N_BINS) + 0.5) * 1800
    q = STATE["model"].predict_quantiles(np.full(N_BINS, p), np.full(N_BINS, i), np.full(N_BINS, j), t)
    lo = int(net.ps_off[p])
    return {
        "from": pl.stop_json(a), "to": pl.stop_json(b), "day": DAYTYPE_NAMES[want], "route": net.pat_name[p], "stops": j - i,
        "distance_km": round(float(net.ps_chain[lo + j] - net.ps_chain[lo + i]) / 1000, 2),
        "timetable_min": round(float(net.ps_sched[lo + j] - net.ps_sched[lo + i]) / 60, 1),
        "by_half_hour": [{"at": f"{k // 2:02d}:{30 * (k % 2):02d}", "ride_min": round(float(q[k, 2]) / 60, 1),
                          "p10_min": round(float(q[k, 0]) / 60, 1), "p90_min": round(float(q[k, 4]) / 60, 1)} for k in range(N_BINS)],
    }


@app.get("/api/segments", summary="Geometry of every stop-to-stop stretch")
def segments():
    """Encoded polylines (precision 5), index = segment id used by /api/traffic."""
    return STATE["segments"]


@app.get("/api/traffic", summary="Bus speed on every stretch, for each half-hour of a typical day")
def traffic(day: str = Query("weekday", description="weekday, saturday or sunday")):
    """speeds: base64 of a uint8 matrix [segments x 48 half-hours], km/h while moving between stops (0 = no data).
    free_flow: base64 uint8 [segments], the fastest half-hour. observed: base64 uint8 [segments], 1 if GPS saw the stretch."""
    want = {"weekday": 0, "saturday": 1, "sunday": 2}.get(day.lower())
    if want is None:
        raise HTTPException(422, "day must be weekday, saturday or sunday")
    model: TravelTimeModel = STATE["model"]
    kmh = 3.6 / np.maximum(model.runs.est[:, want, :], 1e-6)
    kmh = np.clip(np.rint(kmh), 1, 120).astype(np.uint8)
    seen = model.runs.n_key > 0
    kmh[~seen] = 0
    b64 = lambda a: base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
    return {"day": DAYTYPE_NAMES[want], "segments": int(kmh.shape[0]), "bins": N_BINS, "speeds": b64(kmh),
            "free_flow": b64(kmh.max(axis=1)), "observed": b64(seen.astype(np.uint8)),
            "city_kmh_by_half_hour": [round(float(3.6 / v), 1) for v in
                                      (model.runs.rhythm[want] * np.average(model.runs.level[seen], weights=model.runs.n_key[seen]))]}


@app.get("/api/live/status", summary="Is live data flowing, and how is the city running against a usual day")
def live_status():
    """city_vs_usual_pct: how much slower (+) or faster (-) buses are moving than the profile says for this hour,
    averaged over the stop-to-stop runs completed in the last 20 minutes."""
    svc: LiveService | None = STATE["live"]
    return svc.status() if svc else {"usable": False, "reason": "no live feed configured; answers use the typical-day model"}


@app.get("/api/live/vehicles", summary="Where the buses are right now")
def live_vehicles():
    """rows: [bus id, lat, lng, route, seconds since its last position, whether it is matched to a route trip]."""
    svc: LiveService | None = STATE["live"]
    return {"as_of": hhmm_now(), "rows": svc.state.vehicles() if svc else []}


@app.get("/api/about", summary="What the model was built from, and how well it did on days it never saw")
def about():
    net: Network = STATE["net"]
    model: TravelTimeModel = STATE["model"]
    red = STATE.get("reduce_stats.json") or {}
    mat = STATE.get("match_stats.json") or {}
    return {
        "data": {"days": len(red), "raw_rows": sum(d["raw_rows"] for d in red.values()), "fixes": sum(d["fixes"] for d in red.values()),
                 "trips_rebuilt": sum(d["trips"] for d in mat.values()), "stop_events": sum(d["stop_events"] for d in mat.values())},
        "network": {"stops": len(net.stop_id), "patterns": net.n_patterns, "stretches": net.n_segments,
                    "stretches_seen_by_gps": int((model.runs.n_key > 0).sum())},
        "planner": STATE.get("planner_summary.json"),
        "backtest": STATE.get("backtest.json"),
    }


@app.get("/api/health", include_in_schema=False)
def health():
    return {"ok": True}


if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
