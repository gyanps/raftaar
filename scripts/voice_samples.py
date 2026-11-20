"""The same short line in several candidate voices, to choose by ear.

    uv run python scripts/voice_samples.py "Rahul S" "Amay - Professional Indian English" ...

Each name is matched against the start of a voice's name in the account, then in the shared library (male, English,
Indian accent). Writes dist/video/vo-test/<n>-<name>.mp3.
"""
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import voiceover as vo  # noqa: E402  (reuses the key loading and the API helper)

TEXT = ("This is Raftaar. Delhi bus travel times, learned from the buses' own GPS. Ask it for a trip: Kashmere Gate to "
        "Nehru Place, leaving at eight thirty on a weekday morning.")
OUT = vo.ROOT / "dist" / "video" / "vo-test"


def find(name: str) -> tuple[str, str]:
    for v in vo.call("GET", "/voices").get("voices", []):
        if v["name"].lower().startswith(name.lower()):
            return v["voice_id"], v["name"]
    hits = vo.call("GET", "/shared-voices?page_size=40&gender=male&language=en&search=" + urllib.parse.quote(name)).get("voices", [])
    hits = [v for v in hits if v["name"].lower().startswith(name.lower())]
    if not hits:
        raise SystemExit(f"no voice starting with {name!r}")
    v = hits[0]
    new = vo.call("POST", f"/voices/add/{v['public_owner_id']}/{v['voice_id']}", {"new_name": v["name"][:60]})
    return new["voice_id"], v["name"]


OUT.mkdir(parents=True, exist_ok=True)
for n, name in enumerate(sys.argv[1:], 1):
    voice_id, full = find(name)
    audio = vo.call("POST", f"/text-to-speech/{voice_id}?output_format=mp3_44100_128",
                    {"text": TEXT, "model_id": "eleven_multilingual_v2",
                     "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True}}, raw=True)
    path = OUT / f"{n}-{re.sub(r'[^A-Za-z0-9]+', '-', full).strip('-')[:40]}.mp3"
    path.write_bytes(audio)
    print(f"{n}. {full}  ->  {path.name}")
