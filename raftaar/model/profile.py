"""A time-of-day profile for many keys at once (road segments, or stops).

For each key the profile holds one expected value per day type and half-hour.
Three ideas keep it honest where data is thin:

* The city has a common daily rhythm. A key that was seen only at 10 am still
  gets a sensible 6 pm figure: its own level times the city's 6 pm factor.
* A cell borrows from the half-hours either side (weights 1/4, 1/2, 1/4).
* A cell with n observations is pulled toward that borrowed figure with
  weight K / (n + K): with 2 observations it mostly trusts the city rhythm,
  with 50 it mostly trusts itself.

Values are winsorised against the key's own median first, so one bus that
broke down does not move the mean. Means (not medians) are stored because
means add up along a route.
"""
from __future__ import annotations

import numpy as np

from ..calendar import BIN_S, N_BINS, N_DAYTYPES, daytype, second_of_day

K_CELL = 8.0
K_KEY = 8.0
CLIP_LO, CLIP_HI = 0.4, 3.0


def _key_medians(key: np.ndarray, value: np.ndarray, n_keys: int) -> np.ndarray:
    order = np.lexsort((value, key))
    k_sorted = key[order]
    starts = np.searchsorted(k_sorted, np.arange(n_keys), side="left")
    ends = np.searchsorted(k_sorted, np.arange(n_keys), side="right")
    med = np.full(n_keys, np.nan)
    has = ends > starts
    med[has] = value[order][(starts[has] + ends[has] - 1) // 2]
    return med


class Profile:
    def __init__(self, n_keys: int):
        self.n_keys = n_keys
        self.est = np.zeros((n_keys, N_DAYTYPES, N_BINS), dtype=np.float32)
        self.n_cell = np.zeros((n_keys, N_DAYTYPES, N_BINS), dtype=np.float32)
        self.n_key = np.zeros(n_keys, dtype=np.int64)
        self.level = np.zeros(n_keys)
        self.rhythm = np.ones((N_DAYTYPES, N_BINS))
        self.flat = np.zeros(n_keys, dtype=np.float32)   # the same key with no time of day (a baseline)

    def fit(self, key: np.ndarray, t: np.ndarray, value: np.ndarray, prior_level: np.ndarray | float | None = None,
            floor: float = 0.0, weight: np.ndarray | None = None) -> "Profile":
        """weight: one per observation (recent days can count for more); counts below are sums of weights."""
        key = key.astype(np.int64)
        value = value.astype(np.float64)
        w = np.ones(len(key)) if weight is None else weight.astype(np.float64)
        dtp = daytype(t).astype(np.int64)
        b = (second_of_day(t) // BIN_S).astype(np.int64)
        med = _key_medians(key, value, self.n_keys)
        m = np.maximum(med[key], floor)
        value = np.clip(value, CLIP_LO * m, np.maximum(CLIP_HI * m, floor))

        self.n_key = np.bincount(key, minlength=self.n_keys)
        w_key = np.bincount(key, weights=w, minlength=self.n_keys)
        mean_key = np.bincount(key, weights=w * value, minlength=self.n_keys) / np.maximum(w_key, 1e-9)
        overall = np.average(value, weights=w)

        # City rhythm: how each half-hour compares with the key's own average, pooled over keys.
        cell = dtp * N_BINS + b
        rel = value / np.maximum(mean_key[key], 1e-9)
        w_cell = np.bincount(cell, weights=w, minlength=N_DAYTYPES * N_BINS)
        rhythm = np.bincount(cell, weights=w * rel, minlength=N_DAYTYPES * N_BINS) / np.maximum(w_cell, 1e-9)
        rhythm[rhythm == 0] = 1.0
        self.rhythm = rhythm.reshape(N_DAYTYPES, N_BINS)

        # Key level with the rhythm taken out, pulled toward the prior where the key is rare.
        if prior_level is None:
            prior_level = overall / np.average(rhythm, weights=w_cell)
        prior = np.broadcast_to(np.asarray(prior_level, dtype=np.float64), (self.n_keys,))
        level_sum = np.bincount(key, weights=w * value / rhythm[cell], minlength=self.n_keys)
        self.level = (level_sum + K_KEY * prior) / (w_key + K_KEY)
        self.flat = ((np.bincount(key, weights=w * value, minlength=self.n_keys) + K_KEY * prior) / (w_key + K_KEY)).astype(np.float32)

        # Cells: borrow from the neighbouring half-hours, then shrink toward level x rhythm.
        flat_idx = key * (N_DAYTYPES * N_BINS) + cell
        size = self.n_keys * N_DAYTYPES * N_BINS
        s = np.bincount(flat_idx, weights=w * value, minlength=size).reshape(self.n_keys, N_DAYTYPES, N_BINS)
        n = np.bincount(flat_idx, weights=w, minlength=size).reshape(self.n_keys, N_DAYTYPES, N_BINS)
        s_smooth = 0.5 * s + 0.25 * np.roll(s, 1, axis=2) + 0.25 * np.roll(s, -1, axis=2)
        n_smooth = 0.5 * n + 0.25 * np.roll(n, 1, axis=2) + 0.25 * np.roll(n, -1, axis=2)
        prior_cell = self.level[:, None, None] * self.rhythm[None, :, :]
        self.est = ((s_smooth + K_CELL * prior_cell) / (n_smooth + K_CELL)).astype(np.float32)
        self.n_cell = n_smooth.astype(np.float32)
        return self

    def save(self, path) -> None:
        np.savez_compressed(path, est=self.est, n_cell=self.n_cell, n_key=self.n_key, level=self.level, rhythm=self.rhythm,
                            flat=self.flat)

    @classmethod
    def load(cls, path) -> "Profile":
        z = np.load(path)
        p = cls(len(z["n_key"]))
        p.est, p.n_cell, p.n_key, p.level, p.rhythm, p.flat = z["est"], z["n_cell"], z["n_key"], z["level"], z["rhythm"], z["flat"]
        return p
