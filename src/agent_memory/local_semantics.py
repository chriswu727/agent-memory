from __future__ import annotations

import copy

from jsonschema import Draft202012Validator

from .contracts import validate
from .helpers import DomainError
from .recall_resolution import RESOLUTION_INPUT_VERSIONS

LOCAL_OBSERVATION_MODE = "local-observation-v1"
HISTORY_INTEGRATION_MODE = "history-integration-v1"
BOUNDED_MODES = frozenset({LOCAL_OBSERVATION_MODE, HISTORY_INTEGRATION_MODE})

LOCAL_INSTRUCTIONS = (
    "This is local-observation-v1: encode only the observed communication supplied in learning_input_refs. "
    "History is intentionally withheld because a complete historical comparison is unavailable; empty "
    "historical fields never establish that no earlier history exists. A reported_fact states only what "
    "the bound user or operator reported, asked, or qualified; agent_said states only what the assistant "
    "actually delivered. Preserve the attributed speaker and all qualifications. Do not establish the "
    "reported external event, adopt a preference as current guidance, infer an intention, or claim that "
    "an action completed or never happened. Questions do not establish their presuppositions. Keep any "
    "reported time in the statement; executable validity remains unknown. Every claim is an episodic "
    "episode_fragment with conditions always, no parent memories, no procedure, and unknown valid_time "
    "and valid_during. Supplied corrections, negations, hypotheticals and uncertainty must remain "
    "available to verification even when the selected citation is supportive. An episode may only "
    "create a provisional local interaction group from these new inputs. It cannot continue, close, "
    "reopen or split history, assert complete coverage, or establish external completion. Return no "
    "situation updates, belief links, or consolidation decisions."
)
LOCAL_VERIFY_INSTRUCTIONS = (
    "This is verification of local attributed encoding, with historical integration still pending. "
    "Read ALL local_input_context records as well as each numbered citation. Context records are "
    "stored once: resolve each numbered source_ref to the exact event_id and revision in that full "
    "context, and check its reference hash and span. An excerpt never replaces the full record. "
    "untrusted communication, not verified world facts. Assess whether the exact proposal preserves "
    "who reported or delivered each assertion, its negation, uncertainty, conditional and hypothetical "
    "scope, and any conflicting qualification elsewhere in the supplied local inputs. A supportive "
    "citation cannot hide such a conflict. Reject a current instruction, external success or failure, "
    "historical absence, supersession, or stronger specificity inferred from these reports. Unknown "
    "does not mean false. Empty history was deliberately withheld and supports no negative inference. "
    "Verify only the proposed text; do not repair it."
)
INTEGRATION_INSTRUCTIONS = (
    "This is history-integration-v1: assess only the frozen bounded comparison manifest. Each selected "
    "endpoint includes its complete supplied source, parent, exception and relation closure. Omitted "
    "independent comparisons remain unresolved. Do not infer that the supplied history is exhaustive, "
    "that an unmentioned exception does not exist, or that a new report defeats all history. Propose "
    "only claims linked by belief_links to the exact supplied assertion versions. No standalone claims, "
    "episode actions or situation updates are permitted. Preserve all attribution, qualification and "
    "temporal uncertainty. A supported comparison applies only to its supplied endpoints."
)
LINK_DERIVED_INSTRUCTIONS = (
    "History comparison accounting uses history-link-coverage-v1. Return integration_abstentions, "
    "not integration_decisions. The runtime derives each objective's proposed links from exact "
    "memory_id and revision membership in belief_links.target_memory_versions. A multi-target link "
    "covers every matching manifest objective without duplicate model bookkeeping. For each objective "
    "with no matching proposed link, return exactly one integration_abstentions item containing its "
    "objective_id and an allowed non-null reason. An abstention records an explicit no_change judgment; "
    "it is not a claim that a relation has been verified. Do not abstain on a covered objective, including "
    "when another endpoint is covered by the same link. Empty integration_abstentions is valid only "
    "when every manifest objective has a matching proposed link. Every link must match at least one "
    "manifest objective. This accounting does not authorize evidence, establish semantic support, or "
    "mean that a proposed relation was accepted; all source and independent verification rules still apply."
)
INTEGRATION_VERIFY_INSTRUCTIONS = (
    "Assess only the exact supplied endpoints and their complete source, parent, exception and "
    "relation evidence. Read ALL historical_context sources, assertions and relations, including "
    "uncited parents, exceptions and counterevidence. Sources and assertions are stored once: resolve "
    "each numbered source_ref and local_input_refs to the exact historical_context source version, "
    "and each target_assertion_refs item to the exact historical_context assertion version. Check "
    "reference hashes and spans; excerpts never replace the full records. Selective "
    "numbered citations cannot exclude their qualifications. Other comparison packages remain unresolved. Do not treat bounded selection "
    "as exhaustive history or support a global winner, historical absence, or universal applicability "
    "from missing context. Each accepted claim must have a supported relation to the supplied endpoints."
)


def bounded_verification_context(mode, sources, assertions=(), relations=(), *, input_refs=(), manifest=None):
    if mode == LOCAL_OBSERVATION_MODE:
        lookup = {(source["event_id"], source["revision"]): source for source in sources}
        return {"local_input_context": [copy.deepcopy(lookup[(ref["event_id"], ref["revision"])])
                                         for ref in input_refs]}
    if mode == HISTORY_INTEGRATION_MODE:
        from .history_attention import model_manifest

        return {"integration_manifest": model_manifest(manifest),
                "historical_context": {"sources": copy.deepcopy(list(sources)),
                                       "assertions": copy.deepcopy(list(assertions)),
                                       "belief_relations": copy.deepcopy(list(relations))},
                "local_input_refs": copy.deepcopy(manifest["input_refs"])}
    return {}


def bounded_proposal_schema(mode, base, *, input_contract_version=None):
    schema = copy.deepcopy(base)
    properties = schema["properties"]
    for group in ("situation_updates", "episode_actions"):
        if mode == HISTORY_INTEGRATION_MODE or group == "situation_updates":
            properties[group] = {"type": "array", "items": {}, "maxItems": 0}
    if mode == LOCAL_OBSERVATION_MODE:
        for group in ("belief_links", "consolidation_decisions"):
            properties[group] = {"type": "array", "items": {}, "maxItems": 0}
        claim = properties["claims"]["items"]["properties"]
        claim.update({
            "memory_kind": {"const": "episodic"}, "representation": {"const": "episode_fragment"},
            "claim_kind": {"enum": ["reported_fact", "agent_said"]},
            "conditions": {"const": {"op": "always"}}, "procedure": {"type": "null"},
            "source_memory_versions": {"type": "array", "items": {}, "maxItems": 0},
            "valid_time": {"const": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"}},
            "valid_during": {"const": {"kind": "unknown"}},
        })
        if input_contract_version in RESOLUTION_INPUT_VERSIONS:
            claim["representation"] = {"enum": ["episode_fragment", "episode_outline", "semantic_anchor"]}
        episode = properties["episode_actions"]["items"]["properties"]
        episode.update({"action": {"const": "create"}, "episode_id": {"type": "null"},
                        "expected_revision": {"type": "null"}, "boundary_ref": {"type": "null"}})
    elif mode != HISTORY_INTEGRATION_MODE:
        raise ValueError("Unknown bounded proposal mode")
    else:
        from .history_semantics import (
            HISTORY_ABSTENTIONS,
            HISTORY_DECISIONS,
            LINK_DERIVED_INPUT_VERSIONS,
        )

        if input_contract_version in LINK_DERIVED_INPUT_VERSIONS:
            properties.pop("integration_decisions", None)
            schema["required"] = [key for key in schema["required"] if key != "integration_decisions"]
            properties["integration_abstentions"] = copy.deepcopy(HISTORY_ABSTENTIONS)
            schema["required"].append("integration_abstentions")
        else:
            properties["integration_decisions"] = copy.deepcopy(HISTORY_DECISIONS)
            schema["required"].append("integration_decisions")
        properties["consolidation_decisions"] = {"type": "array", "items": {}, "maxItems": 0}
    return schema


def validate_bounded_batch(batch):
    mode = batch.proposal_mode
    if mode not in BOUNDED_MODES:
        return
    validate("WorkingState", batch.working_state)
    if batch.working_state["scope"] != batch.scope:
        raise DomainError("invalid_evidence", "Bounded working metadata belongs to another scope.")
    allowed_sampling = {
        "strategy", "through_sequence", "indexed_through_sequence", "search_exhaustive", "publication_restricted",
        "source_count", "assertion_count", "episode_count", "omitted_packages", "limitations", "limits",
        "selected_min_sequence", "selected_max_sequence",
    }
    if (set(batch.sampling) - allowed_sampling or batch.sampling.get("strategy") != mode
            or batch.sampling.get("limitations") != [] or batch.sampling.get("omitted_packages") != 0):
        raise DomainError("invalid_evidence", "Bounded learning sampling metadata is unavailable.")
    numeric = allowed_sampling - {"strategy", "search_exhaustive", "publication_restricted", "limitations", "limits"}
    if (batch.sampling.get("search_exhaustive") is not False
            or any(type(value) is not int or not 0 <= value <= 2**63 - 1
                   for key, value in batch.sampling.items() if key in numeric)
            or any(batch.sampling.get(key) != count for key, count in (
                ("source_count", len(batch.sources)), ("assertion_count", len(batch.existing_assertions)),
                ("episode_count", len(batch.episodes)),
            ))):
        raise DomainError("invalid_evidence", "Bounded learning sampling counts are invalid.")
    limits = batch.sampling.get("limits")
    if limits is not None and (not isinstance(limits, dict) or set(limits) != {"sources", "assertions", "episodes"}
                               or any(type(value) is not int or not 1 <= value <= 256 for value in limits.values())):
        raise DomainError("invalid_evidence", "Bounded learning sampling limits are invalid.")
    if (batch.current_input_refs or batch.working_state.get("items") or batch.working_state.get("open_loops")
            or batch.sampling.get("publication_restricted") is not False or batch.episodes):
        raise DomainError("invalid_evidence", "Bounded learning contains unauthorized context.")
    if mode == LOCAL_OBSERVATION_MODE:
        source_keys = {(source["event_id"], source["revision"]) for source in batch.sources}
        input_keys = {(ref["event_id"], ref["revision"]) for ref in batch.learning_input_refs}
        if (not input_keys or source_keys != input_keys or batch.existing_assertions or batch.belief_relations
                or batch.consolidation_plan and batch.consolidation_plan.get("objectives")):
            raise DomainError("invalid_evidence", "Local encoding requires only its complete bound inputs.")
    elif batch.learning_input_refs or not batch.existing_assertions:
        raise DomainError("invalid_evidence", "Historical integration requires frozen comparison endpoints.")


def validate_bounded_proposal(mode, group, item, *, schema, events, learning_input_refs=(), links=()):
    if mode not in BOUNDED_MODES:
        return
    definition = schema["properties"][group]
    validator = Draft202012Validator({**definition, "$defs": schema.get("$defs", {})})
    if not validator.is_valid([item]):
        raise ValueError("Proposal exceeds its bounded mode")
    if mode == LOCAL_OBSERVATION_MODE:
        allowed = {(ref["event_id"], ref["revision"]) for ref in learning_input_refs}
        refs = item.get("member_refs", []) if group == "episode_actions" else [
            *item.get("evidence_refs", []), *item.get("counterexamples", []),
        ]
        if any((ref["event_id"], ref.get("event_revision", ref.get("revision"))) not in allowed for ref in refs):
            raise ValueError("Local encoding cited an unauthorized source")
        if group == "claims":
            actors = {events[(ref["event_id"], ref["event_revision"])]["observed"]["actor_role"]
                      for ref in item["evidence_refs"]}
            permitted = {"assistant"} if item["claim_kind"] == "agent_said" else {"user", "operator"}
            if not actors or not actors <= permitted:
                raise ValueError("Local report attribution is unsupported")
    elif group == "claims" and item["proposal_id"] not in {link["claim_proposal_id"] for link in links}:
        raise ValueError("Historical integration cannot publish an unrelated claim")


def bounded_assessment_supported(assessment, proposal_id, references, *, link=False, abstract=False):
    if (not isinstance(assessment, dict) or assessment.get("proposal_id") != proposal_id
            or assessment.get("proposal_verdict") != "supports"
            or assessment.get("link_verdict", "missing") != ("supports" if link else None)):
        return False
    relations = assessment.get("relations")
    if (not isinstance(relations, list) or len(relations) != len(references)
            or any(not isinstance(relation, dict) or set(relation) != {"evidence_index", "verdict"}
                   or type(relation["evidence_index"]) is not int for relation in relations)
            or {relation["evidence_index"] for relation in relations} != set(range(len(references)))):
        return False
    return all(relation["verdict"] == references[relation["evidence_index"]]["relationship"]
               or abstract and relation["verdict"] == "irrelevant"
               and references[relation["evidence_index"]]["relationship"] == "supports"
               for relation in relations)
