from __future__ import annotations

import copy

CITATION_COVERAGE_INPUT_VERSION = "citation-coverage-v1"
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


def citation_scope(references, *, evidence_selection=None):
    return {
        "version": CITATION_COVERAGE_INPUT_VERSION,
        "positive_evidence_indices": [index for index, reference in enumerate(references)
                                      if reference["relationship"] == "supports"],
        "coverage_target": "entire_proposal",
        "support_selection": ("supporting_evidence_indices" if evidence_selection == "minimal_shared_support"
                              else "all_positive_evidence_indices"),
        "context_authority": "qualifications_and_counterevidence_only",
        "other_proposals_authority": "none",
    }


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
