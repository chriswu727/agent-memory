from __future__ import annotations

import copy
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from jsonschema import Draft202012Validator, FormatChecker

from .ontology import CORE_REGISTRY, PREDICATE_REF_SCHEMA, TYPED_VALUE_SCHEMA, Registry
from .temporal import VALID_DURING_SCHEMA, evaluate_valid_during, validate_valid_during

_ID = {"type": "string", "minLength": 1, "maxLength": 256}
_POLARITY = {"enum": ["positive", "negative"]}


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


_CONDITION_REF = {"$ref": "#/$defs/TypedCondition"}
_CONDITION = {"oneOf": [
    _object({"op": {"const": "always"}}),
    _object({"op": {"const": "unresolved"},
             "label": {"type": "string", "minLength": 1, "maxLength": 512}}),
    _object({"op": {"const": "holds"}, "subject_ref": _ID,
             "predicate_ref": {"$ref": "#/$defs/TypedPredicateRef"},
             "value": {"$ref": "#/$defs/TypedValue"}, "polarity": _POLARITY},
            ["op", "subject_ref", "predicate_ref", "value"]),
    _object({"op": {"enum": ["all", "any"]},
             "args": {"type": "array", "items": _CONDITION_REF, "minItems": 1, "maxItems": 8}}),
    _object({"op": {"const": "not"}, "arg": _CONDITION_REF}),
    _object({"op": {"enum": ["eq", "ne"]},
             "key": {"type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_]{0,63}$"},
             "value": {"type": ["string", "number", "boolean", "null"], "maxLength": 512}}),
]}
_EVIDENCE = _object({
    "event_id": _ID, "event_revision": {"type": "integer", "minimum": 1},
    "source_content_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    "relationship": {"const": "supports"},
    "span": _object({"start": {"type": "integer", "minimum": 0},
                     "end": {"type": "integer", "minimum": 1}, "unit": {"const": "unicode_code_point"}}),
})
_OBSERVATION = _object({
    "observation_id": _ID, "subject_ref": _ID,
    "predicate_ref": {"$ref": "#/$defs/TypedPredicateRef"},
    "value": {"$ref": "#/$defs/TypedValue"}, "polarity": _POLARITY,
    "valid_during": {"$ref": "#/$defs/ValidDuring"},
    "evidence_refs": {"type": "array", "items": {"$ref": "#/$defs/ObservationEvidenceRef"},
                      "minItems": 1, "maxItems": 16},
})
SCHEMA_DEFINITIONS = {
    "TypedCondition": _CONDITION, "TypedValue": copy.deepcopy(TYPED_VALUE_SCHEMA),
    "TypedPredicateRef": copy.deepcopy(PREDICATE_REF_SCHEMA),
    "ValidDuring": copy.deepcopy(VALID_DURING_SCHEMA), "ObservationEvidenceRef": _EVIDENCE,
    "ContextObservation": _OBSERVATION,
}
CONDITION_SCHEMA = {"$defs": SCHEMA_DEFINITIONS, "$ref": "#/$defs/TypedCondition"}
NEW_SCHEMA_DEFINITIONS = copy.deepcopy(SCHEMA_DEFINITIONS)
NEW_SCHEMA_DEFINITIONS["TypedCondition"]["oneOf"] = [
    branch for branch in NEW_SCHEMA_DEFINITIONS["TypedCondition"]["oneOf"]
    if branch["properties"]["op"] != {"enum": ["eq", "ne"]}
]
NEW_CONDITION_SCHEMA = {"$defs": NEW_SCHEMA_DEFINITIONS, "$ref": "#/$defs/TypedCondition"}
OBSERVATION_SCHEMA = {"$defs": SCHEMA_DEFINITIONS, "$ref": "#/$defs/ContextObservation"}
_CONDITION_VALIDATOR = Draft202012Validator(CONDITION_SCHEMA, format_checker=FormatChecker())
_NEW_CONDITION_VALIDATOR = Draft202012Validator(NEW_CONDITION_SCHEMA, format_checker=FormatChecker())
_OBSERVATION_VALIDATOR = Draft202012Validator(OBSERVATION_SCHEMA, format_checker=FormatChecker())


def _bounded(value):
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 24 or count > 32768:
            raise ValueError("Applicability input exceeds structural limits")
        if isinstance(item, (dict, list, tuple)):
            if len(item) > 4096:
                raise ValueError("Applicability collection exceeds its limit")
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in children)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite applicability value")
        elif isinstance(item, int) and not isinstance(item, bool) and abs(item) > 1e100:
            raise ValueError("Applicability number exceeds its limit")
        elif isinstance(item, str) and len(item) > 262144:
            raise ValueError("Applicability string exceeds its limit")
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 262144:
            raise ValueError("Applicability input exceeds its byte limit")
    except (TypeError, OverflowError, RecursionError):
        raise ValueError("Invalid applicability input") from None


def validate_condition(expression, registry=CORE_REGISTRY):
    _validate_condition(expression, registry, _CONDITION_VALIDATOR)


def validate_new_condition(expression, registry=CORE_REGISTRY):
    _validate_condition(expression, registry, _NEW_CONDITION_VALIDATOR)


def _validate_condition(expression, registry, validator):
    if not isinstance(registry, Registry):
        raise ValueError("A typed registry is required")
    _bounded(expression)
    if not validator.is_valid(expression):
        raise ValueError("Invalid condition expression")
    pending, count = [(expression, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 8 or count > 64:
            raise ValueError("Condition exceeds its depth or node limit")
        if node["op"] == "holds":
            registry.validate_value(node["predicate_ref"], node["value"], subject_ref=node["subject_ref"])
        elif node["op"] == "not":
            pending.append((node["arg"], depth + 1))
        elif node["op"] in {"all", "any"}:
            pending.extend((child, depth + 1) for child in node["args"])


def validate_observation(observation, registry=CORE_REGISTRY):
    if not isinstance(registry, Registry):
        raise ValueError("A typed registry is required")
    _bounded(observation)
    if not _OBSERVATION_VALIDATOR.is_valid(observation):
        raise ValueError("Invalid context observation")
    registry.validate_value(observation["predicate_ref"], observation["value"],
                            subject_ref=observation["subject_ref"])
    validate_valid_during(observation["valid_during"])
    refs = set()
    for ref in observation["evidence_refs"]:
        span = ref["span"]
        if (type(ref["event_revision"]) is not int or type(span["start"]) is not int
                or type(span["end"]) is not int or span["end"] <= span["start"]):
            raise ValueError("Invalid observation evidence bounds")
        key = json.dumps(ref, sort_keys=True, separators=(",", ":"))
        if key in refs:
            raise ValueError("Observation evidence must be unique")
        refs.add(key)


@dataclass(frozen=True)
class ApplicabilityResult:
    value: str
    conflict: bool = False
    supporting_observation_ids: tuple[str, ...] = ()
    opposing_observation_ids: tuple[str, ...] = ()
    evidence_refs: tuple[dict, ...] = ()
    reasons: tuple[str, ...] = ()
    registry_fingerprint: str = ""
    unresolved_observation_ids: tuple[str, ...] = ()

    @property
    def status(self):
        return "conflict" if self.conflict else self.value

    def to_dict(self):
        return {"value": self.value, "conflict": self.conflict, "status": self.status,
                "supporting_observation_ids": list(self.supporting_observation_ids),
                "opposing_observation_ids": list(self.opposing_observation_ids),
                "evidence_refs": copy.deepcopy(list(self.evidence_refs)), "reasons": list(self.reasons),
                "registry_fingerprint": self.registry_fingerprint,
                "unresolved_observation_ids": list(self.unresolved_observation_ids)}


def _value_key(value):
    if value["type"] == "number":
        return ("number", value["value"])
    return (value["type"], value["id"] if value["type"] == "concept" else value["value"])


def evaluate_applicability(expression, observations, *, at, registry=CORE_REGISTRY):
    validate_condition(expression, registry)
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)) or len(observations) > 128:
        raise ValueError("At most 128 context observations are allowed")
    _bounded(observations)
    evaluate_valid_during({"kind": "unknown"}, at=at)
    by_id, validity = {}, {}
    for observation in observations:
        validate_observation(observation, registry)
        identifier = observation["observation_id"]
        if identifier in by_id and by_id[identifier] != observation:
            raise ValueError("Observation identity has conflicting content")
        by_id[identifier] = observation
        validity[identifier] = evaluate_valid_during(observation["valid_during"], at=at)
    fingerprint = registry.fingerprint

    def result(value, *, supporting=(), opposing=(), unresolved=(), reasons=(), conflict=False):
        supporting, opposing = tuple(sorted(set(supporting))), tuple(sorted(set(opposing)))
        unresolved = tuple(sorted(set(unresolved)))
        evidence = {}
        for identifier in supporting + opposing + unresolved:
            for ref in by_id[identifier]["evidence_refs"]:
                evidence[json.dumps(ref, sort_keys=True, separators=(",", ":"))] = ref
        return ApplicabilityResult(value, conflict, supporting, opposing,
                                   tuple(copy.deepcopy(evidence[key]) for key in sorted(evidence)),
                                   tuple(sorted(set(reasons))), fingerprint, unresolved)

    def combine(children, operator):
        decisive = "false" if operator == "all" else "true"
        selected = [child for child in children if child.value == decisive and not child.conflict]
        if selected:
            value, conflict = decisive, False
        elif all(child.value == ("true" if operator == "all" else "false") and not child.conflict for child in children):
            selected, value, conflict = children, "true" if operator == "all" else "false", False
        else:
            selected, value, conflict = children, "unknown", any(child.conflict for child in children)
        return result(value, supporting=[i for child in selected for i in child.supporting_observation_ids],
                      opposing=[i for child in selected for i in child.opposing_observation_ids],
                      unresolved=[i for child in selected for i in child.unresolved_observation_ids],
                      reasons=[reason for child in selected for reason in child.reasons], conflict=conflict)

    def negate(child):
        return result({"true": "false", "false": "true", "unknown": "unknown"}[child.value],
                      supporting=child.opposing_observation_ids, opposing=child.supporting_observation_ids,
                      unresolved=child.unresolved_observation_ids, reasons=child.reasons, conflict=child.conflict)

    def atom(node):
        predicate = registry.predicate(node["predicate_ref"])
        candidates = [observation for observation in by_id.values()
                      if observation["subject_ref"] == node["subject_ref"]
                      and observation["predicate_ref"] == node["predicate_ref"]]
        active = [observation for observation in candidates if validity[observation["observation_id"]].value == "true"]
        unresolved = [observation for observation in candidates if validity[observation["observation_id"]].value == "unknown"]
        desired = _value_key(node["value"])
        supporting = [observation["observation_id"] for observation in active
                      if _value_key(observation["value"]) == desired and observation["polarity"] == "positive"]
        opposing = [observation["observation_id"] for observation in active
                    if ((_value_key(observation["value"]) == desired and observation["polarity"] == "negative")
                        or (predicate.cardinality == "one" and _value_key(observation["value"]) != desired
                            and observation["polarity"] == "positive"))]
        positive_values = {_value_key(observation["value"]) for observation in active if observation["polarity"] == "positive"}
        negative_values = {_value_key(observation["value"]) for observation in active if observation["polarity"] == "negative"}
        inconsistent = predicate.cardinality == "one" and (len(positive_values) > 1 or bool(positive_values & negative_values))
        if inconsistent:
            opposing.extend(observation["observation_id"] for observation in active
                            if observation["polarity"] == "positive" or _value_key(observation["value"]) in positive_values)
        uncertain_support = [observation["observation_id"] for observation in unresolved
                             if _value_key(observation["value"]) == desired and observation["polarity"] == "positive"]
        uncertain_opposition = [observation["observation_id"] for observation in unresolved
                                if ((_value_key(observation["value"]) == desired and observation["polarity"] == "negative")
                                    or (predicate.cardinality == "one" and _value_key(observation["value"]) != desired
                                        and observation["polarity"] == "positive"))]
        uncertain_consistency = [observation["observation_id"] for observation in unresolved
                                 if predicate.cardinality == "one" and positive_values
                                 and ((observation["polarity"] == "negative" and _value_key(observation["value"]) in positive_values)
                                      or (observation["polarity"] == "positive" and _value_key(observation["value"]) not in positive_values))]
        if supporting and opposing or inconsistent:
            outcome = result("unknown", supporting=supporting, opposing=opposing,
                             conflict=True, reasons=("conflicting_observations",))
        elif ((supporting and uncertain_opposition) or (opposing and uncertain_support)
              or ((supporting or opposing) and uncertain_consistency)):
            unresolved_ids = ((uncertain_opposition if supporting else uncertain_support) + uncertain_consistency)
            outcome = result("unknown", supporting=supporting, opposing=opposing, unresolved=unresolved_ids,
                             reasons=("unresolved_temporal_opposition",))
        elif supporting:
            outcome = result("true", supporting=supporting)
        elif opposing:
            outcome = result("false", opposing=opposing)
        else:
            reasons = [reason for observation in candidates for reason in validity[observation["observation_id"]].reasons]
            outcome = result("unknown", unresolved=uncertain_support + uncertain_opposition,
                             reasons=["no_determining_observation", *reasons])
        return negate(outcome) if node.get("polarity", "positive") == "negative" else outcome

    def visit(node):
        if node["op"] == "always":
            return result("true")
        if node["op"] == "unresolved":
            return result("unknown", reasons=("unresolved_condition",))
        if node["op"] in {"eq", "ne"}:
            return result("unknown", reasons=("legacy_condition",))
        if node["op"] == "holds":
            return atom(node)
        if node["op"] == "not":
            return negate(visit(node["arg"]))
        return combine([visit(child) for child in node["args"]], node["op"])

    return visit(expression)
