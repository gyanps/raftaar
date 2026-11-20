"""What the buses are doing right now, turned into the same numbers the replay test uses.

Every bus keeps a rolling 90 minutes of fixes. Once a minute the offline matcher (the very same functions)
runs on each bus's window: trips, then stop arrivals and departures, then stop-to-stop runs. Each recent run
is compared with the profile; the average surprise over the last 20 minutes, city-wide and per stretch,
becomes a multiplier for every stretch and look-ahead band (see model/live.py for the formula and how its
weights were fitted).
"""
from __future__ import annotations

import time

import numpy as np

from ..calendar import IST_OFFSET_S, daytype
from ..config import LAT_MAX, LAT_MIN, LNG_MAX, LNG_MIN
from ..geo.planar import to_xy
from ..model import live as blend
from ..model.observations import MAX_KMH, MIN_KMH, MIN_RUN_M
from ..pipeline import match as M
from ..store import Network

BUFFER_S = 5400            # fixes kept per bus
WINDOW_S = blend.WINDOW_BUCKETS * blend.BUCKET_S
MIN_RUNS = 150             # fewer recent runs than this city-wide: not enough to call it "live"
FACTOR_MIN, FACTOR_MAX = 0.5, 2.5
FRESH_S = 300              # a bus whose last fix is older than this is not shown as live


def factors_from_runs(seg: np.ndarray, rho: np.ndarray, n_seg: int, weights: np.ndarray) -> tuple[np.ndarray, float]:
    """[look-ahead band, stretch] multipliers and the city-wide surprise, from the recent runs' surprises."""
    if len(rho) == 0:
        return np.ones((len(weights), n_seg), dtype=np.float32), 0.0
    city = float(rho.mean())
    n = np.bincount(seg, minlength=n_seg)
    own_mean = np.bincount(seg, weights=rho, minlength=n_seg) / np.maximum(n, 1)
    own = np.where(n > 0, (own_mean - city) * n / (n + blend.KAPPA), 0.0)
    f = np.exp(weights[:, 0:1] * city + weights[:, 1:2] * own[None, :])
    return np.clip(f, FACTOR_MIN, FACTOR_MAX).astype(np.float32), city


class LiveState:
    def __init__(self, net: Network, model, weights: np.ndarray, centre: np.ndarray):
        self.net, self.model, self.weights, self.centre = net, model, weights, centre
        self.fixes: dict[str, list[tuple[int, float, float, int]]] = {}    # bus -> [(ts, x, y, route label)]
        self.dirty: set[str] = set()
        self.runs: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}   # bus -> (stretch, time known, surprise)
        self.on_trip: dict[str, tuple[int, float]] = {}                     # bus -> (pattern, metres along it)
        self.last: dict[str, tuple[int, float, float, int]] = {}            # bus -> (ts, lat, lng, route)
        self.factors = np.ones((len(weights), net.n_segments), dtype=np.float32)
        self.summary = {"healthy": False, "runs_last_20_min": 0, "city_vs_usual_pct": 0.0, "stretches_with_live_data": 0,
                        "buses_on_a_trip": 0}

    def ingest(self, batch: dict) -> int:
        """Add the newest position of each bus. Returns how many were new."""
        x, y = to_xy(batch["lat"], batch["lng"])
        new = 0
        for i, vid in enumerate(batch["vehicle"]):
            ts = int(batch["ts"][i])
            lat, lng = float(batch["lat"][i]), float(batch["lng"][i])
            if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
                continue
            buf = self.fixes.setdefault(vid, [])
            if buf and ts <= buf[-1][0]:
                continue                                   # the same fix again, or an older one arriving late
            buf.append((ts, float(x[i]), float(y[i]), int(batch["route"][i])))
            self.last[vid] = (ts, lat, lng, int(batch["route"][i]))
            self.dirty.add(vid)
            new += 1
        return new

    def rematch(self, now: float | None = None) -> dict:
        """Re-read trips and runs for every bus that sent something new, then rebuild the multipliers."""
        now = time.time() if now is None else now
        net = self.net
        for vid in list(self.dirty):
            buf = [f for f in self.fixes[vid] if f[0] >= now - BUFFER_S]
            self.fixes[vid] = buf
            if len(buf) < 8:
                continue
            ts = np.array([f[0] for f in buf], dtype=np.int64)
            x = np.array([f[1] for f in buf])
            y = np.array([f[2] for f in buf])
            label = np.array([f[3] for f in buf], dtype=np.int32)
            segs, known, t_dep, secs, metres = [], [], [], [], []
            self.on_trip.pop(vid, None)
            for p, row, covered, t_on, c_on in M.match_vehicle(net, ts, x, y, label):
                sl = net.stops(p)
                k_n = sl.stop - sl.start
                arr, dep = np.empty(k_n), np.empty(k_n)
                M.stop_times(t_on, c_on, net.ps_chain[sl], net.ps_zb[sl], net.ps_za[sl], arr, dep)
                run_s = arr[1:] - dep[:-1]
                run_m = net.ps_run_m[sl][:-1]
                ok = ~np.isnan(run_s) & (run_s > 0) & (run_m >= MIN_RUN_M) & (net.ps_seg[sl][:-1] >= 0)
                ok &= (run_m / np.where(ok, run_s, 1.0) >= MIN_KMH / 3.6) & (run_m / np.where(ok, run_s, 1.0) <= MAX_KMH / 3.6)
                segs.append(net.ps_seg[sl][:-1][ok])
                known.append(arr[1:][ok])
                t_dep.append(dep[:-1][ok])
                secs.append(run_s[ok])
                metres.append(run_m[ok])
                if t_on[-1] >= now - FRESH_S:
                    self.on_trip[vid] = (int(p), float(c_on[-1]))
            if segs and sum(len(s) for s in segs):
                r = {"seg": np.concatenate(segs), "t": np.concatenate(t_dep), "run_s": np.concatenate(secs), "run_m": np.concatenate(metres)}
                self.runs[vid] = (r["seg"].astype(np.int64), np.concatenate(known), blend.surprises(self.model, r, self.centre))
            else:
                self.runs.pop(vid, None)
        self.dirty.clear()
        for vid in [v for v, f in self.last.items() if f[0] < now - BUFFER_S]:      # buses that went quiet
            for store in (self.fixes, self.runs, self.last, self.on_trip):
                store.pop(vid, None)

        seg = [r[0][(r[1] > now - WINDOW_S) & (r[1] <= now)] for r in self.runs.values()]
        rho = [r[2][(r[1] > now - WINDOW_S) & (r[1] <= now)] for r in self.runs.values()]
        seg = np.concatenate(seg) if seg else np.empty(0, dtype=np.int64)
        rho = np.concatenate(rho) if rho else np.empty(0)
        healthy = len(rho) >= MIN_RUNS
        factors, city = factors_from_runs(seg, rho, net.n_segments, self.weights)
        self.factors = factors if healthy else np.ones_like(factors)
        self.summary = {
            "healthy": bool(healthy), "runs_last_20_min": int(len(rho)),
            "city_vs_usual_pct": round((float(np.exp(city)) - 1) * 100, 1) if healthy else 0.0,
            "stretches_with_live_data": int(len(np.unique(seg))), "buses_on_a_trip": len(self.on_trip),
        }
        return self.summary

    def vehicles(self, now: float | None = None) -> list[list]:
        """[id, lat, lng, route name or '', seconds since its last fix, on a matched trip] for buses heard from recently."""
        now = time.time() if now is None else now
        names = {}
        out = []
        for vid, (ts, lat, lng, route) in self.last.items():
            if now - ts > FRESH_S:
                continue
            if route not in names:
                pats = self.net.route_patterns.get(route, [])
                names[route] = self.net.pat_name[pats[0]] if pats else ""
            out.append([vid, round(lat, 5), round(lng, 5), names[route], int(now - ts), vid in self.on_trip])
        return out
