"""Speak the narration (scripts/narration.json) with ElevenLabs, one clip per scene.

    uv run python scripts/voiceover.py                      # every line, model from narration.json
    uv run python scripts/voiceover.py --model eleven_v4    # another model
    uv run python scripts/voiceover.py s5                   # redo one line

Needs ELEVENLABS_API_KEY in .env. Writes dist/video/vo/<scene>.wav and dist/video/vo/durations.json; the recorder
(scripts/demo_video.mjs) then holds each scene for as long as its line takes, and scripts/mix_video.mjs lays the
clips over the music.

Three things are done for sound quality:
* the best file format the account's plan allows is asked for first (48 kHz WAV, then Opus 192, then MP3 128);
* each call is told which requests came before it (ElevenLabs "request stitching"), so tone and level carry
  from one line to the next as in a single read;
* clips are stored as WAV, so nothing is re-compressed before the final mix.

The voice is looked up by name: first among the account's own voices, then in the shared voice library
(and added to the account from there). Use a voice that is verified for the narration's language and accent.
"""
import argparse
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        k, sep, v = line.strip().partition("=")
        if sep:
            os.environ.setdefault(k, v)
KEY = os.environ.get("ELEVENLABS_API_KEY", "")
API = "https://api.elevenlabs.io/v1"
OUT = ROOT / "dist" / "video" / "vo"
FORMATS = ["wav_48000", "opus_48000_192", "mp3_44100_128"]   # best first; the plan decides which is allowed


class Refused(Exception):
    pass


def call(method: str, path: str, body: dict | None = None, raw: bool = False, soft: bool = False):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"xi-api-key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            data = res.read()
            rid = res.headers.get("request-id")
    except urllib.error.HTTPError as exc:
        text = exc.read()[:400].decode(errors="replace")
        if soft:
            raise Refused(f"{exc.code}: {text}") from exc
        raise SystemExit(f"ElevenLabs answered {exc.code} for {path.split('?')[0]}: {text}") from exc
    return (data, rid) if raw else json.loads(data or b"{}")


def find_voice(name: str) -> str:
    mine = [v for v in call("GET", "/voices").get("voices", []) if v["name"].lower().startswith(name.lower())]
    if mine:
        return mine[0]["voice_id"]
    shared = call("GET", "/shared-voices?page_size=20&search=" + urllib.parse.quote(name)).get("voices", [])
    hits = [v for v in shared if v["name"].lower().startswith(name.lower())]
    if not hits:
        raise SystemExit(f"No voice called {name!r} in the account or the shared library")
    v = hits[0]
    print(f"adding library voice: {v['name']}")
    return call("POST", f"/voices/add/{v['public_owner_id']}/{v['voice_id']}", {"new_name": v["name"][:60]})["voice_id"]


def speak(voice: str, body: dict, formats: list[str]) -> tuple[bytes, str, str | None]:
    """Audio in the first format the plan allows: (bytes, format, request id)."""
    last = ""
    for fmt in list(formats):
        try:
            data, rid = call("POST", f"/text-to-speech/{voice}?output_format={fmt}", body, raw=True, soft=True)
            return data, fmt, rid
        except Refused as exc:
            last = str(exc)
            if len(formats) > 1 and fmt == formats[0]:
                formats.pop(0)          # not on this plan: do not ask again
    raise SystemExit(f"ElevenLabs refused every format; last answer: {last}")


def to_wav(data: bytes, fmt: str, path: Path) -> float:
    src = path.with_suffix("." + fmt.split("_")[0])
    src.write_bytes(data)
    if src != path:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le", str(path)], check=True)
        src.unlink()
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="scene keys to redo (default: all)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None, help="folder for the clips (default dist/video/vo)")
    args = ap.parse_args()
    if not KEY:
        raise SystemExit("ELEVENLABS_API_KEY is not set in .env")
    spec = json.loads((ROOT / "scripts" / "narration.json").read_text())
    model = args.model or spec["model"]
    out = Path(args.out) if args.out else OUT
    voice = find_voice(spec["voice"])
    out.mkdir(parents=True, exist_ok=True)
    keys = list(spec["lines"])
    durations = json.loads((out / "durations.json").read_text()) if (out / "durations.json").exists() else {}
    formats = list(FORMATS)
    request_ids: list[str] = []
    got = None
    for i, key in enumerate(keys):
        if args.only and key not in args.only:
            continue
        body = {"text": spec["lines"][key], "model_id": model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True}}
        if request_ids:
            body["previous_request_ids"] = request_ids[-3:]
        elif i > 0:
            body["previous_text"] = spec["lines"][keys[i - 1]]
        if i + 1 < len(keys):
            body["next_text"] = spec["lines"][keys[i + 1]]
        try:
            audio, got, rid = speak(voice, body, formats)
        except SystemExit:
            if "previous_request_ids" not in body:
                raise
            body.pop("previous_request_ids")                     # stitching not accepted for this model: fall back to text
            body["previous_text"] = spec["lines"][keys[i - 1]]
            request_ids.clear()
            audio, got, rid = speak(voice, body, formats)
            rid = None
        if rid:
            request_ids.append(rid)
        for old in out.glob(key + ".*"):
            old.unlink()
        secs = to_wav(audio, got, out / f"{key}.wav")
        durations[key] = round(secs, 2)
        print(f"{key}: {secs:5.1f} s  {spec['lines'][key][:64]}")
    (out / "durations.json").write_text(json.dumps(durations, indent=1))
    (out / "source.json").write_text(json.dumps({"voice": spec["voice"], "model": model, "format": got, "stitched": bool(request_ids)}, indent=1))
    print(f"total speech {sum(durations.values()):.1f} s | model {model} | file format {got} | stitched {bool(request_ids)}")


if __name__ == "__main__":
    main()
