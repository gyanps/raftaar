"""Origin, destination, departure time -> journey options a person can follow."""
from __future__ import annotations

import datetime as dt
import time

import numpy as np

from ..calendar import IST_OFFSET_S, N_BINS, BIN_S, daytype
from ..config import ART
from ..geo import polyline
from ..geo.planar import to_xy
from ..model.traveltime import TravelTimeModel
from ..store import Network
from . import raptor
from .build import OUT, TRANSFER_PENALTY_S, walk_seconds

ACCESS_M = 800.0
ACCESS_FALLBACK_M = 2000.0
MAX_BUSES = 3
WORTH_ANOTHER_BUS_S = 180       # an extra change must save at least this much
WALK_ONLY_M = 1500.0
ALT_MAX_RATIO = 1.6             # an alternative may take at most this many times the best option...
ALT_MAX_EXTRA_S = 1500          # ...and at most this much longer


def hhmm(epoch: float) -> str:
    return (dt.datetime(1970, 1, 1) + dt.timedelta(seconds=epoch + IST_OFFSET_S)).strftime("%H:%M")


class Planner:
    def __init__(self, net: Network | None = None, model: TravelTimeModel | None = None):
        self.net = net or Network()
        self.model = model or TravelTimeModel.load(self.net)
        z = np.load(OUT)
        self.freq = z["freq"]
        self.freq_from_gps = z["freq_from_gps"]
        self.alpha_s = float(z["alpha"]) * 3600.0
        self.sp_off, self.sp_pat, self.sp_seq = z["sp_off"], z["sp_pat"].astype(np.int64), z["sp_seq"].astype(np.int64)
        self.fp_off, self.fp_to, self.fp_s = z["fp_off"], z["fp_to"].astype(np.int64), z["fp_s"].astype(np.float64)
        n = self.net
        self.ps_stop = n.ps_stop.astype(np.int64)
        self.ps_seg = n.ps_seg.astype(np.int64)
        self.n_stops = len(n.stop_id)
        self.no_ban = np.zeros(n.n_patterns, dtype=np.bool_)
        # "A typical day": one band, every stretch multiplied by 1. Live mode passes real multipliers instead.
        self.typical = (np.ones((1, n.n_segments), dtype=np.float32), np.empty(0))

    # ---- geometry helpers ---------------------------------------------------------------------
    def stops_near(self, lat: float, lng: float, radius: float = ACCESS_M):
        x, y = to_xy(lat, lng)
        d = np.hypot(self.net.stop_x - x, self.net.stop_y - y)
        idx = np.flatnonzero(d <= radius)
        if len(idx) == 0:
            idx = np.argsort(d)[:3]
            idx = idx[d[idx] <= ACCESS_FALLBACK_M]
        return idx.astype(np.int64), d[idx]

    def leg_shape(self, p: int, a: int, b: int) -> list[tuple[float, float]]:
        n = self.net
        lo = n.ps_off[p]
        s0, s1 = n.ps_chain[lo + a], n.ps_chain[lo + b]
        sh = n.pat_shape[p]
        i0, i1 = n.sh_off[sh], n.sh_off[sh + 1]
        cum = n.sh_cum[i0:i1]
        inside = np.flatnonzero((cum > s0) & (cum < s1)) + i0
        pts = [(float(n.stop_lat[n.ps_stop[lo + a]]), float(n.stop_lng[n.ps_stop[lo + a]]))]
        pts += [(float(n.sh_lat[i]), float(n.sh_lng[i])) for i in inside]
        pts.append((float(n.stop_lat[n.ps_stop[lo + b]]), float(n.stop_lng[n.ps_stop[lo + b]])))
        return pts

    # ---- one bus leg, with every line worth taking ------------------------------------------------
    def lines_between(self, a_stop: int, b_stop: int, t_at_stop: float, dtp: int, banned: np.ndarray, live=None, t0: float = 0.0):
        """Every pattern that goes a_stop -> b_stop, pooled the way a waiting rider would use them.
        live = (multipliers, band edges) describing the network at t0; None = a typical day."""
        lf, edges = live or self.typical
        n = self.net
        hour = int(((t_at_stop + IST_OFFSET_S) % 86400) // 3600)
        cands = []
        for q in range(self.sp_off[a_stop], self.sp_off[a_stop + 1]):
            p, i = int(self.sp_pat[q]), int(self.sp_seq[q])
            f = float(self.freq[p, dtp, hour])
            if banned[p] or f < raptor.MIN_FREQ:
                continue
            lo, hi = int(n.ps_off[p]), int(n.ps_off[p + 1])
            later = np.flatnonzero(self.ps_stop[lo + i + 1 : hi] == b_stop)
            if len(later) == 0:
                continue
            j = i + 1 + int(later[0])
            board = t_at_stop + min(self.alpha_s / f, raptor.MAX_SINGLE_WAIT_S)
            ride = raptor.ride_seconds(self.model.runs.est, self.model.dwells.est, n.ps_off, self.ps_stop, self.ps_seg,
                                       n.ps_run_m, p, i, j, board, dtp, lf, edges, t0)
            cands.append((ride, f, p, i, j))
        cands.sort()
        chosen, f_sum, ft_sum, expect = [], 0.0, 0.0, np.inf
        for ride, f, p, i, j in cands:
            if ride < expect:
                chosen.append((ride, f, p, i, j))
                f_sum += f
                ft_sum += f * ride
                expect = (self.alpha_s + ft_sum) / f_sum
        return chosen, (self.alpha_s / f_sum if f_sum else np.inf), (ft_sum / f_sum if f_sum else np.inf)

    def bus_leg(self, a_stop: int, b_stop: int, t_at_stop: float, dtp: int, banned: np.ndarray, live=None, t0: float = 0.0) -> dict | None:
        n = self.net
        chosen, wait, ride = self.lines_between(a_stop, b_stop, t_at_stop, dtp, banned, live, t0)
        if not chosen:
            return None
        main = max(chosen, key=lambda c: c[1])                       # the line a rider is most likely to end up on
        _, _, p, i, j = main
        factors = self.model.calib.factors(j - i, t_at_stop + wait)[0]
        ride_q = ride * factors
        # The same ride at the emptiest half-hour of this kind of day, and what the timetable claims.
        base = t_at_stop - ((t_at_stop + IST_OFFSET_S) % 86400)
        by_bin = np.array([raptor.ride_seconds(self.model.runs.est, self.model.dwells.est, n.ps_off, self.ps_stop, self.ps_seg,
                                               n.ps_run_m, p, i, j, base + (b + 0.5) * BIN_S, dtp, self.typical[0], self.typical[1], 0.0)
                           for b in range(N_BINS)])
        open_bins = np.flatnonzero(self.freq[p, dtp].repeat(2) >= raptor.MIN_FREQ)
        quiet_bin = int(open_bins[np.argmin(by_bin[open_bins])]) if len(open_bins) else int(np.argmin(by_bin))
        # The main line's quiet-to-now ratio is applied to the pooled ride, so the two figures describe the same set of buses.
        quiet_ratio = min(1.0, float(by_bin[quiet_bin]) / main[0]) if main[0] > 0 else 1.0
        lo = int(n.ps_off[p])
        return {
            "mode": "bus",
            "board": self.stop_json(a_stop), "alight": self.stop_json(b_stop),
            "stops": j - i,
            "distance_km": round(float(n.ps_chain[lo + j] - n.ps_chain[lo + i]) / 1000, 2),
            "lines": self._lines(chosen, float(factors[2])),
            "wait_min": round(wait / 60, 1),
            "ride_min": round(float(ride_q[2]) / 60, 1),
            "ride_range_min": [round(float(ride_q[0]) / 60, 1), round(float(ride_q[4]) / 60, 1)],
            "ride_quiet_min": round(float(ride_q[2]) * quiet_ratio / 60, 1),
            "quiet_at": f"{quiet_bin // 2:02d}:{30 * (quiet_bin % 2):02d}",
            "traffic_delay_min": round(float(ride_q[2]) * (1.0 - quiet_ratio) / 60, 1),
            "ride_timetable_min": round(float(n.ps_sched[lo + j] - n.ps_sched[lo + i]) / 60, 1),
            "polyline": polyline.encode(self.leg_shape(p, i, j)),
            "_seconds": (wait, float(ride_q[2]), float(ride_q[0]), float(ride_q[4])),
            "_patterns": [c[2] for c in chosen],
        }

    def _lines(self, chosen, factor: float) -> list[dict]:
        """One entry per route name (a route's full and short-turn patterns are the same bus to a rider)."""
        n = self.net
        merged: dict[str, dict] = {}
        for ride, f, p, _, _ in chosen:
            m = merged.setdefault(n.pat_name[p], {"route": n.pat_name[p], "towards": (n.pat_desc[p] or "").split(" to ")[-1],
                                                  "f": 0.0, "ft": 0.0, "gps": False})
            m["f"] += f
            m["ft"] += f * ride
            m["gps"] = m["gps"] or bool(self.freq_from_gps[p])
        return [{"route": m["route"], "towards": m["towards"], "buses_per_hour": round(m["f"], 1),
                 "ride_min": round(m["ft"] / m["f"] * factor / 60, 1), "frequency_source": "gps" if m["gps"] else "timetable"}
                for m in sorted(merged.values(), key=lambda m: -m["f"])]

    def stop_json(self, s: int) -> dict:
        n = self.net
        return {"id": n.stop_id[s], "name": n.stop_name[s], "lat": round(float(n.stop_lat[s]), 6), "lng": round(float(n.stop_lng[s]), 6)}

    @staticmethod
    def walk_leg(frm: dict, to: dict, seconds: float, metres: float) -> dict:
        return {"mode": "walk", "from": frm, "to": to, "minutes": round(seconds / 60, 1), "distance_m": int(round(metres)),
                "_seconds": seconds}

    # ---- search ---------------------------------------------------------------------------------
    def _search(self, t0, dtp, acc, egr, banned, live=None):
        n = self.net
        lf, edges = live or self.typical
        k1 = MAX_BUSES + 1
        tau = np.full((k1, self.n_stops), np.inf)
        par_stop = np.full((k1, self.n_stops), -1, dtype=np.int64)
        par_walk = np.full((k1, self.n_stops), -1, dtype=np.int64)
        par_pat = np.full((k1, self.n_stops), -1, dtype=np.int64)
        par_bseq = np.zeros((k1, self.n_stops), dtype=np.int64)
        par_aseq = np.zeros((k1, self.n_stops), dtype=np.int64)
        par_wait = np.zeros((k1, self.n_stops))
        par_ride = np.zeros((k1, self.n_stops))
        dest_time = np.full(k1, np.inf)
        dest_stop = np.full(k1, -1, dtype=np.int64)
        raptor.search(t0, dtp, acc[0], acc[1], egr[0], egr[1], MAX_BUSES, self.alpha_s,
                      n.ps_off, self.ps_stop, self.ps_seg, n.ps_run_m, self.sp_off, self.sp_pat, self.sp_seq, self.freq, banned,
                      self.fp_off, self.fp_to, self.fp_s, self.model.runs.est, self.model.dwells.est, lf, edges,
                      tau, par_stop, par_walk, par_pat, par_bseq, par_aseq, par_wait, par_ride, dest_time, dest_stop)
        return tau, par_stop, par_walk, dest_time, dest_stop

    def _trace(self, k, tau, par_stop, par_walk, dest_stop):
        """Hops of the k-bus journey in travel order: [(kind, from_stop, to_stop, when the search had the rider at from_stop)]."""
        hops = []
        s = int(dest_stop[k])
        while k > 0:
            w = int(par_walk[k, s])
            if w >= 0:
                hops.append(("walk", w, s, float(tau[k, w])))
                s = w
            a = int(par_stop[k, s])
            hops.append(("bus", a, s, float(tau[k - 1, a])))
            s = a
            k -= 1
        return hops[::-1], s

    def plan(self, origin: tuple[float, float], dest: tuple[float, float], depart: float, max_options: int = 3, live=None) -> dict:
        """live = (multipliers [band, stretch], band edges in seconds) from the live feed, valid for 'depart' = now."""
        t_start = time.perf_counter()
        n = self.net
        dtp = int(daytype(depart))
        o_stops, o_d = self.stops_near(*origin)
        d_stops, d_d = self.stops_near(*dest)
        ox, oy = to_xy(*origin)
        dx, dy = to_xy(*dest)
        direct_m = float(np.hypot(dx - ox, dy - oy))
        options: list[dict] = []
        if len(o_stops) and len(d_stops):
            acc = (o_stops, walk_seconds(o_d).astype(np.float64))
            egr = (d_stops, walk_seconds(d_d).astype(np.float64))
            banned = self.no_ban.copy()
            for attempt in range(max_options):
                tau, par_stop, par_walk, dest_time, dest_stop = self._search(depart, dtp, acc, egr, banned, live)
                found = []
                best_so_far = np.inf
                for k in range(1, MAX_BUSES + 1):
                    if dest_time[k] < best_so_far - WORTH_ANOTHER_BUS_S:
                        best_so_far = dest_time[k]
                        found.append(self._build(k, tau, par_stop, par_walk, dest_stop, origin, dest, depart, dtp, acc, egr, banned, live))
                found = [f for f in found if f]
                if not found:
                    break
                for f in found:
                    best = min((o["duration_min"] for o in options), default=None)
                    if best is not None and (f["duration_min"] > best * ALT_MAX_RATIO or f["duration_min"] > best + ALT_MAX_EXTRA_S / 60):
                        continue
                    if any(self._same(f, o) for o in options):
                        continue
                    options.append(f)
                # next attempt: forbid the lines of the fastest journey's first bus, to surface a different corridor
                first_bus = next(l for l in min(found, key=lambda f: f["duration_min"])["legs"] if l["mode"] == "bus")
                banned[first_bus["_patterns"]] = True
                if len(options) >= max_options:
                    break
        if direct_m <= WALK_ONLY_M or not options:
            sec = float(walk_seconds(direct_m))
            if direct_m <= 4000:
                options.append({"summary": "Walk", "buses": 0, "depart": hhmm(depart), "arrive": hhmm(depart + sec),
                                "duration_min": round(sec / 60, 1), "range_min": [round(sec / 60, 1)] * 2, "walk_min": round(sec / 60, 1),
                                "wait_min": 0.0, "ride_min": 0.0, "quiet_duration_min": round(sec / 60, 1), "traffic_delay_min": 0.0,
                                "timetable_duration_min": round(sec / 60, 1),
                                "legs": [self.walk_leg({"name": "Origin", "lat": origin[0], "lng": origin[1]},
                                                       {"name": "Destination", "lat": dest[0], "lng": dest[1]}, sec, direct_m * 1.3)]})
        # An option with more buses has to earn it: drop it if one with fewer buses is within 3 minutes.
        options = [o for o in options
                   if not any(p["buses"] < o["buses"] and p["duration_min"] <= o["duration_min"] + WORTH_ANOTHER_BUS_S / 60 for p in options)]
        options.sort(key=lambda o: o["duration_min"])
        options = options[:max_options]
        for o in options:
            for leg in o["legs"]:
                for key in [k for k in leg if k.startswith("_")]:
                    del leg[key]
        return {"origin": {"lat": origin[0], "lng": origin[1]}, "destination": {"lat": dest[0], "lng": dest[1]},
                "depart": hhmm(depart), "day_type": ("weekday", "saturday", "sunday_holiday")[dtp],
                "mode": "live" if live else "typical",
                "straight_line_km": round(direct_m / 1000, 2), "options": options,
                "took_ms": round((time.perf_counter() - t_start) * 1000, 1)}

    @staticmethod
    def _same(a: dict, b: dict) -> bool:
        key = lambda o: [(l["board"]["name"], l["alight"]["name"], l["lines"][0]["route"]) for l in o["legs"] if l["mode"] == "bus"]
        return key(a) == key(b)

    def _build(self, k, tau, par_stop, par_walk, dest_stop, origin, dest, depart, dtp, acc, egr, banned, live=None) -> dict | None:
        n = self.net
        hops, first_stop = self._trace(k, tau, par_stop, par_walk, dest_stop)
        legs = []
        t = depart
        a_sec = float(acc[1][np.flatnonzero(acc[0] == first_stop)[0]])
        legs.append(self.walk_leg({"name": "Origin", "lat": origin[0], "lng": origin[1]}, self.stop_json(first_stop), a_sec,
                                  a_sec * 1.2))
        t += a_sec
        lo = hi = 0.0
        for kind, a, b, t_search in hops:
            if kind == "walk":
                d = float(np.hypot(n.stop_x[a] - n.stop_x[b], n.stop_y[a] - n.stop_y[b]))
                sec = float(walk_seconds(d)) + TRANSFER_PENALTY_S
                legs.append(self.walk_leg(self.stop_json(a), self.stop_json(b), sec, d * 1.3))
                t += sec
            else:
                # Price the leg at the moment the search boarded it, so the same routes and frequencies apply.
                leg = self.bus_leg(a, b, t_search, dtp, banned, live, depart)
                if leg is None:
                    return None
                wait, ride, r_lo, r_hi = leg["_seconds"]
                leg["board_at"] = hhmm(t + wait)
                leg["alight_at"] = hhmm(t + wait + ride)
                legs.append(leg)
                t += wait + ride
                lo += ride - r_lo
                hi += r_hi - ride
        last = int(dest_stop[k])
        e_sec = float(egr[1][np.flatnonzero(egr[0] == last)[0]])
        legs.append(self.walk_leg(self.stop_json(last), {"name": "Destination", "lat": dest[0], "lng": dest[1]}, e_sec, e_sec * 1.2))
        t += e_sec
        buses = [l for l in legs if l["mode"] == "bus"]
        total = t - depart
        delay = sum(l["traffic_delay_min"] for l in buses)
        ride_min = sum(l["ride_min"] for l in buses)
        return {
            "summary": " → ".join(l["lines"][0]["route"] + ("+" if len(l["lines"]) > 1 else "") for l in buses),
            "buses": len(buses),
            "depart": hhmm(depart), "arrive": hhmm(t),
            "duration_min": round(total / 60, 1),
            "range_min": [round((total - lo) / 60, 1), round((total + hi) / 60, 1)],
            "walk_min": round(sum(l["minutes"] for l in legs if l["mode"] == "walk"), 1),
            "wait_min": round(sum(l["wait_min"] for l in buses), 1),
            "ride_min": round(ride_min, 1),
            "quiet_duration_min": round(total / 60 - delay, 1),
            "traffic_delay_min": round(delay, 1),
            "timetable_duration_min": round(total / 60 - ride_min + sum(l["ride_timetable_min"] for l in buses), 1),
            "legs": legs,
        }
