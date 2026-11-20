"""Scoring a file of test trips (raftaar.validation.predict)."""
import csv
import datetime as dt

import pytest

from raftaar.calendar import IST_OFFSET_S
from raftaar.config import ART, ROOT
from raftaar.validation import predict

needs_model = pytest.mark.skipif(not (ART / "planner.npz").exists() or not (ART / "model" / "runs.npz").exists(),
                                 reason="run 'make demo' or 'make all' first")


def ist(epoch: float) -> dt.datetime:
    return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=epoch + IST_OFFSET_S)


def test_departure_times_in_every_supported_form():
    assert ist(predict.parse_when({"depart": "2026-10-28 08:30"})[0]) == dt.datetime(2026, 10, 28, 8, 30)
    assert ist(predict.parse_when({"datetime": "2026-10-28T03:00:00Z"})[0]) == dt.datetime(2026, 10, 28, 8, 30)       # UTC -> IST
    assert ist(predict.parse_when({"time": "2026-10-28T08:30:00+05:30"})[0]) == dt.datetime(2026, 10, 28, 8, 30)
    assert ist(predict.parse_when({"time": "6:30 PM", "date": "28-10-2026"})[0]) == dt.datetime(2026, 10, 28, 18, 30)
    assert ist(predict.parse_when({"timestamp": "1793156400"})[0]) == dt.datetime(2026, 10, 28, 8, 30)
    sat, note = predict.parse_when({"depart": "09:00", "day": "Saturday"})
    assert ist(sat).weekday() == 5 and note == ""
    plain, note = predict.parse_when({"depart": "09:00"})
    assert ist(plain).weekday() < 5 and "weekday assumed" in note
    with pytest.raises(ValueError):
        predict.parse_when({"from": "x"})


def test_observed_time_column_and_its_unit():
    assert predict.actual_column(["from", "actual_s"], []) == ("actual_s", 1 / 60)
    assert predict.actual_column(["observed_min"], []) == ("observed_min", 1.0)
    assert predict.actual_column(["travel_time"], [{"travel_time": "1500"}, {"travel_time": "900"}]) == ("travel_time", 1 / 60)
    assert predict.actual_column(["travel_time"], [{"travel_time": "25"}, {"travel_time": "40"}]) == ("travel_time", 1.0)
    assert predict.actual_column(["from", "to", "depart"], []) is None


@needs_model
def test_the_sample_file_is_answered_row_by_row(tmp_path):
    out = tmp_path / "pred.csv"
    assert predict.main([str(ROOT / "validation" / "sample_cases.csv"), "-o", str(out)]) == 0
    rows = list(csv.DictReader(open(out)))
    assert len(rows) == 7
    for r in rows:
        assert r["matched_from"] and r["matched_to"], r["note"]
        assert float(r["door_to_door_min"]) > 0
        if r["bus_ride_min"]:
            assert float(r["bus_ride_p10_min"]) <= float(r["bus_ride_min"]) <= float(r["bus_ride_p90_min"])
    assert rows[2]["route_used"].startswith("425")                 # route '425' given
    assert "stop 333" in rows[3]["matched_from"]                   # stop_id given
    evening, morning = float(rows[1]["door_to_door_min"]), float(rows[0]["door_to_door_min"])
    assert evening > morning                                       # the same trip at 18:30 and at 08:30


@needs_model
def test_a_row_that_cannot_be_read_says_why_and_the_rest_still_run(tmp_path):
    src = tmp_path / "cases.csv"
    src.write_text("Origin,Destination,Departure Time,Observed (min)\nRed Fort,Nehru Place Terminal,08:30,45\nNowhere At All Xyz,Red Fort,08:30,10\n")
    assert predict.main([str(src)]) == 0
    rows = list(csv.DictReader(open(tmp_path / "cases.predictions.csv")))
    assert rows[0]["bus_ride_min"] and rows[0]["error_min"]
    assert rows[1]["bus_ride_min"] == "" and "no bus stop matches" in rows[1]["note"]
