import copy

import pytest
from psycopg.types.json import Jsonb
from test_candidates import candidates as candidates
from test_candidates import key
from test_cognition import request
from test_learning import learning as learning
from test_learning_neighborhood import literal, publish, select, source, versions

from agent_memory import cognition
from agent_memory.contracts import CONTRACT_VERSION
from agent_memory.helpers import digest, uid
from agent_memory.source_candidates import SOURCE_ROUTE_VERSION

pytestmark = pytest.mark.integration


def derived(candidates, parent, statement="The user reports two incompatible states without resolving either.", **changes):
    memory = copy.deepcopy(parent)
    memory.update(memory_id="semantic-" + uid(), statement=statement, epistemic_status="derived_inference")
    memory["derivation"]["method"] = "aggregation"
    memory.update(changes)
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        return candidates.service._publish(conn, candidates.principal, brain, memory)


def recall(candidates, query):
    response = candidates.service.handle(candidates.principal, {
        "contract_version": CONTRACT_VERSION, "message_type": "request", "request_id": uid(),
        "action": "recall", "target": candidates.target, "payload": request(query),
    })
    assert response["outcome"] == "completed", response
    return response["result"]


def change_row(candidates, table, identifier, **changes):
    assert table in {"memory_assertions", "memory_events"}
    assert set(changes) <= {"document", "is_current", "deleted", "suppressed", "invalidated"}
    values = [Jsonb(value) if name == "document" else value for name, value in changes.items()]
    with candidates.database.transaction(candidates.principal) as conn:
        conn.execute("UPDATE " + table + " SET " + ",".join(name + "=%s" for name in changes)
                     + " WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*values, *candidates.params, identifier))


def test_chinese_source_recovers_english_semantic_assertion_without_embeddings(candidates):
    captured = candidates.add("我同时说自己正在备考，也说自己没有备考。两句话都还没有确认。")
    memory = derived(candidates, captured)
    assert cognition._lexical_score("备考", memory["statement"]) == 0
    _, state = candidates.query("备考")
    assert key(memory) in state["candidate_versions"]
    assert state["rank_signals"][key(memory)][0]["model_or_index_version"] == SOURCE_ROUTE_VERSION
    result = recall(candidates, "备考")
    assert key(memory) in {key(item["memory"]) for item in result["items"]}
    assert result["usage"]["model_calls"] == result["usage"]["embedding_calls"] == 0
    assert result["coverage"]["search_exhaustive"] is False


def test_root_context_or_unrelated_span_cannot_supply_a_source_keyword_match(candidates):
    captured = candidates.add("备考时需要安静。周末我喜欢散步。")
    unrelated = candidates.add("窗外有一座灯塔。")
    context_only = derived(candidates, unrelated, root_event_ids=[captured["evidence_refs"][0]["event_id"]])
    fragment = copy.deepcopy(captured)
    reference = fragment["evidence_refs"][0]
    reference["span"]["start"] = len("备考时需要安静。")
    fragment["disclosure_basis"]["support_paths"][0]["evidence_refs"] = [copy.deepcopy(reference)]
    other_span = derived(candidates, fragment)
    _, state = candidates.query("备考")
    assert key(captured) in state["candidate_versions"]
    assert key(context_only) not in state["candidate_versions"]
    assert key(other_span) not in state["candidate_versions"]


def test_broad_citation_does_not_inflate_concentration_of_disconnected_query_cues(candidates):
    focused = candidates.add("The kiln workshop starts on Tuesday.")
    wanted = derived(candidates, focused, statement="The user supplied the schedule.")
    text = "The kiln is blue.\nThe workshop has windows.\nTuesday was rainy."
    broad = candidates.add(text)
    parent = copy.deepcopy(broad)
    parent["evidence_refs"][0]["span"]["end"] = len(text)
    parent["disclosure_basis"]["support_paths"][0]["evidence_refs"] = copy.deepcopy(parent["evidence_refs"])
    distractor = derived(candidates, parent, statement="The user described the room and weather.")
    memories, state = candidates.query("kiln workshop Tuesday")
    signals = state["rank_signals"]
    assert signals[key(wanted)][0]["score"] > 2 * signals[key(distractor)][0]["score"]
    assert state["candidate_versions"].index(key(wanted)) < state["candidate_versions"].index(key(distractor))
    returned = next(memory for memory in memories if key(memory) == key(distractor))
    assert returned["evidence_refs"] == distractor["evidence_refs"]
    assert returned["statement"] == distractor["statement"]


@pytest.mark.parametrize("damage", ["revision", "hash", "span", "context_only", "contradicts", "branch", "conversation", "retracted"])
def test_source_route_checks_exact_reference_and_assertion_scope(candidates, damage):
    captured = candidates.add("备考时需要安静。")
    memory = derived(candidates, captured)
    if damage == "revision":
        memory["evidence_refs"][0]["event_revision"] += 1
    elif damage == "hash":
        memory["evidence_refs"][0]["source_content_sha256"] = digest("not the source")
    elif damage == "span":
        memory["evidence_refs"][0]["span"]["end"] = 999
    elif damage in {"context_only", "contradicts"}:
        memory["evidence_refs"][0]["relationship"] = damage
    elif damage == "branch":
        memory["scope"]["branch_id"] = "another"
    elif damage == "conversation":
        memory["scope"].update(conversation_id="another", visibility="conversation")
    else:
        memory["lifecycle"] = "retracted"
    change_row(candidates, "memory_assertions", memory["memory_id"], document=memory)
    assert key(memory) not in candidates.query("备考")[1]["candidate_versions"]
    assert key(memory) not in {key(item["memory"]) for item in recall(candidates, "备考")["items"]}


@pytest.mark.parametrize("table,field", [
    ("memory_assertions", "is_current"), ("memory_assertions", "deleted"),
    ("memory_assertions", "suppressed"), ("memory_assertions", "invalidated"),
    ("memory_events", "is_current"), ("memory_events", "deleted"), ("memory_events", "suppressed"),
])
def test_source_route_cannot_restore_removed_memory_or_source(candidates, table, field):
    captured = candidates.add("备考时需要安静。")
    memory = derived(candidates, captured)
    identifier = memory["memory_id"] if table == "memory_assertions" else memory["evidence_refs"][0]["event_id"]
    change_row(candidates, table, identifier, **{field: field != "is_current"})
    assert key(memory) not in candidates.query("备考")[1]["candidate_versions"]
    assert key(memory) not in {key(item["memory"]) for item in recall(candidates, "备考")["items"]}


def test_corrected_source_does_not_bridge_to_old_derived_assertion(candidates):
    captured = candidates.add("备考时需要安静。")
    memory = derived(candidates, captured)
    event_id = captured["evidence_refs"][0]["event_id"]
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        event = candidates.service._events(conn, candidates.principal, brain, event_ids=[event_id])[0]
    changed = copy.deepcopy(event["observed"])
    changed.update(source_revision=2, content={"media_type": "text/plain", "text": "备考时希望有人陪伴。"})
    response = candidates.service.handle(candidates.principal, {
        "contract_version": CONTRACT_VERSION, "message_type": "request", "request_id": uid(),
        "action": "observe", "target": candidates.target,
        "payload": {"idempotency_key": uid(), "events": [changed]},
    })
    assert response["outcome"] == "completed", response
    assert key(memory) not in candidates.query("备考")[1]["candidate_versions"]
    assert key(memory) not in {key(item["memory"]) for item in recall(candidates, "备考")["items"]}


@pytest.mark.parametrize("damage", ["branch", "conversation", "cancelled"])
def test_source_scope_and_delivery_are_checked_before_candidate_propagation(candidates, damage):
    captured = candidates.add("备考时需要安静。")
    memory = derived(candidates, captured)
    event_id = memory["evidence_refs"][0]["event_id"]
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        event = candidates.service._events(conn, candidates.principal, brain, event_ids=[event_id])[0]
    if damage == "branch":
        event["scope"]["branch_id"] = "another"
    elif damage == "conversation":
        event["scope"].update(conversation_id="another", visibility="conversation")
    else:
        event["observed"]["delivery_state"] = "cancelled"
    change_row(candidates, "memory_events", event_id, document=event)
    assert key(memory) not in candidates.query("备考")[1]["candidate_versions"]
    assert key(memory) not in {key(item["memory"]) for item in recall(candidates, "备考")["items"]}


def test_source_route_is_bounded_and_does_not_expand_without_a_literal_hit(candidates):
    captured = candidates.add("备考时需要安静。")
    for _ in range(24):
        derived(candidates, captured)
    _, state = candidates.query("备考", budget=2048)
    assert len(state["candidate_versions"]) == 4
    assert state["candidate_truncated"]
    assert not state["source_truncated"]
    assert candidates.query("zygomatic")[1]["candidate_versions"] == []


def test_source_route_does_not_reintroduce_current_cue_as_historical_evidence(candidates):
    captured = candidates.add("你是否记得备考时需要安静？")
    memory = derived(candidates, captured)
    query = request("备考", evidence_mode="prior_record_check", cue_event_ids=[captured["evidence_refs"][0]["event_id"]])
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        result, _, _ = candidates.service._retrieval(conn, candidates.principal, brain, candidates.target, query)
    assert key(memory) not in {key(item["memory"]) for item in result["items"]}


def test_learning_neighborhood_recovers_foreign_language_assertion_beyond_recent_window(learning):
    original = source(learning, "备考时需要安静。", conversation="old")
    literal(learning, original)
    memory = publish(learning, original, epistemic_status="derived_inference",
                     statement="The user prefers a quiet environment during exam preparation.")
    for _ in range(30):
        source(learning, "窗外没有新消息。")
    current = source(learning, "备考时应该怎样安排？")
    result = select(learning, current)
    assert key(memory) in versions(result)
    assert result.sampling["source_candidate_count"] == 1
    assert not result.sampling["publication_restricted"]
