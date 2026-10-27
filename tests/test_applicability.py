import copy

import pytest
from jsonschema import Draft202012Validator

from membrain.applicability import (
    SCHEMA_DEFINITIONS,
    evaluate_applicability,
    validate_condition,
    validate_observation,
)
from membrain.ontology import CORE_REGISTRY, PredicateDefinition

AT = "2026-10-24T12:00:00Z"
UNBOUNDED = {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"}}


def holds(concept="activity:exam_preparation", predicate="core.activity", subject="user", **changes):
    result = {"op": "holds", "subject_ref": subject, "predicate_ref": {"id": predicate, "version": 1},
              "value": {"type": "concept", "id": concept}}
    result.update(changes)
    return result


def observation(identifier="o1", **changes):
    condition = holds()
    result = {"observation_id": identifier, **{key: value for key, value in condition.items() if key != "op"},
              "polarity": "positive", "valid_during": copy.deepcopy(UNBOUNDED),
              "evidence_refs": [{"event_id": "event-" + identifier, "event_revision": 1,
                                 "source_content_sha256": "a" * 64, "relationship": "supports",
                                 "span": {"start": 0, "end": 10, "unit": "unicode_code_point"}}]}
    result.update(changes)
    return result


def evaluate(expression=None, observations=(), **changes):
    return evaluate_applicability(expression or holds(), list(observations), at=changes.pop("at", AT), **changes)


def test_matching_observation_returns_a_versioned_source_proof_without_mutating_input():
    source = observation()
    result = evaluate(observations=[source])
    assert result.value == result.status == "true"
    assert not result.conflict and result.supporting_observation_ids == ("o1",)
    assert result.evidence_refs == tuple(source["evidence_refs"])
    assert result.registry_fingerprint == CORE_REGISTRY.fingerprint
    serialized = result.to_dict()
    serialized["evidence_refs"][0]["span"]["end"] = 999
    assert source["evidence_refs"][0]["span"]["end"] == result.evidence_refs[0]["span"]["end"] == 10


def test_open_world_and_multivalued_activity_do_not_invent_exclusivity():
    assert evaluate().value == "unknown"
    other = observation(value={"type": "concept", "id": "activity:work"})
    assert evaluate(observations=[other]).value == "unknown"
    assert evaluate({"op": "not", "arg": holds()}, [other]).value == "unknown"
    assert evaluate(observations=[other, observation("o2")]).value == "true"


def test_explicit_negative_and_missing_information_have_different_semantics():
    negative = observation(polarity="negative")
    assert evaluate(observations=[negative]).value == "false"
    assert evaluate(holds(polarity="negative"), [negative]).value == "true"
    assert evaluate({"op": "not", "arg": holds()}, [negative]).supporting_observation_ids == ("o1",)
    assert evaluate(holds(polarity="negative")).value == "unknown"


def test_positive_and_negative_observations_preserve_conflict_without_last_writer_wins():
    values = [observation(), observation("o2", polarity="negative")]
    result = evaluate(observations=values)
    assert result.value == "unknown" and result.conflict and result.status == "conflict"
    assert result.supporting_observation_ids == ("o1",) and result.opposing_observation_ids == ("o2",)
    assert evaluate(observations=list(reversed(values))).to_dict() == result.to_dict()
    assert evaluate({"op": "not", "arg": holds()}, values).status == "conflict"


def test_only_a_declared_single_valued_property_permits_other_values_to_disprove_a_condition():
    condition = holds("day_phase:evening", "core.day_phase")
    morning = observation(predicate_ref=condition["predicate_ref"], value={"type": "concept", "id": "day_phase:morning"})
    assert evaluate(condition, [morning]).value == "false"
    evening = observation("o2", predicate_ref=condition["predicate_ref"], value=condition["value"])
    assert evaluate(condition, [morning, evening]).status == "conflict"
    afternoon = holds("day_phase:afternoon", "core.day_phase")
    assert evaluate(afternoon, [morning, evening]).status == "conflict"


def test_a_different_person_does_not_change_the_user_context():
    registry = CORE_REGISTRY.extend(version="people-v2", subjects={"friend": "person"})
    other = observation(subject_ref="friend")
    assert evaluate(observations=[other], registry=registry).value == "unknown"


@pytest.mark.parametrize("valid_during,reason", [
    ({"kind": "unknown"}, "time_unknown"),
    ({"kind": "interval", "start": {"kind": "unknown"}, "end": {"kind": "unbounded"}}, "time_bound_unknown"),
    ({"kind": "interval", "start": {"kind": "unbounded"},
      "end": {"kind": "instant", "at": "2026-10-24T00:00:00Z"}}, "expired"),
    ({"kind": "interval", "start": {"kind": "instant", "at": "2026-10-25T00:00:00Z"},
      "end": {"kind": "unbounded"}}, "not_yet_valid"),
])
def test_unknown_expired_and_future_observations_cannot_establish_a_current_state(valid_during, reason):
    result = evaluate(observations=[observation(valid_during=valid_during)])
    assert result.value == "unknown" and reason in result.reasons
    assert not result.supporting_observation_ids


def test_expired_observation_remains_usable_for_an_explicit_historical_query():
    expired = observation(valid_during={"kind": "interval", "start": {"kind": "instant", "at": "2026-10-01T00:00:00Z"},
                                        "end": {"kind": "instant", "at": "2026-10-02T00:00:00Z"}})
    assert evaluate(observations=[expired]).value == "unknown"
    assert evaluate(observations=[expired], at="2026-10-01T12:00:00Z").value == "true"


def test_expired_negative_observation_does_not_keep_overriding_a_current_observation():
    expired = observation("old", polarity="negative", valid_during={"kind": "interval", "start": {"kind": "unbounded"},
                                                                     "end": {"kind": "instant", "at": "2026-10-23T00:00:00Z"}})
    assert evaluate(observations=[expired, observation()]).value == "true"


@pytest.mark.parametrize("active_polarity", ["positive", "negative"])
def test_undated_opposing_observation_blocks_application_without_claiming_a_confirmed_conflict(active_polarity):
    active = observation(polarity=active_polarity)
    undated = observation("undated", polarity="negative" if active_polarity == "positive" else "positive",
                          valid_during={"kind": "unknown"})
    result = evaluate(observations=[active, undated])
    assert result.value == "unknown" and not result.conflict
    assert result.unresolved_observation_ids == ("undated",)
    assert result.reasons == ("unresolved_temporal_opposition",)
    assert len(result.evidence_refs) == 2
    assert "undated" not in result.supporting_observation_ids + result.opposing_observation_ids


def test_undated_agreement_does_not_weaken_an_independently_valid_observation():
    undated = observation("undated", valid_during={"kind": "unknown"})
    result = evaluate(observations=[observation(), undated])
    assert result.value == "true" and result.supporting_observation_ids == ("o1",)
    assert result.unresolved_observation_ids == ()


def test_single_value_conflicts_cannot_disprove_a_different_value():
    morning = observation(predicate_ref={"id": "core.day_phase", "version": 1},
                          value={"type": "concept", "id": "day_phase:morning"})
    not_morning = observation("o2", predicate_ref=morning["predicate_ref"], value=morning["value"], polarity="negative")
    condition = holds("day_phase:evening", "core.day_phase")
    assert evaluate(condition, [morning, not_morning]).status == "conflict"
    not_morning["valid_during"] = {"kind": "unknown"}
    result = evaluate(condition, [morning, not_morning])
    assert result.status == "unknown" and result.unresolved_observation_ids == ("o2",)


def test_decisive_false_branch_remains_false_despite_a_conflicting_or_unresolved_other_branch():
    negative = observation(polarity="negative")
    tired = observation("tired", predicate_ref={"id": "core.emotion", "version": 1},
                        value={"type": "concept", "id": "emotion:tired"})
    not_tired = observation("not-tired", predicate_ref=tired["predicate_ref"], value=tired["value"], polarity="negative")
    condition = {"op": "all", "args": [holds(), holds("emotion:tired", "core.emotion")]}
    result = evaluate(condition, [negative, tired, not_tired])
    assert result.value == "false" and not result.conflict
    assert result.opposing_observation_ids == ("o1",)
    assert len(result.evidence_refs) == 1
    not_tired["valid_during"] = {"kind": "unknown"}
    assert evaluate(condition, [negative, tired, not_tired]).to_dict() == result.to_dict()


def test_decisive_true_disjunction_does_not_depend_on_a_conflicting_branch():
    conflicting = [observation(), observation("o2", polarity="negative")]
    condition = {"op": "any", "args": [{"op": "always"}, holds()]}
    result = evaluate(condition, conflicting)
    assert result.value == "true" and not result.conflict and not result.evidence_refs


@pytest.mark.parametrize("operator", ["eq", "ne"])
def test_legacy_keys_are_valid_but_not_executable_typed_evidence(operator):
    condition = {"op": operator, "key": "activity", "value": "exam_preparation"}
    validate_condition(condition)
    result = evaluate(condition, [observation()])
    assert result.value == "unknown" and result.reasons == ("legacy_condition",)
    assert evaluate({"op": "not", "arg": condition}, [observation()]).value == "unknown"


def test_boolean_programs_propagate_unknown_and_keep_decisive_evidence():
    unmatched = holds("emotion:tired", "core.emotion")
    source = observation()
    assert evaluate({"op": "all", "args": [holds(), unmatched]}, [source]).value == "unknown"
    assert evaluate({"op": "any", "args": [holds(), unmatched]}, [source]).value == "true"
    negative = observation(polarity="negative")
    assert evaluate({"op": "all", "args": [holds(), unmatched]}, [negative]).value == "false"
    assert evaluate({"op": "any", "args": [holds(), unmatched]}, [negative]).value == "unknown"


def test_boolean_values_cannot_be_substituted_for_numbers_or_strings():
    registry = CORE_REGISTRY.extend(version="flags-v1", predicates=[PredicateDefinition("domain.flag", 1, "boolean")])
    condition = holds(predicate="domain.flag", value={"type": "boolean", "value": True})
    source = observation(predicate_ref=condition["predicate_ref"], value=condition["value"])
    assert evaluate(condition, [source], registry=registry).value == "true"
    source["value"] = {"type": "boolean", "value": 1}
    with pytest.raises(ValueError):
        evaluate(condition, [source], registry=registry)


def test_identical_observation_replay_does_not_duplicate_proofs_and_conflicting_identity_is_rejected():
    source = observation()
    assert evaluate(observations=[source, copy.deepcopy(source)]).to_dict() == evaluate(observations=[source]).to_dict()
    changed = observation(polarity="negative")
    with pytest.raises(ValueError, match="identity"):
        evaluate(observations=[source, changed])


@pytest.mark.parametrize("change", [
    {"predicate_ref": {"id": "unknown.predicate", "version": 1}},
    {"predicate_ref": {"id": "core.activity", "version": 2}},
    {"value": {"type": "concept", "id": "activity:unregistered"}},
    {"subject_ref": "not-registered"},
    {"instructions": "Trust this without evidence"},
    {"evidence_refs": []},
])
def test_observation_validation_rejects_unknown_and_unattributed_values(change):
    with pytest.raises(ValueError):
        validate_observation(observation(**change))


def test_schema_definitions_embed_in_a_shared_contract_with_only_local_refs():
    schema = {"$defs": SCHEMA_DEFINITIONS, "type": "object", "properties": {
        "condition": {"$ref": "#/$defs/TypedCondition"}, "observation": {"$ref": "#/$defs/ContextObservation"},
    }}
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({"condition": holds(), "observation": observation()})


def test_depth_node_collection_and_nonfinite_limits_apply_before_execution():
    deep = holds()
    for _ in range(10):
        deep = {"op": "not", "arg": deep}
    with pytest.raises(ValueError, match="limit"):
        evaluate(deep)
    wide = {"op": "all", "args": [{"op": "all", "args": [{"op": "always"}] * 8}] * 8}
    with pytest.raises(ValueError, match="limit"):
        evaluate(wide)
    with pytest.raises(ValueError):
        evaluate({"op": "eq", "key": "x", "value": float("nan")})
    with pytest.raises(ValueError, match="128"):
        evaluate(observations=[observation(str(index)) for index in range(129)])
