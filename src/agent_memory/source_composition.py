from __future__ import annotations

import copy
import json

SOURCE_COMPOSITION_INPUT_VERSION = "source-composition-v1"
COMPOSITION_V2_INPUT_VERSION = "source-composition-v2"
SOURCE_COMPOSITION_INPUT_VERSIONS = (SOURCE_COMPOSITION_INPUT_VERSION, COMPOSITION_V2_INPUT_VERSION)
PARTICIPANT_ACTIONS = (
    "Preserve the actor of each described action, separately from who reported the account. "
    "For each predicate, check who acted, what they did, to whom or what, and with which qualifiers. "
    "Do not attach several people's actions to one named subject, infer that an observer participated, "
    "or resolve an ambiguous pronoun by plausibility. A collective activity does not establish that "
    "every named participant performed every action. Split clauses with explicit actors, or describe "
    "the shared topic without assigning unsupported actions."
)
COMPOSITION_INSTRUCTIONS = (
    "Source composition procedure: before writing a factual description, identify the supported "
    "event clauses and keep their participants, actions, objects, time, frequency, uncertainty and "
    "expressed meaning fixed. " + PARTICIPANT_ACTIONS + " "
    "For text in the user's voice, build the remembered scene from those clauses first; then change "
    "wording and sentence rhythm only. A natural-sounding modifier can assert a new fact: check "
    "adjectives, possessives, pluralization and habitual phrasing as carefully as main verbs. Do "
    "not infer circumstances from a setting or transfer a detail from one occasion to others. "
    "Read the whole draft once more against the source, "
    "including decorative phrases and the final sentence. Remove unsupported factual additions "
    "rather than qualifying them in an earlier disclaimer. If faithful paraphrase is uncertain, "
    "quote the relevant source wording and write around it. Do not expose this checking procedure "
    "or a factual checklist in ordinary conversation."
)
PARTICIPANT_VERIFY_INSTRUCTIONS = (
    "For episode_actions only, also return organization_fidelity.participant_actions. This is distinct from speaker "
    "attribution: check the people and objects INSIDE the reported scene. Evaluate every predicate "
    "and modifier in the organization_statement, including compressed titles and coordinated "
    "phrases. " + PARTICIPANT_ACTIONS + " "
    "If the wording admits an unsupported actor-action assignment under its ordinary reading, "
    "return unknown even if a more charitable reading would match the source. All five fidelity "
    "facets are required. A positive whole-proposal or attribution verdict cannot override this "
    "check. Do not rewrite the proposed statement."
)
CLAUSE_VERIFY_INSTRUCTIONS = (
    "For every non-episode proposal, also return participant_actions_verdict using the same "
    "actor-action standard. Check every predicate, coordinated phrase and quoted draft voice, "
    "in both the full statement and any recall_summary. "
    "An assistant's first-person draft does not mean the assistant performed the user's actions. "
    "Do not attach the user's actions to another named participant during compression. Return "
    "unknown for an ambiguous assignment, contradicts for a conflicting one, and supports only "
    "when all assignments are supported (including when no action is asserted). This verdict "
    "cannot be overridden by citation coverage or whole-proposal support."
)
COHERENT_ENCODING_INSTRUCTIONS = (
    "A recall_summary is an alternate view of the same stored claim, not a second duplicate "
    "memory. For a detailed recollection, prefer a coherent account retaining the scene AND "
    "the source-expressed meaning, with a short view when it saves context; do not mechanically "
    "split every sentence into an isolated claim. Separate incompatible scope or qualifications. "
    "Choose retention_priority from the source rather than giving every record the same default. "
    "Citation event_revision may also be spelled revision under this schema; use exactly one. "
    "Episode members and memory versions retain their own revision fields."
)


def compatible_reference_schema(base):
    result = copy.deepcopy(base)
    for group in ("claims", "situation_updates", "belief_links"):
        properties = result["properties"][group]["items"].get("properties", {})
        for field in ("evidence_refs", "counterexamples"):
            if field not in properties:
                continue
            reference = properties[field]["items"]
            if "revision" in reference["properties"]:
                continue
            reference["properties"]["revision"] = copy.deepcopy(reference["properties"]["event_revision"])
            reference["required"].remove("event_revision")
            reference["oneOf"] = [
                {"required": ["event_revision"], "not": {"required": ["revision"]}},
                {"required": ["revision"], "not": {"required": ["event_revision"]}},
            ]
    definitions = {}
    for group in ("claims", "situation_updates", "belief_links"):
        properties = result["properties"][group]["items"].get("properties", {})
        for field in ("evidence_refs", "counterexamples"):
            if field not in properties or "$ref" in properties[field]["items"]:
                continue
            reference = properties[field]["items"]
            signature = json.dumps(reference, sort_keys=True)
            name = definitions.setdefault(signature, (f"citation{len(definitions) + 1}", reference))[0]
            properties[field]["items"] = {"$ref": f"#/$defs/{name}"}
    result.setdefault("$defs", {}).update({name: reference for name, reference in definitions.values()})
    return result


def canonical_references(proposed):
    result = copy.deepcopy(proposed)
    count = 0
    for group in ("claims", "situation_updates", "belief_links"):
        for item in result.get(group, []):
            for field in ("evidence_refs", "counterexamples"):
                for reference in item.get(field, []):
                    if "revision" in reference:
                        if "event_revision" in reference:
                            raise ValueError("Ambiguous citation version fields")
                        reference["event_revision"] = reference.pop("revision")
                        count += 1
    return result, count


def participant_verification_schema(base, identifiers):
    result = copy.deepcopy(base)
    if identifiers:
        assessment = result["properties"]["assessments"]["items"]
        assessment["properties"]["participant_actions_verdict"] = {"enum": ["supports", "contradicts", "unknown"]}
        assessment.setdefault("allOf", []).append({
            "if": {"properties": {"proposal_id": {"enum": sorted(set(identifiers))}}},
            "then": {"required": ["participant_actions_verdict"]},
            "else": {"not": {"required": ["participant_actions_verdict"]}},
        })
    return result
