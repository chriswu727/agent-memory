from psycopg.types.json import Jsonb

from .helpers import digest
from .semantic import EPISODE_SCOPE_INPUT_VERSION

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


def enqueue_window(conn, service, principal, brain, target, start, end, *, coalesce=False, origin=None):
    payload = {"target": {key: target[key] for key in ("conversation_ref", "branch_ref")},
               "from_sequence": start, "through_sequence": end}
    bounds(payload)
    params = service._params(principal, brain["id"])
    if coalesce:
        rows = conn.execute(
            "SELECT id,payload,state,attempts FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND kind='consolidate' AND payload #>> '{target,conversation_ref}'=%s "
            "AND payload #>> '{target,branch_ref}'=%s "
            "ORDER BY (payload->>'through_sequence')::bigint DESC,created_at DESC,id LIMIT 1 FOR UPDATE",
            (*params, target["conversation_ref"], target["branch_ref"]),
        ).fetchone()
        if rows and rows["state"] == "queued" and rows["attempts"] == 0:
            previous = rows["payload"]
            payload["from_sequence"] = min(previous.get("from_sequence", previous["through_sequence"]), start)
            payload["through_sequence"] = max(previous["through_sequence"], end)
            conn.execute(
                "UPDATE memory_jobs SET payload=%s,updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb(payload), *params, rows["id"]),
            )
            return rows["id"]
    identifier = "consolidate:" + digest([brain["id"], payload, origin])
    conn.execute(
        "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state) "
        "VALUES(%s,%s,%s,%s,'consolidate',%s,'queued') ON CONFLICT DO NOTHING",
        (*params, identifier, Jsonb(payload)),
    )
    return identifier


def select_window(conn, service, principal, brain, payload, *, policy,
                  input_contract_version=EPISODE_SCOPE_INPUT_VERSION):
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
    blocked = {reference["event_id"] for reference in service._superseded_evidence(
        conn, principal, brain, [row["id"] for row in selected])}
    events = []
    for index, row in enumerate(selected):
        if row["id"] in blocked:
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
    available = [row for row in selected if row["id"] not in blocked]
    window = {**pending_window(payload), "attempted_through_sequence": through,
              "input_count": len(available), "skipped_unavailable_count": len(selected) - len(available),
              "status": "attempting"}
    references = [{"event_id": row["id"], "revision": row["revision"]} for row in available]
    return references, window


def record_progress(conn, service, principal, brain, job, window, *, succeeded):
    window = {**window, "processing_succeeded": succeeded,
              "status": "processed" if succeeded else "deferred"}
    if succeeded:
        window["processed_through_sequence"] = window["attempted_through_sequence"]
    window["has_more"] = window["processed_through_sequence"] < window["through_sequence"]
    if succeeded:
        payload = {**job["payload"], "processed_through_sequence": window["processed_through_sequence"]}
        if window["has_more"]:
            payload["continuation_job_id"] = enqueue_window(
                conn, service, principal, brain, payload["target"],
                window["processed_through_sequence"] + 1, window["through_sequence"], origin=job["id"],
            )
        conn.execute(
            "UPDATE memory_jobs SET payload=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb(payload), *service._params(principal, brain["id"]), job["id"]),
        )
    return window


def recover_windows(conn, service, principal, brain):
    params = service._params(principal, brain["id"])
    rows = conn.execute(
        "SELECT id,payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND payload ? 'from_sequence' AND NOT payload ? 'recovery_job_id' "
        "AND (state='superseded' OR (state='cancelled' AND last_error IN ('source_unavailable','context_stale'))) "
        "ORDER BY created_at,id LIMIT 8 FOR UPDATE",
        params,
    ).fetchall()
    for row in rows:
        payload = row["payload"]
        start, end = bounds(payload)
        end = payload.get("processed_through_sequence", end)
        replacement = enqueue_window(conn, service, principal, brain, payload["target"], start, end,
                                     origin=["recovery", row["id"]])
        conn.execute(
            "UPDATE memory_jobs SET payload=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb({**payload, "recovery_job_id": replacement}), *params, row["id"]),
        )
