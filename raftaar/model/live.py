"""Blend what buses did in the last few minutes into the estimate.

The profile says what a stretch usually takes at this time on this kind of day.
Live data says how today differs. For every run seen recently,

    surprise = log(observed seconds per metre / profile's seconds per metre)

and the state of the network at a moment T is the average surprise over the
last WINDOW_S seconds: one number for the whole city, and one per stretch
(pulled toward the city's when the stretch had few buses). A stretch nobody
drove recently simply carries the city's number: that is what happens when
there is little or no live data on a road, and with no live data at all both
are zero and the estimate is the plain profile.

A surprise fades. How much of it still holds h minutes later is learned from
history: for each look-ahead band the two weights (city, stretch-specific)
are fitted by least squares on training days, by asking how well the state at
T predicted the surprise of runs that started h later.

    estimate(stretch, T + h) = profile x exp(w_city[h] x city(T) + w_own[h] x own(T))

The same code scores the blend on past days (replaying a day, using only what
was known before each trip left) and runs on a live feed.
"""
from __future__ import annotations

import numpy as np
from numba import njit

from ..calendar import BIN_S, IST_OFFSET_S, N_BINS, daytype
from .observations import load_runs

BUCKET_S = 300
N_BUCKETS = 86400 // BUCKET_S
WINDOW_BUCKETS = 4               # the last 20 minutes
KAPPA = 2.0                      # a stretch's own average counts fully once it has many more runs than this
CLIP = 1.2                       # a single run cannot say "more than 3.3x slower or faster than usual"
HORIZON_EDGES_S = np.array([600.0, 1200.0, 2400.0, 3600.0])   # look-ahead bands: 0-10, 10-20, 20-40, 40-60, 60+ min
HORIZON_MID_S = np.array([300.0, 900.0, 1800.0, 3000.0, 4500.0])


@njit(cache=True)
def _lookup_many(est, key, dtp, sod, out):
    for i in range(len(key)):
        pos = sod[i] / BIN_S - 0.5
        b0 = int(np.floor(pos))
        w = pos - b0
        b1 = b0 + 1
        if b0 < 0:
            b0 += N_BINS
        if b1 >= N_BINS:
            b1 -= N_BINS
        out[i] = est[key[i], dtp[i], b0] * (1.0 - w) + est[key[i], dtp[i], b1] * w
    return 0


def surprises(model, runs: dict, centre: np.ndarray | None = None) -> np.ndarray:
    """log(observed / profile) for each run, clipped, minus the stretch's usual value of that log.

    The profile stores means, and the log of a typical run sits below the log of the mean (run times are
    skewed), by an amount that differs from stretch to stretch. Without the centre, an ordinary day would
    read as "faster than usual" everywhere.
    """
    sod = np.mod(runs["t"] + IST_OFFSET_S, 86400.0)
    prof = np.empty(len(sod))
    seg = runs["seg"].astype(np.int64)
    _lookup_many(model.runs.est, seg, daytype(runs["t"]).astype(np.int64), sod, prof)
    rho = np.log(runs["run_s"].astype(np.float64) / runs["run_m"] / prof)
    if centre is not None:
        rho -= centre[seg]
    return np.clip(rho, -CLIP, CLIP)


def fit_centre(model, days, k: float = 8.0) -> np.ndarray:
    """Each stretch's usual log(observed / profile), pulled toward the city's where the stretch is rare."""
    r = load_runs(days)
    rho = surprises(model, r)
    seg = r["seg"].astype(np.int64)
    n = np.bincount(seg, minlength=model.net.n_segments)
    s = np.bincount(seg, weights=rho, minlength=model.net.n_segments)
    return (s + k * rho.mean()) / (n + k)


class DayState:
    """Running totals of surprise for one day, so the state at any 5-minute mark is two subtractions."""

    def __init__(self, n_seg: int, seg: np.ndarray, t_known: np.ndarray, rho: np.ndarray):
        bucket = np.minimum((np.mod(t_known + IST_OFFSET_S, 86400.0) // BUCKET_S).astype(np.int64), N_BUCKETS - 1)
        flat = seg.astype(np.int64) * N_BUCKETS + bucket
        s = np.bincount(flat, weights=rho, minlength=n_seg * N_BUCKETS).reshape(n_seg, N_BUCKETS)
        n = np.bincount(flat, minlength=n_seg * N_BUCKETS).reshape(n_seg, N_BUCKETS)
        self.cs = np.zeros((n_seg, N_BUCKETS + 1), dtype=np.float32)
        self.cn = np.zeros((n_seg, N_BUCKETS + 1), dtype=np.float32)
        self.cs[:, 1:] = np.cumsum(s, axis=1)
        self.cn[:, 1:] = np.cumsum(n, axis=1)
        self.city_s = self.cs.sum(axis=0, dtype=np.float64)
        self.city_n = self.cn.sum(axis=0, dtype=np.float64)

    def at(self, seg: np.ndarray, t: np.ndarray):
        """(city surprise, stretch-specific surprise) known just before each time t, for the given stretches."""
        b = np.clip((np.mod(t + IST_OFFSET_S, 86400.0) // BUCKET_S).astype(np.int64), 0, N_BUCKETS)   # whole buckets before t
        a = np.maximum(b - WINDOW_BUCKETS, 0)
        city_n = self.city_n[b] - self.city_n[a]
        city = (self.city_s[b] - self.city_s[a]) / np.maximum(city_n, 1.0)
        n = self.cn[seg, b] - self.cn[seg, a]
        own_mean = (self.cs[seg, b] - self.cs[seg, a]) / np.maximum(n, 1.0)
        own = np.where(n > 0, (own_mean - city) * n / (n + KAPPA), 0.0)
        return city, own


def day_state(model, day: int, centre: np.ndarray) -> DayState:
    r = load_runs([day])
    return DayState(model.net.n_segments, r["seg"], r["t"] + r["run_s"], surprises(model, r, centre))


def fit_weights(model, days, centre: np.ndarray, sample: int = 400_000, seed: int = 5) -> np.ndarray:
    """[look-ahead band, 2] weights (city, own) by least squares on the given days."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((len(HORIZON_MID_S), 5))      # sums of cc, cd, dd, c*rho, d*rho
    for d in days:
        r = load_runs([d])
        rho = surprises(model, r, centre)
        state = DayState(model.net.n_segments, r["seg"], r["t"] + r["run_s"], rho)
        pick = rng.choice(len(rho), min(sample, len(rho)), replace=False)
        seg, t, y = r["seg"][pick].astype(np.int64), r["t"][pick], rho[pick]
        for h, ahead in enumerate(HORIZON_MID_S):
            c, own = state.at(seg, t - ahead)
            acc[h] += [np.sum(c * c), np.sum(c * own), np.sum(own * own), np.sum(c * y), np.sum(own * y)]
    w = np.zeros((len(HORIZON_MID_S), 2))
    for h, (cc, cd, dd, cy, dy) in enumerate(acc):
        w[h] = np.linalg.solve(np.array([[cc, cd], [cd, dd]]) + 1e-9 * np.eye(2), np.array([cy, dy]))
    return np.clip(w, 0.0, 1.5)


@njit(cache=True)
def _lookup(est, key, dtp, t):
    pos = ((t + IST_OFFSET_S) % 86400.0) / BIN_S - 0.5
    b0 = int(np.floor(pos))
    w = pos - b0
    b1 = b0 + 1
    if b0 < 0:
        b0 += N_BINS
    if b1 >= N_BINS:
        b1 -= N_BINS
    return est[key, dtp, b0] * (1.0 - w) + est[key, dtp, b1] * w


@njit(cache=True)
def _spans_live(run_est, dwell_est, ps_seg, ps_stop, ps_run_m, ps_off, pattern, a, b, t0, dtp, cs, cn, city_s, city_n, weights, edges, out):
    """Like the plain span sum, but each stretch is scaled by what was known at departure, faded by how far ahead it lies."""
    for i in range(len(pattern)):
        lo = ps_off[pattern[i]]
        bk = int(((t0[i] + IST_OFFSET_S) % 86400.0) // BUCKET_S)
        ak = max(bk - WINDOW_BUCKETS, 0)
        cnn = city_n[bk] - city_n[ak]
        city = (city_s[bk] - city_s[ak]) / max(cnn, 1.0)
        t = t0[i]
        for k in range(a[i], b[i]):
            row = lo + k
            seg = ps_seg[row]
            if seg >= 0 and ps_run_m[row] > 0.0:
                h = 0
                while h < len(edges) and t - t0[i] >= edges[h]:
                    h += 1
                n = cn[seg, bk] - cn[seg, ak]
                own = 0.0
                if n > 0:
                    own = ((cs[seg, bk] - cs[seg, ak]) / n - city) * n / (n + KAPPA)
                t += _lookup(run_est, seg, dtp[i], t) * ps_run_m[row] * np.exp(weights[h, 0] * city + weights[h, 1] * own)
            if k + 1 < b[i]:
                t += _lookup(dwell_est, ps_stop[row + 1], dtp[i], t)
        out[i] = t - t0[i]
    return 0


def predict_live(model, weights: np.ndarray, state: DayState, pattern, a, b, t0) -> np.ndarray:
    """Raw seconds (before calibration) for trips leaving at t0, knowing the network state just before t0."""
    n = model.net
    out = np.empty(len(pattern))
    _spans_live(model.runs.est, model.dwells.est, n.ps_seg, n.ps_stop, n.ps_run_m, n.ps_off,
                np.asarray(pattern, dtype=np.int64), np.asarray(a, dtype=np.int64), np.asarray(b, dtype=np.int64),
                np.asarray(t0, dtype=np.float64), daytype(t0).astype(np.int64),
                state.cs, state.cn, state.city_s, state.city_n, weights, HORIZON_EDGES_S, out)
    return out
