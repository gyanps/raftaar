"""The matcher on small synthetic routes: known speeds in, known stop times out."""
import numpy as np
import pytest

from raftaar.pipeline import match as M
from raftaar.pipeline.gtfs import stop_chainages


def line_shape(n=401, step=25.0):
    """A straight 10 km road heading east, a vertex every 25 m."""
    x = np.arange(n) * step
    y = np.zeros(n)
    return x, y, x.copy(), np.array([x.min(), x.max(), 0.0, 0.0])


def drive(shape_x, speed, t0=1000, every=10, start=0.0, end=None, offset=3.0):
    end = shape_x[-1] if end is None else end
    t = np.arange(t0, t0 + (end - start) / speed + 1, every).astype(np.int64)
    s = start + (t - t0) * speed
    return t, s, np.full(len(t), offset)


def run(ts, px, py, shape):
    sx, sy, cum, box = shape
    chain = np.empty(len(ts))
    off = np.empty(len(ts))
    M.project_fixes(ts, px, py, sx, sy, cum, box, chain, off)
    buf = np.empty((32, 9))
    m = M.find_tracks(ts, chain, off, np.ones(len(ts), dtype=np.int64), buf)
    return chain, off, buf[:m]


def test_steady_bus_gives_exact_stop_times():
    shape = line_shape()
    ts, px, py = drive(shape[0], speed=8.0)
    chain, off, tracks = run(ts, px, py, shape)
    assert len(tracks) == 1
    assert off.max() == pytest.approx(3.0)
    assert tracks[0][3] - tracks[0][2] == pytest.approx(ts[-1] * 8.0 - 8000.0, abs=1.0)

    stops = np.array([1000.0, 2000.0, 5000.0])
    zone = np.full(3, 30.0)
    arr, dep = np.empty(3), np.empty(3)
    M.stop_times(ts.astype(np.float64), chain, stops, zone, zone, arr, dep)
    assert arr == pytest.approx(1000 + (stops - 30) / 8.0, abs=0.01)
    assert dep == pytest.approx(1000 + (stops + 30) / 8.0, abs=0.01)


def test_dwell_at_a_stop_lands_between_arrival_and_departure():
    shape = line_shape()
    t1, x1, y1 = drive(shape[0], 10.0, t0=0, end=2000.0)
    wait = np.arange(t1[-1] + 10, t1[-1] + 100, 10)            # 90 s standing at the 2 km stop
    t2, x2, y2 = drive(shape[0], 10.0, t0=wait[-1] + 10, start=2000.0)
    ts = np.r_[t1, wait, t2].astype(np.int64)
    px = np.r_[x1, np.full(len(wait), 2000.0), x2]
    py = np.r_[y1, np.full(len(wait), 3.0), y2]
    chain, off, tracks = run(ts, px, py, shape)
    assert len(tracks) == 1
    arr, dep = np.empty(1), np.empty(1)
    M.stop_times(ts.astype(np.float64), chain, np.array([2000.0]), np.array([30.0]), np.array([30.0]), arr, dep)
    assert dep[0] - arr[0] == pytest.approx(100 + 6, abs=11)   # the wait plus 60 m of zone at 10 m/s


def test_a_hole_in_the_feed_gives_no_stop_time_but_keeps_the_trip():
    shape = line_shape()
    ts, px, py = drive(shape[0], 10.0, t0=0)
    keep = (px < 2500) | (px > 4500)                             # 200 s of silence across the 3 km stop
    ts, px, py = ts[keep], px[keep], py[keep]
    chain, off, tracks = run(ts, px, py, shape)
    assert len(tracks) == 1
    stops = np.array([1000.0, 3000.0, 6000.0])
    zone = np.full(3, 30.0)
    arr, dep = np.empty(3), np.empty(3)
    M.stop_times(ts.astype(np.float64), chain, stops, zone, zone, arr, dep)
    assert np.isnan(arr[1]) and np.isnan(dep[1])
    assert not np.isnan(arr[0]) and not np.isnan(arr[2])


def test_a_bus_driving_the_wrong_way_makes_no_trip():
    shape = line_shape()
    ts, px, py = drive(shape[0], 10.0, t0=0)
    chain, off, tracks = run(ts, px[::-1].copy(), py, shape)      # same road, opposite direction
    assert len(tracks) == 0


def test_far_from_the_shape_is_not_matched():
    shape = line_shape()
    ts, px, py = drive(shape[0], 10.0, t0=0, offset=120.0)
    chain, off, tracks = run(ts, px, py, shape)
    assert len(tracks) == 0


def test_standing_ten_minutes_splits_the_trip():
    shape = line_shape()
    t1, x1, y1 = drive(shape[0], 10.0, t0=0, end=4000.0)
    wait = np.arange(t1[-1] + 10, t1[-1] + 900, 10)
    t2, x2, y2 = drive(shape[0], 10.0, t0=wait[-1] + 10, start=4000.0)
    ts = np.r_[t1, wait, t2].astype(np.int64)
    px = np.r_[x1, np.full(len(wait), 4000.0), x2]
    py = np.r_[y1, np.full(len(wait), 3.0), y2]
    chain, off, tracks = run(ts, px, py, shape)
    assert len(tracks) == 2
    assert tracks[0][3] == pytest.approx(4000.0, abs=1.0)
    assert ts[int(tracks[0][1])] <= t1[-1] + 10                   # the first trip ends when the bus stopped moving


def test_out_and_back_keeps_each_stop_on_its_own_pass():
    # 2 km east, U-turn, 2 km back on the far side of the same road (12 m away).
    out_x = np.arange(0, 2001, 25.0)
    back_x = out_x[::-1]
    sx = np.r_[out_x, back_x]
    sy = np.r_[np.zeros(len(out_x)), np.full(len(back_x), 12.0)]
    cum = np.r_[0.0, np.cumsum(np.hypot(np.diff(sx), np.diff(sy)))]
    px = np.array([500.0, 1500.0, 1500.0, 500.0])                 # two stops out, the same two back
    py = np.array([0.0, 0.0, 12.0, 12.0])
    chain, offset, ok = stop_chainages(sx, sy, cum, px, py)
    assert ok
    assert np.all(np.diff(chain) > 0)
    assert chain == pytest.approx([500.0, 1500.0, 2512.0, 3512.0], abs=1.0)
    assert offset.max() < 1.0

    # A bus on the outbound leg must be followed on the outbound pass, not the nearer-looking return pass.
    ts = np.arange(0, 190, 10).astype(np.int64)
    bx = ts * 10.0
    by = np.full(len(ts), 7.0)                                     # GPS drifted toward the return lane
    c = np.empty(len(ts))
    o = np.empty(len(ts))
    M.project_fixes(ts, bx, by, sx, sy, cum, np.array([0.0, 2000.0, 0.0, 12.0]), c, o)
    # The very first fix has no direction of travel yet; from the second one on the heading decides.
    assert np.all(c[1:] <= 2000.0)
    assert np.all(np.diff(c[1:]) > 0)
    buf = np.empty((8, 9))
    m = M.find_tracks(ts, c, o, np.ones(len(ts), dtype=np.int64), buf)
    assert m == 1 and buf[0][3] - buf[0][2] == pytest.approx(1700.0, abs=1.0)
