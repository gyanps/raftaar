# Raftaar: from the raw October 2026 archive to a running journey planner.
#   make all      everything below, in order (about 15 minutes on a laptop, about 12 GB of free disk)
#   make demo     skip the data: download the fitted model (32 MB) instead, then 'make serve'
#   make serve    the API and the map at http://localhost:8000
#   make predict CASES=cases.csv    score a file of test trips -> cases.predictions.csv
PORT ?= 8000
WORKERS ?= 6
CASES ?= validation/sample_cases.csv

.PHONY: all demo setup gtfs reduce match model planner backtest predict test serve shots

all: setup gtfs reduce match model planner backtest

demo: setup       ## no raw data needed: fetch the fitted model (32 MB) from the public demo, then run 'make serve'
	uv run python scripts/fetch_artifacts.py

setup:
	uv sync

gtfs:            ## static GTFS -> patterns, stop positions along each route, stop-to-stop stretches
	uv run python -m raftaar.pipeline.gtfs

reduce:          ## each day's 31M raw rows -> ~18M real fixes in Parquet (unpacks one day at a time)
	uv run python -m raftaar.pipeline.reduce --all --delete-extracted

match:           ## fixes -> bus trips on route patterns -> arrival and departure at every stop
	uv run python -m raftaar.pipeline.match --all --workers $(WORKERS)

model:           ## time-of-day travel-time profiles from every matched day
	uv run python -m raftaar.model.fit

planner:         ## observed bus frequencies, walking links
	uv run python -m raftaar.planner.build

backtest:        ## train on 1-24 Oct, test on 25-31 Oct -> artifacts/backtest.md
	uv run python -m raftaar.validation.backtest --train 1-24 --test 25-31

predict:         ## score a file of test trips (CSV or JSON): make predict CASES=cases.csv -> cases.predictions.csv
	uv run python -m raftaar.validation.predict $(CASES)

test:
	uv run pytest -q

serve:
	uv run uvicorn raftaar.api.main:app --host 0.0.0.0 --port $(PORT)

shots:           ## screenshots of the running app into docs/img (needs Chrome and 'make serve' in another terminal)
	node scripts/shots.mjs docs/img/plan.png "http://localhost:$(PORT)/?from=ISBT Kashmere Gate&to=Nehru Place Terminal&time=18:30&day=weekday" --wait "document.querySelector('.other')" --settle 3500
	node scripts/shots.mjs docs/img/speeds.png "http://localhost:$(PORT)/?tab=traffic&time=18:30" --wait "__map.getSource('seg') && __map.querySourceFeatures('seg').length > 100" --settle 3500
	node scripts/shots.mjs docs/img/accuracy.png "http://localhost:$(PORT)/?tab=accuracy" --wait "document.getElementById('accuracy').dataset.done" --settle 1500
