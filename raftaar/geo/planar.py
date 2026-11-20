"""A flat local grid for Delhi, in metres.

Everything bulk (31M fixes a day against 3,500 shapes) runs on plane
coordinates: x east, y north, origin at Connaught Place. The longitude scale
is fixed at the origin's latitude, which is off by at most 0.4% at the edge of
the NCR box. That is far below GPS noise and keeps every distance a plain
Pythagoras.
"""
from __future__ import annotations

import math

import numpy as np

from ..config import LAT0, LNG0

M_PER_DEG = 111195.08
KX = M_PER_DEG * math.cos(math.radians(LAT0))


def to_xy(lat, lng):
    lat = np.asarray(lat, dtype=np.float64)
    lng = np.asarray(lng, dtype=np.float64)
    return (lng - LNG0) * KX, (lat - LAT0) * M_PER_DEG


def to_latlng(x, y):
    return LAT0 + np.asarray(y, dtype=np.float64) / M_PER_DEG, LNG0 + np.asarray(x, dtype=np.float64) / KX
