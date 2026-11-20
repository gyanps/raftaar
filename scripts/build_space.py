"""Assemble what the hosted demo needs into dist/space/ (code, map screen, and only the small serving artefacts).

    uv run python scripts/build_space.py

The raw GPS, the reduced fixes and the stop events stay out: the server needs the network, the fitted model,
the planner tables and the report figures, about 35 MB in all.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "space"
SPACE_README = """---
title: Raftaar
emoji: 🚌
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Delhi bus travel times from the buses' own GPS
---

# Raftaar

Delhi bus travel times learned from one month of bus GPS, with a journey planner on top.
Transport Stack Hackathon 2026, Problem 1. Open the app above; the API reference is at `/docs`.
"""

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
for name in ("pyproject.toml", "uv.lock", "Dockerfile", "LICENSE"):
    shutil.copy(ROOT / name, OUT / name)
shutil.copytree(ROOT / "raftaar", OUT / "raftaar", ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(ROOT / "web", OUT / "web")
art = OUT / "artifacts"
shutil.copytree(ROOT / "artifacts" / "gtfs", art / "gtfs")
shutil.copytree(ROOT / "artifacts" / "model", art / "model")
for name in ("planner.npz", "planner_summary.json", "backtest.json", "match_stats.json", "reduce_stats.json"):
    shutil.copy(ROOT / "artifacts" / name, art / name)
(OUT / "README.md").write_text(SPACE_README)
size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
print(f"dist/space ready: {sum(1 for p in OUT.rglob('*') if p.is_file())} files, {size / 1e6:.1f} MB")
