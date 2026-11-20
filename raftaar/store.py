"""The static network as flat numpy arrays (built by pipeline/gtfs.py)."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from .config import GTFS_ART

ZONE_M = 30.0  # a bus is "at" a stop within this distance along the route (less where stops are close)


class Network:
    def __init__(self, art: Path = GTFS_ART):
        z = np.load(art / "shapes.npz")
        self.sh_off = z["offsets"].astype(np.int64)
        self.sh_x, self.sh_y, self.sh_cum = z["x"], z["y"], z["cum"]
        self.sh_lat, self.sh_lng = z["lat"], z["lng"]
        n_shapes = len(self.sh_off) - 1
        self.sh_box = np.empty((n_shapes, 4))
        for s in range(n_shapes):
            a, b = self.sh_off[s], self.sh_off[s + 1]
            if b > a:
                self.sh_box[s] = (self.sh_x[a:b].min(), self.sh_x[a:b].max(), self.sh_y[a:b].min(), self.sh_y[a:b].max())
            else:
                self.sh_box[s] = (np.inf, -np.inf, np.inf, -np.inf)

        p = pq.read_table(art / "patterns.parquet").to_pydict()
        self.n_patterns = len(p["pattern_idx"])
        self.pat_route = np.array(p["route_id"], dtype=np.int32)
        self.pat_shape = np.array(p["shape_idx"], dtype=np.int32)
        self.pat_monotone = np.array(p["monotone"], dtype=bool)
        self.pat_sibling = np.array(p["sibling_route_id"], dtype=np.int32)
        self.pat_trips = np.array(p["n_trips"], dtype=np.int32)
        self.pat_name = p["route_name"]
        self.pat_desc = p["route_desc"]
        self.route_patterns: dict[int, list[int]] = defaultdict(list)
        for i, r in enumerate(self.pat_route):
            self.route_patterns[int(r)].append(i)
        self.route_sibling = {int(r): int(s) for r, s in zip(self.pat_route, self.pat_sibling)}

        ps = pq.read_table(art / "pattern_stops.parquet").sort_by([("pattern_idx", "ascending"), ("seq", "ascending")])
        pidx = ps["pattern_idx"].to_numpy()
        self.ps_off = np.r_[np.flatnonzero(np.r_[True, pidx[1:] != pidx[:-1]]), len(pidx)].astype(np.int64)
        self.ps_stop = ps["stop_idx"].to_numpy().astype(np.int32)
        self.ps_chain = ps["chainage_m"].to_numpy().astype(np.float64)
        self.ps_sched = ps["sched_s"].to_numpy().astype(np.int32)
        self.ps_seg = ps["seg_idx"].to_numpy().astype(np.int32)
        # Stop zones: half the gap to the neighbour where stops are closer than 2 x ZONE_M.
        gap = np.diff(self.ps_chain)
        first = np.zeros(len(pidx), dtype=bool)
        first[self.ps_off[:-1]] = True
        last = np.zeros(len(pidx), dtype=bool)
        last[self.ps_off[1:] - 1] = True
        before = np.r_[np.inf, gap]
        before[first] = np.inf
        after = np.r_[gap, np.inf]
        after[last] = np.inf
        self.ps_zb = np.minimum(ZONE_M, np.maximum(before, 0.0) / 2)
        self.ps_za = np.minimum(ZONE_M, np.maximum(after, 0.0) / 2)
        # Metres of open road from the end of this stop's zone to the start of the next one (0 on a pattern's last stop).
        run = np.r_[(self.ps_chain[1:] - self.ps_zb[1:]) - (self.ps_chain[:-1] + self.ps_za[:-1]), 0.0]
        run[last] = 0.0
        self.ps_run_m = np.maximum(run, 0.0)
        self.ps_first = first
        self.ps_last = last

        s = pq.read_table(art / "stops.parquet").to_pydict()
        self.stop_id = s["stop_id"]
        self.stop_name = s["stop_name"]
        self.stop_lat = np.array(s["lat"])
        self.stop_lng = np.array(s["lng"])
        self.stop_x = np.array(s["x"])
        self.stop_y = np.array(s["y"])

        g = pq.read_table(art / "segments.parquet").to_pydict()
        self.seg_from = np.array(g["from_stop_idx"], dtype=np.int32)
        self.seg_to = np.array(g["to_stop_idx"], dtype=np.int32)
        self.seg_len = np.array(g["length_m"], dtype=np.float64)
        self.n_segments = len(self.seg_len)

    def shape(self, pattern: int):
        s = self.pat_shape[pattern]
        a, b = self.sh_off[s], self.sh_off[s + 1]
        return self.sh_x[a:b], self.sh_y[a:b], self.sh_cum[a:b], self.sh_box[s]

    def stops(self, pattern: int) -> slice:
        return slice(self.ps_off[pattern], self.ps_off[pattern + 1])
