import copy

import pytest

from membrain.candidates import merge_belief_candidates


def memory(identifier, revision=1):
    return {"memory_id": identifier, "revision": revision}


def version(value):
    return value["memory_id"], value["revision"]


def relation(source, *targets):
    return {"source_memory_version": source, "target_memory_versions": list(targets)}


def signal(score, method="keyword"):
    return {"method": method, "score": score, "meaning": "ranking_only_not_probability",
            "model_or_index_version": "ranking-test"}


def state(*roots):
    return {"candidate_versions": [version(m) for m, _ in roots],
            "rank_signals": {version(m): signals for m, signals in roots},
            "valid_dependency_versions": set()}


def test_weak_relation_neighborhood_cannot_overtake_a_stronger_unrelated_query_match():
    scene, current_plan, old_plan = (memory(name) for name in ("scene", "current", "old"))
    current = state((scene, [signal(.01)]), (current_plan, [signal(.00008)]))
    package = {"memories": [current_plan, old_plan],
               "relations": [relation(current_plan, old_plan)]}
    original = copy.deepcopy((scene, current_plan, old_plan, package))
    result = merge_belief_candidates([scene, current_plan], current, package)
    ranked = sorted(current["candidate_versions"],
                    key=lambda key: -sum(s["score"] for s in current["rank_signals"][key]))
    assert ranked[0] == version(scene)
    assert current["rank_signals"][version(old_plan)][0]["score"] == pytest.approx(.00008)
    assert {version(m) for m in result} == {version(scene), version(current_plan), version(old_plan)}
    assert (scene, current_plan, old_plan, package) == original


def test_connected_chain_uses_its_strongest_seed_without_borrowing_another_component():
    a, b, c, d, e = (memory(name) for name in "abcde")
    current = state((a, [signal(.001)]), (c, [signal(.002), signal(.003, "vector")]),
                    (d, [signal(.02)]))
    before = copy.deepcopy(current["rank_signals"])
    package = {"memories": [a, b, c, d, e],
               "relations": [relation(a, b), relation(b, c), relation(c, a), relation(d, e)]}
    merge_belief_candidates([a, c, d], current, package)
    assert current["rank_signals"][version(b)][0]["score"] == pytest.approx(.005)
    assert current["rank_signals"][version(e)][0]["score"] == pytest.approx(.02)
    assert all(current["rank_signals"][key] == signals for key, signals in before.items())


@pytest.mark.parametrize("signals", [[], [signal(0)], [signal(-1)], [signal(1, "recency")]])
def test_unmatched_relation_is_only_available_as_evidence_not_a_new_relevance_root(signals):
    unrelated, first, second = (memory(name) for name in ("unrelated", "first", "second"))
    current = state((unrelated, [signal(.01)]))
    current["rank_signals"][version(first)] = signals
    package = {"memories": [first, second], "relations": [relation(first, second)]}
    merge_belief_candidates([unrelated], current, package)
    assert set(current["candidate_versions"]) == {version(unrelated)}
    assert version(second) not in current["rank_signals"]
    assert {version(first), version(second)} <= current["valid_dependency_versions"]


def test_selected_plan_does_not_acquire_automatic_endpoints_or_rank_changes():
    first, second = memory("first"), memory("second")
    current = state((first, [signal(.001)]))
    before = copy.deepcopy(current)
    merge_belief_candidates([first], current,
                           {"memories": [second], "relations": [relation(first, second)]},
                           selected=True)
    assert current["candidate_versions"] == before["candidate_versions"]
    assert current["rank_signals"] == before["rank_signals"]
    assert version(second) in current["valid_dependency_versions"]


def test_same_identifier_other_revision_is_not_a_matching_seed():
    old, revised, neighbor = memory("same", 1), memory("same", 2), memory("neighbor")
    current = state((old, [signal(.02)]))
    merge_belief_candidates([old], current,
                           {"memories": [revised, neighbor], "relations": [relation(revised, neighbor)]})
    assert current["candidate_versions"] == [version(old)]
    assert version(neighbor) not in current["rank_signals"]
