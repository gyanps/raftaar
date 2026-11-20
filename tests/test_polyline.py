"""Ported with the geometry core: the same cases, the same tolerances."""
import math

import pytest

from raftaar.geo import polyline as G
from raftaar.geo.index import PolylineIndex


def north_line(lat0, lng, n, step=0.01):
    """A straight north-bound line from (lat0, lng), a point every ~1.1 km."""
    return [(lat0 + i * step, lng) for i in range(n)]


def test_encode_matches_googles_reference_example():
    pts = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert G.encode(pts) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    for got, want in zip(G.decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@"), pts):
        assert got == pytest.approx(want, abs=1e-9)


def test_round_trip_keeps_five_decimals():
    pts = [(19.29612, 73.06311), (19.31, 73.1), (26.70001, 77.89999)]
    for got, want in zip(G.decode(G.encode(pts)), pts):
        assert got == pytest.approx(want, abs=1e-5)


def test_decode_of_garbage_or_empty_is_safe():
    assert G.decode("") == []
    assert G.decode(None) == []
    assert isinstance(G.decode("_p~iF~ps|U_"), list)


def test_haversine_mumbai_delhi():
    assert G.haversine_km(19.076, 72.8777, 28.6139, 77.209) == pytest.approx(1150, abs=15)


def test_distance_and_chainage_against_a_straight_line():
    line = PolylineIndex(north_line(20.0, 75.0, 101))  # ~110 km north
    assert line.length_km() == pytest.approx(111.2, abs=0.1)

    # 10 km east of the line, half-way up.
    hit = line.locate(20.5, 75.0 + 10 / (111.32 * math.cos(math.radians(20.5))))
    assert hit["distance_km"] == pytest.approx(10.0, abs=0.1)
    assert hit["chainage_km"] == pytest.approx(55.6, abs=0.1)

    # Beyond the end: distance to the end point, chainage = full length.
    end = line.locate(21.2, 75.0)
    assert end["distance_km"] == pytest.approx(22.24, abs=0.05)
    assert end["chainage_km"] == pytest.approx(line.length_km(), abs=0.01)


def test_index_agrees_with_brute_force_on_a_wiggly_line():
    pts = [(18.0 + i * 0.015, 73.0 + math.sin(i / 15) * 0.3) for i in range(600)]
    line = PolylineIndex(pts)
    for lat, lng in [(19.0, 73.4), (21.5, 72.5), (25.0, 74.0), (18.2, 73.0)]:
        best = min(G.point_segment(lat, lng, pts[i], pts[i + 1])[0] for i in range(len(pts) - 1))
        assert line.locate(lat, lng)["distance_km"] == pytest.approx(best, abs=1e-6)


def test_within_rejects_far_points_and_accepts_near_ones():
    line = PolylineIndex(north_line(20.0, 75.0, 50))
    assert line.within(20.2, 75.02, 5)
    assert not line.within(20.2, 75.2, 5)
    assert not line.within(28.6, 77.2, 5)
    assert not PolylineIndex([]).within(20.0, 75.0, 5)


def test_locate_near_picks_the_pass_closest_to_the_hint():
    # Out 0.5 degrees north, then back south on the same road. A bus at 20.1 on
    # its way OUT (hint ~11 km) must not read as being on the return leg.
    out = north_line(20.0, 75.0, 51)
    back = list(reversed(north_line(20.0, 75.0005, 51)))
    line = PolylineIndex(out + back)

    outbound = line.locate_near(20.1, 75.0002, 9.0, 5.0)
    assert outbound["chainage_km"] == pytest.approx(11.1, abs=0.5)

    inbound = line.locate_near(20.1, 75.0002, 95.0, 5.0)
    assert inbound["chainage_km"] > 90


def test_simplify_keeps_shape_and_endpoints():
    pts = north_line(20.0, 75.0, 200)
    pts[100] = (pts[100][0], pts[100][1] + 0.05)  # a 5 km kink
    s = G.simplify(pts, 0.03)
    assert len(s) < 10
    assert s[0] == pts[0]
    assert s[-1] == pts[199]
    assert pts[100] in s


def test_sample_spacing_and_ends():
    pts = north_line(20.0, 75.0, 101)
    s = G.sample(pts, 1.0)
    assert len(s) == math.ceil(G.length_km(pts)) + 1  # 0, 1, ... 111 km + the end point
    assert s[0][2] == 0.0
    assert s[-1][2] == pytest.approx(G.length_km(pts), abs=1e-9)


def test_valid_coord_rejects_zero_and_swapped():
    assert G.valid_coord(28.6, 77.2)
    assert not G.valid_coord(0, 0)
    assert not G.valid_coord(77.2, 28.6)
    assert not G.valid_coord(None, 77.2)
    assert not G.valid_coord("abc", 77.2)
    assert not G.valid_coord(200.0, 200.0)  # seen in the raw feed


def test_slice_cuts_the_line_between_two_chainages():
    line = PolylineIndex(north_line(20.0, 75.0, 11))  # 20.0 -> 20.1, ~11.1 km
    part = line.slice(2.0, 7.0)
    assert part[0][0] == pytest.approx(20.0 + 2.0 / 111.19508, abs=1e-6)
    assert part[-1][0] == pytest.approx(20.0 + 7.0 / 111.19508, abs=1e-6)
    assert G.length_km(part) == pytest.approx(5.0, abs=0.01)
    assert len(part) == 2 + 5  # vertices strictly inside the range are kept


def test_slice_clamps_to_the_line_and_rejects_backwards_ranges():
    line = PolylineIndex(north_line(20.0, 75.0, 11))
    assert G.length_km(line.slice(-5, 99)) == pytest.approx(line.length_km(), abs=1e-6)
    assert line.slice(7.0, 2.0) == []
    assert line.slice(4.0, 4.0) == []
    assert PolylineIndex([]).slice(0, 1) == []
