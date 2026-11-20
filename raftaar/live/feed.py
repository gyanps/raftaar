"""The live GTFS-Realtime vehicle-positions feed of Delhi buses (Transport Stack).

The key comes from TRANSPORT_STACK_API_KEY in .env. Each poll returns the latest position of every bus.
"""
from __future__ import annotations

import os
import time
import urllib.request

import numpy as np

FEED_URL = ("https://dts-backend.transportstack.in/api/dataset/otd/get-file"
            "?agency=delhi-buses&category=realtime_gtfs&filename=VehiclePositions.pb")
KEY_ENV = "TRANSPORT_STACK_API_KEY"


class Breaker:
    """Stop calling a feed that keeps failing: 3 failures inside 3 minutes open it for 10 minutes."""

    def __init__(self, failures: int = 3, within_s: float = 180.0, open_s: float = 600.0):
        self.failures, self.within_s, self.open_s = failures, within_s, open_s
        self.recent: list[float] = []
        self.open_until = 0.0

    def allow(self, now: float) -> bool:
        return now >= self.open_until

    def ok(self) -> None:
        self.recent.clear()

    def fail(self, now: float) -> None:
        self.recent = [t for t in self.recent if now - t <= self.within_s] + [now]
        if len(self.recent) >= self.failures:
            self.open_until = now + self.open_s
            self.recent.clear()


def fetch(key: str | None = None, timeout: float = 20.0) -> bytes:
    key = key or os.environ.get(KEY_ENV, "")
    if not key:
        raise RuntimeError(f"{KEY_ENV} is not set")
    req = urllib.request.Request(FEED_URL, headers={"x-api-key": key, "User-Agent": "raftaar/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def parse(raw: bytes) -> dict:
    """Feed bytes -> arrays: vehicle (list of ids), ts, lat, lng, route (-1 when missing)."""
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)
    vehicle, ts, lat, lng, route = [], [], [], [], []
    for e in feed.entity:
        v = e.vehicle
        vid = v.vehicle.id or v.vehicle.label
        if not vid or not v.timestamp:
            continue
        vehicle.append(vid)
        ts.append(int(v.timestamp))
        lat.append(v.position.latitude)
        lng.append(v.position.longitude)
        route.append(int(v.trip.route_id) if v.trip.route_id.isdigit() else -1)
    return {"vehicle": vehicle, "ts": np.array(ts, dtype=np.int64), "lat": np.array(lat), "lng": np.array(lng),
            "route": np.array(route, dtype=np.int32), "header_ts": int(feed.header.timestamp or time.time())}
