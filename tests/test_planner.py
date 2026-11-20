"""The planner on the real network (skipped when the artefacts have not been built)."""
import numpy as np
import pytest

from raftaar.config import ART

pytestmark = pytest.mark.skipif(not (ART / "planner.npz").exists() or not (ART / "model" / "runs.npz").exists(),
                                reason="run 'make all' first")


@pytest.fixture(scope="module")
def planner():
    from raftaar.model.traveltime import TravelTimeModel
    from raftaar.planner.journeys import Planner
    from raftaar.store import Network

    net = Network()
    return Planner(net, TravelTimeModel.load(net))


def test_random_trips_never_crash_and_add_up(planner):
    from raftaar.calendar import epoch_ist

    net = planner.net
    rng = np.random.default_rng(3)
    found = 0
    for _ in range(60):
        a, b = rng.integers(0, len(net.stop_lat), 2)
        res = planner.plan((net.stop_lat[a], net.stop_lng[a]), (net.stop_lat[b], net.stop_lng[b]),
                           epoch_ist(2026, 11, 17, int(rng.integers(5, 23)), int(rng.integers(0, 60))))
        for o in res["options"]:
            found += 1
            parts = o["walk_min"] + o["wait_min"] + o["ride_min"]
            assert parts == pytest.approx(o["duration_min"], abs=0.6)            # the three parts are the whole trip
            assert o["range_min"][0] <= o["duration_min"] <= o["range_min"][1]
            assert o["legs"][0]["mode"] == "walk" and o["legs"][-1]["mode"] == "walk"
            for leg in o["legs"]:
                if leg["mode"] == "bus":
                    assert leg["lines"] and leg["ride_min"] > 0 and leg["wait_min"] > 0
                    assert leg["ride_quiet_min"] <= leg["ride_min"] + 0.6          # the quietest half-hour is never slower
    assert found > 40


def test_evening_peak_is_slower_than_late_night_on_the_briefs_example(planner):
    from raftaar.calendar import epoch_ist

    kg, npl = (28.6694, 77.2271), (28.5484, 77.2563)       # ISBT Kashmere Gate, Nehru Place Terminal
    peak = planner.plan(kg, npl, epoch_ist(2026, 11, 17, 18, 30))["options"][0]
    night = planner.plan(kg, npl, epoch_ist(2026, 11, 17, 23, 0))["options"][0]
    assert peak["ride_min"] > night["ride_min"] * 1.3
    assert peak["traffic_delay_min"] > 10


def test_a_slower_city_makes_every_ride_longer(planner):
    from raftaar.calendar import epoch_ist
    from raftaar.model.live import HORIZON_EDGES_S

    kg, npl = (28.6694, 77.2271), (28.5484, 77.2563)
    when = epoch_ist(2026, 11, 17, 10, 0)
    usual = planner.plan(kg, npl, when)["options"][0]
    slow = np.full((5, planner.net.n_segments), 1.25, dtype=np.float32)        # everything 25% slower right now
    live = planner.plan(kg, npl, when, live=(slow, HORIZON_EDGES_S))
    assert live["mode"] == "live"
    assert live["options"][0]["ride_min"] > usual["ride_min"] * 1.1
