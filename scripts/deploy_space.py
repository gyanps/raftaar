"""Push dist/space/ to a Hugging Face Docker Space. HF_TOKEN comes from the environment or .env.

    uv run python scripts/build_space.py
    uv run --with huggingface_hub python scripts/deploy_space.py [--public]

The Space is created private unless --public is given; an existing Space keeps its visibility.
"""
import os
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        k, sep, v = line.strip().partition("=")
        if sep:
            os.environ.setdefault(k, v)

api = HfApi(token=os.environ["HF_TOKEN"])
user = api.whoami()["name"]
repo = f"{user}/raftaar"
api.create_repo(repo, repo_type="space", space_sdk="docker", private="--public" not in sys.argv, exist_ok=True)
api.upload_folder(folder_path=str(ROOT / "dist" / "space"), repo_id=repo, repo_type="space",
                  commit_message="Raftaar: API, map screen and serving artefacts", delete_patterns=["*"])
print("pushed to https://huggingface.co/spaces/" + repo)
