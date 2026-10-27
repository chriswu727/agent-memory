import copy

import pytest
from test_cognition import event, scope
from test_semantic import compressed_example

from membrain import cognition
from membrain.contracts import validate
from membrain.helpers import digest
from membrain.ontology import CORE_REGISTRY
from membrain.patterns import build_patterns, expand_instance, validate_pattern
from membrain.semantic import expand_schema_residual

UNBOUNDED = {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"}}


def version(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def identity(reference):
    return reference["memory_id"], reference["revision"]


def episode_case(identifier, texts, *, offset):
    sources = [event(text, offset + index) for index, text in enumerate(texts)]
    memories = [memory for source in sources for memory in cognition.extract_event(source)]
    document = {
        "episode_id": identifier, "revision": 1, "scope": scope(), "state": "closed",
        "member_refs": [{"event_id": source["event_id"], "revision": source["revision"]}
                        for source in sources],
        "boundary_ref": {"event_id": sources[-1]["event_id"], "revision": sources[-1]["revision"]},
        "organization_status": "inferred", "job_id": "job-" + identifier,
        "proposal_id": "proposal-" + identifier,
        "root_event_ids": [source["event_id"] for source in sources],
    }
    return memories, document


def paired():
    first, first_episode = episode_case("episode-a", ["先交流再复习。", "我喝红茶。"], offset=1)
    second, second_episode = episode_case("episode-b", ["先交流再复习。", "我喝咖啡。"], offset=11)
    return first + second, [first_episode, second_episode]


def one_view(memories, episodes):
    views = build_patterns(scope(), memories, episodes)
    assert len(views) == 1
    assert validate_pattern(views[0], memories, episodes)
    assert validate_pattern(views[0], memories)
    return views[0]


def instance_for(view, identifier):
    return next(instance for instance in view["instances"] if instance["episode_ref"]["episode_id"] == identifier)


def test_shared_meaning_keeps_each_instances_complete_ordered_facts_and_own_evidence():
    memories, episodes = paired()
    before = copy.deepcopy((memories, episodes))
    view = one_view(memories, episodes)
    assert view["format_version"] == "schema-residual-v1"
    assert view["coverage"] == "selected_assertions_only"
    assert view["scope"] == scope()
    assert view["definition"]["revision"] == 1
    assert len(view["definition"]["shared_claims"]) == 1
    slot = view["definition"]["shared_claims"][0]
    assert slot["meaning"]["statement"] == memories[0]["statement"]
    assert not {"memory_id", "revision", "evidence_refs", "system_time", "dynamics"} & slot["meaning"].keys()
    for identifier, actual in (("episode-a", memories[:2]), ("episode-b", memories[2:])):
        instance = instance_for(view, identifier)
        assert instance["observed_memory_versions"] == [version(memory) for memory in actual]
        assert instance["shared_bindings"] == [{"slot_id": slot["slot_id"], "memory_version": version(actual[0])}]
        assert instance["residual_memory_versions"] == [version(actual[1])]
        expanded = expand_instance(view, instance, memories)
        assert expanded == actual
        assert {ref["event_id"] for memory in expanded for ref in memory["evidence_refs"]} == {
            ref["event_id"] for memory in actual for ref in memory["evidence_refs"]
        }
        expanded[0]["statement"] = "Changed returned copy"
        assert actual[0]["statement"] != "Changed returned copy"
    assert {identity(ref) for ref in view["source_memory_versions"]} == {identity(version(memory)) for memory in memories}
    assert set(view["root_event_ids"]) == {ref["event_id"] for episode in episodes for ref in episode["member_refs"]}
    assert (memories, episodes) == before


def test_build_is_idempotent_and_independent_of_input_collection_order():
    memories, episodes = paired()
    expected = build_patterns(scope(), memories, episodes)
    assert expected
    assert build_patterns(scope(), memories, episodes) == expected
    assert build_patterns(scope(), list(reversed(memories)), list(reversed(episodes))) == expected


def test_legacy_schema_expansion_preserves_observed_order_when_residual_precedes_shared_claim():
    schema, instance = compressed_example()
    instance["observed_claims"].reverse()
    assert expand_schema_residual(schema, instance) == tuple(instance["observed_claims"])


def test_order_uses_episode_members_then_span_instead_of_lexical_identifiers():
    first, first_episode = episode_case("episode-a", ["先交流。再复习。", "我喝红茶。"], offset=9)
    second, second_episode = episode_case("episode-b", ["先交流。再复习。", "我喝咖啡。"], offset=19)
    assert len(first) == len(second) == 3
    memories, episodes = first + second, [first_episode, second_episode]
    view = one_view(list(reversed(memories)), list(reversed(episodes)))
    for identifier, actual in (("episode-a", first), ("episode-b", second)):
        assert expand_instance(view, instance_for(view, identifier), memories) == actual


def test_a_third_instance_does_not_inherit_a_shared_claim_it_never_observed():
    first, a = episode_case("episode-a", ["先交流再复习。", "每次中途休息。", "我喝红茶。"], offset=1)
    second, b = episode_case("episode-b", ["先交流再复习。", "每次中途休息。", "我喝咖啡。"], offset=11)
    third, c = episode_case("episode-c", ["每次中途休息。", "我喝白水。"], offset=21)
    memories, episodes = first + second + third, [a, b, c]
    views = build_patterns(scope(), memories, episodes)
    found = []
    for view in views:
        assert validate_pattern(view, memories, episodes)
        for instance in view["instances"]:
            if instance["episode_ref"]["episode_id"] == "episode-c":
                found.append(expand_instance(view, instance, memories))
    assert found
    assert all(expanded == third for expanded in found)
    assert all(first[0]["statement"] not in [memory["statement"] for memory in expanded] for expanded in found)


def test_incidence_groups_validate_from_their_own_frozen_sources_without_being_regrouped():
    first, a = episode_case("episode-a", ["先交流再复习。", "每次中途休息。", "我喝红茶。"], offset=1)
    second, b = episode_case("episode-b", ["先交流再复习。", "每次中途休息。", "我喝咖啡。"], offset=11)
    third, c = episode_case("episode-c", ["每次中途休息。", "我喝白水。"], offset=21)
    memories, episodes = first + second + third, [a, b, c]
    views = build_patterns(scope(), memories, episodes)
    assert {frozenset(instance["episode_ref"]["episode_id"] for instance in view["instances"])
            for view in views} == {frozenset({"episode-a", "episode-b"}),
                                   frozenset({"episode-a", "episode-b", "episode-c"})}
    for view in views:
        own_keys = {identity(ref) for ref in view["source_memory_versions"]}
        own_memories = [memory for memory in memories if identity(version(memory)) in own_keys]
        episode_keys = {(instance["episode_ref"]["episode_id"], instance["episode_ref"]["revision"])
                        for instance in view["instances"]}
        own_episodes = [episode for episode in episodes if (episode["episode_id"], episode["revision"]) in episode_keys]
        assert validate_pattern(view, own_memories, own_episodes)
        for instance in view["instances"]:
            expected = {"episode-a": first, "episode-b": second, "episode-c": third}[
                instance["episode_ref"]["episode_id"]
            ]
            assert expand_instance(view, instance, own_memories) == expected


@pytest.mark.parametrize("field,value", [
    ("statement", "我不想先交流再复习。"),
    ("memory_kind", "interaction_preference"),
    ("world", "hypothetical"),
    ("conditions", {"op": "always"}),
    ("valid_during", {"kind": "unknown"}),
    ("valid_time", {"start": "2026-10-22T00:00:00Z", "end": "2026-10-23T00:00:00Z",
                    "precision": "day", "basis": "source_explicit"}),
    ("epistemic_status", "observed_result"),
    ("claim_kind", "reported_preference"),
    ("procedure", None),
    ("condition_registry_fingerprint", CORE_REGISTRY.fingerprint),
    ("representation", "episode_fragment"),
    ("verification", {"state": "supported_inference", "method": "semantic_model", "not_a_truth_certificate": True}),
])
def test_semantic_differences_cannot_be_factored_as_the_same_claim(field, value):
    memories, episodes = paired()
    memories[2][field] = copy.deepcopy(value)
    validate("MemoryAssertion", memories[2])
    assert build_patterns(scope(), memories, episodes) == []


@pytest.mark.parametrize("field,left,right", [
    ("conditions", {"op": "always"}, {"op": "not", "arg": {"op": "always"}}),
    ("valid_during", UNBOUNDED, {"kind": "unknown"}),
    ("valid_during", {"kind": "interval", "start": {"kind": "unbounded"},
                      "end": {"kind": "instant", "at": "2026-10-22T00:00:00Z"}}, UNBOUNDED),
    ("verification", {"state": "supported_inference", "method": "semantic_model", "not_a_truth_certificate": True},
     {"state": "contested", "method": "semantic_model", "not_a_truth_certificate": True}),
])
def test_explicit_negation_time_and_verification_differences_remain_distinct(field, left, right):
    memories, episodes = paired()
    memories[0][field], memories[2][field] = copy.deepcopy(left), copy.deepcopy(right)
    assert build_patterns(scope(), memories, episodes) == []


def test_system_timestamps_and_access_dynamics_do_not_change_shared_meaning():
    memories, episodes = paired()
    memories[2]["system_time"]["recorded_at"] = "2026-10-23T00:00:00Z"
    memories[2]["dynamics"].update(activation=0.9, retention_priority=0.8, user_pinned=True)
    view = one_view(memories, episodes)
    assert expand_instance(view, instance_for(view, "episode-b"), memories) == memories[2:]


@pytest.mark.parametrize("mutation", ["missing_residual", "duplicate_residual", "unknown_slot", "foreign_binding",
                                      "missing_observed", "changed_meaning", "definition_hash", "foreign_root"])
def test_forged_or_incomplete_view_is_rejected_without_partial_expansion(mutation):
    memories, episodes = paired()
    view = one_view(memories, episodes)
    instance = instance_for(view, "episode-a")
    if mutation == "missing_residual":
        instance["residual_memory_versions"] = []
    elif mutation == "duplicate_residual":
        instance["residual_memory_versions"].append(copy.deepcopy(instance["residual_memory_versions"][0]))
    elif mutation == "unknown_slot":
        instance["shared_bindings"][0]["slot_id"] = "unknown-slot"
    elif mutation == "foreign_binding":
        instance["shared_bindings"][0]["memory_version"] = version(memories[2])
    elif mutation == "missing_observed":
        instance["observed_memory_versions"].pop()
    elif mutation == "changed_meaning":
        view["definition"]["shared_claims"][0]["meaning"]["statement"] = "Never observed statement"
    elif mutation == "definition_hash":
        view["definition"]["content_sha256"] = "0" * 64
    else:
        view["root_event_ids"].append("foreign-private-source")
    assert not validate_pattern(view, memories, episodes)
    with pytest.raises(ValueError):
        expand_instance(view, instance, memories)


def test_expansion_rejects_an_instance_not_in_the_view():
    memories, episodes = paired()
    view = one_view(memories, episodes)
    forged = copy.deepcopy(view["instances"][0])
    forged["episode_ref"]["episode_id"] = "unrelated-episode"
    with pytest.raises(ValueError):
        expand_instance(view, forged, memories)


@pytest.mark.parametrize("mutation", ["foreign_binding", "omit_residual"])
def test_recomputing_view_identity_cannot_legitimize_an_invalid_observation_partition(mutation):
    memories, episodes = paired()
    view = one_view(memories, episodes)
    instance = instance_for(view, "episode-a")
    if mutation == "foreign_binding":
        instance["shared_bindings"][0]["memory_version"] = version(memories[2])
    else:
        instance["residual_memory_versions"] = []
    view["view_id"] = "pattern-" + digest({key: value for key, value in view.items() if key != "view_id"})
    assert not validate_pattern(view, memories)
    with pytest.raises(ValueError):
        expand_instance(view, instance, memories)


def test_exact_memory_version_is_required_and_newer_version_is_not_substituted():
    memories, episodes = paired()
    memories[0]["revision"] = 7
    view = one_view(memories, episodes)
    instance = instance_for(view, "episode-a")
    assert instance["observed_memory_versions"][0] == version(memories[0])
    changed = copy.deepcopy(memories)
    changed[0]["revision"] = 8
    assert not validate_pattern(view, changed, episodes)
    with pytest.raises(ValueError):
        expand_instance(view, instance, changed)
    with_both = changed + [copy.deepcopy(memories[0])]
    assert expand_instance(view, instance, with_both) == memories[:2]


def test_episode_revision_mismatch_invalidates_the_bound_view():
    memories, episodes = paired()
    view = one_view(memories, episodes)
    changed = copy.deepcopy(episodes)
    changed[0]["revision"] += 1
    assert not validate_pattern(view, memories, changed)


@pytest.mark.parametrize("change", ["member_order", "state", "source_roots", "scope"])
def test_episode_content_changed_at_the_same_revision_invalidates_the_bound_view(change):
    memories, episodes = paired()
    view = one_view(memories, episodes)
    changed = copy.deepcopy(episodes)
    if change == "member_order":
        changed[0]["member_refs"].reverse()
    elif change == "state":
        changed[0]["state"] = "open"
    elif change == "source_roots":
        changed[0]["root_event_ids"].append("unselected-context-root")
    else:
        changed[0]["scope"]["branch_id"] = "changed-branch"
    assert not validate_pattern(view, memories, changed)


def test_conflicting_duplicate_episode_versions_are_not_resolved_by_input_order():
    memories, episodes = paired()
    view = one_view(memories, episodes)
    forged = copy.deepcopy(episodes[0])
    forged["state"] = "open"
    assert not validate_pattern(view, memories, [forged, *episodes])
    assert not validate_pattern(view, memories, [*episodes, forged])


def test_changed_meaning_at_the_same_memory_version_invalidates_the_view():
    memories, episodes = paired()
    view = one_view(memories, episodes)
    changed = copy.deepcopy(memories)
    changed[0]["statement"] = "A corrected order of actions"
    assert not validate_pattern(view, changed, episodes)
    with pytest.raises(ValueError):
        expand_instance(view, view["instances"][0], changed)


def test_changed_source_reference_at_the_same_memory_version_invalidates_the_view():
    memories, episodes = paired()
    view = one_view(memories, episodes)
    changed = copy.deepcopy(memories)
    changed[0]["evidence_refs"][0]["source_content_sha256"] = "0" * 64
    assert not validate_pattern(view, changed, episodes)
    with pytest.raises(ValueError):
        expand_instance(view, view["instances"][0], changed)


def test_support_path_parent_changes_cannot_reuse_the_same_pattern_snapshot():
    memories, episodes = paired()
    view = one_view(memories, episodes)
    changed = copy.deepcopy(memories)
    changed[0]["disclosure_basis"]["support_paths"][0]["source_memory_versions"] = [
        {"memory_id": "different-parent", "revision": 1}
    ]
    assert not validate_pattern(view, changed)


@pytest.mark.parametrize("change", ["overlap", "duplicate_episode"])
def test_overlapping_or_repeated_episode_sources_do_not_count_as_independent_experiences(change):
    memories, episodes = paired()
    if change == "overlap":
        episodes[1]["member_refs"].append(copy.deepcopy(episodes[0]["member_refs"][1]))
        episodes[1]["root_event_ids"].append(episodes[0]["member_refs"][1]["event_id"])
    else:
        episodes[1] = copy.deepcopy(episodes[0])
        episodes[1]["episode_id"] = "episode-b"
    assert build_patterns(scope(), memories, episodes) == []


def test_two_revisions_of_one_source_do_not_count_as_independent_experiences():
    memories, episodes = paired()
    source_id = memories[0]["evidence_refs"][0]["event_id"]
    later = memories[2]
    for ref in later["evidence_refs"] + later["disclosure_basis"]["support_paths"][0]["evidence_refs"]:
        ref.update(event_id=source_id, event_revision=2)
    episodes[1]["member_refs"][0] = {"event_id": source_id, "revision": 2}
    episodes[1]["root_event_ids"][0] = source_id
    assert build_patterns(scope(), memories, episodes) == []


def test_two_revisions_of_one_episode_do_not_count_as_independent_experiences():
    memories, episodes = paired()
    episodes[1]["episode_id"] = episodes[0]["episode_id"]
    episodes[1]["revision"] = 2
    with pytest.raises(ValueError, match="Repeated episode"):
        build_patterns(scope(), memories, episodes)


@pytest.mark.parametrize("location", ["evidence_refs", "counterexamples", "support_path"])
def test_a_claim_with_cross_episode_evidence_cannot_be_an_instance_fact(location):
    memories, episodes = paired()
    mixed = copy.deepcopy(memories[0])
    mixed["memory_id"] = "cross-episode-summary"
    other_ref = copy.deepcopy(memories[2]["evidence_refs"][0])
    if location == "support_path":
        mixed["disclosure_basis"]["support_paths"][0]["evidence_refs"].append(other_ref)
    else:
        if location == "counterexamples":
            other_ref["relationship"] = "contradicts"
        mixed.setdefault(location, []).append(other_ref)
    memories.append(mixed)
    view = one_view(memories, episodes)
    assert version(mixed) not in view["source_memory_versions"]
    assert all(mixed not in expand_instance(view, instance, memories) for instance in view["instances"])


@pytest.mark.parametrize("representation", ["schema", "episode_outline"])
def test_summaries_do_not_enter_the_observed_instance_claim_set(representation):
    memories, episodes = paired()
    summary = copy.deepcopy(memories[1])
    summary.update(memory_id="summary", representation=representation)
    memories.append(summary)
    view = one_view(memories, episodes)
    assert version(summary) not in view["source_memory_versions"]
    assert expand_instance(view, instance_for(view, "episode-a"), memories) == memories[:2]


@pytest.mark.parametrize("field,value", [("lifecycle", "retracted"), ("scope", scope(brain_id="another-brain"))])
def test_unavailable_or_out_of_scope_claims_cannot_establish_a_pattern(field, value):
    memories, episodes = paired()
    memories[0][field] = value
    assert build_patterns(scope(), memories, episodes) == []


def test_a_stale_disclosure_cannot_establish_a_pattern():
    memories, episodes = paired()
    memories[0]["disclosure_basis"]["validation_state"] = "invalidated"
    assert build_patterns(scope(), memories, episodes) == []


@pytest.mark.parametrize("verified", [False, True])
def test_derived_atomic_claims_require_verification(verified):
    memories, episodes = paired()
    for memory in (memories[0], memories[2]):
        memory["epistemic_status"] = "derived_inference"
        memory["derivation"]["method"] = "aggregation"
        if verified:
            memory["verification"] = {"state": "supported_inference", "method": "semantic_model",
                                      "not_a_truth_certificate": True}
    views = build_patterns(scope(), memories, episodes)
    assert bool(views) is verified
    if verified:
        assert validate_pattern(views[0], memories, episodes)


def test_contested_disputed_fact_remains_an_instance_residual():
    memories, episodes = paired()
    exception = memories[1]
    exception.update(epistemic_status="disputed", verification={
        "state": "contested", "method": "semantic_model", "not_a_truth_certificate": True,
    })
    exception["derivation"]["method"] = "aggregation"
    view = one_view(memories, episodes)
    instance = instance_for(view, "episode-a")
    assert version(exception) in instance["residual_memory_versions"]
    assert exception in expand_instance(view, instance, memories)


@pytest.mark.parametrize("collection,count", [("memories", 65), ("episodes", 17)])
def test_input_limits_are_explicit(collection, count):
    memories, episodes = paired()
    if collection == "memories":
        memories = [copy.deepcopy(memories[index % 4]) for index in range(count)]
        for index, memory in enumerate(memories):
            memory["memory_id"] = f"memory-{index}"
    else:
        episodes = [copy.deepcopy(episodes[index % 2]) for index in range(count)]
        for index, episode in enumerate(episodes):
            episode["episode_id"] = f"episode-{index}"
    with pytest.raises(ValueError):
        build_patterns(scope(), memories, episodes)


@pytest.mark.parametrize("malformed", [None, [], "pattern", 1, True, {}, {"view_id": "incomplete"}])
def test_validation_returns_false_for_malformed_top_level_documents(malformed):
    memories, episodes = paired()
    assert validate_pattern(malformed, memories, episodes) is False


@pytest.mark.parametrize("field,value", [
    ("definition", None), ("definition", {"shared_claims": [None]}),
    ("instances", None), ("instances", [None, {}]),
    ("source_memory_versions", [None]), ("source_snapshots", [None]),
    ("root_event_ids", [None]), ("scope", None),
])
def test_validation_returns_false_for_malformed_nested_data_even_with_a_recomputed_identity(field, value):
    memories, episodes = paired()
    view = one_view(memories, episodes)
    view[field] = copy.deepcopy(value)
    view["view_id"] = "pattern-" + digest({key: value for key, value in view.items() if key != "view_id"})
    assert validate_pattern(view, memories, episodes) is False


@pytest.mark.parametrize("malformed", [None, [], [None], [{}], ["memory"]])
def test_validation_returns_false_for_malformed_source_documents(malformed):
    memories, episodes = paired()
    view = one_view(memories, episodes)
    assert validate_pattern(view, malformed, episodes) is False


@pytest.mark.parametrize("malformed", [[], [None], [{}], ["episode"]])
def test_validation_returns_false_for_malformed_episode_documents(malformed):
    memories, episodes = paired()
    view = one_view(memories, episodes)
    assert validate_pattern(view, memories, malformed) is False
