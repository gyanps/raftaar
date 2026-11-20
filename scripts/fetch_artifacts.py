"""Download the small serving artefacts (about 32 MB) from the public demo, so the app runs without the 16 GB archive.

    uv run python scripts/fetch_artifacts.py        # or: make demo

They are the outputs of 'make gtfs', 'make model', 'make planner' and 'make backtest' on the full October data:
the network, the fitted travel-time model, the planner tables and the report figures.

A stalled or dropped connection is retried, and files already complete are skipped, so it is safe to run again.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "gyanps/raftaar"
ROOT = Path(__file__).resolve().parent.parent
TREE = f"https://huggingface.co/api/spaces/{REPO}/tree/main/artifacts?recursive=true"
FILE = f"https://huggingface.co/spaces/{REPO}/resolve/main/"
TRIES = 5


def fetch(url: str, out: Path | None = None, size: int | None = None) -> bytes:
    """The body of url; with out, streamed to that file instead (and checked against size)."""
    for attempt in range(1, TRIES + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "raftaar-demo"}), timeout=45) as res:
                if out is None:
                    return res.read()
                part = out.with_name(out.name + ".part")
                with open(part, "wb") as fh:
                    while chunk := res.read(1 << 18):
                        fh.write(chunk)
            if size is not None and part.stat().st_size != size:
                raise OSError(f"got {part.stat().st_size} of {size} bytes")
            part.replace(out)
            return b""
        except (OSError, urllib.error.URLError) as exc:          # timeouts and resets are OSErrors
            if attempt == TRIES:
                raise SystemExit(f"Could not download {url} after {TRIES} tries: {exc}. Run 'make demo' again to continue.") from exc
            print(f"  connection problem ({exc}); trying again ({attempt + 1} of {TRIES})")
            time.sleep(2 * attempt)
    return b""


files = [f for f in json.loads(fetch(TREE)) if f["type"] == "file"]
total = 0
for f in files:
    out = ROOT / f["path"]
    if not (out.exists() and out.stat().st_size == f["size"]):
        out.parent.mkdir(parents=True, exist_ok=True)
        fetch(FILE + urllib.parse.quote(f["path"]), out, f["size"])
        print(f"  {f['path']}  {out.stat().st_size / 1e6:.1f} MB")
    total += f["size"]
print(f"{len(files)} files, {total / 1e6:.1f} MB in {ROOT / 'artifacts'}")
