"""Find a place by typing part of a bus stop's name."""
from __future__ import annotations

import difflib
import re
from collections import defaultdict

import numpy as np

from ..store import Network

CLUSTER_M = 400.0  # stops with the same name this close together are one place


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()


class Places:
    def __init__(self, net: Network, patterns_at_stop: np.ndarray):
        by_name: dict[str, list[int]] = defaultdict(list)
        for i, name in enumerate(net.stop_name):
            by_name[norm(name)].append(i)
        self.items: list[dict] = []
        for key, idx in by_name.items():
            left = list(idx)
            while left:
                seed = left[0]
                d = np.hypot(net.stop_x[left] - net.stop_x[seed], net.stop_y[left] - net.stop_y[seed])
                group = [s for s, dd in zip(left, d) if dd <= CLUSTER_M]
                left = [s for s in left if s not in group]
                self.items.append({
                    "name": net.stop_name[group[0]].strip(), "key": key, "words": key.split(),
                    "lat": round(float(np.mean(net.stop_lat[group])), 6), "lng": round(float(np.mean(net.stop_lng[group])), 6),
                    "stops": len(group), "routes": int(patterns_at_stop[group].sum()),
                })
        self.vocab = sorted({w for it in self.items for w in it["words"] if len(w) > 2})

    def search(self, q: str, limit: int = 8, _retry: bool = False) -> list[dict]:
        tokens = norm(q).split()
        if not tokens:
            return []
        scored = []
        for it in self.items:
            score = 0.0
            for t in tokens:
                if any(w.startswith(t) for w in it["words"]):
                    score += 2.0
                elif t in it["key"]:
                    score += 1.0
                else:
                    score = -1.0
                    break
            if score > 0:
                if it["key"] == " ".join(tokens):
                    score += 3.0
                scored.append((score + min(it["routes"], 200) / 100.0 - len(it["words"]) * 0.05, it))
        if not scored and not _retry:  # nothing contains the words as typed: fix spelling slips word by word
            fixed = [(difflib.get_close_matches(t, self.vocab, n=1, cutoff=0.75) or [t])[0] for t in tokens]
            if fixed != tokens:
                return self.search(" ".join(fixed), limit, _retry=True)
        scored.sort(key=lambda s: -s[0])
        return [{k: it[k] for k in ("name", "lat", "lng", "stops", "routes")} for _, it in scored[:limit]]
