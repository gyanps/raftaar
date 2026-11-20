"""Download the small serving artefacts (about 32 MB) from the public demo, so the app runs without the 16 GB archive.

    uv run python scripts/fetch_artifacts.py        # or: make demo

They are the outputs of 'make gtfs', 'make model', 'make planner' and 'make backtest' on the full October data:
the network, the fitted travel-time model, the planner tables and the report figures.
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "gyanps/raftaar"
ROOT = Path(__file__).resolve().parent.parent
TREE = f"https://huggingface.co/api/spaces/{REPO}/tree/main/artifacts?recursive=true"
FILE = f"https://huggingface.co/spaces/{REPO}/resolve/main/"


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "raftaar-demo"}), timeout=120) as res:
        return res.read()


files = [f for f in json.loads(get(TREE)) if f["type"] == "file"]
total = 0
for f in files:
    out = ROOT / f["path"]
    if out.exists() and out.stat().st_size == f["size"]:
        total += f["size"]
        continue
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(get(FILE + urllib.parse.quote(f["path"])))
    total += out.stat().st_size
    print(f"  {f['path']}  {out.stat().st_size / 1e6:.1f} MB")
print(f"{len(files)} files, {total / 1e6:.1f} MB in {ROOT / 'artifacts'}")
