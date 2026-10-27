import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_bounded_learning_semantics import publication_snapshot
from test_semantic import encoded, verifier

from membrain.consolidation import _check_result
from membrain.helpers import DomainError
from membrain.history_semantics import LINK_DERIVED_INPUT_VERSION
from membrain.local_semantics import LINK_DERIVED_INSTRUCTIONS
from membrain.semantic import CognitiveBatch, SemanticPolicy, SemanticProcessor


@pytest.fixture
def recorded():
    path = Path(__file__).parent / "fixtures/recorded_history_link_incidence_failure.json"
    fixture = json.loads(path.read_text())
    return CognitiveBatch(**fixture["batch"]), fixture


def candidate_response(fixture):
    response = copy.deepcopy(fixture["response"])
    targets = {(ref["memory_id"], ref["revision"])
               for link in response["belief_links"] for ref in link["target_memory_versions"]}
    objectives = {item["objective_id"]: item["target_memory_version"]
                  for item in fixture["batch"]["integration_manifest"]["objectives"]}
    response["integration_abstentions"] = [
        {"objective_id": item["objective_id"], "reason": item["reason"]}
        for item in response.pop("integration_decisions")
        if (objectives[item["objective_id"]]["memory_id"],
            objectives[item["objective_id"]]["revision"]) not in targets
    ]
    return response


def evaluate(data, fixture, response, *, reject_link=False):
    calls, reservations = [], []

    def propose(request):
        calls.append(request)
        return encoded(response)

    def verify(request):
        calls.append(request)
        result = json.loads(verifier(request))
        entries = json.loads(request.messages[-1]["content"])["proposals"]
        for entry, assessment in zip(entries, result["assessments"], strict=True):
            if entry["group"] == "belief_links":
                assessment["link_verdict"] = "unknown" if reject_link else "supports"
        return encoded(result)

    result = SemanticProcessor(propose, verify, SemanticPolicy(**fixture["policy"])).process(
        data, reserve=lambda **values: reservations.append(values),
    )
    return result, calls, reservations


def test_recorded_legacy_request_and_failure_remain_exact(recorded):
    data, fixture = recorded
    result, calls, reservations = evaluate(data, fixture, fixture["response"])
    assert len(calls) == len(reservations) == result.model_calls == 1
    original = fixture["request"]
    assert list(calls[0].messages) == original["messages"]
    assert calls[0].response_schema == original["response_schema"]
    assert calls[0].request_id == original["request_id"]
    assert result.status == fixture["observed_result"]["status"] == "invalid_response"
    assert result.input_bytes == fixture["observed_result"]["input_bytes"]
    assert result.history_integration == fixture["observed_result"]["history_integration"]
    assert not result.claims and not result.belief_links


def test_new_wire_derives_both_endpoints_then_requires_verification_and_publication_checks(recorded):
    old, fixture = recorded
    data = replace(old, input_contract_version=LINK_DERIVED_INPUT_VERSION)
    response = candidate_response(fixture)
    result, calls, reservations = evaluate(data, fixture, response)
    assert result.status == "completed" and result.model_calls == len(calls) == len(reservations) == 2
    assert LINK_DERIVED_INSTRUCTIONS in calls[0].messages[0]["content"]
    assert "integration_abstentions" in calls[0].response_schema["required"]
    assert "integration_decisions" not in calls[0].response_schema["properties"]
    assert result.input_bytes == sum(item["input_bytes"] for item in reservations)
    assert result.input_bytes <= fixture["policy"]["max_input_bytes"]
    assert len(result.claims) == len(result.belief_links) == 1
    assert result.claims[0]["statement"] == fixture["response"]["claims"][0]["statement"]
    assert result.history_integration["assessed_count"] == 3
    assert result.history_integration["published_count"] == 2
    proposed = [item for item in result.integration_decisions if item["outcome"] == "proposed"]
    assert len(proposed) == 2
    assert all(item["link_proposal_ids"] == ["link-0001"] for item in proposed)
    _check_result(data, result, publication_snapshot(data))


@pytest.mark.parametrize("violation", ["old_wire", "contradictory_abstention", "missing_abstention"])
def test_new_wire_never_repairs_a_contradiction_or_retries_it(recorded, violation):
    old, fixture = recorded
    data = replace(old, input_contract_version=LINK_DERIVED_INPUT_VERSION)
    response = candidate_response(fixture)
    if violation == "old_wire":
        response = fixture["response"]
    elif violation == "missing_abstention":
        response["integration_abstentions"] = []
    else:
        response["integration_abstentions"].append({
            "objective_id": old.integration_manifest["objectives"][0]["objective_id"],
            "reason": "already_represented",
        })
    result, calls, reservations = evaluate(data, fixture, response)
    assert result.status == "invalid_response"
    assert len(calls) == len(reservations) == result.model_calls == 1
    assert not result.claims and not result.belief_links
    assert result.history_integration["assessed_count"] == 0


def test_derived_coverage_is_not_semantic_approval(recorded):
    old, fixture = recorded
    data = replace(old, input_contract_version=LINK_DERIVED_INPUT_VERSION)
    result, calls, _ = evaluate(data, fixture, candidate_response(fixture), reject_link=True)
    assert len(calls) == 2 and result.status == "partial"
    assert not result.claims and not result.belief_links
    assert result.history_integration["published_count"] == 0
    _check_result(data, result, publication_snapshot(data))


def test_publication_rechecks_derived_decisions_and_original_links(recorded):
    old, fixture = recorded
    data = replace(old, input_contract_version=LINK_DERIVED_INPUT_VERSION)
    result, _, _ = evaluate(data, fixture, candidate_response(fixture))
    decisions = copy.deepcopy(list(result.integration_decisions))
    decisions[0].update(outcome="no_change", reason="already_represented", link_proposal_ids=[])
    forged = replace(result, integration_decisions=tuple(decisions))
    with pytest.raises(DomainError):
        _check_result(data, forged, publication_snapshot(data))
