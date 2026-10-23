import copy

import pytest
from test_cognition import event, request, scope, state

from agent_memory import cognition
from agent_memory.helpers import DomainError
from agent_memory.retrieval import fuse_rankings, resolve_dependency_closure


def version(memory):
    return memory["memory_id"], memory["revision"]


def reference(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def document(name, revision=1, parents=(), paths=(), selected=()):
    return {
        "memory_id": name, "revision": revision,
        "derivation": {"source_memory_versions": [
            {"memory_id": key, "revision": rev} for key, rev in parents
        ]},
        "disclosure_basis": {"support_paths": list(paths), "selected_path_ids": list(selected)},
    }


def test_rank_fusion_combines_routes_without_comparing_incompatible_raw_scores():
    ranked = fuse_rankings({
        "keyword": [("literal", 1), ("shared", 2)],
        "vector": [("semantic", 1), ("shared", 2)],
        "association": [("shared", 2)],
    }, index_versions={"vector": "test-semantic-v1"})
    assert [item["memory_id"] for item in ranked] == ["shared", "literal", "semantic"]
    assert ranked[0]["score"] == pytest.approx(1 / 61 + 2 / 62)
    assert {signal["method"] for signal in ranked[0]["rank_signals"]} == {"keyword", "vector", "association"}
    assert next(signal for signal in ranked[0]["rank_signals"] if signal["method"] == "vector")["model_or_index_version"] == "test-semantic-v1"


def test_rank_fusion_deduplicates_within_routes_and_preserves_exact_versions():
    baseline = fuse_rankings({"keyword": [("a", 1), ("b", 1), ("a", 2)]})
    duplicate = fuse_rankings({"keyword": [("a", 1), ("a", 1), ("b", 1), ("a", 2)]})
    assert duplicate == baseline
    assert [version(item) for item in duplicate] == [("a", 1), ("b", 1), ("a", 2)]
    assert fuse_rankings({"vector": [("a", 1)]}, weights={"vector": 0}) == []
    assert len(fuse_rankings({"vector": [("a", 1), ("b", 1)]}, limit=1)) == 1


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), -1, True])
def test_rank_fusion_rejects_invalid_weights(weight):
    with pytest.raises(ValueError):
        fuse_rankings({"vector": [("a", 1)]}, weights={"vector": weight})


def test_dependency_closure_fetches_only_exact_selected_versions():
    root = document("episode", parents=[("schema", 2)], paths=[
        {"path_id": "selected", "source_memory_versions": [{"memory_id": "detail", "revision": 3}]},
        {"path_id": "unused", "source_memory_versions": [{"memory_id": "secret", "revision": 1}]},
    ], selected=["selected"])
    schema = document("schema", 2, parents=[("source", 1)])
    detail, source = document("detail", 3), document("source", 1)
    available = {version(item): item for item in [schema, detail, source]}
    calls = []

    def fetch(keys):
        calls.append(keys)
        return [available[key] for key in keys]

    original = copy.deepcopy(root)
    closure = resolve_dependency_closure([root], fetch)
    assert calls == [[("detail", 3), ("schema", 2)], [("source", 1)]]
    assert {version(item) for item in closure.memories} == {("episode", 1), *available}
    assert closure.missing_versions == ()
    assert closure.cycle_versions == ()
    assert not closure.truncated
    assert root == original


def test_dependency_closure_bounds_missing_loads_and_does_not_follow_latest_revision():
    root = document("root", parents=[("a", 1), ("b", 1), ("c", 1)])
    calls = []

    def missing(keys):
        calls.append(keys)
        return []

    closure = resolve_dependency_closure([root], missing, max_nodes=3)
    assert calls == [[("a", 1), ("b", 1)]]
    assert closure.truncated
    assert closure.missing_versions == (("a", 1), ("b", 1))
    with pytest.raises(ValueError, match="unrequested"):
        resolve_dependency_closure([root], lambda keys: [document("a", 2)])


def test_dependency_depth_limit_and_cycles_are_explicit():
    first = document("a", parents=[("b", 1)])
    second = document("b", parents=[("a", 1)])
    bounded = resolve_dependency_closure([first], lambda keys: pytest.fail("No fetch budget"), max_depth=0)
    assert bounded.truncated
    closure = resolve_dependency_closure([first], lambda keys: [second])
    assert not closure.truncated
    assert closure.cycle_versions == (("a", 1), ("b", 1))


def test_shared_dependency_does_not_become_a_cycle_or_duplicate_fetch():
    root = document("root", parents=[("a", 1), ("b", 1)])
    objects = [document("a", parents=[("common", 1)]),
               document("b", parents=[("common", 1)]), document("common")]
    available = {version(item): item for item in objects}
    calls = []

    def fetch(keys):
        calls.append(keys)
        return [available[key] for key in keys]

    closure = resolve_dependency_closure([root], fetch)
    assert calls == [[("a", 1), ("b", 1)], [("common", 1)]]
    assert closure.cycle_versions == ()


def semantic_state(memory, **changes):
    signal = {"method": "vector", "score": 0.25,
              "meaning": "ranking_only_not_probability", "model_or_index_version": "test-semantic-v1"}
    return state(rank_signals={version(memory): [signal]}, **changes)


def test_semantic_route_recovers_nonliteral_memory_but_legacy_stays_lexical():
    source = event("书店外有一棵银杏树。")
    memory = cognition.extract_event(source)[0]
    query = request("arboreal landmark")
    assert cognition._lexical_score(query["query"], memory["statement"]) == 0
    assert cognition.recall(scope(), query, [memory], [source], state())["items"] == []
    result = cognition.recall(scope(), query, [memory], [source], semantic_state(memory))
    assert [version(item["memory"]) for item in result["items"]] == [version(memory)]
    assert result["items"][0]["rank_signals"][0]["method"] == "vector"


def test_recency_alone_cannot_admit_unrelated_memory():
    source = event("书店外有一棵银杏树。")
    memory = cognition.extract_event(source)[0]
    metadata = semantic_state(memory)
    metadata["rank_signals"][version(memory)][0]["method"] = "recency"
    assert cognition.recall(scope(), request("unrelated"), [memory], [source], metadata)["items"] == []


@pytest.mark.parametrize("failure", ["missing", "revision", "scope", "retracted", "generation", "corrected"])
def test_semantic_scores_never_bypass_evidence_checks(failure):
    source = event("书店外有一棵银杏树。")
    memory = cognition.extract_event(source)[0]
    metadata = semantic_state(memory)
    events = [source]
    if failure == "missing":
        events = []
    elif failure == "revision":
        source["revision"] = 2
    elif failure == "scope":
        source["scope"]["brain_id"] = "other"
    elif failure == "retracted":
        memory["lifecycle"] = "retracted"
    elif failure == "generation":
        metadata["dependency_generation"] = 1
    elif failure == "corrected":
        metadata["superseded_evidence_refs"] = copy.deepcopy(memory["evidence_refs"])
    assert cognition.recall(scope(), request("arboreal landmark"), [memory], events, metadata)["items"] == []


def test_semantic_ranking_does_not_turn_current_cue_into_old_evidence():
    cue = event("还记得银杏树吗？")
    memory = cognition.extract_event(cue)[0]
    recalled = cognition.recall(scope(), request("arboreal landmark", evidence_mode="prior_record_check",
                                                cue_event_ids=[cue["event_id"]]),
                                [memory], [cue], semantic_state(memory))
    assert recalled["items"] == []


def test_nonfinite_rank_signal_is_rejected():
    source = event("银杏树。")
    memory = cognition.extract_event(source)[0]
    metadata = semantic_state(memory)
    metadata["rank_signals"][version(memory)][0]["score"] = float("nan")
    with pytest.raises(DomainError, match="finite"):
        cognition.recall(scope(), request("arboreal"), [memory], [source], metadata)


def derived_pair():
    source = event("工作时喜欢直接的建议。")
    parent = cognition.extract_event(source)[0]
    parent["memory_id"] = "schema"
    parent["revision"] = 2
    child = copy.deepcopy(parent)
    child["memory_id"], child["revision"] = "episode", 1
    child["derivation"]["source_memory_versions"] = [reference(parent)]
    return source, parent, child


def test_exact_parent_closure_validates_without_exposing_dependency_as_candidate():
    source, parent, child = derived_pair()
    metadata = state(candidate_versions={version(child)})
    result = cognition.recall(scope(), request("建议"), [child, parent], [source], metadata)
    assert [version(item["memory"]) for item in result["items"]] == [version(child)]
    wrong_parent = copy.deepcopy(parent)
    wrong_parent["revision"] = 3
    assert cognition.recall(scope(), request("建议"), [child, wrong_parent], [source], metadata)["items"] == []


def test_explicit_retained_schema_version_can_support_child_but_never_undo_retraction():
    source, parent, child = derived_pair()
    parent["lifecycle"] = "superseded"
    metadata = state(candidate_versions={version(child)})
    assert cognition.recall(scope(), request("建议"), [child, parent], [source], metadata)["items"] == []
    metadata["valid_dependency_versions"] = {version(parent)}
    assert cognition.recall(scope(), request("建议"), [child, parent], [source], metadata)["items"]
    parent["lifecycle"] = "retracted"
    assert cognition.recall(scope(), request("建议"), [child, parent], [source], metadata)["items"] == []


def test_retained_schema_cannot_hide_corrected_evidence_behind_child_source():
    source, parent, child = derived_pair()
    another = event("周末选择自己安静一会。", sequence=2)
    separate_child = cognition.extract_event(another)[0]
    separate_child["derivation"]["source_memory_versions"] = [reference(parent)]
    parent["lifecycle"] = "superseded"
    metadata = state(2, candidate_versions={version(separate_child)},
                     valid_dependency_versions={version(parent)},
                     superseded_evidence_refs=parent["evidence_refs"])
    assert cognition.recall(scope(), request("周末"), [separate_child, parent], [source, another], metadata)["items"] == []


@pytest.mark.parametrize("kind", ["agent_said", "proposal_offered", "commitment_expressed"])
@pytest.mark.parametrize("delivery", ["delivered", "partially_delivered"])
def test_delivered_assistant_source_can_support_its_own_speech_act(kind, delivery):
    source = event("明天一起检查计划。", role="assistant")
    source["observed"]["delivery_state"] = delivery
    memory = cognition.extract_event(source)[0]
    memory["claim_kind"] = kind
    result = cognition.recall(scope(), request("计划", evidence_mode="prior_record_check",
                                             evidence_cutoff={"mode": "through_sequence", "through_sequence": 1}),
                              [memory], [source], state())
    assert len(result["items"]) == 1
    memory.pop("claim_kind")
    assert cognition.recall(scope(), request("计划", evidence_mode="prior_record_check",
                                            evidence_cutoff={"mode": "through_sequence", "through_sequence": 1}),
                             [memory], [source], state())["items"] == []


@pytest.mark.parametrize("kind", ["external_world_fact", "task_completed"])
@pytest.mark.parametrize("intent", ["explore", "exact_quote"])
def test_assistant_claim_cannot_certify_the_world_or_task_completion(kind, intent):
    source = event("银杏树检查已经完成。", role="assistant")
    memory = cognition.extract_event(source)[0]
    memory["claim_kind"] = kind
    assert cognition.recall(scope(), request("银杏树", intent=intent), [memory], [source], state())["items"] == []


@pytest.mark.parametrize("delivery", ["draft", "cancelled", "unknown"])
def test_unconfirmed_speech_act_is_not_recalled(delivery):
    source = event("明天一起检查计划。", role="assistant")
    memory = cognition.extract_event(source)[0]
    memory["claim_kind"] = "commitment_expressed"
    source["observed"]["delivery_state"] = delivery
    assert cognition.recall(scope(), request("计划"), [memory], [source], state())["items"] == []
