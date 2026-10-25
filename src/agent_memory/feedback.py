from datetime import datetime

from psycopg.types.json import Jsonb

from . import cognition
from .delivery_receipts import recorded_delivery_receipt
from .helpers import digest
from .retrieval import resolve_dependency_closure

ATTRIBUTION_VERSION = "sole-exact-statement-v1"
ACCESS_VERSION = "contextual-access-v2"


def _source_proof(conn, service, principal, brain, target, memory):
    params = service._params(principal, brain["id"])

    def fetch(versions):
        rows = conn.execute(
            "SELECT document,is_current FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND (id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
            "AND NOT invalidated AND NOT deleted AND NOT suppressed AND document IS NOT NULL",
            (*params, [key[0] for key in versions], [key[1] for key in versions]),
        ).fetchall()
        result = []
        for row in rows:
            value = service._rebind(row["document"], principal, brain)
            if not row["is_current"]:
                value["lifecycle"] = "superseded"
            result.append(value)
        return result

    closure = resolve_dependency_closure([memory], fetch, max_nodes=256, max_depth=8)
    if closure.truncated or closure.missing_versions or closure.cycle_versions:
        return None
    memories = {(item["memory_id"], item["revision"]): item for item in closure.memories}
    event_ids = {ref["event_id"] for item in closure.memories
                 for ref in item["evidence_refs"] + item.get("counterexamples", [])}
    event_ids.update(identifier for item in closure.memories for identifier in item.get("root_event_ids", []))
    if len(event_ids) > 1024:
        return None
    events = {item["event_id"]: item for item in service._events(
        conn, principal, brain, event_ids=sorted(event_ids), limit=1024,
    )}
    if set(events) != event_ids:
        return None
    if not cognition._evidence_valid(
        memory, memories, events, service._scope(principal, brain, target), brain["dependency_generation"],
        None, set(), False, "exact_quote", valid_dependency_versions=frozenset(memories),
        superseded_evidence_refs=tuple(service._superseded_evidence(conn, principal, brain, sorted(event_ids))),
    ):
        return None
    return {"events": list(events.values()), "memories": sorted({key[0] for key in memories})}


def _sole_statement(text, statement):
    if not isinstance(text, str) or not isinstance(statement, str) or not statement:
        return False
    choices = {statement}
    choices.update(start + statement + end for start, end in (
        ('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"),
    ))
    return text.strip() in choices


def _selected_statements(conn, service, principal, brain, selected):
    versions = {(item["document"]["memory_id"], item["document"]["memory_revision"]) for item in selected}
    rows = conn.execute(
        "SELECT id,revision,document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND (id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
        "AND is_current AND NOT invalidated AND NOT deleted AND NOT suppressed AND document IS NOT NULL",
        (*service._params(principal, brain["id"]), [key[0] for key in versions], [key[1] for key in versions]),
    ).fetchall()
    statements = {(row["id"], row["revision"]): row["document"]["statement"] for row in rows}
    missing = versions - statements.keys()
    if missing:
        event_ids = {ref["event_id"] for item in selected
                     if (item["document"]["memory_id"], item["document"]["memory_revision"]) in missing
                     for ref in item["document"]["evidence_refs"]}
        if len(event_ids) > 1024:
            return None
        for event in service._events(conn, principal, brain, event_ids=sorted(event_ids), limit=1024):
            for memory in cognition.extract_event(event):
                key = memory["memory_id"], memory["revision"]
                if key in missing:
                    statements[key] = memory["statement"]
    return statements if set(statements) == versions else None


def _single_prepared_selection(conn, params, turn, selected, delivered_at):
    groups = conn.execute(
        "SELECT DISTINCT document->>'recall_id' AS recall_id FROM memory_traces "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND document->>'turn_id'=%s "
        "AND document->>'kind' IN ('context_selected','quoted') "
        "AND (document->>'recorded_at')::timestamptz<%s LIMIT 2",
        (*params, turn["turn_id"], delivered_at),
    ).fetchall()
    if len(groups) != 1 or groups[0]["recall_id"] != selected[0]["document"]["recall_id"]:
        return False
    packets = conn.execute(
        "SELECT response #> '{result,recall_traces}' AS traces FROM memory_requests "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND action='prepare' "
        "AND response #>> '{result,turn_id}'=%s "
        "AND response #>> '{result,scope,conversation_id}'=%s AND response #>> '{result,scope,branch_id}'=%s "
        "AND (response #>> '{result,created_at}')::timestamptz<%s LIMIT 2",
        (*params, turn["turn_id"], turn["conversation_id"], turn["branch_id"], delivered_at),
    ).fetchall()
    return len(packets) == 1 and {trace["trace_id"] for trace in packets[0]["traces"]} == {
        item["id"] for item in selected}


def _delivered_use(conn, service, principal, brain, target, memory, proposal, event):
    params = service._params(principal, brain["id"])
    selected = conn.execute(
        "SELECT id,document FROM memory_traces WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND document->>'recall_id'=%s AND document->>'kind' IN ('context_selected','quoted') "
        "AND document #>> '{scope,conversation_id}'=%s AND document #>> '{scope,branch_id}'=%s "
        "ORDER BY id LIMIT 65",
        (*params, proposal["recall_id"], target.get("conversation_ref"), target.get("branch_ref")),
    ).fetchall()
    if not selected or len(selected) == 65:
        return None
    if any(type(item["document"].get("memory_revision")) is not int for item in selected):
        return None
    matching = [item for item in selected if (item["document"]["memory_id"], item["document"]["memory_revision"])
                == (memory["memory_id"], memory["revision"])]
    if not matching:
        return None
    turn_ids = {item["document"].get("turn_id") for item in selected}
    if len(turn_ids) != 1 or None in turn_ids:
        return None
    used = matching[0]
    if any(cognition._stamp(event["system_time"]["recorded_at"]) <= cognition._stamp(
            item["document"]["recorded_at"]) for item in selected):
        return None
    turn = conn.execute(
        "SELECT * FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND turn_id=%s AND conversation_id=%s AND branch_id=%s",
        (*params, used["document"]["turn_id"], target.get("conversation_ref"), target.get("branch_ref")),
    ).fetchone()
    if not turn or (turn["stage"] not in {"delivered", "committed", "completed"}
                    and not recorded_delivery_receipt(turn)):
        return None
    preceding = conn.execute(
        "SELECT id,revision,sequence,document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND conversation_id=%s AND branch_id=%s AND sequence<%s AND is_current AND NOT retired AND NOT suppressed "
        "AND NOT deleted AND document #>> '{observed,actor_role}'='assistant' "
        "AND document #>> '{observed,delivery_state}' IN ('delivered','partially_delivered') "
        "ORDER BY sequence DESC LIMIT 1",
        (*params, target.get("conversation_ref"), target.get("branch_ref"), event["sequence"]),
    ).fetchone()
    if not preceding or turn["output_event_refs"] != [{"event_id": preceding["id"], "revision": preceding["revision"]}]:
        return None
    output = preceding["document"]
    delivered_at = cognition._stamp(output["system_time"]["recorded_at"])
    if any(cognition._stamp(item["document"]["recorded_at"]) >= delivered_at for item in selected):
        return None
    if not _single_prepared_selection(conn, params, turn, selected, delivered_at):
        return None
    if any({"event_id": ref["event_id"], "revision": ref["event_revision"]} not in turn["context_event_refs"]
           for item in matching for ref in item["document"]["evidence_refs"]):
        return None
    if (output["origin"] != "assistant_output" or output["origin_verification"] == "unverified_import"
            or output["content_sha256"] != digest(output["observed"]["content"])
            or not _sole_statement(output["observed"]["content"].get("text"), memory["statement"])):
        return None
    statements = _selected_statements(conn, service, principal, brain, selected)
    if statements is None or {key for key, statement in statements.items()
                              if _sole_statement(output["observed"]["content"].get("text"), statement)} != {
                                  (memory["memory_id"], memory["revision"])}:
        return None
    intervening = conn.execute(
        "SELECT 1 FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND conversation_id=%s AND branch_id=%s AND sequence>%s AND sequence<%s AND is_current "
        "AND NOT retired AND NOT deleted AND NOT suppressed AND document #>> '{observed,delivery_state}'='received' "
        "AND document #>> '{observed,actor_role}' IN ('user','external','operator') LIMIT 1",
        (*params, target.get("conversation_ref"), target.get("branch_ref"), preceding["sequence"], event["sequence"]),
    ).fetchone()
    if intervening:
        return None
    sources = _source_proof(conn, service, principal, brain, target, memory)
    return {"selection": used, "output": output, **sources} if sources else None


def recompute_access(conn, params, versions):
    versions = sorted(set(versions))
    if not versions:
        return
    ids, revisions = [key[0] for key in versions], [key[1] for key in versions]
    conn.execute(
        "INSERT INTO memory_access_state(tenant_id,owner_id,brain_id,memory_id,memory_revision,external_uses,"
        "successes,failures,last_external_use_at,last_reinforcement_at,last_feedback_event_id,policy_version) "
        "SELECT tenant_id,owner_id,brain_id,memory_id,memory_revision,count(*),"
        "count(*) FILTER (WHERE kind='user_confirmed'),count(*) FILTER (WHERE kind='user_corrected'),"
        "max(observed_at),max(observed_at) FILTER (WHERE kind<>'user_corrected'),"
        "(array_agg(feedback_event_id ORDER BY observed_at DESC,feedback_event_id,feedback_event_revision DESC))[1],%s "
        "FROM memory_feedback_contributions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND (memory_id,memory_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
        "AND invalidated_at IS NULL GROUP BY tenant_id,owner_id,brain_id,memory_id,memory_revision "
        "ON CONFLICT(tenant_id,brain_id,memory_id,memory_revision) DO UPDATE SET "
        "revision=memory_access_state.revision+1,external_uses=EXCLUDED.external_uses,successes=EXCLUDED.successes,"
        "failures=EXCLUDED.failures,last_external_use_at=EXCLUDED.last_external_use_at,"
        "last_reinforcement_at=EXCLUDED.last_reinforcement_at,last_feedback_event_id=EXCLUDED.last_feedback_event_id,"
        "policy_version=EXCLUDED.policy_version",
        (ACCESS_VERSION, *params, ids, revisions),
    )
    conn.execute(
        "DELETE FROM memory_access_state a WHERE a.tenant_id=%s AND a.owner_id=%s AND a.brain_id=%s "
        "AND (a.memory_id,a.memory_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
        "AND NOT EXISTS (SELECT 1 FROM memory_feedback_contributions c WHERE c.tenant_id=a.tenant_id "
        "AND c.owner_id=a.owner_id AND c.brain_id=a.brain_id AND c.memory_id=a.memory_id "
        "AND c.memory_revision=a.memory_revision AND c.invalidated_at IS NULL)",
        (*params, ids, revisions),
    )


def record_feedback(conn, service, principal, brain, target, memory, proposal, event):
    proof = _delivered_use(conn, service, principal, brain, target, memory, proposal, event)
    if proof is None:
        return False
    params = service._params(principal, brain["id"])
    source_events = {item["event_id"]: item for item in [*proof["events"], proof["output"], event]}
    refs = [{"event_id": item["event_id"], "revision": item["revision"], "content_sha256": item["content_sha256"]}
            for item in sorted(source_events.values(), key=lambda item: item["event_id"])]
    result = conn.execute(
        "INSERT INTO memory_feedback_contributions(tenant_id,owner_id,brain_id,memory_id,memory_revision,"
        "feedback_event_id,feedback_event_revision,feedback_content_hash,delivery_event_id,delivery_event_revision,"
        "delivery_content_hash,selection_trace_id,recall_id,turn_id,conversation_id,branch_id,kind,attribution_version,"
        "source_event_refs,source_event_ids,source_memory_ids,observed_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (*params, memory["memory_id"], memory["revision"], event["event_id"], event["revision"], event["content_sha256"],
         proof["output"]["event_id"], proof["output"]["revision"], proof["output"]["content_sha256"],
         proof["selection"]["id"], proposal["recall_id"], proof["selection"]["document"]["turn_id"],
         target["conversation_ref"], target["branch_ref"], proposal["kind"], ATTRIBUTION_VERSION,
         Jsonb(refs), sorted(source_events), proof["memories"], datetime.fromisoformat(event["system_time"]["recorded_at"])),
    )
    if result.rowcount:
        recompute_access(conn, params, [(memory["memory_id"], memory["revision"])])
    return bool(result.rowcount)


def invalidate_contributions(conn, params, *, event_ids=(), memory_ids=()):
    rows = conn.execute(
        "UPDATE memory_feedback_contributions SET invalidated_at=clock_timestamp() "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND invalidated_at IS NULL "
        "AND (source_event_ids && %s::text[] OR source_memory_ids && %s::text[]) RETURNING memory_id,memory_revision",
        (*params, list(event_ids), list(memory_ids)),
    ).fetchall()
    recompute_access(conn, params, [(row["memory_id"], row["memory_revision"]) for row in rows])
