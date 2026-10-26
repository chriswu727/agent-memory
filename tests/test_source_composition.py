import copy
import json
from dataclasses import replace

import pytest
from test_bounded_learning_semantics import local_batch, publication_snapshot
from test_cognition import event
from test_semantic import encoded, episode, proposals, verifier

from agent_memory.consolidation import _assertion, _check_result
from agent_memory.episode_semantics import EPISODE_FIDELITY_INSTRUCTIONS, EPISODE_INSTRUCTIONS
from agent_memory.helpers import DomainError
from agent_memory.recall_resolution import RESOLUTION_INPUT_VERSION
from agent_memory.semantic import SemanticProcessor, proposal_schema, request_payload
from agent_memory.source_composition import PARTICIPANT_ACTIONS, SOURCE_COMPOSITION_INPUT_VERSION


def run(statement, verdict="supports", *, version=SOURCE_COMPOSITION_INPUT_VERSION, missing=False):
    source = event("My aunt unpacked the clay. I shaped a bowl while my cousin watched.")
    proposed = episode(source, organization_statement=statement)
    data = local_batch(source, input_contract_version=version)
    calls = []

    def verify(request):
        calls.append(request)
        value = json.loads(verifier(request))
        fidelity = value["assessments"][0]["organization_fidelity"]
        if version == SOURCE_COMPOSITION_INPUT_VERSION:
            if missing:
                fidelity.pop("participant_actions")
            else:
                fidelity["participant_actions"] = verdict
        return encoded(value)

    result = SemanticProcessor(lambda _: encoded(proposals(episodes=[proposed])), verify).process(
        data, reserve=lambda **_: None)
    return data, result, calls


@pytest.mark.parametrize("verdict", ["unknown", "contradicts"])
def test_actor_action_veto_is_independent_of_speaker_and_other_positive_facets(verdict):
    _, result, calls = run("The user described their aunt unpacking clay and shaping a bowl.", verdict)
    assert result.status == "partial" and not result.episode_actions
    assert result.model_calls == 2 and len(calls) == 1
    audit = result.verification_assessments[0]["organization_fidelity"]
    assert audit["attribution"] == "supports" and audit["participant_actions"] == verdict


def test_four_facet_verifier_cannot_silently_pass_new_contract():
    _, result, _ = run("The user described a family clay activity.", missing=True)
    assert result.status == "invalid_response" and not result.episode_actions


def test_publication_rechecks_actor_action_fidelity_without_repairing_the_audit():
    data, result, _ = run("The user shaped a bowl; their aunt unpacked clay and their cousin watched.")
    assert result.status == "completed" and len(result.episode_actions) == 1
    _check_result(data, result, publication_snapshot(data))
    for value in ("unknown", None):
        audit = copy.deepcopy(result.verification_assessments)
        if value is None:
            audit[0]["organization_fidelity"].pop("participant_actions")
        else:
            audit[0]["organization_fidelity"]["participant_actions"] = value
        with pytest.raises(DomainError, match="Episode fidelity"):
            _check_result(data, replace(result, verification_assessments=audit), publication_snapshot(data))


def test_preceding_frozen_contract_keeps_its_four_facet_schema():
    _, result, calls = run("The user described a family clay activity.", version=RESOLUTION_INPUT_VERSION)
    assert result.status == "completed"
    schema = calls[0].response_schema["properties"]["assessments"]["items"]["properties"]
    assert "participant_actions" not in schema["organization_fidelity"]["properties"]
    assert "exactly five verdicts" not in calls[0].messages[0]["content"]


def test_history_claims_keep_actor_constraints_without_spending_budget_on_unavailable_episode_actions():
    schema = proposal_schema("history-integration-v1", input_contract_version=SOURCE_COMPOSITION_INPUT_VERSION)
    assert schema["properties"]["episode_actions"]["maxItems"] == 0
    messages, _ = request_payload("proposal", {}, schema, 4096, proposal_mode="history-integration-v1",
                                  input_contract_version=SOURCE_COMPOSITION_INPUT_VERSION)
    instructions = messages[0]["content"]
    assert PARTICIPANT_ACTIONS in instructions
    assert EPISODE_INSTRUCTIONS not in instructions and EPISODE_FIDELITY_INSTRUCTIONS not in instructions
    assert "For text in the user's voice" not in instructions


def test_new_contract_keeps_verified_short_expression_and_full_source_recovery():
    from test_recall_resolution import SHORT, example, read, visible

    source, data, result, _ = example(version=SOURCE_COMPOSITION_INPUT_VERSION)
    assert result.status == "completed"
    _check_result(data, result, publication_snapshot(data))
    memory = _assertion(result.claims[0], data, {"dependency_generation": 0},
                        {"root_event_ids": [source["event_id"]]})
    assert memory["derivation"]["rule_version"] == SOURCE_COMPOSITION_INPUT_VERSION
    aged = read(source, memory, at="2040-10-22T12:00:00Z")
    item = next(item for item in visible(aged) if item["memory"]["memory_id"] == memory["memory_id"])
    assert item["presentation"]["level"] == "summary" and SHORT in aged["model_context"]
    expanded = read(source, memory, "Grandma yellow lid", at="2040-10-22T12:00:00Z")
    assert "yellow lid" in expanded["model_context"]
