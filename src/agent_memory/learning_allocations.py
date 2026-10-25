from psycopg.types.json import Jsonb

from .helpers import DomainError
from .learning_funding import (
    FUNDING_FIELDS,
    validate_funded_root,
    validate_funding_metadata,
    validate_private_funding_sources,
)
from .turns import _hash, _identifier

CONTRACT_VERSION = "encoding-integration-v1"
ENCODING_PHASE = "encoding"
HISTORY_PHASE = "history_integration"
ALLOCATION_POLICY = "encoding-integration-budget-v1"
RECOVERY_POLICY = "explicit-phase-recovery-budget-v1"
ENCODING_REPLAN_VERSION = "unreserved-encoding-replan-v1"
ENCODING_WINDOW_VERSION = "encoding-window-snapshot-v1"
PHASE_CALLS = 2
PHASE_BYTES = 196608
AUTHORITY_FIELDS = (
    "learning_contract_version", "learning_phase", "authorization_root_job_id", "allocation_policy",
    "allocation_budget", "phase_budget", "allocation_input_fingerprint", "history_attention_policy", *FUNDING_FIELDS,
)


def _invalid():
    return DomainError("dependency_unavailable", "Learning allocation metadata is unavailable or inconsistent.")


def _budget(calls, size):
    return {"max_model_calls": calls, "max_input_bytes": size}


def _metadata_phase(payload):
    if not isinstance(payload, dict):
        raise _invalid()
    if not any(key in payload for key in AUTHORITY_FIELDS):
        return None
    try:
        value = payload["learning_phase"]
        policy = payload["allocation_policy"]
        if (payload["learning_contract_version"] != CONTRACT_VERSION
                or not isinstance(value, str) or value not in {ENCODING_PHASE, HISTORY_PHASE}
                or not isinstance(policy, str) or policy not in {ALLOCATION_POLICY, RECOVERY_POLICY}):
            raise ValueError
        _identifier(payload["authorization_root_job_id"])
        expected = _budget(4, 393216) if policy == ALLOCATION_POLICY else _budget(2, PHASE_BYTES)
        for key, budget in (("allocation_budget", expected), ("phase_budget", _budget(2, PHASE_BYTES))):
            actual = payload[key]
            if (not isinstance(actual, dict) or actual != budget
                    or any(type(number) is not int for number in actual.values())):
                raise ValueError
        if "allocation_input_fingerprint" in payload:
            _hash(payload["allocation_input_fingerprint"])
        validate_funding_metadata(payload)
    except (DomainError, KeyError, TypeError, ValueError):
        raise _invalid() from None
    return value


def phase(payload):
    return _metadata_phase(payload)


def allocation_metadata(job_id, *, phase=ENCODING_PHASE, root_job_id=None, recovery=False):
    _identifier(job_id)
    root_job_id = job_id if root_job_id is None else _identifier(root_job_id)
    result = {
        "learning_contract_version": CONTRACT_VERSION, "learning_phase": phase,
        "authorization_root_job_id": root_job_id,
        "allocation_policy": RECOVERY_POLICY if recovery else ALLOCATION_POLICY,
        "allocation_budget": _budget(2, PHASE_BYTES) if recovery else _budget(4, 393216),
        "phase_budget": _budget(PHASE_CALLS, PHASE_BYTES),
    }
    _metadata_phase(result)
    return result


def _allocation_row(conn, params, identifier):
    row = conn.execute(
        "SELECT id,kind,state,payload,model_calls,input_bytes,last_error FROM memory_jobs "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id)", (*params, identifier),
    ).fetchone()
    if row is None or row["kind"] != "consolidate":
        raise _invalid()
    return row


def _encoding_lineage(conn, params, row, root):
    current, seen = row, set()
    while current["id"] != root["id"]:
        payload = current["payload"]
        if current["id"] in seen or payload.get("encoding_replan_version") != ENCODING_REPLAN_VERSION:
            raise _invalid()
        seen.add(current["id"])
        parent_id = payload.get("recovery_parent_job_id")
        _identifier(parent_id)
        parent = _allocation_row(conn, params, parent_id)
        previous = parent["payload"]
        if (phase(previous) != ENCODING_PHASE or parent["state"] not in {"superseded", "cancelled"}
                or parent["state"] == "cancelled" and parent["last_error"] not in {"context_stale", "source_unavailable"}
                or parent["model_calls"] != 0 or parent["input_bytes"] != 0
                or previous.get("recovery_job_id") != current["id"]
                or any(previous.get(key) != payload.get(key) for key in (
                    "authorization_root_job_id", "allocation_policy", "allocation_budget", "phase_budget",
                    "target", "from_sequence", "through_sequence"))):
            raise _invalid()
        start, end = previous.get("from_sequence"), previous.get("through_sequence")
        processed = previous.get("processed_through_sequence", start - 1 if type(start) is int else None)
        if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= 2**63 - 1
                or type(processed) is not int or processed != start - 1
                or previous.get("continuation_job_id") is not None
                or conn.execute("SELECT 1 FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s "
                                "AND brain_id=%s AND job_id=%s LIMIT 1", (*params, parent_id)).fetchone()):
            raise _invalid()
        current = parent


def encoding_allocation_ready(conn, params, root, *, allow_running=False):
    current, seen = root, set()
    while "recovery_job_id" in current["payload"]:
        if current["id"] in seen:
            raise _invalid()
        seen.add(current["id"])
        current = _allocation_row(conn, params, _identifier(current["payload"]["recovery_job_id"]))
        if (phase(current["payload"]) != ENCODING_PHASE
                or current["payload"].get("authorization_root_job_id") != root["id"]):
            raise _invalid()
    _encoding_lineage(conn, params, current, root)
    return current["state"] in ({"running", "completed"} if allow_running else {"completed"})


def unreserved_encoding_root(conn, params, row):
    if phase(row["payload"]) != ENCODING_PHASE:
        raise _invalid()
    root = allocation_root(conn, params, row)
    if conn.execute(
        "SELECT 1 FROM memory_cognitive_operations o JOIN memory_jobs j ON j.tenant_id=o.tenant_id "
        "AND j.owner_id=o.owner_id AND j.brain_id=o.brain_id AND j.id=o.job_id "
        "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s "
        "AND j.authorization_root_job_id=%s LIMIT 1", (*params, root["id"]),
    ).fetchone():
        return None
    return root


def allocation_root(conn, params, row):
    payload = row["payload"]
    selected_phase = phase(payload)
    if selected_phase is None:
        return None
    if row["kind"] != "consolidate":
        raise _invalid()
    root_id = payload["authorization_root_job_id"]
    root = _allocation_row(conn, params, root_id)
    root_phase = phase(root["payload"])
    if (root_phase is None or root["payload"]["authorization_root_job_id"] != root_id
            or any(root["payload"][key] != payload[key] for key in ("allocation_policy", "allocation_budget"))
            or (payload["allocation_policy"] == ALLOCATION_POLICY and root_phase != ENCODING_PHASE)
            or (payload["allocation_policy"] == RECOVERY_POLICY and root_phase != selected_phase)):
        raise _invalid()
    try:
        for target in (payload["target"], root["payload"]["target"]):
            if not isinstance(target, dict) or set(target) != {"conversation_ref", "branch_ref"}:
                raise ValueError
            for value in target.values():
                _identifier(value)
        if "history_funding_permission" in payload and row["id"] != root_id:
            raise ValueError
        if payload["target"] != root["payload"]["target"]:
            if selected_phase != HISTORY_PHASE:
                raise ValueError
            validate_funded_root(conn, params, row, root)
    except (KeyError, ValueError, DomainError):
        raise _invalid() from None
    if selected_phase == ENCODING_PHASE and root_id != row["id"]:
        _encoding_lineage(conn, params, row, root)
    return root


def encoding_window(payload):
    value = payload.get("encoding_window_snapshot")
    if value is None and "encoding_window_snapshot" not in payload:
        return None
    if (phase(payload) != ENCODING_PHASE or not isinstance(value, dict)
            or set(value) != {"version", "from_sequence", "through_sequence", "attempted_through_sequence"}
            or value["version"] != ENCODING_WINDOW_VERSION):
        raise _invalid()
    start, end, attempted = (value[key] for key in (
        "from_sequence", "through_sequence", "attempted_through_sequence"))
    if (any(type(number) is not int for number in (start, end, attempted))
            or not 1 <= start <= attempted <= end <= 2**63 - 1
            or (start, end) != (payload.get("from_sequence"), payload.get("through_sequence"))):
        raise _invalid()
    return value


def freeze_input(conn, params, job_id, *, input_fingerprint, window=None):
    _identifier(job_id)
    _hash(input_fingerprint)
    row = conn.execute(
        "SELECT id,kind,payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s FOR UPDATE",
        (*params, job_id),
    ).fetchone()
    if row is None:
        raise _invalid()
    root = allocation_root(conn, params, row)
    if root is None:
        raise _invalid()
    if row["payload"]["target"] != root["payload"]["target"]:
        validate_funded_root(conn, params, row, root, assigned=True)
    payload = row["payload"]
    window_marker = None
    if window is not None:
        if not isinstance(window, dict):
            raise _invalid()
        window_marker = {"version": ENCODING_WINDOW_VERSION, **{
            key: window.get(key) for key in ("from_sequence", "through_sequence", "attempted_through_sequence")}}
        encoding_window({**payload, "encoding_window_snapshot": window_marker})
    if "allocation_input_fingerprint" in payload:
        if payload["allocation_input_fingerprint"] != input_fingerprint:
            raise DomainError("idempotency_conflict", "Learning allocation already binds another input plan.")
        if "encoding_window_snapshot" in payload and encoding_window(payload) != window_marker:
            raise DomainError("idempotency_conflict", "Learning allocation already binds another input window.")
        return payload
    if conn.execute(
        "SELECT 1 FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND job_id=%s LIMIT 1", (*params, job_id),
    ).fetchone():
        raise _invalid()
    payload = {**payload, "allocation_input_fingerprint": input_fingerprint}
    if window_marker is not None:
        if "encoding_window_snapshot" in payload and encoding_window(payload) != window_marker:
            raise _invalid()
        payload["encoding_window_snapshot"] = window_marker
    conn.execute(
        "UPDATE memory_jobs SET payload=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
        (Jsonb(payload), *params, job_id),
    )
    return payload


def check_reservation(conn, params, row, *, input_bytes):
    selected_phase = phase(row["payload"])
    if selected_phase is None:
        return
    root = allocation_root(conn, params, row)
    payload = row["payload"]
    if payload["target"] != root["payload"]["target"]:
        validate_funded_root(conn, params, row, root, assigned=True)
        validate_private_funding_sources(conn, params, row, root)
    if (selected_phase == HISTORY_PHASE and payload["allocation_policy"] == ALLOCATION_POLICY
            and not encoding_allocation_ready(conn, params, root)):
        raise DomainError("dependency_unavailable", "History integration requires completed encoding.")
    if "allocation_input_fingerprint" not in payload:
        raise _invalid()
    usage = conn.execute(
        "SELECT count(*) AS model_calls,coalesce(sum(o.input_bytes),0) AS input_bytes,"
        "count(*) FILTER (WHERE j.payload->>'learning_phase'=%s) AS phase_calls,"
        "coalesce(sum(o.input_bytes) FILTER (WHERE j.payload->>'learning_phase'=%s),0) AS phase_bytes "
        "FROM memory_cognitive_operations o JOIN memory_jobs j ON j.tenant_id=o.tenant_id "
        "AND j.owner_id=o.owner_id AND j.brain_id=o.brain_id AND j.id=o.job_id "
        "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.authorization_root_job_id=%s",
        (selected_phase, selected_phase, *params, payload["authorization_root_job_id"]),
    ).fetchone()
    allocation = payload["allocation_budget"]
    if (usage["model_calls"] + 1 > allocation["max_model_calls"]
            or usage["input_bytes"] + input_bytes > allocation["max_input_bytes"]
            or usage["phase_calls"] + 1 > PHASE_CALLS or usage["phase_bytes"] + input_bytes > PHASE_BYTES):
        raise DomainError("budget_exceeded", "Durable learning allocation is exhausted.")
