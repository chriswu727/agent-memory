import copy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from test_applicability import AT, holds, observation
from test_belief_semantic import scenario
from test_cognition import event
from test_semantic import batch, claim, proposals, run, situation

from agent_memory.applicability import (
    CONDITION_SCHEMA,
    NEW_CONDITION_SCHEMA,
    evaluate_applicability,
    validate_condition,
    validate_new_condition,
    validate_observation,
)
from agent_memory.semantic import PROPOSAL_SCHEMA, SemanticProcessor, evaluate_condition

UNRESOLVED = {"op": "unresolved", "label": "银色状态"}
LEGACY = {"op": "eq", "key": "user_defined_state", "value": "silver"}


@pytest.mark.parametrize("expression", [
    LEGACY,
    {**LEGACY, "op": "ne"},
    {"op": "not", "arg": LEGACY},
    {"op": "any", "args": [{"op": "always"}, LEGACY]},
    {"op": "all", "args": [holds(), {"op": "not", "arg": LEGACY}]},
])
def test_legacy_condition_remains_readable_but_cannot_be_new_model_output(expression):
    before = copy.deepcopy(expression)
    validate_condition(expression)
    assert Draft202012Validator(CONDITION_SCHEMA).is_valid(expression)
    assert not Draft202012Validator(NEW_CONDITION_SCHEMA).is_valid(expression)
    with pytest.raises(ValueError, match="Invalid condition"):
        validate_new_condition(expression)
    assert expression == before


def test_historical_free_key_conditions_do_not_prove_current_context():
    result = evaluate_applicability(LEGACY, [observation()], at=AT)
    assert result.value == "unknown"
    assert result.reasons == ("legacy_condition",)
    assert not result.supporting_observation_ids


def test_unresolved_label_is_never_implicitly_mapped_to_a_registered_concept():
    expression = {"op": "unresolved", "label": "备考"}
    validate_new_condition(expression)
    result = evaluate_applicability(expression, [observation()], at=AT)
    assert result.value == "unknown"
    assert result.reasons == ("unresolved_condition",)
    assert not result.supporting_observation_ids


@pytest.mark.parametrize("expression,expected", [
    (UNRESOLVED, "unknown"),
    ({"op": "not", "arg": UNRESOLVED}, "unknown"),
    ({"op": "all", "args": [{"op": "always"}, UNRESOLVED]}, "unknown"),
    ({"op": "any", "args": [{"op": "always"}, UNRESOLVED]}, "true"),
    ({"op": "all", "args": [{"op": "not", "arg": {"op": "always"}}, UNRESOLVED]}, "false"),
])
def test_unresolved_concept_preserves_three_valued_logic(expression, expected):
    validate_new_condition(expression)
    result = evaluate_applicability(expression, [observation()], at=AT)
    assert result.value == expected
    assert evaluate_condition(expression, {"user_defined_state": "silver"}) == expected
    assert not result.supporting_observation_ids
    if expected == "unknown":
        assert result.reasons == ("unresolved_condition",)


@pytest.mark.parametrize("expression", [
    {"op": "unresolved", "label": ""},
    {"op": "unresolved", "label": "x" * 513},
    {"op": "unresolved", "label": "silver", "value": True},
    {"op": "unresolved", "label": "silver", "predicate_ref": {"id": "core.emotion", "version": 1}},
    holds("activity:silver"),
    holds(value={"type": "string", "value": "silver"}),
])
def test_unknown_concept_cannot_invent_a_registry_mapping_or_untyped_value(expression):
    with pytest.raises(ValueError):
        validate_new_condition(expression)


@pytest.mark.parametrize("changes", [
    {"value": UNRESOLVED},
    {"value": {"type": "unresolved", "label": "银色状态"}},
    {"value": {"type": "concept", "id": "activity:silver"}},
    {"predicate_ref": {"id": "user_defined_state", "version": 1}},
])
def test_unresolved_condition_is_not_a_registered_current_state_observation(changes):
    with pytest.raises(ValueError):
        validate_observation(observation(**changes))


def test_model_can_preserve_unresolved_concept_without_activating_it():
    source = event("只有处于银色状态时，我才需要诗歌式回答。我没有解释银色状态是什么意思。")
    proposed = claim(source, statement="用户报告只有在银色状态时才需要诗歌式回答。",
                     conditions=copy.deepcopy(UNRESOLVED))
    result = run(batch(source), proposals(proposed))
    assert result.status == "completed"
    assert len(result.claims) == 1
    assert result.claims[0]["conditions"] == UNRESOLVED
    assert evaluate_applicability(result.claims[0]["conditions"], [], at=AT).value == "unknown"


def proposal_with_condition(location, condition):
    data, response = scenario("exception")
    item = response["claims"][0]
    group = "claims"
    if location == "situation":
        item = situation(data.sources[-1])
        response["situation_updates"] = [item]
        group = "situation_updates"
    elif location == "link":
        item = response["belief_links"][0]
        group = "belief_links"
    if location in {"exit_conditions", "failure_conditions"}:
        source = event("任务检查已完成。", 2, role="tool")
        data = replace(data, sources=(data.sources[0], source))
        item = claim(source, conditions={"op": "always"})
        response["claims"], response["belief_links"] = [item], []
        item.update(memory_kind="procedural", representation="procedure", claim_kind="procedure",
                    procedure={"steps": ["执行任务"], "exit_conditions": {"op": "always"},
                               "failure_conditions": {"op": "not", "arg": {"op": "always"}},
                               "environment": "test", "attempt_event_ids": [data.sources[-1]["event_id"]]})
        item["procedure"][location] = condition
    else:
        item["conditions"] = condition
    return data, response, group, item


@pytest.mark.parametrize("location", ["claim", "situation", "link", "exit_conditions", "failure_conditions"])
@pytest.mark.parametrize("condition", [LEGACY, {"op": "any", "args": [{"op": "always"}, LEGACY]}])
def test_all_new_semantic_condition_positions_reject_legacy_before_verification(location, condition):
    data, response, group, item = proposal_with_condition(location, copy.deepcopy(condition))
    assert not Draft202012Validator(PROPOSAL_SCHEMA).is_valid(response)
    with pytest.raises(ValueError, match="Invalid (condition|belief relationship)"):
        SemanticProcessor._validate_proposal(
            group, item, data.scope,
            {(source["event_id"], source["revision"]): source for source in data.sources},
            {(target["memory_id"], target["revision"]): target for target in data.existing_assertions},
            {}, {proposed["proposal_id"]: proposed for proposed in response["claims"]},
        )

    def unexpected_verification(_):
        pytest.fail("Invalid generated conditions must not reach a verifier")

    result = run(data, response, verify=unexpected_verification)
    assert result.status == "invalid_response"
    assert result.claims == result.situation_updates == result.belief_links == ()
    assert result.model_calls == 1
