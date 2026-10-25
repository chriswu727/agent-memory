import copy

from psycopg.types.json import Jsonb

from .helpers import DomainError, digest
from .learning_allocations import ENCODING_PHASE, allocation_metadata, phase
from .learning_windows import bounds
from .turns import _identifier, _integer

DEFAULT_MODEL_CALLS = 2
DEFAULT_INPUT_BYTES = 196608
MAX_AUTOMATIC_ATTEMPTS = 3
POLICY_REFRESH_VERSION = "explicit-recovery-current-policy-v1"


def _remaining(row):
    payload = row["payload"]
    try:
        if row["kind"] != "consolidate" or not isinstance(payload, dict):
            raise ValueError
        start, end = bounds(payload)
        target = {key: _identifier(payload["target"][key]) for key in ("conversation_ref", "branch_ref")}
        result = row.get("result")
        result = {} if result is None else result
        if not isinstance(result, dict):
            raise ValueError
        window = result.get("learning_window")
        processed = _integer(payload.get("processed_through_sequence", start - 1), start - 1, end)
        if window is not None:
            if not isinstance(window, dict) or bounds(window) != (start, end):
                raise ValueError
            attempted = _integer(window["attempted_through_sequence"], start - 1, end)
            recorded = _integer(window["processed_through_sequence"], start - 1, attempted)
            if "processed_through_sequence" in payload and recorded != processed:
                raise ValueError
            processed = recorded
        if row["state"] == "completed" and (
                window is None or window.get("processing_succeeded") is not True
                or processed != window["attempted_through_sequence"]):
            raise ValueError
        if processed >= end:
            raise ValueError
    except (DomainError, KeyError, TypeError, ValueError):
        raise DomainError("stage_conflict", "Job has no valid unprocessed learning window.") from None
    return target, processed + 1, end


def _phase_window(row):
    try:
        if row["kind"] != "consolidate" or row["state"] == "completed":
            raise ValueError
        start, end = bounds(row["payload"])
        target = {key: _identifier(row["payload"]["target"][key])
                  for key in ("conversation_ref", "branch_ref")}
        if row.get("result") is not None and not isinstance(row["result"], dict):
            raise ValueError
        if phase(row["payload"]) == ENCODING_PHASE:
            processed = _integer(row["payload"].get("processed_through_sequence", start - 1), start - 1, end)
            window = (row.get("result") or {}).get("learning_window")
            if window is not None:
                if not isinstance(window, dict) or bounds(window) != (start, end):
                    raise ValueError
                attempted = _integer(window["attempted_through_sequence"], start - 1, end)
                if _integer(window["processed_through_sequence"], start - 1, attempted) != processed:
                    raise ValueError
            if processed != start - 1:
                raise ValueError
    except (DomainError, KeyError, TypeError, ValueError):
        raise DomainError("stage_conflict", "Job has no failed learning phase to recover.") from None
    if (any(key in row["payload"] for key in (
            "allocation_input_fingerprint", "recovery_input_fingerprint", "recovery_policy_refresh"))
            and (not isinstance(row.get("result"), dict) or "working_snapshot" not in row["result"])):
        raise DomainError("context_stale", "The frozen learning input plan is no longer available.")
    return target, start, end


def retry_learning(store, conn, principal, brain_id, job_id, *, request_id, expected_fence):
    request_id = _identifier(request_id)
    expected_fence = _integer(expected_fence)
    parent = store._row(conn, principal, brain_id, job_id)
    fingerprint = digest(["retry-learning-v1", job_id, expected_fence, request_id])
    payload = parent["payload"]
    if not isinstance(payload, dict):
        raise DomainError("stage_conflict", "Job has no valid unprocessed learning window.")
    selected_phase = phase(payload)
    if "recovery_job_id" in payload:
        if (payload.get("recovery_request_fingerprint") != fingerprint
                or payload.get("recovery_expected_fence") != expected_fence):
            raise DomainError("idempotency_conflict", "This job already has a different recovery authorization.")
        child = store._row(conn, principal, brain_id, payload["recovery_job_id"])
        if (child["kind"] != "consolidate" or not isinstance(child["payload"], dict)
                or child["payload"].get("recovery_parent_job_id") != parent["id"]
                or phase(child["payload"]) != selected_phase):
            raise DomainError("stage_conflict", "Learning recovery lineage is inconsistent.")
        return store._public(child)
    if parent["fence"] != expected_fence:
        raise DomainError("version_conflict", "Job fence changed before recovery.")
    exhausted_lease = (parent["state"] == "running" and parent["attempts"] >= MAX_AUTOMATIC_ATTEMPTS
                       and parent["lease_expires_at"] is not None
                       and parent["lease_expires_at"] <= parent["db_now"])
    if (parent["state"] not in {"cancelled", "superseded", "completed"}
            and not (parent["state"] == "failed" and parent["attempts"] >= MAX_AUTOMATIC_ATTEMPTS)
            and not exhausted_lease):
        raise DomainError("stage_conflict", "Job is active or still eligible for automatic retry.")
    if "continuation_job_id" in payload:
        raise DomainError("stage_conflict", "The remaining window already has a continuation.")
    if "integration_continuation_job_id" in payload:
        raise DomainError("stage_conflict", "The unattempted historical comparisons already have a continuation.")
    target, start, end = _phase_window(parent) if selected_phase else _remaining(parent)
    params = store._params(principal, brain_id)
    brain = conn.execute(
        "SELECT durable_sequence FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s",
        params,
    ).fetchone()
    if end > brain["durable_sequence"]:
        raise DomainError("stage_conflict", "Learning window exceeds recorded input.")
    if exhausted_lease:
        conn.execute(
            "UPDATE memory_cognitive_operations SET outcome='uncertain' "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND job_id=%s AND outcome='reserved'",
            (*params, job_id),
        )
        conn.execute(
            "UPDATE memory_jobs SET state='cancelled',lease_token_digest=NULL,lease_expires_at=NULL,"
            "worker_id=NULL,last_error='provider_uncertain' "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, job_id),
        )
    identifier = "consolidate:" + digest([brain_id, parent["id"], fingerprint])
    child_payload = {
        "target": target, "from_sequence": start, "through_sequence": end,
        "recovery_parent_job_id": parent["id"], "recovery_root_job_id": identifier,
        "recovery_budget": {"max_model_calls": DEFAULT_MODEL_CALLS, "max_input_bytes": DEFAULT_INPUT_BYTES},
    }
    if selected_phase is not None:
        preserved = {key: copy.deepcopy(value) for key, value in payload.items() if key not in {
            "recovery_job_id", "recovery_request_fingerprint", "recovery_expected_fence",
            "continuation_job_id", "continuation_parent_job_id",
            "encoding_replan_version", "history_replan_version", "integration_continuation_parent_job_id",
            "integration_continuation_job_id",
            "history_allocation_job_id", "last_reconsidered_token", "last_history_replan_token",
            "allocation_input_fingerprint", "recovery_policy_refresh", "recovery_input_fingerprint",
        }}
        child_payload = {**preserved, **child_payload,
                         **allocation_metadata(identifier, phase=selected_phase, recovery=True)}
        if "allocation_input_fingerprint" in payload:
            child_payload.update(recovery_policy_refresh=POLICY_REFRESH_VERSION,
                                 recovery_input_fingerprint=payload["allocation_input_fingerprint"])
    # Authorization covers finite continuation batches; historical reservations remain charged.
    conn.execute(
        "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state,max_model_calls,max_input_bytes) "
        "VALUES(%s,%s,%s,%s,'consolidate',%s,'queued',%s,%s)",
        (*params, identifier, Jsonb(child_payload), DEFAULT_MODEL_CALLS, DEFAULT_INPUT_BYTES),
    )
    if selected_phase is not None:
        conn.execute(
            "UPDATE memory_jobs SET result=%s,source_event_ids=%s,source_memory_ids=%s "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb(parent.get("result")), parent.get("source_event_ids", []),
             parent.get("source_memory_ids", []), *params, identifier),
        )
    conn.execute(
        "UPDATE memory_jobs SET payload=%s,updated_at=clock_timestamp() "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
        (Jsonb({**payload, "recovery_job_id": identifier, "recovery_request_fingerprint": fingerprint,
                "recovery_expected_fence": expected_fence}), *params, job_id),
    )
    return store._public(store._row(conn, principal, brain_id, identifier))
