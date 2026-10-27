import copy
import hashlib

from .helpers import DomainError
from .turns import _hash, _identifier, _integer, _refs

VERSION = "runtime-delivery-receipt-v1"
STATES = frozenset({"delivered", "partially_delivered", "cancelled"})
REASONS = frozenset({
    "source_unavailable", "context_generation_changed", "working_revision_changed", "orientation_changed",
})
FAILURES = frozenset({"source_unavailable", "context_stale", "commit_failed", "cancelled"})


def sanitize_delivery_receipt(value):
    if not isinstance(value, dict):
        return None
    try:
        state, disposition, failure = (value[key] for key in ("state", "disposition", "failure_code"))
        if (value["version"] != VERSION or not isinstance(state, str) or state not in STATES
                or disposition not in ("content_recorded", "content_withheld")
                or failure is not None and (not isinstance(failure, str) or failure not in FAILURES)):
            return None
        content_hash = _hash(value["content_sha256"])
        size = _integer(value["content_bytes"], 0 if state == "cancelled" else 1, 64000)
        if state == "cancelled" and (size or content_hash != hashlib.sha256(b"").hexdigest()):
            return None
        reasons = value["stale_reasons"]
        if (not isinstance(reasons, list) or len(reasons) > len(REASONS)
                or any(not isinstance(reason, str) or reason not in REASONS for reason in reasons)
                or len(set(reasons)) != len(reasons)):
            return None
        if ((disposition == "content_withheld" and "source_unavailable" not in reasons)
                or failure != failure_code(reasons, state)):
            return None
        binding = value["authorization"]
        reference = binding["draft_event_ref"]
        authorized = {
            "draft_event_ref": {"event_id": _identifier(reference["event_id"]),
                                "revision": _integer(reference["revision"], 1)},
            "context_fingerprint": _hash(binding["context_fingerprint"]),
            "context_generation": _integer(binding["context_generation"]),
            "working_revision": _integer(binding["working_revision"]),
            "turn_fence": _integer(binding["turn_fence"], 1),
            "turn_revision": _integer(binding["turn_revision"], 1),
        }
        return {"version": VERSION, "state": state, "content_sha256": content_hash, "content_bytes": size,
                "authorization": authorized, "disposition": disposition,
                "stale_reasons": sorted(reasons), "failure_code": failure}
    except (DomainError, KeyError, TypeError):
        return None


def failure_code(reasons, state):
    if "source_unavailable" in reasons:
        return "source_unavailable"
    if "working_revision_changed" in reasons:
        return "commit_failed"
    if reasons:
        return "context_stale"
    return "cancelled" if state == "cancelled" else None


def receipt_for(row, text, state, *, reasons=(), content_recorded):
    encoded = text.encode("utf-8")
    value = {
        "version": VERSION, "state": state,
        "content_sha256": hashlib.sha256(encoded).hexdigest(), "content_bytes": len(encoded),
        "authorization": {
            "draft_event_ref": copy.deepcopy(row["output_event_refs"][0]),
            "context_fingerprint": row["context_fingerprint"], "context_generation": row["context_generation"],
            "working_revision": row["working_revision"], "turn_fence": row["fence"],
            "turn_revision": row["revision"],
        },
        "disposition": "content_recorded" if content_recorded else "content_withheld",
        "stale_reasons": sorted(set(reasons)), "failure_code": failure_code(reasons, state),
    }
    checked = sanitize_delivery_receipt(value)
    if checked is None:
        raise DomainError("invalid_evidence", "Delivery authorization metadata is invalid.")
    return checked


def receipt_matches(receipt, text, state):
    encoded = text.encode("utf-8")
    return (receipt["state"] == state and receipt["content_bytes"] == len(encoded)
            and receipt["content_sha256"] == hashlib.sha256(encoded).hexdigest())


def failed_receipt(receipt, reasons):
    reasons = sorted(set(receipt["stale_reasons"]) | set(reasons))
    return {**receipt, "stale_reasons": reasons, "failure_code": failure_code(reasons, receipt["state"])}


def recorded_delivery_receipt(row):
    if (not isinstance(row, dict) or not isinstance(row.get("stage"), str)
            or row["stage"] not in {"delivered", "completed", "failed"}):
        return False
    receipt = sanitize_delivery_receipt(row.get("delivery_receipt"))
    if receipt is None or row.get("failure_code") != receipt["failure_code"]:
        return False
    terminal = row["stage"] != "delivered"
    if row.get("scope_reserved") is not (not terminal):
        return False
    if (row["stage"] == "failed") != (receipt["failure_code"] is not None):
        return False
    binding = receipt["authorization"]
    try:
        if (_hash(row["context_fingerprint"]) != binding["context_fingerprint"]
                or _integer(row["context_generation"]) != binding["context_generation"]
                or _integer(row["working_revision"]) != binding["working_revision"] + int(row["stage"] == "completed")
                or _integer(row["fence"], 1) < binding["turn_fence"]
                or _integer(row["revision"], 1) <= binding["turn_revision"]):
            return False
        reference = binding["draft_event_ref"]
        expected = {"event_id": reference["event_id"], "revision": reference["revision"] + int(
            receipt["disposition"] == "content_recorded")}
        return _refs(row["output_event_refs"]) == [expected]
    except (DomainError, KeyError, TypeError):
        return False


def confirmed_delivery_receipt(row):
    return recorded_delivery_receipt(row) and row["stage"] == "failed"
