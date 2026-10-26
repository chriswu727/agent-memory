"""Execute existing learning allocations for one delivered session exchange."""

from __future__ import annotations

import copy

from agent_memory.helpers import digest, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import (
    ALLOCATION_POLICY,
    ENCODING_PHASE,
    HISTORY_PHASE,
    allocation_root,
    phase,
)


def _untouched(row):
    return (row["state"] == "queued" and row["available"] is True and not row["has_operations"]
            and all(type(row[key]) is int and row[key] == 0 for key in ("attempts", "model_calls", "input_bytes")))


def _rows(service, principal, brain_id, clause, arguments=()):
    with service.database.transaction(principal) as conn:
        service._brain(conn, principal, brain_id)
        rows = conn.execute(
            "SELECT j.id,j.kind,j.state,j.payload,j.result,j.last_error,j.attempts,j.fence,j.model_calls,j.input_bytes,"
            "j.available_at<=clock_timestamp() AS available,EXISTS (SELECT 1 FROM memory_cognitive_operations o "
            "WHERE o.tenant_id=j.tenant_id AND o.owner_id=j.owner_id AND o.brain_id=j.brain_id "
            "AND o.job_id=j.id) AS has_operations FROM memory_jobs j "
            "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s " + clause
            + " ORDER BY j.created_at,j.id LIMIT 1025", (*service._params(principal, brain_id), *arguments),
        ).fetchall()
    if len(rows) > 1024:
        raise RuntimeError("Session learning metadata exceeds its inspection bound")
    return [dict(row) for row in rows]


def _sources(service, principal, brain_id, conversation_id, current_sources):
    if not isinstance(conversation_id, str) or not conversation_id or not isinstance(current_sources, dict):
        raise ValueError("Learning requires a conversation and delivered source references")
    expected, groups = {}, {}
    for group in ("input_events", "output_events"):
        values = current_sources.get(group)
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise ValueError("Learning requires bounded input and delivered output references")
        for source in values:
            if (not isinstance(source, dict) or not isinstance(source.get("event_id"), str)
                    or not source["event_id"] or type(source.get("revision")) is not int or source["revision"] < 1
                    or source["event_id"] in expected):
                raise ValueError("Learning source references must be distinct exact event versions")
            expected[source["event_id"]], groups[source["event_id"]] = source, group
    with service.database.transaction(principal) as conn:
        service._brain(conn, principal, brain_id)
        rows = conn.execute(
            "SELECT id,revision,sequence,conversation_id,branch_id,document FROM memory_events "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s::text[]) "
            "AND is_current AND NOT retired AND NOT deleted AND NOT suppressed AND document IS NOT NULL",
            (*service._params(principal, brain_id), list(expected)),
        ).fetchall()
    if len(rows) != len(expected) or len({row["branch_id"] for row in rows}) != 1:
        raise ValueError("Learning sources are unavailable or cross a session branch")
    for row in rows:
        source, observed = expected[row["id"]], row["document"]["observed"]
        roles, deliveries = (({"user", "operator"}, {"received"}) if groups[row["id"]] == "input_events"
                             else ({"assistant"}, {"delivered", "partially_delivered"}))
        if (row["conversation_id"] != conversation_id or row["revision"] != source["revision"]
                or observed["actor_role"] not in roles or observed["delivery_state"] not in deliveries
                or any(key in source and source[key] != row["document"][key]
                       for key in ("sequence", "content_sha256"))):
            raise ValueError("Learning sources do not match the persisted delivered exchange")
    return sorted(rows, key=lambda row: row["sequence"])


def _ref(row):
    return {"event_id": row["id"], "revision": row["revision"]}


def _encoding(rows, sources):
    sequences = [row["sequence"] for row in sources]
    selected = []
    for row in rows:
        if phase(row["payload"]) != ENCODING_PHASE:
            continue
        start, end = row["payload"].get("from_sequence"), row["payload"].get("through_sequence")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise RuntimeError("Session encoding window is malformed")
        if any(start <= sequence <= end for sequence in sequences):
            selected.append(row)
    return selected


def _coverage(rows, sources):
    covered, owners = set(), []
    expected = {(row["id"], row["revision"]) for row in sources}
    for row in rows:
        result = row["result"] if isinstance(row["result"], dict) else {}
        if (row["state"] != "completed" or result.get("status") not in {"completed", "partial"}
                or result.get("learning_window", {}).get("processing_succeeded") is not True):
            continue
        references = {(item["event_id"], item["revision"]) for item in result.get("learning_input_refs", [])}
        if references & expected:
            owners.append(row["id"])
            covered.update(references & expected)
    return {"input_refs": [_ref(row) for row in sources], "job_ids": owners,
            "covered_refs": [_ref(row) for row in sources if (row["id"], row["revision"]) in covered],
            "uncovered_refs": [_ref(row) for row in sources if (row["id"], row["revision"]) not in covered],
            "complete": covered == expected}


def _literal(service, principal, brain_id, candidate, cutoff):
    payload = candidate["payload"]
    with service.database.transaction(principal) as conn:
        sources = conn.execute(
            "SELECT id,revision FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND conversation_id=%s AND branch_id=%s AND sequence BETWEEN %s AND %s "
            "AND is_current AND NOT retired AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
            "AND document #>> '{observed,delivery_state}' IN ('received','delivered','partially_delivered')",
            (*service._params(principal, brain_id), payload["target"]["conversation_ref"],
             payload["target"]["branch_ref"], payload["from_sequence"], min(payload["through_sequence"], cutoff)),
        ).fetchall()
    versions = {row["id"]: row["revision"] for row in sources}
    rows = _rows(service, principal, brain_id,
                 "AND j.kind='extract' AND j.payload->>'event_id'=ANY(%s::text[])", (list(versions),))
    if any(type(row["payload"].get("revision")) is not int or not
           1 <= row["payload"]["revision"] <= versions[row["payload"]["event_id"]] for row in rows):
        raise RuntimeError("Literal extraction exceeds the persisted session source boundary")
    return [row for row in rows if row["state"] != "completed"]


def _operations(service, principal, brain_id, job_id):
    with service.database.transaction(principal) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT operation_id,fence,request_fingerprint,input_bytes,outcome,usage,response_fingerprint "
            "FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND job_id=%s "
            "ORDER BY operation_id", (*service._params(principal, brain_id), job_id),
        )]


def _failure(row, operations=()):
    if row.get("last_error") == "provider_uncertain" or any(
            item["outcome"] in {"reserved", "uncertain"} for item in operations):
        return "unknown_provider_usage"
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    if result.get("status") in {"budget_exhausted", "provider_unavailable", "invalid_response", "verification_unavailable"}:
        return "cognitive_processing_unavailable"
    if (row.get("execution_error") or row["state"] != "completed"
            or result.get("sampling", {}).get("publication_restricted") is True
            or result.get("learning_window", {}).get("processing_succeeded") is False):
        return "learning_processing_incomplete"
    return None


def _execute(service, principal, brain_id, candidate, boundary, record):
    boundary()
    current = _rows(service, principal, brain_id, "AND j.id=%s", (candidate["id"],))
    if len(current) != 1 or not _untouched(current[0]) or current[0]["payload"] != candidate["payload"]:
        raise RuntimeError("The selected untouched learning job changed before claim")
    if candidate["kind"] == "consolidate":
        with service.database.transaction(principal) as conn:
            root = allocation_root(conn, service._params(principal, brain_id), current[0])
            if root is None or root["payload"]["allocation_policy"] != ALLOCATION_POLICY:
                raise RuntimeError("Session learning cannot create or use explicit recovery authority")
    job = JobStore(service.database).claim(principal, brain_id, worker_id=uid(), job_id=candidate["id"],
                                           kinds=[candidate["kind"]], max_attempts=1, lease_seconds=120)
    if job is None:
        raise RuntimeError("The selected untouched learning job could not be claimed")
    selected_phase = phase(job["payload"]) if job["kind"] == "consolidate" else None
    authority = {"job_id": job["id"], "payload": copy.deepcopy(job["payload"]),
                 "payload_sha256": digest(job["payload"])}
    base = {"job_id": job["id"], "kind": job["kind"], "learning_phase": selected_phase,
            "authorization_root_job_id": job["payload"].get("authorization_root_job_id"),
            "execution_authority": authority, "fence": job["fence"], "attempts": job["attempts"]}
    record({**copy.deepcopy(base), "record_type": "claimed", "state": job["state"]})
    failure, interrupted = None, None
    try:
        if job["payload"] != candidate["payload"]:
            raise RuntimeError("Claimed learning authority differs from its selected snapshot")
        LearningEngine(service)._execute(principal, brain_id, job)
    except BaseException as error:
        failure = {"type": type(error).__name__, "code": getattr(error, "code", None)}
        if not isinstance(error, Exception):
            interrupted = error
    finally:
        final = _rows(service, principal, brain_id, "AND j.id=%s", (job["id"],))[0]
        report = {**base, "record_type": "finished", "execution_error": failure,
                  **{key: final[key] for key in ("state", "result", "last_error", "model_calls", "input_bytes")},
                  "operations": _operations(service, principal, brain_id, job["id"])}
        record(copy.deepcopy(report))
    if interrupted is not None:
        raise interrupted
    return report


def process_learning(service, principal, brain_id, *, conversation_id, current_sources, boundary, record, max_jobs=16):
    if type(max_jobs) is not int or not 1 <= max_jobs <= 64 or not callable(boundary) or not callable(record):
        raise ValueError("Learning requires callbacks and a job bound between one and 64")
    sources = _sources(service, principal, brain_id, conversation_id, current_sources)
    branch, cutoff = sources[0]["branch_id"], max(row["sequence"] for row in sources)
    reports, stop_reason = [], None
    dispatch_limit_reached = False
    while True:
        scoped = _rows(service, principal, brain_id,
                       "AND j.kind='consolidate' AND j.payload #>> '{target,conversation_ref}'=%s "
                       "AND j.payload #>> '{target,branch_ref}'=%s", (conversation_id, branch))
        encoding = _encoding(scoped, sources)
        coverage = _coverage(encoding, sources)
        candidate = None
        if not coverage["complete"]:
            pending = {(item["event_id"], item["revision"]) for item in coverage["uncovered_refs"]}
            sequences = [row["sequence"] for row in sources if (row["id"], row["revision"]) in pending]
            available = [row for row in encoding if _untouched(row) and any(
                row["payload"]["from_sequence"] <= sequence <= row["payload"]["through_sequence"]
                for sequence in sequences)]
            if len(available) != 1 or available[0]["payload"]["through_sequence"] > cutoff:
                attempted = [row for row in encoding if row["attempts"] and row["state"] != "completed"]
                stop_reason = next((failure for row in attempted if (failure := _failure(
                    row, _operations(service, principal, brain_id, row["id"])))), "current_encoding_not_ready")
                break
            candidate = available[0]
            literal = _literal(service, principal, brain_id, candidate, cutoff)
            if any(not _untouched(row) for row in literal):
                stop_reason = "literal_work_previously_attempted"
                break
            if literal:
                candidate = literal[0]
        else:
            roots = sorted({row["payload"]["authorization_root_job_id"] for row in encoding
                            if row["payload"]["through_sequence"] <= cutoff})
            history = _rows(service, principal, brain_id,
                            "AND j.kind='consolidate' AND j.payload->>'learning_phase'=%s "
                            "AND j.payload->>'authorization_root_job_id'=ANY(%s::text[])", (HISTORY_PHASE, roots))
            for row in history:
                if row["attempts"] or row["model_calls"] or row["input_bytes"] or row["has_operations"]:
                    stop_reason = _failure(row, _operations(service, principal, brain_id, row["id"]))
                    if stop_reason:
                        break
            if stop_reason:
                break
            ready = [row for row in history if _untouched(row)]
            candidate = ready[0] if ready else None
            if candidate is not None and (type(candidate["payload"].get("history_cutoff_sequence")) is not int
                                          or candidate["payload"]["history_cutoff_sequence"] > cutoff):
                stop_reason = "history_exceeds_current_source_boundary"
                break
        if candidate is None:
            break
        if len(reports) == max_jobs:
            dispatch_limit_reached = True
            stop_reason = "learning_dispatch_limit"
            break
        report = _execute(service, principal, brain_id, candidate, boundary, record)
        reports.append(report)
        stop_reason = _failure(report, report["operations"])
        if stop_reason:
            break
    scoped = _rows(service, principal, brain_id,
                   "AND j.kind='consolidate' AND j.payload #>> '{target,conversation_ref}'=%s "
                   "AND j.payload #>> '{target,branch_ref}'=%s", (conversation_id, branch))
    return {"jobs": reports, "encoding_coverage": _coverage(_encoding(scoped, sources), sources),
            "pending_debt": JobStore(service.database).learning_progress(principal, brain_id,
                                                                          configured=service.semantic_processor is not None),
            "stop_reason": stop_reason, "dispatch_limit_reached": dispatch_limit_reached}
