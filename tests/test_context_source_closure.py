import copy
import json

import pytest
from test_cognition import STAMP, event, prepare_payload, request, scope, state, working

from agent_memory import cognition
from agent_memory.contracts import validate


def observation(source, *, polarity="positive", unknown_time=False):
    return {
        "observation_id": "observation-" + source["event_id"],
        "subject_ref": "user", "predicate_ref": {"id": "core.activity", "version": 1},
        "value": {"type": "concept", "id": "activity:exam_preparation"},
        "polarity": polarity,
        "valid_during": ({"kind": "unknown"} if unknown_time else {
            "kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"},
        }),
        "evidence_refs": cognition.extract_event(source)[0]["evidence_refs"],
    }


def situation(observed, text):
    return {
        "kind": "context", "key": observed["observation_id"], "value": text,
        "state": "active", "world": "real_world", "conditions": {"op": "always"},
        "verification": {"state": "supported_inference", "method": "semantic_model",
                         "not_a_truth_certificate": True},
        "observation": copy.deepcopy(observed), "evidence_refs": observed["evidence_refs"],
    }


def working_item(observed, text):
    return {
        "key": observed["observation_id"], "value": situation(observed, text),
        "knowledge_state": "inferred", "evidence_refs": observed["evidence_refs"],
        "expires_at": "2026-10-23T12:00:00Z", "updated_at": STAMP,
    }


def context_case(section, *, unknown_time=False):
    sources = [event("我正在备考。"), event("我已经不在备考了。", 2)]
    positive = observation(sources[0])
    negative = observation(sources[1], polarity="negative", unknown_time=unknown_time)
    current = state(
        2, candidate_total=0, context_observations=[positive, negative],
        working_event_metadata={source["event_id"]: source for source in sources},
        verified_working_evidence_refs=positive["evidence_refs"] + negative["evidence_refs"],
    )
    local = working()
    if section == "current_situation":
        current["current_situation"] = [situation(positive, "正在备考")]
    else:
        local["items"] = [working_item(positive, "正在备考")]
    recalled = cognition.recall(scope(), request("current context"), [], sources, current)
    assert recalled["items"] == []
    return positive, negative, current, local, recalled


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: STAMP)


def assert_source_closure(packet, section, positive, negative, *, unknown_time=False):
    blocks = [block for block in packet["blocks"] if block["section"] == section]
    assert len(blocks) == 1
    block = blocks[0]
    value = json.loads(block["text"])
    if section == "working_state":
        value = value["value"]
    assessment = value["applicability"]
    assert assessment["value"] == "unknown"
    assert assessment["conflict"] is not unknown_time
    if unknown_time:
        assert assessment["unresolved_observation_ids"] == [negative["observation_id"]]
    else:
        assert assessment["opposing_observation_ids"] == [negative["observation_id"]]
    assert {ref["event_id"] for ref in assessment["evidence_refs"]} == {"event-1", "event-2"}
    for reference in positive["evidence_refs"] + negative["evidence_refs"]:
        assert {**reference, "relationship": "context_only"} in block["evidence_refs"]
    runtime_refs = {(reference["event_id"], reference["event_revision"])
                    for emitted in packet["blocks"] for reference in emitted["evidence_refs"]}
    assert runtime_refs == {("event-1", 1), ("event-2", 1)}
    validate("ContextPacket", packet)
    assert packet["usage"]["context_tokens"] == cognition.estimate_tokens(cognition.render_context(packet))


@pytest.mark.parametrize("section", ["current_situation", "working_state"])
@pytest.mark.parametrize("unknown_time", [False, True])
def test_context_proof_sources_are_bound_without_a_separate_opposing_block(section, unknown_time):
    positive, negative, current, local, recalled = context_case(section, unknown_time=unknown_time)
    packet = cognition.prepare(scope(), prepare_payload(), local, recalled, current)
    assert_source_closure(packet, section, positive, negative, unknown_time=unknown_time)


@pytest.mark.parametrize("section", ["current_situation", "working_state"])
def test_context_proof_sources_remain_bound_when_the_opposing_block_exceeds_budget(section):
    positive, negative, current, local, recalled = context_case(section)
    baseline = cognition.prepare(scope(), prepare_payload(), local, recalled, current)
    limit = baseline["usage"]["context_tokens"] + 512
    local["items"].append(working_item(negative, "opposing-block-omitted " * 2000))
    packet = cognition.prepare(scope(), prepare_payload(tokens=limit), local, recalled, current)
    assert "opposing-block-omitted" not in cognition.render_context(packet)
    assert json.loads(packet["blocks"][0]["text"])["context_truncated"] is True
    assert packet["usage"]["stopped_by"] == "context_limit"
    assert_source_closure(packet, section, positive, negative)
