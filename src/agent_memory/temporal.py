from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, FormatChecker

_STAMP = {"type": "string", "format": "date-time", "maxLength": 64}
_BOUND = {"oneOf": [
    {"type": "object", "properties": {"kind": {"const": "instant"}, "at": _STAMP},
     "required": ["kind", "at"], "additionalProperties": False},
    {"type": "object", "properties": {"kind": {"enum": ["unknown", "unbounded"]}},
     "required": ["kind"], "additionalProperties": False},
]}
_ANCHOR = {
    "type": "object", "properties": {
        "event_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "revision": {"type": "integer", "minimum": 1},
        "timestamp": _STAMP, "timezone": {"type": "string", "minLength": 1, "maxLength": 128},
        "expression": {"const": "today"},
    }, "required": ["event_id", "revision", "timestamp", "timezone", "expression"],
    "additionalProperties": False,
}
VALID_DURING_SCHEMA = {"oneOf": [
    {"type": "object", "properties": {"kind": {"const": "unknown"}},
     "required": ["kind"], "additionalProperties": False},
    {"type": "object", "properties": {
        "kind": {"const": "interval"}, "start": _BOUND, "end": _BOUND, "anchor": _ANCHOR,
    }, "required": ["kind", "start", "end"], "additionalProperties": False},
]}
_VALIDATOR = Draft202012Validator(VALID_DURING_SCHEMA, format_checker=FormatChecker())


def _timestamp(value):
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        return timestamp.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("An aware timestamp is required") from None


def _day_bounds(timestamp, timezone_name):
    try:
        zone = ZoneInfo(timezone_name)
        local_date = _timestamp(timestamp).astimezone(zone).date()
        start = datetime.combine(local_date, datetime.min.time(), zone)
        end = datetime.combine(local_date + timedelta(days=1), datetime.min.time(), zone)
        return start.astimezone(UTC), end.astimezone(UTC)
    except (TypeError, ValueError, OverflowError, ZoneInfoNotFoundError):
        raise ValueError("Invalid local-day anchor") from None


def validate_valid_during(value):
    if not _VALIDATOR.is_valid(value):
        raise ValueError("Invalid temporal interval")
    if value["kind"] == "unknown":
        return
    start = _timestamp(value["start"]["at"]) if value["start"]["kind"] == "instant" else None
    end = _timestamp(value["end"]["at"]) if value["end"]["kind"] == "instant" else None
    if start is not None and end is not None and end <= start:
        raise ValueError("Temporal intervals must have positive duration")
    if "anchor" in value:
        anchor = value["anchor"]
        if type(anchor["revision"]) is not int:
            raise ValueError("Invalid anchor revision")
        if (start, end) != _day_bounds(anchor["timestamp"], anchor["timezone"]):
            raise ValueError("Resolved interval does not match its source anchor")


@dataclass(frozen=True)
class TemporalResult:
    value: str
    reasons: tuple[str, ...] = ()

    def to_dict(self):
        return {"value": self.value, "reasons": list(self.reasons)}


def evaluate_valid_during(value, *, at):
    validate_valid_during(value)
    instant = _timestamp(at)
    if value["kind"] == "unknown":
        return TemporalResult("unknown", ("time_unknown",))
    start, end = value["start"], value["end"]
    if start["kind"] == "instant" and instant < _timestamp(start["at"]):
        return TemporalResult("false", ("not_yet_valid",))
    if end["kind"] == "instant" and instant >= _timestamp(end["at"]):
        return TemporalResult("false", ("expired",))
    if "unknown" in (start["kind"], end["kind"]):
        return TemporalResult("unknown", ("time_bound_unknown",))
    return TemporalResult("true")


def resolve_today(*, anchor_time, timezone_name, anchor_ref, reliable=False):
    if reliable is not True or anchor_time is None or timezone_name is None or anchor_ref is None:
        return {"kind": "unknown"}
    if not isinstance(anchor_ref, dict) or set(anchor_ref) != {"event_id", "revision"}:
        raise ValueError("A versioned source anchor is required")
    start, end = _day_bounds(anchor_time, timezone_name)
    result = {
        "kind": "interval", "start": {"kind": "instant", "at": start.isoformat()},
        "end": {"kind": "instant", "at": end.isoformat()},
        "anchor": {**anchor_ref, "timestamp": _timestamp(anchor_time).isoformat(),
                   "timezone": timezone_name, "expression": "today"},
    }
    validate_valid_during(result)
    return result
