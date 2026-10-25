from psycopg.types.json import Jsonb

from .helpers import DomainError, digest
from .learning_allocations import (
    AUTHORITY_FIELDS,
    CONTRACT_VERSION,
    ENCODING_PHASE,
    ENCODING_REPLAN_VERSION,
    PHASE_BYTES,
    PHASE_CALLS,
    RECOVERY_POLICY,
    allocation_metadata,
    allocation_root,
    encoding_window,
    phase,
    unreserved_encoding_root,
)
from .learning_funding import ordinary_root_metadata
from .semantic import COMMUNICATION_INTENT_INPUT_VERSION
from .turns import _identifier

WINDOW_SIZE = 8
SAMPLING_RESERVE_BYTES = 2048


def bounds(payload):
    start, end = payload["from_sequence"], payload["through_sequence"]
    if any(type(value) is not int or not 1 <= value <= 2**63 - 1 for value in (start, end)) or start > end:
        raise ValueError("Invalid learning window")
    return start, end


def pending_window(payload):
    start, end = bounds(payload)
    return {"from_sequence": start, "through_sequence": end,
            "attempted_through_sequence": start - 1, "processed_through_sequence": start - 1,
            "has_more": True, "processing_succeeded": False, "status": "pending"}


def enqueue_window(conn, service, principal, brain, target, start, end, *, coalesce=False, origin=None,
                   lineage=None, learning_contract_version=None):
    if learning_contract_version not in (None, CONTRACT_VERSION):
        raise DomainError("dependency_unavailable", "Learning contract version is unsupported.")
    if any(key in (lineage or {}) for key in AUTHORITY_FIELDS):
        raise DomainError("dependency_unavailable", "Encoding allocation must be created from its own window.")
    payload = {"target": {key: target[key] for key in ("conversation_ref", "branch_ref")},
               "from_sequence": start, "through_sequence": end, **(lineage or {})}
    bounds(payload)
    params = service._params(principal, brain["id"])
    if isinstance(origin, str):
        parent = conn.execute(
            "SELECT payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, origin),
        ).fetchone()
        if parent and phase(parent["payload"]) != (ENCODING_PHASE if learning_contract_version else None):
            raise DomainError("stage_conflict", "Encoding continuation cannot change its learning contract.")
    if coalesce:
        rows = conn.execute(
            "SELECT id,payload,state,attempts FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND kind='consolidate' AND payload #>> '{target,conversation_ref}'=%s "
            "AND payload #>> '{target,branch_ref}'=%s "
            "ORDER BY (payload->>'through_sequence')::bigint DESC,created_at DESC,id LIMIT 1 FOR UPDATE",
            (*params, target["conversation_ref"], target["branch_ref"]),
        ).fetchone()
        if (rows and rows["state"] == "queued" and rows["attempts"] == 0
                and not any(key in rows["payload"] for key in (
                    "recovery_parent_job_id", "recovery_root_job_id", "allocation_input_fingerprint"))
                and phase(rows["payload"]) == (ENCODING_PHASE if learning_contract_version else None)):
            previous = rows["payload"]
            payload = {**previous, **payload}
            payload["from_sequence"] = min(previous.get("from_sequence", previous["through_sequence"]), start)
            payload["through_sequence"] = max(previous["through_sequence"], end)
            conn.execute(
                "UPDATE memory_jobs SET payload=%s,updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb(payload), *params, rows["id"]),
            )
            return rows["id"]
    identity = {key: payload[key] for key in ("target", "from_sequence", "through_sequence")}
    identifier = "consolidate:" + digest([brain["id"], identity, origin])
    if learning_contract_version is not None:
        identifier = "consolidate:" + digest([brain["id"], identity, origin, learning_contract_version])
        payload.update(allocation_metadata(identifier, recovery="recovery_budget" in payload))
        if "recovery_budget" not in payload:
            payload.update(ordinary_root_metadata(service._scope(principal, brain, target)))
            if isinstance(origin, str) and parent is not None:
                authority = conn.execute(
                    "SELECT payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                    (*params, parent["payload"].get("authorization_root_job_id")),
                ).fetchone()
                if authority is None or "history_funding_permission" not in authority["payload"]:
                    payload.pop("history_funding_permission", None)
                if "history_attention_policy" in parent["payload"]:
                    payload["history_attention_policy"] = parent["payload"]["history_attention_policy"]
                else:
                    payload.pop("history_attention_policy", None)
    budget = payload.get("recovery_budget", {})
    if learning_contract_version is not None:
        budget = {"max_model_calls": PHASE_CALLS, "max_input_bytes": PHASE_BYTES}
    conn.execute(
        "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state,max_model_calls,max_input_bytes) "
        "VALUES(%s,%s,%s,%s,'consolidate',%s,'queued',%s,%s) ON CONFLICT DO NOTHING",
        (*params, identifier, Jsonb(payload), budget.get("max_model_calls"), budget.get("max_input_bytes")),
    )
    return identifier


def select_window(conn, service, principal, brain, payload, *, policy,
                  input_contract_version=COMMUNICATION_INTENT_INPUT_VERSION):
    from .semantic import PROPOSAL_SCHEMA, proposal_data, request_payload

    start, end = bounds(payload)
    target = payload["target"]
    count = min(WINDOW_SIZE, policy.max_sources)
    rows = conn.execute(
        "SELECT id,revision,sequence,document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND conversation_id=%s AND branch_id=%s AND sequence BETWEEN %s AND %s "
        "AND is_current AND NOT retired AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
        "AND document #>> '{observed,delivery_state}' IN ('received','delivered','partially_delivered') "
        "AND document #>> '{observed,event_kind}' NOT IN ('source_correction','delivery_receipt') "
        "ORDER BY sequence,id LIMIT %s",
        (*service._params(principal, brain["id"]), target["conversation_ref"], target["branch_ref"],
         start, end, count + 1),
    ).fetchall()
    selected = rows[:count]
    blocked = {(reference["event_id"], reference["event_revision"], reference["source_content_sha256"])
               for reference in service._superseded_evidence(
        conn, principal, brain, [row["id"] for row in selected])}
    events = []
    for index, row in enumerate(selected):
        if (row["id"], row["revision"], row["document"]["content_sha256"]) in blocked:
            continue
        events.append(service._rebind(row["document"], principal, brain))
        working = service._working(conn, principal, brain, target, events)
        data = proposal_data(events, (), working, (), {}, (),
                             learning_input_refs=[{"event_id": event["event_id"], "revision": event["revision"]}
                                                  for event in events],
                             input_contract_version=input_contract_version)
        size = request_payload("proposal", data, PROPOSAL_SCHEMA, policy.max_output_tokens,
                               input_contract_version=input_contract_version)[1]
        if size + SAMPLING_RESERVE_BYTES > min(policy.max_request_bytes, policy.max_input_bytes):
            selected = selected[:index if len(events) > 1 else index + 1]
            break
    through = selected[-1]["sequence"] if len(rows) > len(selected) else end
    available = [row for row in selected
                 if (row["id"], row["revision"], row["document"]["content_sha256"]) not in blocked]
    window = {**pending_window(payload), "attempted_through_sequence": through,
              "input_count": len(available), "skipped_unavailable_count": len(selected) - len(available),
              "status": "attempting"}
    references = [{"event_id": row["id"], "revision": row["revision"]} for row in available]
    return references, window


def record_progress(conn, service, principal, brain, job, window, *, succeeded):
    selected_phase = phase(job["payload"])
    if selected_phase not in (None, ENCODING_PHASE):
        raise DomainError("stage_conflict", "History integration does not advance an encoding window.")
    window = {**window, "processing_succeeded": succeeded,
              "status": "processed" if succeeded else "deferred"}
    if succeeded:
        window["processed_through_sequence"] = window["attempted_through_sequence"]
    window["has_more"] = window["processed_through_sequence"] < window["through_sequence"]
    if succeeded:
        saved = conn.execute(
            "SELECT payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s FOR UPDATE",
            (*service._params(principal, brain["id"]), job["id"]),
        ).fetchone()
        if (saved is None or phase(saved["payload"]) != selected_phase
                or bounds(saved["payload"]) != bounds(job["payload"])):
            raise DomainError("context_stale", "Encoding progress no longer matches its input window.")
        payload = {**saved["payload"], "processed_through_sequence": window["processed_through_sequence"]}
        if window["has_more"]:
            lineage = {"continuation_parent_job_id": job["id"]}
            if "recovery_root_job_id" in payload:
                lineage["recovery_root_job_id"] = payload["recovery_root_job_id"]
            if "recovery_budget" in payload:
                lineage["recovery_budget"] = payload["recovery_budget"]
            payload["continuation_job_id"] = enqueue_window(
                conn, service, principal, brain, payload["target"],
                window["processed_through_sequence"] + 1, window["through_sequence"], origin=job["id"],
                lineage=lineage,
                learning_contract_version=CONTRACT_VERSION if selected_phase else None,
            )
        conn.execute(
            "UPDATE memory_jobs SET payload=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb(payload), *service._params(principal, brain["id"]), job["id"]),
        )
    return window


def recover_windows(conn, service, principal, brain):
    _replan_unreserved_encoding(conn, service, principal, brain)
    _continue_invalidated_encoding(conn, service, principal, brain)
    params = service._params(principal, brain["id"])
    rows = conn.execute(
        "SELECT id,payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND payload ? 'from_sequence' AND NOT payload ? 'recovery_job_id' "
        "AND NOT payload ?| %s::text[] "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id) "
        "AND (state='superseded' OR (state='cancelled' AND last_error IN ('source_unavailable','context_stale'))) "
        "ORDER BY created_at,id LIMIT 8 FOR UPDATE",
        (*params, list(AUTHORITY_FIELDS)),
    ).fetchall()
    for row in rows:
        payload = row["payload"]
        try:
            start, end = bounds(payload)
            target = {key: _identifier(payload["target"][key]) for key in ("conversation_ref", "branch_ref")}
            processed = payload.get("processed_through_sequence", start - 1)
            if type(processed) is not int or not start - 1 <= processed <= end:
                continue
            if processed >= start:
                end = processed
            if "recovery_root_job_id" in payload:
                _identifier(payload["recovery_root_job_id"])
            if "recovery_budget" in payload and payload["recovery_budget"] != {
                    "max_model_calls": 2, "max_input_bytes": 196608}:
                continue
        except (DomainError, KeyError, TypeError, ValueError):
            continue
        lineage = {"recovery_parent_job_id": row["id"]}
        if "recovery_budget" in payload:
            lineage["recovery_budget"] = payload["recovery_budget"]
        replacement = enqueue_window(conn, service, principal, brain, target, start, end,
                                     origin=["recovery", row["id"]],
                                     lineage=lineage)
        conn.execute(
            "UPDATE memory_jobs SET payload=payload || %s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb({"recovery_root_job_id": payload.get("recovery_root_job_id", replacement)}), *params, replacement),
        )
        conn.execute(
            "UPDATE memory_jobs SET payload=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb({**payload, "recovery_job_id": replacement}), *params, row["id"]),
        )


def _continue_invalidated_encoding(conn, service, principal, brain):
    params = service._params(principal, brain["id"])
    rows = conn.execute(
        "SELECT id,kind,payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND payload->>'learning_contract_version'=%s "
        "AND payload->>'learning_phase'='encoding' AND payload ? 'encoding_window_snapshot' "
        "AND NOT payload ?| ARRAY['recovery_job_id','continuation_job_id'] "
        "AND (state='superseded' OR state='cancelled' AND last_error IN ('source_unavailable','context_stale')) "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id) ORDER BY created_at,id LIMIT 8 FOR UPDATE",
        (*params, CONTRACT_VERSION),
    ).fetchall()
    for row in rows:
        payload = row["payload"]
        try:
            allocation_root(conn, params, row)
            marker = encoding_window(payload)
            start, end = bounds(payload)
            attempted = marker["attempted_through_sequence"]
            processed = payload.get("processed_through_sequence", start - 1)
            target = {key: _identifier(payload["target"][key]) for key in ("conversation_ref", "branch_ref")}
            if (type(processed) is not int or processed not in {start - 1, attempted}
                    or "allocation_input_fingerprint" not in payload or attempted >= end):
                continue
            if processed < start and not conn.execute(
                "SELECT 1 FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s "
                "AND brain_id=%s AND job_id=%s LIMIT 1", (*params, row["id"]),
            ).fetchone():
                continue
            lineage = {"continuation_parent_job_id": row["id"]}
            if "recovery_budget" in payload:
                lineage["recovery_budget"] = payload["recovery_budget"]
                lineage["recovery_root_job_id"] = payload["recovery_root_job_id"]
            child = enqueue_window(
                conn, service, principal, brain, target, attempted + 1, end,
                origin=row["id"], lineage=lineage, learning_contract_version=CONTRACT_VERSION,
            )
            conn.execute(
                "UPDATE memory_jobs SET payload=payload || %s "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb({"continuation_job_id": child}), *params, row["id"]),
            )
        except (DomainError, KeyError, TypeError, ValueError):
            continue


def _replan_unreserved_encoding(conn, service, principal, brain):
    params = service._params(principal, brain["id"])
    rows = conn.execute(
        "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND payload->>'learning_contract_version'=%s "
        "AND payload->>'learning_phase'='encoding' AND model_calls=0 AND input_bytes=0 "
        "AND NOT payload ?| ARRAY['recovery_job_id','continuation_job_id','recovery_input_fingerprint',"
        "'recovery_policy_refresh'] "
        "AND (state='superseded' OR state='cancelled' AND last_error IN ('source_unavailable','context_stale')) "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id) ORDER BY created_at,id LIMIT 8 FOR UPDATE",
        (*params, CONTRACT_VERSION),
    ).fetchall()
    for old in rows:
        payload = old["payload"]
        try:
            if phase(payload) != ENCODING_PHASE:
                continue
            start, end = bounds(payload)
            processed = payload.get("processed_through_sequence", start - 1)
            if type(processed) is not int or processed != start - 1:
                continue
            root = unreserved_encoding_root(conn, params, old)
            if root is None:
                continue
            root_id = root["id"]
            replacement = "consolidate:" + digest([ENCODING_REPLAN_VERSION, old["id"], root_id])
            metadata = {"target": payload["target"], "from_sequence": start, "through_sequence": end,
                        "encoding_replan_version": ENCODING_REPLAN_VERSION, "recovery_parent_job_id": old["id"],
                        **allocation_metadata(replacement, root_job_id=root_id,
                                              recovery=payload["allocation_policy"] == RECOVERY_POLICY)}
            if "history_attention_policy" in payload:
                metadata["history_attention_policy"] = payload["history_attention_policy"]
            conn.execute(
                "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,state,payload,max_model_calls,max_input_bytes) "
                "VALUES(%s,%s,%s,%s,'consolidate','queued',%s,%s,%s) ON CONFLICT DO NOTHING",
                (*params, replacement, Jsonb(metadata), PHASE_CALLS, PHASE_BYTES),
            )
            conn.execute(
                "UPDATE memory_jobs SET payload=payload || %s "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb({"recovery_job_id": replacement}), *params, old["id"]),
            )
        except (DomainError, KeyError, TypeError, ValueError):
            continue
