# The API and the map, with the small serving artefacts baked in (about 35 MB). Built remotely by Hugging Face Spaces.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv && useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
ENV UV_LINK_MODE=copy NUMBA_CACHE_DIR=/tmp/numba PYTHONUNBUFFERED=1

COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY --chown=user raftaar ./raftaar
COPY --chown=user web ./web
COPY --chown=user artifacts ./artifacts

EXPOSE 7860
CMD [".venv/bin/uvicorn", "raftaar.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
