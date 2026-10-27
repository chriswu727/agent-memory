import copy
from dataclasses import replace

import pytest
from test_consolidation_planning import LONG_INSTANCE_TEXTS, text_instances

from membrain.consolidation_planning import (
    MAX_OBJECTIVES,
    PLANNER_VERSION,
    SELECTION_POLICY,
    plan_consolidation,
    validate_consolidation_plan,
)

V3 = "independent-instance-comparison-v3"
CROWDED_TEXTS = (
    "cedar garden fence shade", "cedar garden fence shade",
    "kayak ocean tide launch blue safety", "kayak ocean paddle shore calm return",
)


def fragmented_data(texts=CROWDED_TEXTS, copies=(3, 3, 1, 1)):
    data = text_instances(texts)
    memories = []
    for index, original in enumerate(data.existing_assertions):
        for fragment in range(copies[index]):
            memory = copy.deepcopy(original)
            memory["memory_id"] = f"group-{index}-fragment-{fragment}"
            words = memory["statement"].split()
            memory["statement"] = " ".join(words[:len(words) - fragment])
            memories.append(memory)
    return replace(data, existing_assertions=tuple(memories))


def plan(data, **options):
    return plan_consolidation(data.sources, data.existing_assertions, data.belief_relations, data.sampling, **options)


def source_pairs(data, objectives):
    memories = {(item["memory_id"], item["revision"]): item for item in data.existing_assertions}
    return [frozenset(frozenset(reference["event_id"] for reference in
                               memories[(version["memory_id"], version["revision"])]["evidence_refs"]
                               if reference["relationship"] == "supports")
                      for version in objective["instance_memory_versions"])
            for objective in objectives]


def test_repeated_fragments_no_longer_consume_every_comparison_slot():
    data = fragmented_data()
    old = plan(data, planner_version=V3)
    revised = plan(data)
    old_pairs = source_pairs(data, old["objectives"])
    revised_pairs = source_pairs(data, revised["objectives"])
    assert old["candidate_count"] == revised["candidate_count"] == 10
    assert len(old["objectives"]) == len(revised["objectives"]) == MAX_OBJECTIVES
    assert len(set(old_pairs)) == 1
    assert len(set(revised_pairs)) == 2 and revised_pairs[0] != revised_pairs[1]
    assert revised["planner_version"] == PLANNER_VERSION
    assert revised["selection_policy"] == SELECTION_POLICY
    validate_consolidation_plan(revised, data.sources, data.existing_assertions, (), data.sampling)


@pytest.mark.parametrize("texts", [
    ("copper wire coil circuit", "copper wire coil circuit", "copper wire coil meter",
     "pottery kiln glaze firing cooling", "pottery kiln bowl workshop clay"),
    ("camera lens shutter focus", "camera lens shutter focus", "camera lens shutter tripod",
     "bread yeast flour kneading warm", "bread yeast dough baking crust"),
])
def test_unused_sources_outrank_another_high_overlap_pair_with_an_already_selected_source(texts):
    data = text_instances(texts)
    old = plan(data, planner_version=V3)
    revised = plan(data)
    old_sources = set().union(*(set().union(*pair) for pair in source_pairs(data, old["objectives"])))
    pairs = source_pairs(data, revised["objectives"])
    independent = frozenset((frozenset({"event-4"}), frozenset({"event-5"})))
    assert {"event-4", "event-5"}.isdisjoint(old_sources)
    assert pairs[1] == independent
    assert set().union(*pairs[0]).isdisjoint(set().union(*pairs[1]))
    assert len(revised["objectives"]) == MAX_OBJECTIVES


@pytest.mark.parametrize("copies", [(1, 1, 1, 1, 1, 1), (4, 4, 2, 2, 1, 1)])
def test_three_independent_groups_are_covered_before_reusing_any_source_pair(copies):
    texts = (*CROWDED_TEXTS, "pottery kiln glaze firing cooling", "pottery kiln bowl workshop clay")
    data = fragmented_data(texts, copies)
    result = plan(data)
    assert len(result["objectives"]) == MAX_OBJECTIVES
    pairs = source_pairs(data, result["objectives"])
    assert len(set(pairs)) == MAX_OBJECTIVES
    assert len(set().union(*(set().union(*pair) for pair in pairs))) == 6
    reversed_data = replace(data, sources=tuple(reversed(data.sources)),
                            existing_assertions=tuple(reversed(data.existing_assertions)))
    assert plan(reversed_data) == result


def test_source_pair_grouping_uses_complete_multi_event_support_sets():
    data = fragmented_data()
    additional = text_instances(tuple(f"Extra original context {number}." for number in range(4)))
    sources = list(data.sources)
    extra_by_group = {}
    for index, source in enumerate(additional.sources):
        source = copy.deepcopy(source)
        source["event_id"] = f"extra-{index}"
        sources.append(source)
        extra_by_group[index] = source
    memories = []
    for original in data.existing_assertions:
        memory = copy.deepcopy(original)
        group = int(memory["memory_id"].split("-")[1])
        reference = copy.deepcopy(memory["evidence_refs"][0])
        extra = extra_by_group[group]
        reference.update(event_id=extra["event_id"], event_revision=extra["revision"],
                         source_content_sha256=extra["content_sha256"])
        reference["span"]["end"] = len(extra["observed"]["content"]["text"])
        memory["evidence_refs"].append(reference)
        if memory["memory_id"].endswith("-1"):
            memory["evidence_refs"].reverse()
        memories.append(memory)
    data = replace(data, sources=tuple(sources), existing_assertions=tuple(memories))
    result = plan(data)
    pairs = source_pairs(data, result["objectives"])
    assert len(set(pairs)) == 2 and pairs[0] != pairs[1]
    assert all(len(instance) == 2 for pair in pairs for instance in pair)
    validate_consolidation_plan(result, data.sources, data.existing_assertions, (), data.sampling)


def test_weak_exploration_stays_bounded_and_does_not_remove_strong_comparisons():
    texts = (*CROWDED_TEXTS, *LONG_INSTANCE_TEXTS)
    data = fragmented_data(texts, (3, 3, 1, 1, 1, 1, 1))
    result = plan(data)
    assert len(result["objectives"]) == MAX_OBJECTIVES
    assert [item["selection_basis"] for item in result["objectives"]] == [
        "strong_lexical_lead", "strong_lexical_lead", "weak_lexical_exploration",
    ]
    pairs = source_pairs(data, result["objectives"])
    assert len(set(pairs)) == 3
    assert set().union(*pairs[2]).isdisjoint(set().union(*pairs[0], *pairs[1]))
    validate_consolidation_plan(result, data.sources, data.existing_assertions, (), data.sampling)


def test_ranking_upgrade_keeps_v3_comparison_identity_and_suppresses_prior_assessments():
    data = fragmented_data()
    old = plan(data, planner_version=V3, include_all=True)
    current = plan(data, include_all=True)
    assert old["objectives"] == current["objectives"]
    first_pair = source_pairs(data, old["objectives"])[0]
    attempted = [item["objective_id"] for item, pair in zip(
        old["objectives"], source_pairs(data, old["objectives"]), strict=True) if pair == first_pair]
    revised = plan(data, attempted_ids=attempted)
    assert revised["suppressed_count"] == 9 and len(revised["objectives"]) == 1
    assert revised["objectives"][0]["objective_id"] not in attempted
    assert source_pairs(data, revised["objectives"])[0] != first_pair
    exhausted = plan(data, attempted_ids=[item["objective_id"] for item in old["objectives"]])
    assert exhausted["objectives"] == [] and exhausted["suppressed_count"] == old["candidate_count"]


def test_attempt_suppression_precedes_diversity_selection():
    data = fragmented_data()
    first = plan(data)
    attempted = [item["objective_id"] for item in first["objectives"]]
    following = plan(data, attempted_ids=attempted)
    assert not set(attempted) & {item["objective_id"] for item in following["objectives"]}
    assert following["suppressed_count"] == MAX_OBJECTIVES
    assert len(following["objectives"]) == MAX_OBJECTIVES
    assert len(set(source_pairs(data, following["objectives"]))) == 1


def test_diversity_does_not_authorize_publication_with_restricted_dependencies():
    data = fragmented_data()
    restricted = replace(data, sampling={"publication_restricted": True})
    assert plan(restricted)["objectives"] == plan(restricted, include_all=True)["objectives"] == []
    assert plan(restricted)["candidate_count"] == 10


@pytest.mark.parametrize("policy", [None, "private-invented-policy", {}, True])
def test_new_plan_requires_its_exact_selection_policy(policy):
    data = fragmented_data()
    changed = plan(data)
    if policy is None:
        del changed["selection_policy"]
    else:
        changed["selection_policy"] = policy
    with pytest.raises(ValueError, match="Invalid consolidation plan"):
        validate_consolidation_plan(changed, data.sources, data.existing_assertions, (), data.sampling)
