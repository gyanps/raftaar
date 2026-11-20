"""Background polling of the live feed while the API runs."""
from __future__ import annotations

import threading
import time

from . import feed
from .state import LiveState

# The first buses to complete a run after start-up are the fast ones, so an early average reads "faster than
# usual". Live estimates are held back until a whole 20-minute window (plus margin) has been observed.
WARMUP_S = 1500.0


class LiveService:
    def __init__(self, state: LiveState, key: str | None = None, poll_s: float = 30.0, match_s: float = 60.0):
        self.state, self.key, self.poll_s, self.match_s = state, key, poll_s, match_s
        self.breaker = feed.Breaker()
        self.started = time.time()
        self.last_poll = 0.0
        self.last_ok = 0.0
        self.last_match = 0.0
        self.last_error = ""
        self.polls = 0
        self.buses_in_feed = 0
        self.match_seconds = 0.0
        self._stop = threading.Event()

    def start(self) -> "LiveService":
        threading.Thread(target=self._loop, name="raftaar-live", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def step(self, now: float | None = None) -> None:
        """One poll, and a re-match if it is due. Never raises."""
        now = time.time() if now is None else now
        self.last_poll = now
        if self.breaker.allow(now):
            try:
                batch = feed.parse(feed.fetch(self.key))
                self.buses_in_feed = len(batch["vehicle"])
                self.state.ingest(batch)
                self.polls += 1
                self.last_ok = now
                self.last_error = ""
                self.breaker.ok()
            except Exception as exc:  # the feed being down must never take the planner down
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                self.breaker.fail(now)
        if now - self.last_match >= self.match_s:
            t = time.time()
            self.state.rematch(now)
            self.match_seconds = round(time.time() - t, 2)
            self.last_match = now

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            self.step(started)
            self._stop.wait(max(1.0, self.poll_s - (time.time() - started)))

    def usable(self, now: float | None = None) -> bool:
        """True when the last 20 minutes hold enough fresh runs to adjust estimates."""
        now = time.time() if now is None else now
        return bool(self.state.summary["healthy"]) and now - self.last_ok <= 180 and now - self.started >= WARMUP_S

    def status(self) -> dict:
        now = time.time()
        return {
            "source": "Transport Stack GTFS-Realtime vehicle positions",
            "usable": self.usable(now), "running_for_min": round((now - self.started) / 60, 1),
            "warm_up_min_left": max(0.0, round((WARMUP_S - (now - self.started)) / 60, 1)),
            "polls": self.polls, "seconds_since_last_good_poll": round(now - self.last_ok) if self.last_ok else None,
            "buses_in_feed": self.buses_in_feed, "buses_heard_in_last_5_min": sum(1 for f in self.state.last.values() if now - f[0] <= 300),
            "last_error": self.last_error or None, "feed_paused_until": self.breaker.open_until if self.breaker.open_until > now else None,
            "last_match_took_s": self.match_seconds, **self.state.summary,
        }
