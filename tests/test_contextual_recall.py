import copy
import json

import pytest
from test_cognition import STAMP, event, prepare_payload, request, scope, state, working

from membrain import cognition
from membrain.contracts import validate
from membrain.helpers import digest

CONDITION = {
    "op": "holds", "subject_ref": "user",
    "predicate_ref": {"id": "core.activity", "version": 1},
    "value": {"type": "concept", "id": "activity:exam_preparation"},
}
UNBOUNDED = {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"}}


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: STAMP)


def observation(source, *, polarity="positive", validity=None):
    return {
        "observation_id": f"observation-{source['sequence']}",
        "subject_ref": CONDITION["subject_ref"],
        "predicate_ref": copy.deepcopy(CONDITION["predicate_ref"]),
        "value": copy.deepcopy(CONDITION["value"]), "polarity": polarity,
        "valid_during": copy.deepcopy(UNBOUNDED if validity is None else validity),
        "evidence_refs": cognition.extract_event(source)[0]["evidence_refs"],
    }


def case(kind="positive"):
    original = event("备考期间，我希望回答简短。")
    memory = cognition.extract_event(original)[0]
    memory["memory_kind"] = "semantic"
    memory["conditions"] = copy.deepcopy(CONDITION)
    memory["valid_during"] = copy.deepcopy(UNBOUNDED)
    sources, observations = [original], []
    if kind != "missing":
        context_source = event("我现在没有备考。" if kind == "negative" else "我正在备考。", 2)
        sources.append(context_source)
        validity = copy.deepcopy(UNBOUNDED)
        if kind == "expired":
            validity["end"] = {"kind": "instant", "at": STAMP}
        elif kind == "future":
            validity["start"] = {"kind": "instant", "at": "2026-10-23T00:00:00Z"}
        elif kind == "unknown_time":
            validity = {"kind": "unknown"}
        observations.append(observation(
            context_source, polarity="negative" if kind == "negative" else "positive", validity=validity,
        ))
        if kind == "conflict":
            contrary = event("我现在没有备考。", 3)
            sources.append(contrary)
            observations.append(observation(contrary, polarity="negative"))
    current = state(
        max(source["sequence"] for source in sources), context_observations=observations,
        working_event_metadata={source["event_id"]: source for source in sources},
    )
    return memory, sources, current


def recall(memory, sources, current, **changes):
    return cognition.recall(scope(), request("回答简短", **changes), [memory], sources, current)


def memory_blocks(packet):
    return [(block, json.loads(block["text"])) for block in packet["blocks"]
            if block["section"] in {"relevant_memory", "conditional_memory"}]


def assert_conditional(packet, memory, value, conflict=False):
    blocks = memory_blocks(packet)
    assert len(blocks) == 1
    block, content = blocks[0]
    assert block["section"] == "conditional_memory"
    assert content["statement"] == memory["statement"]
    assert content["use"] == "conditional_memory_not_current_rule"
    assert content["applicability"]["value"] == value
    assert content["applicability"]["conflict"] is conflict
    assert content["conditions"] == memory["conditions"]
    assert memory["statement"] in cognition.render_context(packet)
    assert "conditional_memory_not_current_rule" in cognition.render_context(packet)
    assert packet["usage"]["context_tokens"] == cognition.estimate_tokens(cognition.render_context(packet))
    validate("ContextPacket", packet)


@pytest.mark.parametrize("kind,value,conflict,reason", [
    ("positive", "true", False, None),
    ("missing", "unknown", False, "no_determining_observation"),
    ("negative", "false", False, None),
    ("conflict", "unknown", True, "conflicting_observations"),
    ("expired", "unknown", False, "expired"),
    ("future", "unknown", False, "not_yet_valid"),
    ("unknown_time", "unknown", False, "time_unknown"),
])
def test_recall_reports_contextual_applicability_without_erasing_the_memory(kind, value, conflict, reason):
    memory, sources, current = case(kind)
    before = copy.deepcopy((memory, current))
    recalled = recall(memory, sources, current)
    assert len(recalled["items"]) == 1
    item = recalled["items"][0]
    assert item["memory"] == memory
    assert item["applicability"]["value"] == value
    assert item["applicability"]["conflict"] is conflict
    assert item["applicability"]["status"] == ("conflict" if conflict else value)
    if reason:
        assert reason in item["applicability"]["reasons"]
    assert (memory, current) == before
    assert recalled["usage"]["context_tokens"] == cognition.estimate_tokens(cognition.render_recall(recalled))
    validate("RecallResult", recalled)


def test_matching_context_retains_its_exact_evidence_and_can_enter_relevant_memory():
    memory, sources, current = case()
    recalled = recall(memory, sources, current)
    applicability = recalled["items"][0]["applicability"]
    assert applicability["supporting_observation_ids"] == ["observation-2"]
    assert applicability["opposing_observation_ids"] == []
    assert applicability["evidence_refs"] == current["context_observations"][0]["evidence_refs"]
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    blocks = memory_blocks(packet)
    assert len(blocks) == 1
    assert blocks[0][0]["section"] == "relevant_memory"
    assert blocks[0][1]["statement"] == memory["statement"]
    assert blocks[0][1]["applicability"] == applicability
    validate("ContextPacket", packet)


@pytest.mark.parametrize("kind,value,conflict", [
    ("positive", "unknown", False), ("negative", "false", False), ("conflict", "unknown", True),
])
def test_unstated_memory_validity_cannot_make_a_condition_current(kind, value, conflict):
    memory, sources, current = case(kind)
    del memory["valid_during"]
    recalled = recall(memory, sources, current)
    result = recalled["items"][0]["applicability"]
    assert result["value"] == value
    assert result["conflict"] is conflict
    assert result["status"] == ("conflict" if conflict else value)
    assert "validity_unstated" in result["reasons"]
    assert {digest(ref) for ref in result["evidence_refs"]} == {
        digest(ref) for observation in current["context_observations"] for ref in observation["evidence_refs"]
    }
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    assert_conditional(packet, memory, value, conflict)


@pytest.mark.parametrize("kind,value,conflict", [
    ("missing", "unknown", False), ("negative", "false", False),
    ("conflict", "unknown", True), ("expired", "unknown", False),
])
def test_prepare_labels_nonapplicable_memories_as_conditional_data(kind, value, conflict):
    memory, sources, current = case(kind)
    packet = cognition.prepare(scope(), prepare_payload(), working(), recall(memory, sources, current), current)
    assert_conditional(packet, memory, value, conflict)


def test_history_query_can_recover_a_rule_that_does_not_apply_now():
    memory, sources, current = case("negative")
    recalled = recall(
        memory, sources, current, intent="episode", evidence_mode="prior_record_check",
        evidence_cutoff={"mode": "through_sequence", "through_sequence": 1},
    )
    assert len(recalled["items"]) == 1
    item = recalled["items"][0]
    assert item["display_mode"] == "explicit_recollection"
    assert item["applicability"]["value"] == "false"
    assert item["memory"]["evidence_refs"] == memory["evidence_refs"]
    assert item["memory"]["evidence_refs"][0]["event_id"] == "event-1"
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    assert_conditional(packet, memory, "false")


def test_explicit_applicability_time_is_used_and_missing_time_falls_back_to_now():
    memory, sources, current = case("expired")
    earlier = {**current, "applicability_at": "2026-10-22T11:59:59Z"}
    assert recall(memory, sources, earlier)["items"][0]["applicability"]["value"] == "true"
    assert recall(memory, sources, current)["items"][0]["applicability"]["value"] == "unknown"
    same_instant = {**current, "applicability_at": "2026-10-22T05:00:00-07:00"}
    assert recall(memory, sources, same_instant)["items"][0]["applicability"]["value"] == "unknown"


def test_prepare_rechecks_expiry_instead_of_reusing_an_earlier_positive_decision():
    memory, sources, current = case("expired")
    earlier = {**current, "applicability_at": "2026-10-22T11:59:59Z"}
    recalled = recall(memory, sources, earlier)
    assert recalled["items"][0]["applicability"]["value"] == "true"
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    assert_conditional(packet, memory, "unknown")


def test_legacy_string_context_cannot_promote_an_untyped_condition_to_current_rule():
    memory, sources, current = case("missing")
    memory["conditions"] = {"op": "eq", "key": "activity", "value": "exam_preparation"}
    current["situation_context"] = {"activity": "exam_preparation"}
    recalled = recall(memory, sources, current)
    applicability = recalled["items"][0]["applicability"]
    assert applicability["value"] == "unknown"
    assert "legacy_condition" in applicability["reasons"]
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    assert_conditional(packet, memory, "unknown")


@pytest.mark.parametrize("unavailable", ["missing", "revised", "changed_hash", "invisible", "superseded"])
def test_invalid_context_sources_cannot_make_a_condition_current(unavailable):
    memory, sources, current = case()
    context_source = sources[1]
    if unavailable == "missing":
        sources = sources[:1]
        current["working_event_metadata"].pop(context_source["event_id"])
    elif unavailable == "revised":
        context_source["revision"] += 1
    elif unavailable == "changed_hash":
        context_source["observed"]["content"]["text"] = "备考已经结束。"
        context_source["content_sha256"] = digest(context_source["observed"]["content"])
    elif unavailable == "invisible":
        context_source["scope"] = scope(tenant_id="another-tenant")
    else:
        current["superseded_evidence_refs"] = current["context_observations"][0]["evidence_refs"]
    recalled = recall(memory, sources, current)
    assert len(recalled["items"]) == 1
    applicability = recalled["items"][0]["applicability"]
    assert applicability["value"] == "unknown"
    assert applicability["supporting_observation_ids"] == []
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    assert_conditional(packet, memory, "unknown")


def test_prepare_rechecks_the_source_of_a_previously_supported_condition():
    memory, sources, current = case()
    recalled = recall(memory, sources, current)
    assert recalled["items"][0]["applicability"]["value"] == "true"
    current["working_event_metadata"].pop(sources[1]["event_id"])
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    assert_conditional(packet, memory, "unknown")


def test_context_evidence_does_not_replace_a_missing_historical_memory_source():
    memory, sources, current = case()
    current["working_event_metadata"].pop(sources[0]["event_id"])
    recalled = recall(memory, sources[1:], current)
    assert recalled["items"] == []
    assert memory["statement"] not in json.dumps(recalled, ensure_ascii=False)
