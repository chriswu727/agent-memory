import copy
import json
from dataclasses import replace

import pytest
from test_cognition import event
from test_semantic import batch, claim, encoded, episode, proposals, run, situation, verifier

from agent_memory.consolidation import _check_result
from agent_memory.helpers import DomainError
from agent_memory.learning import _snapshot_proposal_mode
from agent_memory.semantic import (
    GENERAL_PROPOSAL_MODE,
    ORIENT_PROPOSAL_MODE,
    ORIENT_PROPOSAL_SCHEMA,
    ORIENT_VERIFICATION_SCHEMA,
    PROPOSAL_SCHEMA,
    SemanticProcessor,
    SemanticResult,
    check_result_mode,
    proposal_data,
    request_payload,
)


def oriented(source, **changes):
    return batch(source, proposal_mode=ORIENT_PROPOSAL_MODE, **changes)


def test_foreground_contract_only_proposes_and_verifies_current_situation():
    source = event("现在我要制定复习计划。")
    calls, reservations = [], []

    def propose(request):
        calls.append(request)
        assert request.response_schema == ORIENT_PROPOSAL_SCHEMA
        assert request.response_schema["required"] == ["situation_updates"]
        data = json.loads(request.messages[-1]["content"])
        assert data["proposal_mode"] == ORIENT_PROPOSAL_MODE
        assert data["sources"] == [source]
        assert "episodes" not in data and "consolidation_plan" not in data
        assert "episode_id" not in request.messages[0]["content"]
        assert "instance_verdicts" not in request.messages[0]["content"]
        return encoded({"situation_updates": [situation(source)]})

    def verify(request):
        calls.append(request)
        assert request.response_schema == ORIENT_VERIFICATION_SCHEMA
        entries = json.loads(request.messages[-1]["content"])["proposals"]
        assert [entry["group"] for entry in entries] == ["situation_updates"]
        assert entries[0]["evidence"][0]["source"] == source
        return verifier(request)

    result = SemanticProcessor(propose, verify).process(oriented(source), reserve=lambda **value: reservations.append(value))
    assert result.status == "completed" and len(result.situation_updates) == 1
    assert not result.claims and not result.episode_actions and not result.belief_links
    assert result.consolidation["objective_count"] == 0
    assert result.model_calls == len(calls) == len(reservations) == 2
    assert result.input_bytes == sum(item["input_bytes"] for item in reservations)


@pytest.mark.parametrize("legacy_envelope", [False, True])
def test_empty_orientation_is_no_proposals_and_needs_no_verification(legacy_envelope):
    response = proposals() if legacy_envelope else {"situation_updates": []}
    result = run(oriented(event("你好。")), response, verify=lambda _: pytest.fail("Nothing requires verification"))
    assert result.status == "completed" and result.learning_outcome == "no_proposals"
    assert result.model_calls == 1 and not result.rejected and not result.situation_updates


@pytest.mark.parametrize("group", ["claims", "episode_actions", "belief_links", "consolidation_decisions"])
def test_foreground_rejects_unused_proposals_before_spending_verification(group):
    source = event("现在我要制定复习计划。")
    item = claim(source) if group == "claims" else episode(source) if group == "episode_actions" else {"invented": True}
    result = run(oriented(source), {"situation_updates": [situation(source)], group: [item]},
                 verify=lambda _: pytest.fail("Disallowed proposal cannot reach verification"))
    assert result.status == "invalid_response" and result.model_calls == 1
    assert not result.situation_updates and not result.claims and not result.episode_actions and not result.belief_links


def test_general_mode_keeps_durable_proposals_and_legacy_request_payload_unchanged():
    source = event("用户报告正在准备考试。")
    default_calls, explicit_calls = [], []

    def provider(calls):
        def invoke(request):
            calls.append(request)
            return encoded(proposals(claim(source, conditions={"op": "always"}), episodes=[episode(source)]))
        return invoke

    default = SemanticProcessor(provider(default_calls), verifier).process(batch(source), reserve=lambda **_: None)
    explicit = SemanticProcessor(provider(explicit_calls), verifier).process(
        batch(source, proposal_mode=GENERAL_PROPOSAL_MODE), reserve=lambda **_: None)
    assert default.status == explicit.status == "completed"
    assert len(default.claims) == len(default.episode_actions) == 1
    assert default_calls[0].messages == explicit_calls[0].messages
    assert default_calls[0].request_id == explicit_calls[0].request_id
    assert default_calls[0].response_schema == PROPOSAL_SCHEMA
    data = json.loads(default_calls[0].messages[-1]["content"])
    assert "proposal_mode" not in data
    assert "episodes" in data and "consolidation_plan" in data


def test_foreground_requests_are_smaller_without_changing_available_raw_evidence():
    source = event("现在我要制定复习计划。")
    collected = {}
    for mode in (GENERAL_PROPOSAL_MODE, ORIENT_PROPOSAL_MODE):
        requests = []

        def capture(request):
            requests.append(request)
            return verifier(request) if request.phase == "verification" else encoded(proposals(updates=[situation(source)]))

        result = SemanticProcessor(capture, capture).process(batch(source, proposal_mode=mode), reserve=lambda **_: None)
        assert result.status == "completed"
        collected[mode] = requests, result
    old_requests, old = collected[GENERAL_PROPOSAL_MODE]
    new_requests, new = collected[ORIENT_PROPOSAL_MODE]
    assert new.input_bytes < old.input_bytes
    assert json.loads(new_requests[0].messages[-1]["content"])["sources"] == [source]
    assert new_requests[1].messages[-1] == old_requests[1].messages[-1]
    assert new.model_calls == old.model_calls == 2


@pytest.mark.parametrize("kind", ["whole", "request", "source"])
def test_current_request_still_needs_independent_complete_support(kind):
    source = event("你记得我们以前去过海边吗？")
    refs = ({"event_id": source["event_id"], "revision": source["revision"]},)
    update = situation(source, evidence_use="current_request", value="核实是否有过去去海边的记录。")

    def verify(request):
        body = json.loads(verifier(request))
        assessment = body["assessments"][0]
        if kind == "whole":
            assessment["proposal_verdict"] = "unknown"
        elif kind == "request":
            assessment["request_verdict"] = "unknown"
        else:
            assessment["relations"][0]["verdict"] = "unknown"
        return encoded(body)

    result = run(oriented(source, current_input_refs=refs), {"situation_updates": [update]}, verify=verify)
    assert result.status == "partial" and result.model_calls == 2 and not result.situation_updates


def test_current_request_is_grounded_and_presupposition_is_not_an_observation():
    source = event("你记得我们以前去过海边吗？")
    refs = ({"event_id": source["event_id"], "revision": source["revision"]},)
    update = situation(source, evidence_use="current_request", value="核实是否有过去去海边的记录。")
    result = run(oriented(source, current_input_refs=refs), {"situation_updates": [
        update, situation(source, proposal_id="false-history", kind="context", value="用户以前去过海边。"),
    ]})
    assert result.status == "partial"
    assert [item["proposal_id"] for item in result.situation_updates] == [update["proposal_id"]]
    assert result.situation_updates[0]["request_verification"]["state"] == "supported_request"
    assert "observation" not in result.situation_updates[0]
    unbound = run(oriented(source), {"situation_updates": [update]})
    assert unbound.situation_updates == () and unbound.model_calls == 1


@pytest.mark.parametrize("mode", ["orient", "orient-v2", None, {}, []])
def test_unknown_modes_fail_before_reservation(mode):
    with pytest.raises(DomainError, match="proposal mode"):
        run(batch(event("当前输入。"), proposal_mode=mode), proposals(),
            reserve=lambda **_: pytest.fail("Unknown mode cannot spend"))


def test_foreground_cannot_be_given_background_objectives():
    source = event("当前输入。")
    data = oriented(source)
    plan = proposal_data(data.sources, (), {}, (), {}, ())["consolidation_plan"]
    plan["objectives"] = [{"objective_id": "not-an-orientation-task"}]
    with pytest.raises(DomainError, match="consolidation objectives"):
        run(replace(data, consolidation_plan=plan), proposals(),
            reserve=lambda **_: pytest.fail("Background objective cannot spend in foreground"))


def test_mode_changes_both_prompt_and_request_identity():
    source = event("当前输入。")
    data = proposal_data([source], (), {}, (), {}, ())
    original, original_size = request_payload("proposal", data, PROPOSAL_SCHEMA, 8192)
    unchanged, unchanged_size = request_payload("proposal", data, PROPOSAL_SCHEMA, 8192,
                                                proposal_mode=GENERAL_PROPOSAL_MODE)
    revised, revised_size = request_payload("proposal", data, ORIENT_PROPOSAL_SCHEMA, 8192,
                                            proposal_mode=ORIENT_PROPOSAL_MODE)
    assert original == unchanged and original_size == unchanged_size
    assert revised != original and revised_size < original_size


def test_snapshot_mode_is_explicit_and_legacy_absence_never_upgrades():
    assert _snapshot_proposal_mode({}, "orient") == GENERAL_PROPOSAL_MODE
    assert _snapshot_proposal_mode({"proposal_mode": ORIENT_PROPOSAL_MODE}, "orient") == ORIENT_PROPOSAL_MODE
    with pytest.raises(DomainError, match="proposal mode"):
        _snapshot_proposal_mode({"proposal_mode": ORIENT_PROPOSAL_MODE}, "consolidate")
    with pytest.raises(DomainError, match="proposal mode"):
        _snapshot_proposal_mode({"proposal_mode": "future-v9"}, "orient")


@pytest.mark.parametrize("group", ["claims", "episode_actions", "belief_links", "consolidation_decisions", "consolidation"])
def test_forged_foreground_results_cannot_publish_or_survive_reuse(group):
    result = {group: {"objectives": [{"objective_id": "unsupported"}]} if group == "consolidation" else [{"fake": True}]}
    with pytest.raises(DomainError, match="only current-situation"):
        check_result_mode(ORIENT_PROPOSAL_MODE, result)
    check_result_mode(GENERAL_PROPOSAL_MODE, copy.deepcopy(result))


@pytest.mark.parametrize("field", ["claims", "episode_actions", "belief_links", "consolidation"])
def test_low_level_publication_cannot_bypass_orientation_mode(field):
    value = {"objectives": [{"fake": True}]} if field == "consolidation" else ({"fake": True},)
    result = SemanticResult("completed", **{field: value})
    with pytest.raises(DomainError, match="only current-situation"):
        _check_result(oriented(event("当前输入。")), result, {})


def test_low_level_publication_rejects_unknown_modes_even_with_empty_results():
    with pytest.raises(DomainError, match="proposal mode"):
        _check_result(batch(event("当前输入。"), proposal_mode="unrecognized-mode"), SemanticResult("completed"), {})
