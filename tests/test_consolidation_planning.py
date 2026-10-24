import copy
import json
from dataclasses import replace

import pytest
from test_belief_semantic import abstraction, link_verifier, version
from test_cognition import event
from test_semantic import claim, encoded, proposals, run, verifier

from agent_memory.consolidation_planning import (
    MAX_OBJECTIVES,
    plan_consolidation,
    validate_consolidation_plan,
)
from agent_memory.helpers import digest
from agent_memory.semantic import SemanticPolicy, SemanticProcessor


def instances():
    data, response = abstraction()
    for memory in data.existing_assertions:
        memory["epistemic_status"] = "derived_inference"
    objective = plan(data)["objectives"][0]
    response["consolidation_decisions"] = [{"objective_id": objective["objective_id"], "outcome": "proposed",
                                          "reason": None, "link_proposal_ids": [response["belief_links"][0]["proposal_id"]]}]
    return data, response


def plan(data):
    return plan_consolidation(data.sources, data.existing_assertions, data.belief_relations, data.sampling)


def test_instance_comparison_is_bounded_deterministic_and_not_a_preset_conclusion():
    data, _ = instances()
    planned = plan(data)
    assert planned["candidate_count"] == 1
    assert len(planned["objectives"]) == 1
    objective = planned["objectives"][0]
    assert objective["operation"] == "compare_instances"
    assert "statement" not in objective
    assert objective["instance_memory_versions"] == sorted(
        [version(item) for item in data.existing_assertions], key=lambda item: item["memory_id"])
    assert plan(replace(data, sources=tuple(reversed(data.sources)),
                        existing_assertions=tuple(reversed(data.existing_assertions)))) == planned
    reminder = event("I remembered the earlier events.", 3)
    assert plan(replace(data, sources=(*data.sources, reminder))) == planned


@pytest.mark.parametrize("change", ["shared_source", "other_actor", "other_world", "other_condition",
                                     "unrelated", "schema", "counterexample", "literal", "missing_source"])
def test_unsafe_or_uninformative_instance_pairs_are_not_planned(change):
    data, _ = instances()
    memory = data.existing_assertions[1]
    if change == "shared_source":
        memory["evidence_refs"] = copy.deepcopy(data.existing_assertions[0]["evidence_refs"])
    elif change == "other_actor":
        data.sources[1]["observed"]["actor_ref"] = "different-user"
    elif change == "other_world":
        memory["world"] = "dream"
    elif change == "other_condition":
        memory["conditions"] = {"op": "unresolved", "label": "a different context"}
    elif change == "unrelated":
        memory["statement"] = "Hydraulic valves control industrial pressure."
    elif change == "schema":
        memory["representation"] = "schema"
    elif change == "counterexample":
        memory["counterexamples"] = copy.deepcopy(memory["evidence_refs"])
    elif change == "literal":
        memory["epistemic_status"] = "observed"
    else:
        data = replace(data, sources=data.sources[:1])
    assert plan(data)["objectives"] == []


def test_existing_abstraction_and_completed_attempt_are_not_repeated():
    data, _ = instances()
    identifier = plan(data)["objectives"][0]["objective_id"]
    relation = {"kind": "abstracts", "target_memory_versions": [version(item) for item in data.existing_assertions]}
    assert plan(replace(data, belief_relations=(relation,)))["objectives"] == []
    suppressed = plan_consolidation(data.sources, data.existing_assertions, attempted_ids=[identifier])
    assert suppressed["objectives"] == [] and suppressed["suppressed_count"] == 1
    data.existing_assertions[1]["revision"] += 1
    updated = plan_consolidation(data.sources, data.existing_assertions, attempted_ids=[identifier])
    assert len(updated["objectives"]) == 1 and updated["suppressed_count"] == 0


def test_new_instances_are_selected_after_higher_scored_attempted_groups():
    data, _ = instances()
    sources, memories = list(data.sources), list(data.existing_assertions)
    for index in range(3, 8):
        source = event("I chat before studying.", index)
        memory = copy.deepcopy(memories[1])
        memory["memory_id"] = f"new-memory-{index}"
        memory["evidence_refs"] = [claim(source)["evidence_refs"][0]]
        sources.append(source)
        memories.append(memory)
    first = plan_consolidation(sources, memories)
    assert len(first["objectives"]) == MAX_OBJECTIVES
    attempted = {item["objective_id"] for item in first["objectives"]}
    later = plan_consolidation(sources, memories, attempted_ids=attempted)
    assert len(later["objectives"]) == MAX_OBJECTIVES
    assert not attempted & {item["objective_id"] for item in later["objectives"]}


def test_actual_proposal_receives_objectives_and_verified_link_is_required_for_completion():
    data, response = instances()
    captured = []

    def propose(request):
        captured.append(json.loads(request.messages[-1]["content"]))
        return encoded(response)

    result = SemanticProcessor(propose, link_verifier).process(data, reserve=lambda **_: None)
    assert captured[0]["consolidation_plan"] == plan(data)
    assert result.model_calls == 2
    assert result.consolidation["satisfied_count"] == 1
    assert result.consolidation["unresolved_count"] == 0
    assert result.consolidation["objectives"][0]["reason_basis"] == "verified_relationship"
    rejected = run(data, response, verify=verifier)
    assert rejected.claims == rejected.belief_links == ()
    assert rejected.consolidation["satisfied_count"] == 0
    assert rejected.consolidation["unresolved_count"] == 1


def test_remembering_a_cue_does_not_count_as_a_completed_comparison():
    data, _ = instances()
    source = event("I remembered the earlier events.", 3)
    data = replace(data, sources=(*data.sources, source))
    proposed = claim(source, statement="The user recalled earlier events.", conditions={"op": "always"},
                     memory_kind="episodic", representation="episode_outline", claim_kind="reported_fact")
    result = run(data, proposals(proposed))
    assert result.status == "invalid_response" and not result.claims
    assert result.consolidation["satisfied_count"] == 0
    assert result.consolidation["unresolved_count"] == 1
    assert result.consolidation["objectives"][0]["attempted"] is False


def test_no_change_is_visible_as_unverified_model_judgment_without_extra_calls():
    data, _ = instances()
    decision = {"objective_id": plan(data)["objectives"][0]["objective_id"], "outcome": "no_change",
                "reason": "insufficient_shared_meaning", "link_proposal_ids": []}
    result = run(data, {**proposals(), "consolidation_decisions": [decision]})
    assert result.model_calls == 1 and not result.claims
    assert result.consolidation["satisfied_count"] == 0
    assert result.consolidation["no_change_count"] == 1
    outcome = result.consolidation["objectives"][0]
    assert outcome["reason_basis"] == "model_declared_not_verified"
    assert outcome["reason"] == "insufficient_shared_meaning"


@pytest.mark.parametrize("kind", ["budget", "provider", "invalid_response", "unknown_objective"])
def test_unsent_or_invalid_attempt_never_suppresses_a_future_comparison(kind):
    data, _ = instances()
    policy = SemanticPolicy(max_model_calls=0) if kind == "budget" else None

    def propose(_):
        if kind == "provider":
            raise TimeoutError()
        if kind == "unknown_objective":
            return encoded({**proposals(), "consolidation_decisions": [{
                "objective_id": "invented", "outcome": "no_change", "reason": "insufficient_evidence",
                "link_proposal_ids": []}]})
        return "not json"

    result = SemanticProcessor(propose, verifier, policy).process(data, reserve=lambda **_: None)
    assert result.consolidation["objectives"][0]["attempted"] is False


@pytest.mark.parametrize("change", ["actor", "target_condition", "claim_condition"])
def test_abstraction_validation_preserves_instance_attribution_and_conditions(change):
    data, response = instances()
    if change == "actor":
        data.sources[1]["observed"]["actor_ref"] = "different-user"
    elif change == "target_condition":
        data.existing_assertions[1]["conditions"] = {"op": "unresolved", "label": "nighttime"}
    else:
        response["claims"][0]["conditions"] = {"op": "unresolved", "label": "nighttime"}
    response.pop("consolidation_decisions")
    result = run(data, response, verify=link_verifier)
    assert result.belief_links == ()


@pytest.mark.parametrize("change", ["missing_decision", "missing_link", "wrong_kind", "wrong_targets", "duplicate_link"])
def test_proposed_decision_requires_its_exact_planned_abstraction(change):
    data, response = instances()
    if change == "missing_decision":
        response.pop("consolidation_decisions")
    elif change == "missing_link":
        response["consolidation_decisions"][0]["link_proposal_ids"] = ["nonexistent"]
    elif change == "wrong_kind":
        response["belief_links"][0]["kind"] = "contradicts"
    elif change == "wrong_targets":
        response["belief_links"][0]["target_memory_versions"].pop()
    else:
        response["consolidation_decisions"][0]["link_proposal_ids"] *= 2
    result = run(data, response, verify=link_verifier)
    assert result.status == "invalid_response" and result.model_calls == 1
    assert result.consolidation["objectives"][0]["attempted"] is False


def test_already_related_rules_are_not_compared_as_unorganized_instances():
    data, _ = instances()
    relation = {"kind": "exception", "source_memory_version": version(data.existing_assertions[1]),
                "target_memory_versions": [version(data.existing_assertions[0])]}
    planned = plan(replace(data, belief_relations=(relation,)))
    assert planned["objectives"] == []
    assert planned["excluded_counts"]["already_related"] == 1


@pytest.mark.parametrize("failure", ["provider", "invalid_json", "missing_assessment", "missing_link_verdict"])
def test_incomplete_verification_does_not_permanently_suppress_a_valid_proposal(failure):
    data, response = instances()

    def verify(request):
        if failure == "provider":
            raise TimeoutError()
        if failure == "invalid_json":
            return "not json"
        result = json.loads(link_verifier(request))
        if failure == "missing_assessment":
            result["assessments"].pop()
        else:
            result["assessments"][-1]["link_verdict"] = None
        return encoded(result)

    result = run(data, response, verify=verify)
    outcome = result.consolidation["objectives"][0]
    assert outcome["attempted"] is True
    assert outcome["dedup_eligible"] is False
    assert outcome["status"] == "unresolved"


def test_completed_verification_can_decline_abstraction_without_repeating_identical_goal():
    data, response = instances()
    result = run(data, response, verify=lambda request: link_verifier(request, "unknown"))
    outcome = result.consolidation["objectives"][0]
    assert outcome["dedup_eligible"] is True
    assert outcome["status"] == "unresolved" and not result.belief_links


LONG_INSTANCE_TEXTS = (
    "At dawn I visited the orchard with a canvas bag, counted several green apples, repaired the wooden gate, and watered rows of carrots.",
    "During winter I inspected the orchard beside a frozen stream, photographed broken branches, measured the fence, and recorded cloudy weather.",
    "After lunch the orchard hosted an exhibition with bronze sculptures, velvet curtains, ceramic bowls, and illuminated glass panels.",
)


def text_instances(texts):
    from test_belief_semantic import known
    from test_semantic import batch

    sources = tuple(event(text, index) for index, text in enumerate(texts, 1))
    memories = []
    for source, text in zip(sources, texts, strict=True):
        memory = known(source, text)
        memory.update(epistemic_status="derived_inference", claim_kind="reported_fact", memory_kind="episodic")
        memories.append(memory)
    return batch(*sources, existing_assertions=tuple(memories))


@pytest.mark.parametrize("texts", [LONG_INSTANCE_TEXTS[:2], (
    "上周我乘公交到市图书馆借了三本关于植物学的书，还在门口买了一杯咖啡，随后沿河步行回家。",
    "今天午后我在家查阅植物学资料，准备给阳台上的花换盆，并把整个周末的购物清单重新整理了一遍。",
)])
def test_long_instances_with_small_substantive_overlap_get_one_exploration_slot(texts):
    data = text_instances(texts)
    planned = plan(data)
    assert planned["planner_version"] == "independent-instance-comparison-v3"
    assert planned["candidate_count"] == 1
    assert len(planned["objectives"]) == 1
    assert planned["objectives"][0]["selection_basis"] == "weak_lexical_exploration"


def test_weak_exploration_reserves_only_one_slot_and_retains_two_strong_candidates():
    strong = ("A cedar grows near the garden fence.", "A cedar grows beside the garden fence.",
              "A cedar grows alongside the garden fence.")
    data = text_instances((*strong, *LONG_INSTANCE_TEXTS))
    planned = plan(data)
    assert len(planned["objectives"]) == MAX_OBJECTIVES
    assert [item["selection_basis"] for item in planned["objectives"]] == [
        "strong_lexical_lead", "strong_lexical_lead", "weak_lexical_exploration"]
    complete = plan_consolidation(data.sources, data.existing_assertions, include_all=True)
    assert len(complete["objectives"]) > MAX_OBJECTIVES
    assert sum(item["selection_basis"] == "weak_lexical_exploration" for item in complete["objectives"]) > 1
    assert plan(replace(data, sources=tuple(reversed(data.sources)),
                        existing_assertions=tuple(reversed(data.existing_assertions)))) == planned


def test_no_weak_candidates_leaves_all_three_slots_for_strong_candidates():
    data = text_instances(("A cedar grows near the garden fence.", "A cedar grows beside the garden fence.",
                           "A cedar grows alongside the garden fence."))
    planned = plan(data)
    assert len(planned["objectives"]) == MAX_OBJECTIVES
    assert all(item["selection_basis"] == "strong_lexical_lead" for item in planned["objectives"])


def test_weak_candidates_are_suppressed_before_the_exploration_slot_is_selected():
    data = text_instances(LONG_INSTANCE_TEXTS)
    complete = plan_consolidation(data.sources, data.existing_assertions, include_all=True)
    assert len(complete["objectives"]) == 3
    first = plan(data)
    assert len(first["objectives"]) == 1
    attempted = [first["objectives"][0]["objective_id"]]
    following = plan_consolidation(data.sources, data.existing_assertions, attempted_ids=attempted)
    assert following["suppressed_count"] == 1
    assert following["objectives"] == [complete["objectives"][1]]
    exhausted = plan_consolidation(data.sources, data.existing_assertions,
                                   attempted_ids=[item["objective_id"] for item in complete["objectives"]])
    assert exhausted["objectives"] == [] and exhausted["suppressed_count"] == 3


def test_weak_overlap_can_be_declined_without_becoming_a_verified_commonality():
    data = text_instances(LONG_INSTANCE_TEXTS[:2])
    objective = plan(data)["objectives"][0]
    result = run(data, {**proposals(), "consolidation_decisions": [{
        "objective_id": objective["objective_id"], "outcome": "no_change",
        "reason": "insufficient_shared_meaning", "link_proposal_ids": []}]})
    assert result.model_calls == 1 and not result.claims and not result.belief_links
    outcome = result.consolidation["objectives"][0]
    assert outcome["status"] == "no_change" and outcome["dedup_eligible"] is True
    assert outcome["reason_basis"] == "model_declared_not_verified"


def test_weak_exploration_does_not_include_completely_unrelated_long_instances():
    data = text_instances((LONG_INSTANCE_TEXTS[0], "Hydraulic valves control industrial pressure."))
    planned = plan(data)
    assert planned["candidate_count"] == 0 and planned["objectives"] == []


@pytest.mark.parametrize("planner_version", ["independent-instance-comparison-v1", "independent-instance-comparison-v2"])
def test_frozen_legacy_plan_retains_its_identity_and_is_still_validated(planner_version):
    data, response = instances()
    legacy = plan_consolidation(data.sources, data.existing_assertions,
                               planner_version=planner_version)
    assert ("selection_basis" in legacy["objectives"][0]) == planner_version.endswith("v2")
    assert "comparison_policy" not in legacy["objectives"][0]
    response["consolidation_decisions"][0]["objective_id"] = legacy["objectives"][0]["objective_id"]
    result = run(replace(data, consolidation_plan=legacy), response, verify=link_verifier)
    assert result.status == "completed" and result.consolidation["satisfied_count"] == 1
    assert result.consolidation["planner_version"] == planner_version


@pytest.mark.parametrize(("planner_version", "fingerprint"), [
    ("independent-instance-comparison-v1", "2778d8f87d28490aa4f6bbfef0d9e3d59c5d76286d3d203605d9874304fcd9ed"),
    ("independent-instance-comparison-v2", "fa6806f4382f2bd3788befc7a4b40ec7b89573a257491aa5d1e0f4712bde2cbe"),
])
def test_frozen_legacy_planners_keep_exact_candidate_identity_and_selection(planner_version, fingerprint):
    strong = ("A cedar grows near the garden fence.", "A cedar grows beside the garden fence.",
              "A cedar grows alongside the garden fence.")
    data = text_instances((*strong, *LONG_INSTANCE_TEXTS))
    frozen = plan_consolidation(data.sources, data.existing_assertions, planner_version=planner_version)
    assert digest(frozen) == fingerprint
    validate_consolidation_plan(frozen, data.sources, data.existing_assertions, data.belief_relations, data.sampling)


def test_new_comparison_policy_reopens_a_declined_legacy_goal_only_once():
    data, _ = instances()
    previous = plan_consolidation(data.sources, data.existing_assertions,
                                 planner_version="independent-instance-comparison-v2")
    attempted = [previous["objectives"][0]["objective_id"]]
    revised = plan_consolidation(data.sources, data.existing_assertions, attempted_ids=attempted)
    assert revised["suppressed_count"] == 0 and len(revised["objectives"]) == 1
    objective = revised["objectives"][0]
    assert objective["comparison_policy"] == "minimal-shared-meaning-v1"
    assert objective["objective_id"] not in attempted
    repeated = plan_consolidation(data.sources, data.existing_assertions,
                                   attempted_ids=[*attempted, objective["objective_id"]])
    assert repeated["objectives"] == [] and repeated["suppressed_count"] == 1


@pytest.mark.parametrize("policy", [None, "invented-policy"])
def test_new_frozen_plan_rejects_missing_or_changed_comparison_policy(policy):
    data, _ = instances()
    forged = plan(data)
    if policy is None:
        del forged["objectives"][0]["comparison_policy"]
    else:
        forged["objectives"][0]["comparison_policy"] = policy
    with pytest.raises(ValueError, match="Invalid consolidation objective"):
        validate_consolidation_plan(forged, data.sources, data.existing_assertions, data.belief_relations, data.sampling)


def test_supplied_plan_cannot_expand_the_weak_exploration_budget():
    data = text_instances(LONG_INSTANCE_TEXTS)
    forged = plan_consolidation(data.sources, data.existing_assertions, include_all=True)
    forged["objectives"] = forged["objectives"][:2]
    from agent_memory.helpers import DomainError

    with pytest.raises(DomainError, match="Invalid consolidation plan"):
        run(replace(data, consolidation_plan=forged), proposals())
