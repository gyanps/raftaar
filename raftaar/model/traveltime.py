"""Bus travel time between two stops of a pattern, leaving at a given time.

    time(a -> b) = sum of runs a..b-1  +  sum of dwells at the stops strictly between

Each run is (seconds per metre for that stretch at that time of day) x (its
metres); the clock advances as the bus moves, so a long trip that starts
before the evening peak and ends inside it is priced accordingly.
"""
from __future__ import annotations

import numpy as np
from numba import njit

from ..calendar import BIN_S, IST_OFFSET_S, N_BINS, N_DAYTYPES, daytype
from ..config import ART
from ..store import Network
from .profile import Profile

MODEL_DIR = ART / "model"


@njit(cache=True)
def _lookup(est, key, dtp, sod):
    """Value at a second of day, linear between half-hour centres."""
    pos = sod / BIN_S - 0.5
    b0 = int(np.floor(pos))
    w = pos - b0
    b1 = b0 + 1
    if b0 < 0:
        b0 += N_BINS
    if b1 >= N_BINS:
        b1 -= N_BINS
    return est[key, dtp, b0] * (1.0 - w) + est[key, dtp, b1] * w


@njit(cache=True)
def _span(run_est, dwell_est, ps_seg, ps_stop, ps_run_m, lo, a, b, t0, dtp):
    """Seconds from leaving stop a to reaching stop b on the pattern whose stops start at row lo."""
    t = t0
    for k in range(a, b):
        row = lo + k
        seg = ps_seg[row]
        if seg >= 0 and ps_run_m[row] > 0.0:
            t += _lookup(run_est, seg, dtp, (t + IST_OFFSET_S) % 86400.0) * ps_run_m[row]
        if k + 1 < b:
            t += _lookup(dwell_est, ps_stop[row + 1], dtp, (t + IST_OFFSET_S) % 86400.0)
    return t - t0


@njit(cache=True)
def _spans(run_est, dwell_est, ps_seg, ps_stop, ps_run_m, ps_off, pattern, a, b, t0, dtp, out):
    for i in range(len(pattern)):
        out[i] = _span(run_est, dwell_est, ps_seg, ps_stop, ps_run_m, ps_off[pattern[i]], a[i], b[i], t0[i], dtp[i])
    return 0


@njit(cache=True)
def _spans_flat(run_flat, dwell_flat, ps_seg, ps_stop, ps_run_m, ps_off, pattern, a, b, out):
    for i in range(len(pattern)):
        lo = ps_off[pattern[i]]
        t = 0.0
        for k in range(a[i], b[i]):
            row = lo + k
            if ps_seg[row] >= 0:
                t += run_flat[ps_seg[row]] * ps_run_m[row]
            if k + 1 < b[i]:
                t += dwell_flat[ps_stop[row + 1]]
        out[i] = t
    return 0


STOP_EDGES = np.array([6, 11, 21, 41])          # trips of 1-5, 6-10, 11-20, 21-40, 41+ stops
HOUR_EDGES = np.array([6, 8, 11, 16, 21])       # the same time bands the validation report uses
QUANTILES = (10, 25, 50, 75, 90)
MIN_CALIB_N = 300


class Calibration:
    """How real trips compare with the sum of their parts.

    Adding up typical stretches does not quite give the typical trip: delays on
    neighbouring stretches come together, and the long tail is clipped when each
    stretch is learned. So after the profiles are fitted, real trips from the same
    days are replayed through them and the ratio actual / predicted is kept per
    (trip length, day type, time band): its median corrects the estimate and its
    10th-90th percentiles give the range shown to the rider.
    """

    def __init__(self):
        self.q = np.ones((len(STOP_EDGES) + 1, N_DAYTYPES, len(HOUR_EDGES) + 1, len(QUANTILES)))
        self.q[..., :] = np.array([0.8, 0.9, 1.0, 1.12, 1.3])

    @staticmethod
    def cell(n_stops, t0):
        return (np.searchsorted(STOP_EDGES, n_stops, side="right"), daytype(t0).astype(np.int64),
                np.searchsorted(HOUR_EDGES, (np.asarray(t0) + IST_OFFSET_S) % 86400 / 3600, side="right"))

    def fit(self, n_stops, t0, ratio) -> "Calibration":
        s, d, h = self.cell(n_stops, t0)
        for si in range(self.q.shape[0]):
            by_len = ratio[s == si]
            if len(by_len) >= MIN_CALIB_N:
                self.q[si] = np.percentile(by_len, QUANTILES)
            for di in range(N_DAYTYPES):
                for hi in range(self.q.shape[2]):
                    r = ratio[(s == si) & (d == di) & (h == hi)]
                    if len(r) >= MIN_CALIB_N:
                        self.q[si, di, hi] = np.percentile(r, QUANTILES)
        return self

    def factors(self, n_stops, t0) -> np.ndarray:
        """[n, 5] multipliers: the 10th, 25th, 50th, 75th and 90th percentile of actual / predicted."""
        s, d, h = self.cell(np.atleast_1d(n_stops), np.atleast_1d(t0))
        return self.q[s, d, h]


class TravelTimeModel:
    def __init__(self, net: Network, runs: Profile, dwells: Profile, calib: Calibration | None = None):
        self.net, self.runs, self.dwells = net, runs, dwells
        self.calib = calib or Calibration()

    def predict_raw(self, pattern, a, b, t0) -> np.ndarray:
        """Sum of the parts: runs and dwells from leaving stop a to reaching stop b."""
        pattern = np.atleast_1d(pattern).astype(np.int64)
        a = np.atleast_1d(a).astype(np.int64)
        b = np.atleast_1d(b).astype(np.int64)
        t0 = np.atleast_1d(t0).astype(np.float64)
        out = np.empty(len(pattern))
        n = self.net
        _spans(self.runs.est, self.dwells.est, n.ps_seg, n.ps_stop, n.ps_run_m, n.ps_off, pattern, a, b, t0,
               daytype(t0).astype(np.int64), out)
        return out

    def predict(self, pattern, a, b, t0) -> np.ndarray:
        """Typical seconds from leaving stop a to reaching stop b, for arrays of (pattern, a, b, departure epoch)."""
        return self.predict_quantiles(pattern, a, b, t0)[:, 2]

    def predict_quantiles(self, pattern, a, b, t0) -> np.ndarray:
        """[n, 5] seconds: 10th, 25th, 50th, 75th, 90th percentile of what real trips took."""
        raw = self.predict_raw(pattern, a, b, t0)
        return raw[:, None] * self.calib.factors(np.atleast_1d(b) - np.atleast_1d(a), np.atleast_1d(t0))

    def predict_flat(self, pattern, a, b) -> np.ndarray:
        """The same trip with no time of day: each stretch's all-day average."""
        pattern = np.atleast_1d(pattern).astype(np.int64)
        a = np.atleast_1d(a).astype(np.int64)
        b = np.atleast_1d(b).astype(np.int64)
        out = np.empty(len(pattern))
        n = self.net
        _spans_flat(self.runs.flat, self.dwells.flat, n.ps_seg, n.ps_stop, n.ps_run_m, n.ps_off, pattern, a, b, out)
        return out

    def timetable(self, pattern, a, b) -> np.ndarray:
        n = self.net
        lo = n.ps_off[np.atleast_1d(pattern)]
        return (n.ps_sched[lo + np.atleast_1d(b)] - n.ps_sched[lo + np.atleast_1d(a)]).astype(np.float64)

    def distance_m(self, pattern, a, b) -> np.ndarray:
        n = self.net
        lo = n.ps_off[np.atleast_1d(pattern)]
        return n.ps_chain[lo + np.atleast_1d(b)] - n.ps_chain[lo + np.atleast_1d(a)]

    def save(self, directory=MODEL_DIR) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.runs.save(directory / "runs.npz")
        self.dwells.save(directory / "dwells.npz")
        np.save(directory / "calibration.npy", self.calib.q)

    @classmethod
    def load(cls, net: Network | None = None, directory=MODEL_DIR) -> "TravelTimeModel":
        calib = Calibration()
        if (directory / "calibration.npy").exists():
            calib.q = np.load(directory / "calibration.npy")
        return cls(net or Network(), Profile.load(directory / "runs.npz"), Profile.load(directory / "dwells.npz"), calib)


def fit(net: Network, days, half_life_days: float | None = None, calibrate: bool = True) -> TravelTimeModel:
    """Learn the profiles from the given days of October, then calibrate on real trips from the same days.

    half_life_days: an observation this many days older than the newest day counts half as much.
    """
    from .observations import load_dwells, load_runs, load_spans

    days = list(days)

    def weights(day):
        return None if not half_life_days else 0.5 ** ((max(days) - day.astype(np.float64)) / half_life_days)

    r = load_runs(days)
    pace = r["run_s"].astype(np.float64) / r["run_m"]                      # seconds per metre
    runs = Profile(net.n_segments).fit(r["seg"], r["t"], pace, weight=weights(r["day"]))
    d = load_dwells(days)
    dwells = Profile(len(net.stop_id)).fit(d["stop"], d["t"], d["dwell_s"], floor=5.0, weight=weights(d["day"]))
    model = TravelTimeModel(net, runs, dwells)
    if calibrate:
        s = load_spans(days, per_trip=1, seed=11)
        raw = model.predict_raw(s["pattern"], s["a"], s["b"], s["t0"])
        ok = (raw > 0) & (s["actual_s"] > 30)
        model.calib.fit((s["b"] - s["a"])[ok], s["t0"][ok], s["actual_s"][ok] / raw[ok])
    return model
