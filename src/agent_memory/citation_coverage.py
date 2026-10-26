from __future__ import annotations

import copy

CITATION_COVERAGE_INPUT_VERSION = "citation-coverage-v1"
CITATION_PROVENANCE_INPUT_VERSION = "citation-provenance-v1"
CITATION_COVERAGE_INPUT_VERSIONS = (CITATION_COVERAGE_INPUT_VERSION, CITATION_PROVENANCE_INPUT_VERSION)
CITATION_COVERAGE_VERIFY_INSTRUCTIONS = (
    "Each non-episode proposal has a server-derived citation_scope. Return its separate required "
    "citation_coverage_verdict: supports only when THIS proposal's numbered positive excerpts jointly "
    "cover EVERY material assertion in the entire proposal, including attribution, enumerated details, "
    "conditions, negation, uncertainty, world, time, procedure and relationship. A header introducing a "
    "list does not support uncited list items. Individually relevant citations do not establish complete "
    "coverage. If any material clause lacks support in the authorized excerpts, return unknown even "
    "when it is stated elsewhere in the complete source. Do not repair, expand or replace references. "
    "Full source records, local/historical context, parent assertions and other proposals remain "
    "available to detect qualifications, conflicts and counterevidence, but cannot supply a missing "
    "positive premise. Another proposal's citations, including an episode's full member sources, "
    "cannot support this proposal. Existing endpoint statements identify what a relationship connects, "
    "not independent evidence of that relationship. Counterexample excerpts cannot supply positive "
    "support. For minimal_shared_support, assess coverage using only this proposal's positive indices "
    "also selected in supporting_evidence_indices; an irrelevant or unselected excerpt cannot fill a "
    "gap. Keep all existing whole-proposal, citation, instance, request and relationship verdicts: "
    "citation coverage never overrides an adverse context or fidelity judgment. Episode proposals "
    "retain their complete member-source authority and organization-fidelity checks and must not "
    "return citation_coverage_verdict. These assessments remain fallible, not truth certificates."
)


CITATION_PROVENANCE_VERIFY_INSTRUCTIONS = (
    "Each non-episode proposal has a server-derived citation_scope. Return its separate required "
    "citation_coverage_verdict: supports only when THIS proposal's selected positive excerpts and "
    "their narrowly authorized provenance jointly cover EVERY material assertion in the entire "
    "proposal, including attribution, enumerated details, conditions, negation, uncertainty, world, "
    "time, procedure and relationship. For each selected positive reference, resolve its OWN exact "
    "event_id, event_revision and source_content_sha256 to the supplied source record, directly or "
    "through source_ref. Only that record's observed.actor_role and observed.delivery_state may "
    "provide positive provenance support, and only for the recorded communication's speaker role "
    "and delivery status. Thus a received user message can support 'the user said' its cited text, "
    "and a delivered assistant message can support 'the assistant said' its cited text. These "
    "metadata do not establish a human identity, the truth or completion of a described external "
    "event, a promise's fulfillment, or any uncited content. No other metadata fields supply a "
    "positive premise under this exception. Do not borrow another reference's speaker or delivery "
    "status. All attribution, source eligibility and world constraints remain binding. A header "
    "introducing a list does not support uncited list items. Individually relevant citations do not "
    "establish complete coverage. If any material clause lacks support in the authorized excerpts "
    "and this limited provenance, return unknown even when it is stated elsewhere in the complete "
    "source. Do not repair, expand or replace references. Apart from the listed provenance of each "
    "selected positive reference, full source records, local/historical context, parent assertions "
    "and other proposals may detect qualifications, conflicts and counterevidence but cannot supply "
    "a missing positive premise. Another proposal's citations, including an episode's full member "
    "sources, cannot support this proposal. Existing endpoint statements identify what a relationship "
    "connects, not independent evidence of that relationship. Counterexample excerpts and their "
    "provenance cannot supply positive support. For minimal_shared_support, use only this proposal's "
    "positive indices also selected in supporting_evidence_indices, including for provenance; an "
    "irrelevant or unselected reference cannot fill a gap. Keep all existing whole-proposal, citation, "
    "instance, request and relationship verdicts: citation coverage never overrides an adverse "
    "context or fidelity judgment. Episode proposals retain their complete member-source authority "
    "and organization-fidelity checks and must not return citation_coverage_verdict. These "
    "assessments remain fallible, not truth certificates."
)


def citation_scope(references, *, evidence_selection=None, input_contract_version=CITATION_COVERAGE_INPUT_VERSION):
    if input_contract_version not in CITATION_COVERAGE_INPUT_VERSIONS:
        raise ValueError("Unsupported citation coverage contract")
    scope = {
        "version": input_contract_version,
        "positive_evidence_indices": [index for index, reference in enumerate(references)
                                      if reference["relationship"] == "supports"],
        "coverage_target": "entire_proposal",
        "support_selection": ("supporting_evidence_indices" if evidence_selection == "minimal_shared_support"
                              else "all_positive_evidence_indices"),
        "context_authority": "qualifications_and_counterevidence_only",
        "other_proposals_authority": "none",
    }
    if input_contract_version == CITATION_PROVENANCE_INPUT_VERSION:
        scope["context_authority"] = "qualifications_and_counterevidence_except_listed_provenance"
        scope["provenance_authority"] = {
            "fields": ["observed.actor_role", "observed.delivery_state"],
            "source_binding": "selected_positive_reference_event_id_revision_and_content_sha256",
            "use": "recorded_communication_attribution_and_delivery_only",
        }
    return scope


def citation_coverage_schema(base_schema, proposal_ids):
    schema = copy.deepcopy(base_schema)
    identifiers = sorted(set(proposal_ids))
    if identifiers:
        assessment = schema["properties"]["assessments"]["items"]
        assessment["properties"]["citation_coverage_verdict"] = {
            "enum": ["supports", "contradicts", "unknown"],
        }
        assessment.setdefault("allOf", []).append({
            "if": {"properties": {"proposal_id": {"enum": identifiers}}},
            "then": {"required": ["citation_coverage_verdict"]},
            "else": {"not": {"required": ["citation_coverage_verdict"]}},
        })
    return schema


def citation_coverage_supported(assessment):
    return isinstance(assessment, dict) and assessment.get("citation_coverage_verdict") == "supports"


def selected_citation_refs(assessment, references):
    indices, relations = assessment.get("supporting_evidence_indices"), assessment.get("relations")
    if (not isinstance(indices, list) or any(type(index) is not int for index in indices)
            or len(indices) != len(set(indices)) or not isinstance(relations, list)
            or any(not isinstance(item, dict) or type(item.get("evidence_index")) is not int
                   for item in relations)
            or len(relations) != len(references)
            or {item["evidence_index"] for item in relations} != set(range(len(references)))
            or set(indices) != {item["evidence_index"] for item in relations if item.get("verdict") == "supports"}
            or any(index < 0 or index >= len(references) or not isinstance(references[index], dict)
                   or references[index].get("relationship") != "supports" for index in indices)):
        return None
    return [reference for index, reference in enumerate(references) if index in indices]
