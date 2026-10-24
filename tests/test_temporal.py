import copy
from datetime import datetime

import pytest

from agent_memory.temporal import evaluate_valid_during, resolve_today, validate_valid_during

AT = "2026-10-24T12:00:00Z"


def interval(start=None, end=None):
    return {"kind": "interval",
            "start": {"kind": "instant", "at": start} if start else {"kind": "unbounded"},
            "end": {"kind": "instant", "at": end} if end else {"kind": "unbounded"}}


def test_interval_bounds_are_half_open_and_historical_time_is_explicit():
    value = interval("2026-10-24T00:00:00Z", "2026-10-25T00:00:00Z")
    assert evaluate_valid_during(value, at=value["start"]["at"]).value == "true"
    assert evaluate_valid_during(value, at=value["end"]["at"]).value == "false"
    assert evaluate_valid_during(value, at=AT).value == "true"
    assert evaluate_valid_during(value, at="2026-11-01T00:00:00Z").reasons == ("expired",)


def test_unknown_bounds_are_distinct_from_unbounded_and_known_exclusion_is_still_decisive():
    assert evaluate_valid_during({"kind": "unknown"}, at=AT).value == "unknown"
    assert evaluate_valid_during(interval(), at=AT).value == "true"
    value = interval(end="2026-10-25T00:00:00Z")
    value["start"] = {"kind": "unknown"}
    assert evaluate_valid_during(value, at=AT).value == "unknown"
    assert evaluate_valid_during(value, at="2026-10-26T00:00:00Z").value == "false"
    value = interval(start="2026-11-01T00:00:00Z")
    value["end"] = {"kind": "unknown"}
    assert evaluate_valid_during(value, at=AT).value == "false"


@pytest.mark.parametrize("at", ["2026-10-24T12:00:00", "2026-10-24", "bad", datetime(2026, 10, 24), None])
def test_evaluation_never_assumes_the_host_timezone(at):
    with pytest.raises(ValueError):
        evaluate_valid_during(interval(), at=at)


@pytest.mark.parametrize("value", [
    {"kind": "interval", "start": None, "end": None},
    interval("2026-10-24T00:00:00Z", "2026-10-24T00:00:00Z"),
    interval("2026-10-25T00:00:00Z", "2026-10-24T00:00:00Z"),
    {"kind": "unknown", "assume_forever": True},
])
def test_invalid_or_ambiguous_legacy_time_encodings_are_rejected(value):
    with pytest.raises(ValueError):
        validate_valid_during(value)


@pytest.mark.parametrize("timestamp,hours", [
    ("2024-03-10T16:00:00Z", 23),
    ("2024-11-03T16:00:00Z", 25),
])
def test_today_uses_local_calendar_days_across_daylight_saving_time(timestamp, hours):
    value = resolve_today(anchor_time=timestamp, timezone_name="America/Vancouver",
                          anchor_ref={"event_id": "source-1", "revision": 2}, reliable=True)
    start, end = datetime.fromisoformat(value["start"]["at"]), datetime.fromisoformat(value["end"]["at"])
    assert (end - start).total_seconds() == hours * 3600
    assert value["anchor"] == {"event_id": "source-1", "revision": 2, "timestamp": timestamp.replace("Z", "+00:00"),
                               "timezone": "America/Vancouver", "expression": "today"}


@pytest.mark.parametrize("missing", ["anchor_time", "timezone_name", "anchor_ref", "reliable"])
def test_today_does_not_invent_an_anchor_or_timezone(missing):
    arguments = {"anchor_time": AT, "timezone_name": "America/Vancouver",
                 "anchor_ref": {"event_id": "source-1", "revision": 1}, "reliable": True}
    arguments[missing] = None
    assert resolve_today(**arguments) == {"kind": "unknown"}


def test_today_preserves_source_day_instead_of_import_day_and_rejects_inconsistent_anchor():
    value = resolve_today(anchor_time="2020-01-01T02:00:00Z", timezone_name="America/Vancouver",
                          anchor_ref={"event_id": "old-source", "revision": 1}, reliable=True)
    assert value["start"]["at"].startswith("2019-12-31")
    assert evaluate_valid_during(value, at=AT).value == "false"
    changed = copy.deepcopy(value)
    changed["end"]["at"] = "2026-11-01T00:00:00Z"
    with pytest.raises(ValueError, match="anchor"):
        validate_valid_during(changed)
