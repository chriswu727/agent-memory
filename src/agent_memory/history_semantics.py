from __future__ import annotations

import copy

from jsonschema import Draft202012Validator

from .citation_coverage import CITATION_COVERAGE_INPUT_VERSION
from .helpers import digest
from .history_attention import validate_attention
from .turns import _hash, _identifier, _integer

MANIFEST_VERSION = "bounded-history-manifest-v1"
LINK_DERIVED_INPUT_VERSION = "history-link-coverage-v1"
LINK_DERIVED_INPUT_VERSIONS = (LINK_DERIVED_INPUT_VERSION, CITATION_COVERAGE_INPUT_VERSION)
HISTORY_DECISIONS = {
    "type": "array", "maxItems": 3,
    "items": {
        "type": "object", "additionalProperties": False,
        "required": ["objective_id", "outcome", "reason", "link_proposal_ids"],
        "properties": {
            "objective_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "outcome": {"enum": ["proposed", "no_change"]},
            "reason": {"enum": [None, "insufficient_shared_meaning", "uncertain_attribution",
                                  "incompatible_qualifiers", "insufficient_evidence", "already_represented"]},
            "link_proposal_ids": {"type": "array", "maxItems": 16, "uniqueItems": True,
                                  "items": {"type": "string", "minLength": 1, "maxLength": 256}},
        },
    },
}
HISTORY_ABSTENTIONS = {
    "type": "array", "maxItems": 3,
    "items": {
        "type": "object", "additionalProperties": False,
        "required": ["objective_id", "reason"],
        "properties": {
            "objective_id": copy.deepcopy(HISTORY_DECISIONS["items"]["properties"]["objective_id"]),
            "reason": {"enum": [reason for reason in HISTORY_DECISIONS["items"]["properties"]["reason"]["enum"]
                                if reason is not None]},
        },
    },
}
_LINK_COVERAGE_SCHEMA = {
    "type": "array", "maxItems": 16,
    "items": {
        "type": "object", "required": ["proposal_id", "target_memory_versions"],
        "properties": {
            "proposal_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "target_memory_versions": {
                "type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                "items": {
                    "type": "object", "additionalProperties": False, "required": ["memory_id", "revision"],
                    "properties": {"memory_id": {"type": "string", "minLength": 1, "maxLength": 256},
                                   "revision": {"type": "integer", "minimum": 1}},
                },
            },
        },
    },
}


def validate_manifest(manifest, *, events=None, assertions=None):
    from .belief_store import assertion_fingerprint

    fields = {"version", "input_fingerprint", "input_refs", "through_sequence", "objectives",
              "unresolved_discovery", "discovery_limitations"}
    if (not isinstance(manifest, dict) or set(manifest) not in (fields, fields | {"attention"})
            or manifest["version"] != MANIFEST_VERSION):
        raise ValueError("Invalid historical integration manifest")
    _hash(manifest["input_fingerprint"])
    cutoff = _integer(manifest["through_sequence"], 1)
    if (type(manifest["unresolved_discovery"]) is not bool
            or not isinstance(manifest["discovery_limitations"], list)
            or len(manifest["discovery_limitations"]) > 4
            or any(value not in {"cue_limit", "candidate_scan_limit", "candidate_limit", "source_candidate_limit"}
                   for value in manifest["discovery_limitations"])):
        raise ValueError("Invalid discovery coverage")
    inputs, objectives = manifest["input_refs"], manifest["objectives"]
    if (not isinstance(inputs, list) or not 1 <= len(inputs) <= 8
            or not isinstance(objectives, list) or len(objectives) > 32):
        raise ValueError("Invalid historical integration size")
    seen = set()
    source_sequence = []
    for reference in inputs:
        if not isinstance(reference, dict) or set(reference) != {"event_id", "revision", "content_sha256"}:
            raise ValueError("Invalid historical integration source")
        key = (_identifier(reference["event_id"]), _integer(reference["revision"], 1))
        _hash(reference["content_sha256"])
        if key[0] in seen:
            raise ValueError("Duplicate historical integration source")
        seen.add(key[0])
        if events is not None:
            event = events.get(key)
            if event is None or event["content_sha256"] != reference["content_sha256"] or event["sequence"] > cutoff:
                raise ValueError("Historical integration source changed")
            source_sequence.append(event["sequence"])
    if digest(inputs) != manifest["input_fingerprint"] or source_sequence != sorted(set(source_sequence)):
        raise ValueError("Historical integration input identity changed")
    seen = set()
    for objective in objectives:
        if not isinstance(objective, dict) or set(objective) != {"objective_id", "target_memory_version"}:
            raise ValueError("Invalid historical comparison")
        reference = objective["target_memory_version"]
        if not isinstance(reference, dict) or set(reference) != {"memory_id", "revision", "content_sha256"}:
            raise ValueError("Invalid historical comparison endpoint")
        key = (_identifier(reference["memory_id"]), _integer(reference["revision"], 1))
        _hash(reference["content_sha256"])
        expected = "comparison-" + digest([MANIFEST_VERSION, manifest["input_fingerprint"], reference])
        if objective["objective_id"] != expected or expected in seen:
            raise ValueError("Historical comparison identity changed")
        seen.add(expected)
        if assertions is not None and (key not in assertions or assertion_fingerprint(assertions[key]) != reference["content_sha256"]):
            raise ValueError("Historical comparison endpoint changed")
    validate_attention(manifest)


def validate_decisions(manifest, proposals):
    decisions = proposals.get("integration_decisions", [])
    if not Draft202012Validator(HISTORY_DECISIONS).is_valid(decisions):
        raise ValueError("Invalid historical integration decisions")
    expected = {item["objective_id"]: item for item in manifest["objectives"]}
    if (len(decisions) != len(expected) or len({item["objective_id"] for item in decisions}) != len(decisions)
            or {item["objective_id"] for item in decisions} != expected.keys()):
        raise ValueError("Every bounded comparison needs one explicit outcome")
    links = {item["proposal_id"]: item for item in proposals.get("belief_links", [])}
    if len(links) != len(proposals.get("belief_links", [])):
        raise ValueError("Duplicate historical integration link")
    claimed = set()
    for decision in decisions:
        target = expected[decision["objective_id"]]["target_memory_version"]
        target = {key: target[key] for key in ("memory_id", "revision")}
        identifiers = decision["link_proposal_ids"]
        matching = {identifier for identifier, link in links.items() if target in link["target_memory_versions"]}
        if (len(identifiers) != len(set(identifiers)) or set(identifiers) != matching
                or (decision["outcome"] == "proposed" and (not identifiers or decision["reason"] is not None))
                or (decision["outcome"] == "no_change" and (identifiers or decision["reason"] is None))):
            raise ValueError("Historical comparison outcome differs from its links")
        claimed.update(identifiers)
    if claimed != links.keys():
        raise ValueError("Historical integration contains an unrelated link")
    return copy.deepcopy(decisions)


def derive_link_decisions(manifest, proposals):
    if (not isinstance(proposals, dict) or "integration_decisions" in proposals
            or "integration_abstentions" not in proposals):
        raise ValueError("Historical link coverage requires only explicit abstentions")
    abstentions = proposals["integration_abstentions"]
    if not Draft202012Validator(HISTORY_ABSTENTIONS).is_valid(abstentions):
        raise ValueError("Invalid historical integration abstentions")
    links = proposals.get("belief_links", [])
    if not Draft202012Validator(_LINK_COVERAGE_SCHEMA).is_valid(links):
        raise ValueError("Invalid historical integration link coverage")
    if len({link["proposal_id"] for link in links}) != len(links):
        raise ValueError("Duplicate historical integration link")
    abstained = {item["objective_id"]: item["reason"] for item in abstentions}
    expected = {item["objective_id"] for item in manifest["objectives"]}
    if len(abstained) != len(abstentions) or not set(abstained) <= expected:
        raise ValueError("Duplicate or unknown historical integration abstention")
    decisions = []
    for objective in manifest["objectives"]:
        identifier = objective["objective_id"]
        target = {key: objective["target_memory_version"][key] for key in ("memory_id", "revision")}
        matching = sorted(link["proposal_id"] for link in links if target in link["target_memory_versions"])
        if matching:
            if identifier in abstained:
                raise ValueError("Historical integration abstention contradicts a proposed link")
            decisions.append({"objective_id": identifier, "outcome": "proposed", "reason": None,
                              "link_proposal_ids": matching})
        else:
            if identifier not in abstained:
                raise ValueError("Uncovered historical comparison requires explicit abstention")
            decisions.append({"objective_id": identifier, "outcome": "no_change", "reason": abstained[identifier],
                              "link_proposal_ids": []})
    normalized = {key: value for key, value in proposals.items() if key != "integration_abstentions"}
    normalized["integration_decisions"] = decisions
    return validate_decisions(manifest, normalized)


def integration_coverage(manifest, decisions, links, *, assessed):
    if manifest is None:
        return {}
    selected = {link["proposal_id"] for link in links}
    decisions = {value["objective_id"]: value for value in decisions}
    outcomes = []
    for objective in manifest["objectives"]:
        decision = decisions.get(objective["objective_id"])
        supported = sorted(selected & set(decision["link_proposal_ids"])) if decision else []
        outcomes.append({
            "objective_id": objective["objective_id"], "assessed": bool(assessed and decision),
            "status": "published_relation" if supported else "assessed_no_publication" if assessed and decision
            else "not_evaluated", "verified_link_ids": supported,
            "reason": decision["reason"] if decision and decision["outcome"] == "no_change" else None,
        })
    return {"version": MANIFEST_VERSION, "objectives": outcomes,
            "assessed_count": sum(item["assessed"] for item in outcomes),
            "published_count": sum(bool(item["verified_link_ids"]) for item in outcomes)}
