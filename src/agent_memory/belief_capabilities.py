from __future__ import annotations

import copy
from itertools import combinations

from jsonschema import Draft202012Validator, FormatChecker

from . import cognition
from .applicability import validate_new_condition
from .consolidation_planning import abstraction_profile, validate_abstraction_profiles
from .helpers import DomainError, digest

RELATION_CAPABILITIES_INPUT_VERSION = "relation-capabilities-v1"
RELATIONS = ("abstracts", "equivalent", "exception", "supersedes", "contradicts")
EQUIVALENT_FIELDS = ("memory_kind", "world", "claim_kind", "conditions", "valid_time", "valid_during", "procedure")
CAPABILITY_INSTRUCTIONS = (
    "belief_relation_capabilities states structural restrictions for the exact supplied endpoints, "
    "not semantic support. An unavailable relation must not be proposed. conditional_relations still "
    "require complete validation of the proposed claim and the whole union of endpoint and claim "
    "evidence, then independent semantic assessment. claim_kinds_for_endpoint_evidence applies only "
    "to that endpoint's evidence, not to additional sources in a future proposal. For abstracts, "
    "every selected pair must appear in each other's abstraction_partner_indices; these indices "
    "refer only to this capability table. Assistant speech supports eligible speech reports, never "
    "an inferred_pattern schema. Do not infer missing endpoint conditions or attribution metadata. "
    "Keep an explicit no_change decision when no supported permitted relation is available; do not "
    "invent a relation to complete a comparison. None of these capabilities establishes factual truth."
)


def _references(memory):
    refs = {}
    for ref in [*memory["evidence_refs"], *memory.get("counterexamples", [])]:
        value = {**copy.deepcopy(ref), "relationship": "supports"}
        refs.setdefault(digest(value), value)
    return list(refs.values())


def relation_capabilities(scope, events, assertions, *, claim_schema, check_sources, max_link_evidence):
    memories = sorted(assertions.values(), key=lambda memory: (memory["memory_id"], memory["revision"]))
    definitions = claim_schema["$defs"]
    fields = claim_schema["properties"]
    reference_validator = Draft202012Validator({"$defs": definitions, **fields["evidence_refs"]})
    metadata_validator = Draft202012Validator({
        "$defs": definitions, "type": "object", "properties": {key: fields[key] for key in EQUIVALENT_FIELDS},
        "required": list(EQUIVALENT_FIELDS), "additionalProperties": False,
    }, format_checker=FormatChecker())
    entries, references, profiles = [], [], []
    for memory in memories:
        refs = _references(memory)
        references.append({digest(ref) for ref in refs})
        kinds = []
        if (reference_validator.is_valid(refs) and memory["lifecycle"] != "retracted"
                and cognition._scope_allows(memory["scope"], scope)):
            for kind in fields["claim_kind"]["enum"]:
                try:
                    check_sources({"world": memory["world"], "claim_kind": kind,
                                   "evidence_refs": refs, "counterexamples": []}, events, scope)
                except (ValueError, DomainError, KeyError, TypeError):
                    continue
                kinds.append(kind)
        unavailable = {kind: "endpoint_evidence_incompatible" for kind in RELATIONS} if not kinds else {}
        metadata = {key: copy.deepcopy(memory[key]) for key in EQUIVALENT_FIELDS if key in memory}
        if not metadata_validator.is_valid(metadata):
            unavailable["equivalent"] = "endpoint_metadata_incomplete"
        elif memory["claim_kind"] not in kinds:
            unavailable["equivalent"] = "endpoint_claim_kind_incompatible"
        if "inferred_pattern" not in kinds:
            unavailable["abstracts"] = "abstraction_attribution_unavailable"
        profile = None
        if "abstracts" not in unavailable:
            try:
                validate_new_condition(memory["conditions"])
                profile = abstraction_profile(memory, events)
            except (ValueError, DomainError, KeyError, TypeError):
                unavailable["abstracts"] = "incompatible_instance_metadata"
        profiles.append(profile)
        entries.append({
            "memory_version": {"memory_id": memory["memory_id"], "revision": memory["revision"]},
            "claim_kinds_for_endpoint_evidence": kinds,
            "unavailable_relations": unavailable, "abstraction_partner_indices": [],
        })
    for left, right in combinations(range(len(memories)), 2):
        if any("abstracts" in entries[index]["unavailable_relations"] for index in (left, right)):
            continue
        if len(references[left] | references[right]) > max_link_evidence:
            continue
        try:
            validate_abstraction_profiles([profiles[left], profiles[right]],
                                          memories[left]["conditions"], memories[left]["world"])
        except (ValueError, DomainError, KeyError, TypeError):
            continue
        entries[left]["abstraction_partner_indices"].append(right)
        entries[right]["abstraction_partner_indices"].append(left)
    for entry in entries:
        if not entry["abstraction_partner_indices"]:
            entry["unavailable_relations"].setdefault("abstracts", "no_eligible_independent_partner")
        entry["conditional_relations"] = [kind for kind in RELATIONS if kind not in entry["unavailable_relations"]]
    return {"version": RELATION_CAPABILITIES_INPUT_VERSION,
            "interpretation": "structural_limits_not_semantic_support", "endpoints": entries}


def validate_capability_link(link, capabilities):
    endpoints = capabilities["endpoints"]
    indices = {(entry["memory_version"]["memory_id"], entry["memory_version"]["revision"]): index
               for index, entry in enumerate(endpoints)}
    selected = [indices[(ref["memory_id"], ref["revision"])] for ref in link["target_memory_versions"]]
    if any(link["kind"] not in endpoints[index]["conditional_relations"] for index in selected):
        raise ValueError("Relation is unavailable for its exact endpoint evidence")
    if link["kind"] == "abstracts" and any(
        right not in endpoints[left]["abstraction_partner_indices"] for left, right in combinations(selected, 2)
    ):
        raise ValueError("Abstraction endpoints have incompatible structural requirements")
