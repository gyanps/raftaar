"""Live multipliers: slower-than-usual runs must slow the estimate, and no data must change nothing."""
import numpy as np

from raftaar.live.feed import Breaker
from raftaar.live.state import factors_from_runs

WEIGHTS = np.array([[0.94, 0.63], [0.92, 0.54], [0.87, 0.46], [0.84, 0.40], [0.80, 0.34]])


def test_no_live_data_leaves_every_stretch_alone():
    f, city = factors_from_runs(np.empty(0, dtype=np.int64), np.empty(0), 10, WEIGHTS)
    assert city == 0.0
    assert np.all(f == 1.0)


def test_a_slow_city_slows_every_stretch_and_fades_with_look_ahead():
    seg = np.array([0, 0, 1, 1, 2, 2])
    rho = np.full(6, np.log(1.2))                         # everything seen is 20% slower than usual
    f, city = factors_from_runs(seg, rho, 5, WEIGHTS)
    assert city > 0
    assert np.all(f > 1.0)                                # including stretches 3 and 4, which nobody drove
    assert f[0, 3] > f[4, 3] > 1.0                        # further ahead, less of it remains
    assert f[0, 0] == np.float32(np.exp(0.94 * np.log(1.2)))


def test_one_jammed_stretch_is_slowed_more_than_the_rest():
    seg = np.array([0] * 8 + [1] * 8)
    rho = np.array([np.log(2.0)] * 8 + [0.0] * 8)
    f, _ = factors_from_runs(seg, rho, 3, WEIGHTS)
    assert f[0, 0] > f[0, 2] > f[0, 1]                    # jammed > never seen (city average) > seen running normally


def test_breaker_opens_after_three_quick_failures_and_closes_later():
    b = Breaker()
    for t in (0, 10, 20):
        assert b.allow(t)
        b.fail(t)
    assert not b.allow(30)
    assert b.allow(20 + 601)
