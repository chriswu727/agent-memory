import copy
import json

import pytest
from test_belief_recall import recall, relation, scenario
from test_cognition import STAMP, event, prepare_payload, request, scope, state, working

from agent_memory import cognition
from agent_memory.belief_resolution import factor_relations, relation_assessments
from agent_memory.context_views import context_view, reasoning_context_view, recall_context_view
from agent_memory.contracts import validate
from agent_memory.helpers import DomainError


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: STAMP)


def test_compact_views_intern_certificates_and_keep_inspectable_versions():
    sources, memories, current = scenario(kind="exception")
    result = recall(sources, memories, current)
    assert len(result["belief_relations"]) == 1
    assert all("relations" not in item["belief_resolution"] for item in result["items"])
    assert all(item["memory"] in memories for item in result["items"])
    view = recall_context_view(result)
    assert len(view["relations"]) == 1
    for memory in view["memories"]:
        version = view["memory_versions"][memory["memory_ref"]]
        assert any(value["memory_id"] == version["memory_id"] and value["revision"] == version["revision"]
                   for value in memories)
        assert all(reference in view["sources"] for reference in memory["sources"])
    certificate = next(iter(view["relations"].values()))
    assert certificate["source"] in view["memory_versions"]
    assert all(target in view["memory_versions"] for target in certificate["targets"])
    assert certificate["conditions"] == current["belief_relations"][0]["conditions"]
    assert certificate["valid_during"] == current["belief_relations"][0]["valid_during"]
    assert "source_content_sha256" not in cognition.render_recall(result)
    assert "principal_binding_id" not in cognition.render_recall(result)
    assert result["usage"]["context_tokens"] == len(cognition.render_recall(result).encode())
    assert len(cognition.render_recall(result).encode()) < cognition.estimate_tokens(result)
    validate("RecallResult", result)


def test_factoring_historical_inline_certificates_preserves_exact_model_budget():
    sources, memories, current = scenario()
    result = recall(sources, memories, current)
    inline = copy.deepcopy(result)
    for item in inline["items"]:
        resolution = item["belief_resolution"]
        resolution["relations"] = relation_assessments(resolution, inline["belief_relations"])
        resolution.pop("relation_refs")
        resolution.pop("evidence_refs")
    inline.pop("belief_relations")
    validate("RecallResult", inline)
    before = cognition.render_recall(inline)
    assert before == cognition.render_recall(result)
    factor_relations(inline)
    assert cognition.render_recall(inline) == before


def test_prepare_relation_certificate_is_once_and_source_ids_are_resolvable():
    sources, memories, current = scenario()
    recalled = recall(sources, memories, current)
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    view = context_view(packet)
    assert len(view["relations"]) == 1
    for block in view["blocks"]:
        assert all(identifier in view["sources"] for identifier in block["sources"])
    for block in packet["blocks"]:
        content = json.loads(block["text"])
        assert "relations" not in content.get("belief_resolution", {})
    assert packet["usage"]["context_tokens"] == len(cognition.render_context(packet).encode())


def test_literal_old_preference_remains_history_while_current_rule_is_conditional():
    source = event("My old preference was detailed responses.")
    literal = cognition.extract_event(source)[0]
    semantic = copy.deepcopy(literal)
    semantic.update(memory_id="semantic-rule", memory_kind="semantic", conditions={"op": "unresolved", "label": "silver"},
                    valid_during={"kind": "unknown"})
    recalled = cognition.recall(scope(), request("preference"), [literal, semantic], [source], state())
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, state())
    contents = [block["content"] for block in context_view(packet)["blocks"]
                if block["section"] in {"relevant_memory", "conditional_memory"}]
    assert {content["use"] for content in contents} == {
        "historical_report_not_current_rule", "conditional_memory_not_current_rule"}
    assert any(content.get("conditions") == {"op": "unresolved", "label": "silver"} for content in contents)


def test_independent_small_memory_survives_oversized_connected_group():
    sources, memories, current = scenario()
    memories[1]["statement"] *= 1000
    current["belief_relations"] = [relation(memories[1], memories[:1])]
    standalone = event("routine independent observation", 3)
    sources.append(standalone)
    memories.append(cognition.extract_event(standalone)[0])
    current.update(durable_sequence=3, indexed_sequence=3)
    current["working_event_metadata"][standalone["event_id"]] = standalone
    recalled = recall(sources, memories, current, budget={**request()["budget"], "max_context_tokens": 3000})
    assert [item["memory"]["memory_id"] for item in recalled["items"]] == [memories[-1]["memory_id"]]
    assert recalled["belief_relations"] == []
    assert recalled["usage"]["context_tokens"] <= 3000


def test_missing_relation_certificate_fails_closed():
    sources, memories, current = scenario()
    result = recall(sources, memories, current)
    result["belief_relations"] = []
    with pytest.raises(DomainError, match="certificate is unavailable"):
        recall_context_view(result)


def test_arbitrary_working_value_is_preserved_without_interpreting_embedded_metadata():
    value = {"evidence_refs": ["this is user data"], "registry_fingerprint": "literal value",
             "belief_resolution": {"user_authored": True}, "memory_version": "plain text"}
    packet = {"blocks": [{"section": "working_state", "text": json.dumps({"value": value}), "evidence_refs": []}]}
    assert context_view(packet)["blocks"][0]["content"]["value"] == value


def test_reasoner_projection_keeps_decision_ids_and_compacts_shared_relations():
    sources, memories, current = scenario()
    result = recall(sources, memories, current)
    candidates = []
    for item in result["items"]:
        resolution = copy.deepcopy(item["belief_resolution"])
        resolution["relations"] = relation_assessments(resolution, result["belief_relations"])
        resolution.pop("relation_refs")
        candidates.append({**item["memory"], "belief_resolution": resolution})
    view = reasoning_context_view([], candidates)
    assert [value["memory_id"] for value in view["candidates"]] == [item["memory"]["memory_id"] for item in result["items"]]
    assert len(view["relations"]) == 1
    assert all(reference in view["sources"] for candidate in view["candidates"] for reference in candidate["evidence_refs"])


@pytest.mark.parametrize("start,end,value", [
    ("2026-10-23T00:00:00Z", None, "false"),
    (None, "2026-10-20T00:00:00Z", "false"),
    ("2026-10-20T00:00:00Z", "2026-10-23T00:00:00Z", "true"),
])
def test_legacy_semantic_valid_time_cannot_bypass_current_rule_guard(start, end, value):
    source = event("My preference is concise answers.")
    memory = cognition.extract_event(source)[0]
    memory.update(memory_kind="semantic", valid_time={"start": start, "end": end, "precision": "exact", "basis": "source_explicit"})
    recalled = cognition.recall(scope(), request("preference"), [memory], [source], state())
    assert recalled["items"][0]["applicability"]["value"] == value
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, state())
    block = next(block for block in context_view(packet)["blocks"] if block["section"] != "uncertainty")
    assert block["section"] == ("conditional_memory" if value == "false" else "relevant_memory")
    assert block["content"]["use"] == ("conditional_memory_not_current_rule" if value == "false"
                                         else "source_attributed_applicable_memory")
    if value == "false":
        assert "outside_recorded_valid_time" in block["content"]["applicability"]["reasons"]


def test_authoritative_serialized_context_preserves_numeric_bytes_and_exact_budget():
    current = state(0, candidate_total=0)
    recalled = cognition.recall(scope(), request("unmatched"), [], [], current)
    working_state = working()
    working_state["items"] = [{"key": "numbers", "value": [1e20] * 100, "knowledge_state": "unknown",
                               "evidence_refs": [], "expires_at": None, "updated_at": STAMP}]
    packet = cognition.prepare(scope(), prepare_payload(1500), working_state, recalled, current)
    assert packet["working_state"]["items"] == working_state["items"]
    assert "1e+20" in packet["model_context"]
    assert packet["model_context"] == cognition.render_context(packet)
    assert len(packet["model_context"].encode()) == packet["usage"]["context_tokens"] <= 1500
    assert recalled["model_context"] == cognition.render_recall(recalled)
    assert len(recalled["model_context"].encode()) == recalled["usage"]["context_tokens"]
    validate("ContextPacket", packet)
    validate("RecallResult", recalled)
    packet["model_context"] = "stale external copy"
    assert cognition.render_context(packet) != "stale external copy"
    recalled["model_context"] = "stale external copy"
    assert cognition.render_recall(recalled) != "stale external copy"


def test_prepare_refreshes_nested_recall_authoritative_text_after_applicability_changes():
    sources, memories, current = scenario(kind="exception", valid_during={
        "kind": "interval", "start": {"kind": "unbounded"},
        "end": {"kind": "instant", "at": "2026-10-23T00:00:00Z"}})
    recalled = recall(sources, memories, current)
    original = recalled["model_context"]
    current["applicability_at"] = "2026-10-24T00:00:00Z"
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    nested = packet["recall_results"][0]
    assert nested["model_context"] != original
    assert nested["model_context"] == cognition.render_recall(nested)
    assert nested["usage"]["context_tokens"] == len(nested["model_context"].encode())
    assert packet["model_context"] == cognition.render_context(packet)
    assert packet["usage"]["context_tokens"] == len(packet["model_context"].encode())
    assert recalled["model_context"] == original
