import copy
import json
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest
from test_belief_semantic import known, link_verifier, version
from test_bounded_learning_semantics import publication_snapshot
from test_cognition import event
from test_semantic import batch, claim, encoded, proposals, reference, run, verifier

from agent_memory.belief_capabilities import RELATION_CAPABILITIES_INPUT_VERSION as VERSION
from agent_memory.consolidation import _check_result
from agent_memory.helpers import DomainError
from agent_memory.history_semantics import LINK_DERIVED_INPUT_VERSION
from agent_memory.semantic import (
    SPEECH_EVENT_INSTRUCTIONS,
    CognitiveBatch,
    SemanticPolicy,
    SemanticProcessor,
    belief_capabilities,
)


def speech(source):
    return claim(source, statement="The assistant said: if a reminder is needed, tell the assistant.",
                 memory_kind="episodic", claim_kind="agent_said", conditions={"op": "always"})


@pytest.fixture(scope="module")
def recorded():
    path = Path(__file__).parents[1] / "evaluations/deepseek-2026-10-25-learning-repair-stopped.evidence.tar.gz"
    with tarfile.open(path, "r:gz") as archive:
        stage = json.load(archive.extractfile("run/memory/report.json"))["stages"][0]
    calls, job = stage["cognitive_calls"], stage["learning_jobs"][0]
    snapshot = job["result"]
    body = json.loads(calls[0]["request"]["messages"][-1]["content"])
    data = CognitiveBatch(
        job["job_id"], snapshot["scope"], tuple(body["sources"]),
        existing_assertions=tuple(body["existing_assertions"]), working_state=body["working_state"],
        episodes=tuple(body["episodes"]), sampling=body["sampling"],
        belief_relations=tuple(body["belief_relations"]), consolidation_plan=body["consolidation_plan"],
        current_input_refs=tuple(body["current_input_refs"]), proposal_mode=snapshot["proposal_mode"],
        learning_input_refs=tuple(body["learning_input_refs"]),
        input_contract_version=snapshot["input_contract_version"],
    )
    return data, calls, SemanticPolicy(**snapshot["semantic_policy"]["limits"])


def test_recorded_old_contract_keeps_both_exact_requests_and_rejection(recorded):
    data, calls, policy = recorded
    seen = []

    def replay(request):
        previous = calls[len(seen)]
        seen.append(request)
        assert list(request.messages) == previous["request"]["messages"]
        assert request.response_schema == previous["request"]["response_schema"]
        assert request.request_id == previous["request"]["request_id"]
        assert SPEECH_EVENT_INSTRUCTIONS not in request.messages[0]["content"]
        return encoded(previous["response"])

    result = SemanticProcessor(replay, replay, policy).process(data, reserve=lambda **_: None)
    assert len(seen) == result.model_calls == 2
    assert result.rejected == ({"proposal_id": "claim-3", "reason": "invalid_proposal"},)
    assert len(result.claims) == 2


@pytest.mark.parametrize("input_version", [VERSION, LINK_DERIVED_INPUT_VERSION])
def test_recorded_conditional_speech_reaches_independent_verification_only_under_new_contract(recorded, input_version):
    old, calls, policy = recorded
    candidate = calls[0]["response"]["claims"][2]
    data = replace(old, input_contract_version=input_version)
    seen = []

    def verify(request):
        seen.append(request)
        assert SPEECH_EVENT_INSTRUCTIONS in request.messages[0]["content"]
        item = json.loads(request.messages[-1]["content"])["proposals"][0]
        assert item["proposal"] == candidate
        assert item["evidence"][0]["excerpt"] == "如果你需要我基于这个安排做计划或提醒，请告诉我。"
        return verifier(request)

    result = run(data, proposals(candidate), verify=verify, policy=policy)
    assert result.status == "completed" and len(seen) == 1 and result.model_calls == 2
    assert result.claims[0]["statement"] == candidate["statement"]
    assert result.claims[0]["claim_kind"] == "agent_said"
    assert result.claims[0]["epistemic_status"] == "derived_inference"
    _check_result(data, result, publication_snapshot(data))


@pytest.mark.parametrize("hint", ["unknown", "real_world_report"])
def test_speech_event_requires_verification_and_does_not_turn_condition_into_fact(hint):
    source = event("If you need a reminder, tell me.", role="assistant")
    source["observed"]["interpretation_hint"] = hint
    data = batch(source, input_contract_version=VERSION)
    candidate = speech(source)
    missing = run(data, proposals(candidate), verify=None)
    assert not missing.claims and missing.status == "verification_unavailable"

    def reject(request):
        result = json.loads(verifier(request))
        result["assessments"][0]["proposal_verdict"] = "unknown"
        return encoded(result)

    result = run(data, proposals(candidate), verify=reject)
    assert not result.claims and result.model_calls == 2


@pytest.mark.parametrize("change", [
    "roleplay", "hypothetical", "dream", "quotation", "draft", "partially_delivered",
    "user", "system", "source_correction", "delivery_receipt", "reported_fact", "external_world_fact",
    "inferred_pattern", "proposal_offered", "commitment_expressed", "partial_span", "mixed_counterexample",
])
@pytest.mark.parametrize("input_version", [VERSION, LINK_DERIVED_INPUT_VERSION])
def test_speech_event_does_not_bypass_source_world_attribution_or_complete_unit_guards(change, input_version):
    source = event("If you need a reminder, tell me.", role="assistant")
    candidate = speech(source)
    sources = [source]
    if change in {"roleplay", "hypothetical", "dream", "quotation"}:
        source["observed"]["interpretation_hint"] = change
    elif change in {"draft", "partially_delivered"}:
        source["observed"]["delivery_state"] = change
    elif change in {"user", "system"}:
        source["observed"].update(actor_role=change, delivery_state="received")
    elif change in {"source_correction", "delivery_receipt"}:
        source["observed"]["event_kind"] = change
    elif change == "partial_span":
        candidate["evidence_refs"][0]["span"]["start"] = 3
    elif change == "mixed_counterexample":
        sources.append(event("I do not need reminders.", 2))
        candidate["counterexamples"] = [reference(sources[1], "contradicts")]
    else:
        candidate["claim_kind"] = change
    result = run(batch(*sources, input_contract_version=input_version), proposals(candidate),
                 verify=lambda _: pytest.fail("Ineligible source must not reach verification"))
    assert not result.claims and result.model_calls == 1
    assert result.rejected == ({"proposal_id": candidate["proposal_id"], "reason": "invalid_proposal"},)


@pytest.mark.parametrize("text", [
    "In this roleplay, if you need a reminder, tell me.",
    "Here is a quotation: if you need a reminder, tell me.",
    "I dreamed that if you needed a reminder, you would tell me.",
])
def test_other_content_frames_remain_guarded_even_when_they_contain_if(text):
    source = event(text, role="assistant")
    result = run(batch(source, input_contract_version=VERSION), proposals(speech(source)),
                 verify=lambda _: pytest.fail("The conditional exception cannot erase another frame"))
    assert not result.claims and result.model_calls == 1


def test_speech_publication_rechecks_explicit_source_frame():
    source = event("If you need a reminder, tell me.", role="assistant")
    data = batch(source, input_contract_version=VERSION)
    result = run(data, proposals(speech(source)))
    _check_result(data, result, publication_snapshot(data))
    changed = copy.deepcopy(data)
    changed.sources[0]["observed"]["interpretation_hint"] = "hypothetical"
    with pytest.raises(DomainError, match="unsupported"):
        _check_result(changed, result, publication_snapshot(changed))


@pytest.mark.parametrize("input_version", [VERSION, LINK_DERIVED_INPUT_VERSION])
def test_conditional_speech_relation_uses_same_source_gate_at_projection_and_publication(input_version):
    first = event("If you need a reminder, tell me.", role="assistant")
    second = event("If a reminder is needed, let me know.", 2, role="assistant")
    target = known(first, statement=speech(first)["statement"])
    target.update(world="real_world", claim_kind="agent_said", memory_kind="episodic",
                  valid_during={"kind": "unknown"})
    candidate = speech(second)
    link = {"proposal_id": "link", "kind": "equivalent", "claim_proposal_id": candidate["proposal_id"],
            "target_memory_versions": [version(target)], "evidence_refs": [reference(first), reference(second)],
            "world": "real_world", "conditions": {"op": "always"}, "valid_during": {"kind": "unknown"}}
    data = batch(first, second, existing_assertions=(target,), input_contract_version=input_version)
    entry = belief_capabilities(data.scope, data.sources, data.existing_assertions)["endpoints"][0]
    assert entry["claim_kinds_for_endpoint_evidence"] == ["agent_said"]
    assert "equivalent" in entry["conditional_relations"] and "abstracts" not in entry["conditional_relations"]
    result = run(data, {**proposals(candidate), "belief_links": [link]}, verify=link_verifier)
    assert len(result.claims) == len(result.belief_links) == 1
    _check_result(data, result, publication_snapshot(data))
    changed = copy.deepcopy(data)
    changed.existing_assertions[0]["world"] = "hypothetical"
    rejected = run(changed, {**proposals(candidate), "belief_links": [link]}, verify=link_verifier)
    assert not rejected.belief_links
