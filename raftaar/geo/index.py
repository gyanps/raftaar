"""A polyline prepared for repeated "how far from the line, and how far along it?" queries.

Segments are grouped in fixed-size chunks with a bounding box each, so a query
scans only the chunks whose box could hold a closer segment than the best
found so far.
"""
from __future__ import annotations

import math
from typing import Sequence

from . import polyline as G

CHUNK = 32


class PolylineIndex:
    def __init__(self, points: Sequence[Sequence[float]]):
        self.points = [(float(p[0]), float(p[1])) for p in points]
        self.cum = G.cumulative(self.points)
        self.chunks: list[tuple[int, int, float, float, float, float]] = []
        self.box: tuple[float, float, float, float] | None = None
        n = len(self.points)
        for start in range(0, n - 1, CHUNK):
            end = min(n - 1, start + CHUNK)
            lats = [p[0] for p in self.points[start : end + 1]]
            lngs = [p[1] for p in self.points[start : end + 1]]
            c = (start, end, min(lats), max(lats), min(lngs), max(lngs))
            self.chunks.append(c)
            self.box = (
                (c[2], c[3], c[4], c[5])
                if self.box is None
                else (min(self.box[0], c[2]), max(self.box[1], c[3]), min(self.box[2], c[4]), max(self.box[3], c[5]))
            )

    @classmethod
    def from_encoded(cls, encoded: str | None) -> "PolylineIndex":
        return cls(G.decode(encoded))

    def is_empty(self) -> bool:
        return len(self.points) < 2

    def length_km(self) -> float:
        return self.cum[-1] if self.cum else 0.0

    @staticmethod
    def _box_km(lat: float, lng: float, c, kx: float, ky: float) -> float:
        d_lat = c[2] - lat if lat < c[2] else (lat - c[3] if lat > c[3] else 0.0)
        d_lng = c[4] - lng if lng < c[4] else (lng - c[5] if lng > c[5] else 0.0)
        return math.sqrt((d_lat * ky) ** 2 + (d_lng * kx) ** 2)

    def locate(self, lat: float, lng: float) -> dict | None:
        """Nearest point of the line: distance_km, chainage_km, segment. None for a line with < 2 points."""
        if self.is_empty():
            return None
        kx = G.KM_PER_DEG * math.cos(math.radians(lat))
        ky = G.KM_PER_DEG
        # Lower bound per chunk = distance to its box; scan nearest boxes first.
        order = sorted((self._box_km(lat, lng, c, kx, ky), i) for i, c in enumerate(self.chunks))
        best = math.inf
        best_seg = 0
        best_t = 0.0
        for lower_bound, ci in order:
            if lower_bound > best * 1.005:  # 0.5% slack: the box bound uses this point's longitude scale
                break
            start, end = self.chunks[ci][0], self.chunks[ci][1]
            for i in range(start, end):
                d, t = G.point_segment(lat, lng, self.points[i], self.points[i + 1])
                if d < best:
                    best, best_seg, best_t = d, i, t
        seg_len = self.cum[best_seg + 1] - self.cum[best_seg]
        return {"distance_km": best, "chainage_km": self.cum[best_seg] + best_t * seg_len, "segment": best_seg}

    def locate_near(self, lat: float, lng: float, hint_km: float | None, radius_km: float) -> dict | None:
        """Like locate(), but where the line passes within radius_km more than once
        (a route that doubles back on the same road) pick the pass whose chainage
        is closest to hint_km, the vehicle's last known progress, instead of
        whichever is a few metres nearer."""
        nearest = self.locate(lat, lng)
        if nearest is None or hint_km is None or nearest["distance_km"] > radius_km:
            return nearest
        kx = G.KM_PER_DEG * math.cos(math.radians(lat))
        ky = G.KM_PER_DEG
        # Every segment within the radius, split into passes (runs of consecutive segments).
        passes: list[dict] = []
        prev = None
        for c in self.chunks:
            if self._box_km(lat, lng, c, kx, ky) > radius_km:
                continue
            for i in range(c[0], c[1]):
                d, t = G.point_segment(lat, lng, self.points[i], self.points[i + 1])
                if d > radius_km:
                    continue
                hit = {"distance_km": d, "chainage_km": self.cum[i] + t * (self.cum[i + 1] - self.cum[i]), "segment": i}
                if prev is None or i != prev + 1:
                    passes.append(hit)
                elif d < passes[-1]["distance_km"]:
                    passes[-1] = hit
                prev = i
        best = nearest
        best_gap = math.inf
        for p in passes:
            gap = abs(p["chainage_km"] - hint_km)
            if gap < best_gap:
                best_gap, best = gap, p
        return best

    def point_at(self, km: float) -> tuple[float, float] | None:
        """The point km along the line (clamped to its ends)."""
        n = len(self.points)
        if n == 0:
            return None
        if n == 1 or km <= 0:
            return self.points[0]
        if km >= self.length_km():
            return self.points[n - 1]
        lo, hi = 0, n - 1
        while hi - lo > 1:  # last vertex with cum <= km
            mid = (lo + hi) // 2
            if self.cum[mid] <= km:
                lo = mid
            else:
                hi = mid
        seg = self.cum[lo + 1] - self.cum[lo]
        t = (km - self.cum[lo]) / seg if seg > 0 else 0.0
        a, b = self.points[lo], self.points[lo + 1]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def slice(self, from_km: float, to_km: float) -> list[tuple[float, float]]:
        """The part of the line between two chainages (ends interpolated); [] when empty or backwards."""
        if self.is_empty():
            return []
        from_km = max(0.0, from_km)
        to_km = min(self.length_km(), to_km)
        if to_km <= from_km:
            return []
        out = [self.point_at(from_km)]
        out += [self.points[i] for i, c in enumerate(self.cum) if from_km < c < to_km]
        out.append(self.point_at(to_km))
        return out

    def within(self, lat: float, lng: float, radius_km: float) -> bool:
        """True when (lat, lng) lies within radius_km of the line."""
        if self.box is None:
            return False
        pad_lat = radius_km / G.KM_PER_DEG
        pad_lng = radius_km / (G.KM_PER_DEG * max(0.2, math.cos(math.radians(lat))))
        if lat < self.box[0] - pad_lat or lat > self.box[1] + pad_lat or lng < self.box[2] - pad_lng or lng > self.box[3] + pad_lng:
            return False
        hit = self.locate(lat, lng)
        return hit is not None and hit["distance_km"] <= radius_km
