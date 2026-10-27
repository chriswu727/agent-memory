from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from jsonschema import Draft202012Validator

_IDENTIFIER = r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$"
_ID_SCHEMA = {"type": "string", "pattern": _IDENTIFIER, "maxLength": 128}
PREDICATE_REF_SCHEMA = {
    "type": "object", "properties": {
        "id": _ID_SCHEMA, "version": {"type": "integer", "minimum": 1, "maximum": 2147483647},
    }, "required": ["id", "version"], "additionalProperties": False,
}
TYPED_VALUE_SCHEMA = {"oneOf": [
    {"type": "object", "properties": {"type": {"const": "concept"}, "id": _ID_SCHEMA},
     "required": ["type", "id"], "additionalProperties": False},
    *[{"type": "object", "properties": {
        "type": {"const": kind}, "value": {"type": kind, **({"maxLength": 512} if kind == "string" else {})},
    }, "required": ["type", "value"], "additionalProperties": False}
      for kind in ("string", "number", "boolean")],
]}
_VALUE_VALIDATOR = Draft202012Validator(TYPED_VALUE_SCHEMA)
_REF_VALIDATOR = Draft202012Validator(PREDICATE_REF_SCHEMA)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(_IDENTIFIER, value):
        raise ValueError("Invalid registry identifier")
    return value


def _scalar(value, kind):
    if kind == "string":
        return isinstance(value, str) and len(value) <= 512
    if kind == "boolean":
        return type(value) is bool
    return type(value) in (int, float) and abs(value) <= 1e100 and math.isfinite(value)


@dataclass(frozen=True)
class ConceptDefinition:
    id: str
    label: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self):
        _identifier(self.id)
        if ":" not in self.id or not isinstance(self.label, str) or not 1 <= len(self.label) <= 512:
            raise ValueError("Concepts require a namespace and bounded label")
        if isinstance(self.aliases, (str, bytes)):
            raise ValueError("Concept aliases must be a collection")
        aliases = tuple(self.aliases)
        if len(aliases) > 32 or any(not isinstance(alias, str) or not 1 <= len(alias) <= 512 for alias in aliases):
            raise ValueError("Invalid concept aliases")
        if len(set(aliases)) != len(aliases):
            raise ValueError("Concept aliases must be unique")
        object.__setattr__(self, "aliases", aliases)

    def to_dict(self):
        return {"id": self.id, "label": self.label, "aliases": list(self.aliases)}


@dataclass(frozen=True)
class PredicateDefinition:
    id: str
    version: int
    value_type: str
    cardinality: str = "many"
    concept_namespace: str | None = None
    allowed_values: tuple | None = None
    subject_types: tuple[str, ...] = ("person", "agent")

    def __post_init__(self):
        _identifier(self.id)
        if type(self.version) is not int or not 1 <= self.version <= 2147483647:
            raise ValueError("Predicate version must be a positive integer")
        if self.value_type not in {"concept", "string", "number", "boolean"} or self.cardinality not in {"one", "many"}:
            raise ValueError("Invalid predicate type or cardinality")
        if isinstance(self.subject_types, (str, bytes)):
            raise ValueError("Predicate subject types must be a collection")
        subject_types = tuple(self.subject_types)
        if not 1 <= len(subject_types) <= 16 or len(set(subject_types)) != len(subject_types):
            raise ValueError("Invalid predicate subject types")
        for value in subject_types:
            _identifier(value)
        object.__setattr__(self, "subject_types", subject_types)
        if self.value_type == "concept":
            if self.concept_namespace is None or self.allowed_values is not None:
                raise ValueError("Concept predicates require a namespace")
            _identifier(self.concept_namespace)
        elif self.concept_namespace is not None:
            raise ValueError("Only concept predicates have a namespace")
        if self.allowed_values is not None:
            values = tuple(self.allowed_values)
            if not 1 <= len(values) <= 128 or any(not _scalar(value, self.value_type) for value in values):
                raise ValueError("Invalid predicate allowed values")
            if len({json.dumps(value) for value in values}) != len(values):
                raise ValueError("Predicate allowed values must be unique")
            object.__setattr__(self, "allowed_values", values)

    def to_dict(self):
        return {"id": self.id, "version": self.version, "value_type": self.value_type,
                "cardinality": self.cardinality, "concept_namespace": self.concept_namespace,
                "allowed_values": list(self.allowed_values) if self.allowed_values is not None else None,
                "subject_types": list(self.subject_types), "closed_world": False}


@dataclass(frozen=True)
class Registry:
    version: str
    predicates: tuple[PredicateDefinition, ...]
    concepts: tuple[ConceptDefinition, ...]
    subjects: Mapping[str, str] = field(default_factory=lambda: {"user": "person", "agent": "agent"})
    _predicates: Mapping = field(init=False, repr=False, compare=False)
    _concepts: Mapping = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        _identifier(self.version)
        predicates, concepts = tuple(self.predicates), tuple(self.concepts)
        if len(predicates) > 128 or len(concepts) > 512 or not isinstance(self.subjects, Mapping) or len(self.subjects) > 256:
            raise ValueError("Registry exceeds its bounds")
        if any(not isinstance(value, PredicateDefinition) for value in predicates) or any(
            not isinstance(value, ConceptDefinition) for value in concepts
        ):
            raise ValueError("Registry definitions must be typed")
        by_predicate = {(value.id, value.version): value for value in predicates}
        by_concept = {value.id: value for value in concepts}
        if len(by_predicate) != len(predicates) or len(by_concept) != len(concepts):
            raise ValueError("Registry definitions cannot be redefined")
        subjects = dict(self.subjects)
        for key, value in subjects.items():
            _identifier(key)
            _identifier(value)
        object.__setattr__(self, "predicates", predicates)
        object.__setattr__(self, "concepts", concepts)
        object.__setattr__(self, "subjects", MappingProxyType(subjects))
        object.__setattr__(self, "_predicates", MappingProxyType(by_predicate))
        object.__setattr__(self, "_concepts", MappingProxyType(by_concept))

    def predicate(self, reference):
        if not _REF_VALIDATOR.is_valid(reference) or type(reference["version"]) is not int:
            raise ValueError("Invalid predicate reference")
        result = self._predicates.get((reference["id"], reference["version"]))
        if result is None:
            raise ValueError("Unknown predicate version")
        return result

    def validate_value(self, reference, value, *, subject_ref):
        predicate = self.predicate(reference)
        if subject_ref not in self.subjects or self.subjects[subject_ref] not in predicate.subject_types:
            raise ValueError("Unknown or incompatible observation subject")
        if not _VALUE_VALIDATOR.is_valid(value) or value["type"] != predicate.value_type:
            raise ValueError("Value does not match predicate type")
        if value["type"] == "concept":
            identifier = value["id"]
            if identifier not in self._concepts or identifier.split(":", 1)[0] != predicate.concept_namespace:
                raise ValueError("Unknown or incompatible concept")
        else:
            scalar = value["value"]
            if not _scalar(scalar, predicate.value_type):
                raise ValueError("Invalid scalar value")
            if predicate.allowed_values is not None and scalar not in predicate.allowed_values:
                raise ValueError("Value is outside the predicate domain")

    def extend(self, *, version, predicates=(), concepts=(), subjects=None):
        if version == self.version:
            raise ValueError("Registry extension requires a new version")
        extra_subjects = dict(subjects or {})
        if set(extra_subjects) & set(self.subjects):
            raise ValueError("Registry subjects cannot be redefined")
        return Registry(version, self.predicates + tuple(predicates), self.concepts + tuple(concepts),
                        {**self.subjects, **extra_subjects})

    def to_dict(self):
        return {"version": self.version,
                "predicates": [item.to_dict() for item in sorted(self.predicates, key=lambda item: (item.id, item.version))],
                "concepts": [item.to_dict() for item in sorted(self.concepts, key=lambda item: item.id)],
                "subjects": dict(self.subjects)}

    @property
    def fingerprint(self):
        encoded = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


CORE_REGISTRY = Registry(
    "core-v1",
    (
        PredicateDefinition("core.activity", 1, "concept", concept_namespace="activity"),
        PredicateDefinition("core.day_phase", 1, "concept", cardinality="one", concept_namespace="day_phase"),
        PredicateDefinition("core.emotion", 1, "concept", concept_namespace="emotion"),
        PredicateDefinition("core.task_state", 1, "string", cardinality="one",
                            allowed_values=("pending", "active", "paused", "completed", "cancelled"),
                            subject_types=("person", "agent", "task")),
    ),
    tuple(ConceptDefinition(identifier, label, aliases) for identifier, label, aliases in (
        ("activity:exam_preparation", "Exam preparation", ("备考", "考试准备")),
        ("activity:work", "Work", ("工作",)),
        ("activity:rest", "Rest", ("休息",)),
        ("activity:socializing", "Socializing", ("社交",)),
        ("day_phase:morning", "Morning", ("早晨",)),
        ("day_phase:afternoon", "Afternoon", ("下午",)),
        ("day_phase:evening", "Evening", ("晚上",)),
        ("day_phase:night", "Night", ("深夜",)),
        ("emotion:tired", "Feeling tired", ("疲惫", "累")),
        ("emotion:anxious", "Feeling anxious", ("焦虑",)),
        ("emotion:calm", "Feeling calm", ("平静",)),
    )),
)
