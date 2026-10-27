import copy
import hashlib
import json

from . import cognition
from .contracts import validate
from .helpers import DomainError, now

VERSION = "prepared-memory-selection-v1"
MAX_MEMORIES = 64
MAX_BINDING_BYTES = 131072


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitize_prepared_selection(value):
    if value is None:
        return None
    try:
        validate("PreparedMemorySelection", value)
        if len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()) > MAX_BINDING_BYTES:
            return None
        if value["state"] == "unavailable" and value["unavailable_reason"] == "custom_context":
            return copy.deepcopy(value)
        references = value["input_event_refs"] + value["source_event_refs"]
        if (not value["input_event_refs"] or any(type(ref["revision"]) is not int for ref in references)
                or any(type(value[key]) is not int for key in ("context_generation", "working_revision"))
                or any(len({ref["event_id"] for ref in value[key]}) != len(value[key])
                       for key in ("input_event_refs", "source_event_refs"))
                or len({item["memory_id"] for item in value["memories"]}) != len(value["memories"])):
            return None
        sources = {(ref["event_id"], ref["revision"], ref["content_sha256"]) for ref in value["source_event_refs"]}
        for memory in value["memories"]:
            if (type(memory["memory_revision"]) is not int or not memory["evidence_refs"]
                    or any(type(ref["event_revision"]) is not int
                           or (ref["event_id"], ref["event_revision"], ref["source_content_sha256"]) not in sources
                           for ref in memory["evidence_refs"])):
                return None
    except (DomainError, TypeError, ValueError, UnicodeError):
        return None
    return copy.deepcopy(value)


def selection_for_turn(row):
    selection = sanitize_prepared_selection(row.get("prepared_memory_selection"))
    if (selection is None or selection["state"] != "complete"
            or selection["turn_id"] != row["turn_id"]
            or selection["context_fingerprint"] != row["context_fingerprint"]
            or selection["context_generation"] != row["context_generation"]
            or any(selection[key] != row[key] for key in ("brain_id", "conversation_id", "branch_id"))
            or selection["input_event_refs"] != row["input_event_refs"]
            or any({"event_id": ref["event_id"], "revision": ref["revision"]} not in row["context_event_refs"]
                   for ref in selection["source_event_refs"])):
        return None
    return selection


def bind_prepared_selection(conn, service, principal, brain, turn, packet_id, context_sha256):
    rows = conn.execute(
        "SELECT response->'result' AS packet FROM memory_requests WHERE tenant_id=%s AND owner_id=%s "
        "AND brain_id=%s AND action='prepare' AND response #>> '{result,packet_id}'=%s LIMIT 2",
        (*service._params(principal, brain["id"]), packet_id),
    ).fetchall()
    if len(rows) != 1:
        raise DomainError("context_stale", "The server-issued prepared context is unavailable.")
    packet = rows[0]["packet"]
    validate("ContextPacket", packet)
    scope = packet["scope"]
    if (packet["turn_id"] != turn["turn_id"]
            or any(scope[key] != value for key, value in (
                ("tenant_id", principal.tenant_id), ("principal_binding_id", principal.principal_id),
                ("brain_id", brain["id"]), ("conversation_id", turn["conversation_id"]),
                ("branch_id", turn["branch_id"]), ("scope_generation", brain["scope_generation"])))
            or packet["coverage"]["dependency_generation"] != turn["context_generation"]
            or packet["coverage"]["dependency_generation"] != brain["dependency_generation"]
            or packet["working_state"]["revision"] != turn["working_revision"]
            or sorted(packet["retrieval_snapshot"]["query_event_ids"]) != sorted(
                ref["event_id"] for ref in turn["input_event_refs"])
            or not packet["coverage"]["read_fence_satisfied"]):
        raise DomainError("context_stale", "Prepared selection differs from the bound turn context.")
    context = cognition.render_context(packet)
    if packet.get("model_context", context) != context or text_hash(context) != context_sha256:
        raise DomainError("invalid_evidence", "Prepared selection must match the exact model memory context.")
    references = [ref for block in packet["blocks"] for ref in block["evidence_refs"]]
    if any({"event_id": ref["event_id"], "revision": ref["event_revision"]} not in turn["context_event_refs"]
           for ref in references):
        raise DomainError("invalid_evidence", "Prepared selection sources must be bound to the turn.")
    source_ids = sorted({ref["event_id"] for ref in references})
    events = service._events(conn, principal, brain, event_ids=source_ids, limit=64)
    service._evidence(references, events)
    sources = [{"event_id": event["event_id"], "revision": event["revision"],
                "content_sha256": event["content_sha256"]} for event in events]
    selection = {
        "version": VERSION, "state": "complete", "unavailable_reason": None,
        "turn_id": turn["turn_id"], "brain_id": brain["id"], "conversation_id": turn["conversation_id"],
        "branch_id": turn["branch_id"], "packet_id": packet_id, "model_context_sha256": context_sha256,
        "context_fingerprint": turn["context_fingerprint"], "context_generation": turn["context_generation"],
        "working_revision": turn["working_revision"], "prepared_at": now(),
        "input_event_refs": copy.deepcopy(turn["input_event_refs"]), "source_event_refs": sources,
        "memories": [],
    }
    versions = {(item["memory"]["memory_id"], item["memory"]["revision"]): item["memory"]
                for result in packet["recall_results"] for item in result["items"]}
    seen = set()
    for trace in packet["recall_traces"]:
        key = trace["memory_id"], trace.get("memory_revision")
        if (type(key[1]) is not int or key not in versions
                or trace["turn_id"] != turn["turn_id"] or trace["kind"] != "context_selected"
                or trace["source"] != "server_instrumentation" or trace["scope"] != scope
                or not trace["evidence_refs"] or any(ref not in references for ref in trace["evidence_refs"])):
            selection.update(state="unavailable", unavailable_reason="selection_unverifiable", memories=[])
            break
        if key in seen:
            continue
        seen.add(key)
        memory = versions[key]
        selection["memories"].append({
            "memory_id": key[0], "memory_revision": key[1], "statement_sha256": text_hash(memory["statement"]),
            "trace_id": trace["trace_id"], "recall_id": trace["recall_id"],
            "evidence_refs": copy.deepcopy(trace["evidence_refs"]),
        })
        if len(seen) > MAX_MEMORIES:
            selection.update(state="unavailable", unavailable_reason="selection_limit", memories=[])
            break
    if len(json.dumps(selection, separators=(",", ":"), ensure_ascii=False).encode()) > MAX_BINDING_BYTES:
        selection.update(state="unavailable", unavailable_reason="selection_limit", memories=[])
    validate("PreparedMemorySelection", selection)
    if sanitize_prepared_selection(selection) is None:
        selection.update(state="unavailable", unavailable_reason="selection_unverifiable", memories=[])
    return selection
