"""Pure polyline maths: no I/O.

A point is (lat, lng). Distances are km. Point-to-segment distance uses a
local equirectangular projection centred on the query point, with longitude
scaled by cos(latitude), so a projected distance and a haversine one agree to
well under 1% for segments up to a few hundred km.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_KM = 6371.0088
# km per degree on the same sphere haversine_km() uses (2*pi*R / 360), for
# latitude and, times cos(lat), longitude. One earth model for both.
KM_PER_DEG = 111.19508

Point = tuple[float, float]


def _round_half_away(v: float) -> int:
    return int(math.floor(abs(v) + 0.5)) * (1 if v >= 0 else -1)


def _encode_value(v: int) -> str:
    v = ~(v << 1) if v < 0 else (v << 1)
    out = []
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))
    return "".join(out)


def encode(points: Iterable[Sequence[float]], precision: int = 5) -> str:
    """Google encoded-polyline format."""
    factor = 10**precision
    out = []
    prev_lat = prev_lng = 0
    for p in points:
        lat = _round_half_away(p[0] * factor)
        lng = _round_half_away(p[1] * factor)
        out.append(_encode_value(lat - prev_lat))
        out.append(_encode_value(lng - prev_lng))
        prev_lat, prev_lng = lat, lng
    return "".join(out)


def decode(encoded: str | None, precision: int = 5) -> list[Point]:
    """Decode; a truncated string keeps whatever decoded cleanly."""
    encoded = encoded or ""
    n = len(encoded)
    factor = 10**precision
    points: list[Point] = []
    index = 0
    lat = lng = 0
    while index < n:
        for axis in (0, 1):
            result = 0
            shift = 0
            while True:
                if index >= n:
                    return points
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lng += delta
        points.append((lat / factor, lng / factor))
    return points


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def cumulative(points: Sequence[Sequence[float]]) -> list[float]:
    """Cumulative km at each vertex (first = 0)."""
    cum: list[float] = []
    total = 0.0
    prev = None
    for p in points:
        if prev is not None:
            total += haversine_km(prev[0], prev[1], p[0], p[1])
        cum.append(total)
        prev = p
    return cum


def length_km(points: Sequence[Sequence[float]]) -> float:
    cum = cumulative(points)
    return cum[-1] if cum else 0.0


def point_segment(p_lat: float, p_lng: float, a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Distance (km) from P to segment AB, and how far along AB (0..1) the nearest point lies."""
    kx = KM_PER_DEG * math.cos(math.radians(p_lat))
    ky = KM_PER_DEG
    ax = (a[1] - p_lng) * kx
    ay = (a[0] - p_lat) * ky
    dx = (b[1] - a[1]) * kx
    dy = (b[0] - a[0]) * ky
    len2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / len2)) if len2 > 0 else 0.0
    cx = ax + t * dx
    cy = ay + t * dy
    return math.sqrt(cx * cx + cy * cy), t


def simplify(points: Sequence[Sequence[float]], tolerance_km: float) -> list:
    """Douglas-Peucker, iterative (no recursion limit). Endpoints always kept."""
    pts = list(points)
    n = len(pts)
    if n < 3 or tolerance_km <= 0:
        return pts
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        max_d = 0.0
        idx = -1
        for i in range(first + 1, last):
            d, _ = point_segment(pts[i][0], pts[i][1], pts[first], pts[last])
            if d > max_d:
                max_d = d
                idx = i
        if idx != -1 and max_d > tolerance_km:
            keep[idx] = True
            stack.append((first, idx))
            stack.append((idx, last))
    return [p for i, p in enumerate(pts) if keep[i]]


def sample(points: Sequence[Sequence[float]], every_km: float) -> list[tuple[float, float, float]]:
    """A point every every_km along the line (first and last included): (lat, lng, chainage km)."""
    pts = list(points)
    n = len(pts)
    if n == 0:
        return []
    if n == 1 or every_km <= 0:
        return [(pts[0][0], pts[0][1], 0.0)]
    cum = cumulative(pts)
    total = cum[n - 1]
    out = []
    seg = 0
    d = 0.0
    while d < total:
        while seg < n - 2 and cum[seg + 1] < d:
            seg += 1
        seg_len = cum[seg + 1] - cum[seg]
        t = (d - cum[seg]) / seg_len if seg_len > 0 else 0.0
        a, b = pts[seg], pts[seg + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, d))
        d += every_km
    out.append((pts[n - 1][0], pts[n - 1][1], total))
    return out


def valid_coord(lat, lng, box: tuple[float, float, float, float] = (28.1, 29.8, 76.5, 77.6)) -> bool:
    """Inside the box (lat_min, lat_max, lng_min, lng_max); 0,0 and swapped lat/lng both fail."""
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return False
    return math.isfinite(lat) and math.isfinite(lng) and box[0] <= lat <= box[1] and box[2] <= lng <= box[3]
