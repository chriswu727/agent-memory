from dataclasses import FrozenInstanceError

import pytest

from agent_memory.ontology import CORE_REGISTRY, ConceptDefinition, PredicateDefinition, Registry


def test_registry_and_definitions_are_immutable_and_defensively_copied():
    subjects = {"other": "person"}
    registry = Registry("test-v1", CORE_REGISTRY.predicates, CORE_REGISTRY.concepts, subjects)
    subjects["other"] = "task"
    assert registry.subjects["other"] == "person"
    with pytest.raises(TypeError):
        registry.subjects["new"] = "person"
    with pytest.raises(FrozenInstanceError):
        registry.version = "changed"
    with pytest.raises(FrozenInstanceError):
        registry.predicates[0].cardinality = "one"
    serialized = registry.to_dict()
    serialized["subjects"]["other"] = "task"
    assert registry.subjects["other"] == "person"


def test_registry_extensions_preserve_exact_old_versions_and_have_distinct_fingerprints():
    extended = CORE_REGISTRY.extend(
        version="domain-v2", predicates=[PredicateDefinition("domain.preferred_temperature", 1, "number", cardinality="one")],
        concepts=[ConceptDefinition("activity:cycling", "Cycling", ("骑车",))], subjects={"friend": "person"},
    )
    extended.validate_value({"id": "core.activity", "version": 1},
                            {"type": "concept", "id": "activity:cycling"}, subject_ref="friend")
    extended.validate_value({"id": "domain.preferred_temperature", "version": 1},
                            {"type": "number", "value": 21}, subject_ref="user")
    assert extended.fingerprint != CORE_REGISTRY.fingerprint
    with pytest.raises(ValueError, match="concept"):
        CORE_REGISTRY.validate_value({"id": "core.activity", "version": 1},
                                     {"type": "concept", "id": "activity:cycling"}, subject_ref="user")
    reordered = Registry(extended.version, tuple(reversed(extended.predicates)), tuple(reversed(extended.concepts)), extended.subjects)
    assert reordered.fingerprint == extended.fingerprint


@pytest.mark.parametrize("extension", [
    {"version": "core-v1"},
    {"version": "new", "predicates": [PredicateDefinition("core.activity", 1, "string")]},
    {"version": "new", "concepts": [ConceptDefinition("activity:work", "Redefined work")]},
    {"version": "new", "subjects": {"user": "task"}},
])
def test_extensions_cannot_silently_redefine_existing_semantics(extension):
    with pytest.raises(ValueError):
        CORE_REGISTRY.extend(**extension)


@pytest.mark.parametrize("reference,value,subject", [
    ({"id": "core.activity", "version": 2}, {"type": "concept", "id": "activity:work"}, "user"),
    ({"id": "core.activity", "version": True}, {"type": "concept", "id": "activity:work"}, "user"),
    ({"id": "core.activity", "version": 1.0}, {"type": "concept", "id": "activity:work"}, "user"),
    ({"id": "core.activity", "version": 1}, {"type": "string", "value": "activity:work"}, "user"),
    ({"id": "core.activity", "version": 1}, {"type": "concept", "id": "备考"}, "user"),
    ({"id": "core.activity", "version": 1}, {"type": "concept", "id": "emotion:tired"}, "user"),
    ({"id": "core.activity", "version": 1}, {"type": "concept", "id": "activity:work"}, "unknown-person"),
    ({"id": "core.task_state", "version": 1}, {"type": "string", "value": "probably_finished"}, "user"),
])
def test_reference_type_subject_and_concept_domains_are_checked(reference, value, subject):
    with pytest.raises(ValueError):
        CORE_REGISTRY.validate_value(reference, value, subject_ref=subject)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**500, True])
def test_numeric_properties_cannot_accept_nonfinite_unbounded_or_boolean_values(value):
    registry = CORE_REGISTRY.extend(version="numeric-v1", predicates=[PredicateDefinition("test.amount", 1, "number")])
    with pytest.raises(ValueError):
        registry.validate_value({"id": "test.amount", "version": 1}, {"type": "number", "value": value}, subject_ref="user")


def test_concept_labels_and_aliases_do_not_establish_equivalence():
    registry = CORE_REGISTRY.extend(version="ambiguous-v1", concepts=[
        ConceptDefinition("activity:other_preparation", "Other preparation", ("备考",)),
    ])
    assert {concept.id for concept in registry.concepts if "备考" in concept.aliases} == {
        "activity:exam_preparation", "activity:other_preparation",
    }
