from psycopg.types.json import Jsonb

from .helpers import DomainError, digest
from .learning_windows import bounds
from .turns import _identifier, _integer

DEFAULT_MODEL_CALLS = 2
DEFAULT_INPUT_BYTES = 196608
MAX_AUTOMATIC_ATTEMPTS = 3


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


def retry_learning(store, conn, principal, brain_id, job_id, *, request_id, expected_fence):
    request_id = _identifier(request_id)
    expected_fence = _integer(expected_fence)
    parent = store._row(conn, principal, brain_id, job_id)
    fingerprint = digest(["retry-learning-v1", job_id, expected_fence, request_id])
    payload = parent["payload"]
    if not isinstance(payload, dict):
        raise DomainError("stage_conflict", "Job has no valid unprocessed learning window.")
    if "recovery_job_id" in payload:
        if (payload.get("recovery_request_fingerprint") != fingerprint
                or payload.get("recovery_expected_fence") != expected_fence):
            raise DomainError("idempotency_conflict", "This job already has a different recovery authorization.")
        child = store._row(conn, principal, brain_id, payload["recovery_job_id"])
        if (child["kind"] != "consolidate" or not isinstance(child["payload"], dict)
                or child["payload"].get("recovery_parent_job_id") != parent["id"]):
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
    target, start, end = _remaining(parent)
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
    # Authorization covers finite continuation batches; historical reservations remain charged.
    conn.execute(
        "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state,max_model_calls,max_input_bytes) "
        "VALUES(%s,%s,%s,%s,'consolidate',%s,'queued',%s,%s)",
        (*params, identifier, Jsonb(child_payload), DEFAULT_MODEL_CALLS, DEFAULT_INPUT_BYTES),
    )
    conn.execute(
        "UPDATE memory_jobs SET payload=%s,updated_at=clock_timestamp() "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
        (Jsonb({**payload, "recovery_job_id": identifier, "recovery_request_fingerprint": fingerprint,
                "recovery_expected_fence": expected_fence}), *params, job_id),
    )
    return store._public(store._row(conn, principal, brain_id, identifier))
