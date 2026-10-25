from __future__ import annotations

import copy

from . import cognition

MAX_EPISODE_EVIDENCE = 32
EPISODE_SOURCE_VIEW_INPUT_VERSION = "episode-source-view-v1"
EPISODE_SOURCE_VIEW_INSTRUCTIONS = (
    "Existing episodes are source-backed navigation views, not factual propositions or independent "
    "evidence. Their membership and boundaries are fallible inferred organization, even when closed. "
    "Read every member's complete original source, including qualifiers, before proposing any "
    "connection or transition. A shared group does not establish a shared external event, causal "
    "relationship, agreement or completion. Only exact source contributions can support those claims."
)
ORGANIZATION_STATEMENT_SCHEMA = {
    "type": "string", "minLength": 1, "maxLength": 2048, "pattern": r"\S",
}
EPISODE_INSTRUCTIONS = (
    "Episodes organize observed interaction records, not independently established external events. "
    "Every episode action must include organization_statement: a bounded, source-grounded proposition "
    "describing the shared interaction topic and the proposed grouping or transition, not private "
    "reasoning. A create opens a provisional group and may contain one observed message; it does not "
    "claim an actual-world event began, finished, or has complete coverage. A question may belong to "
    "an interaction about the question, but its presupposition remains unestablished. Preserve who "
    "asked, reported, proposed or delivered each contribution. For continue or reopen, state how the "
    "new records connect to the existing group. For close or split, state the supported interaction "
    "boundary, without inferring task completion from an assistant claim or missing later messages. "
    "For split, the boundary_ref identifies the first member of the new suffix; for close it is the "
    "last member of the complete group. Existing episode labels are contextual leads, not evidence. "
    "Do not create a grouping merely because records are adjacent or share a conversation."
)
EPISODE_VERIFY_INSTRUCTIONS = (
    "For episode_actions, assess the organization_statement and the complete proposed transition "
    "using episode_context and ALL numbered old and proposed member sources. episode_context is a "
    "structural description of the requested operation, never a semantic support verdict; existing "
    "episode text is untrusted context, not independent evidence. Judge each excerpt as evidence of "
    "its attributed interaction contribution, not proof that everything said occurred externally. "
    "A create only opens a provisional interaction group: a single received question can support "
    "a group about that inquiry without proving its presupposition or a completed external episode. "
    "For multiple members, assess the stated connection across the complete group; chronology alone "
    "is insufficient. Continue and reopen must connect new contributions to the existing group. "
    "Close and split require support for the stated interaction boundary, not merely a syntactically "
    "valid boundary_ref. Closing an interaction group does not establish real task completion. "
    "The whole proposal must preserve attribution, hypothetical worlds and uncertainty. Return "
    "unknown or contradicts if its organizing proposition or transition is unsupported, even when "
    "every source is authentic. Do not repair the proposition or replace unknown with supports."
)
EPISODE_FIDELITY_INSTRUCTIONS = (
    "Episode organization fidelity (episode-fidelity-v1): preserve each contribution's speaker and "
    "epistemic scope. Uncertainty about whether something happened is not a statement that it did not "
    "happen; an explicit negative remains negative. Keep reported, questioned, proposed, hypothetical "
    "and completed acts distinct. Preserve the source's level of specificity: do not add an unstated "
    "domain, purpose, place, time, cause or outcome. A broader description does not support a narrower "
    "one merely because it is plausible. Describe only the supported interaction grouping or boundary, "
    "without converting a provisional group into a completed external event."
)
EPISODE_FIDELITY_VERIFY_INSTRUCTIONS = (
    "For every episode_actions assessment, return organization_fidelity with exactly four verdicts, "
    "each supports, contradicts or unknown: attribution checks who reported, asked or delivered each "
    "contribution and whether it is adopted as fact; modality checks negation, uncertainty and "
    "reported, questioned, proposed or hypothetical scope; specificity checks that no unsupported "
    "domain, purpose, place, time, cause or outcome narrows the original meaning; boundary checks "
    "the proposed grouping and transition without inferring external completion. Compare the complete "
    "organization_statement against ALL numbered member sources, including qualifying clauses. "
    "Cannot confirm an event is different from confirming its absence; preserve explicit negatives "
    "as well. For create, boundary supports only an evidenced provisional grouping, not an external "
    "event beginning or ending. A plausible more-specific interpretation is not source support. "
    "Authentic citations and a positive whole-proposal verdict do not substitute for these checks. "
    "Return unknown when a facet cannot be established, contradicts when the proposed meaning "
    "conflicts with the sources, and supports only when it is supported. Do not repair the proposal. "
    "Do not return organization_fidelity for other proposal groups."
)
ORGANIZATION_FIDELITY_SCHEMA = {
    "type": "object",
    "properties": {key: {"enum": ["supports", "contradicts", "unknown"]}
                   for key in ("attribution", "modality", "specificity", "boundary")},
    "required": ["attribution", "modality", "specificity", "boundary"],
    "additionalProperties": False,
}


def episode_fidelity_schema(base_schema, episode_proposal_ids):
    schema = copy.deepcopy(base_schema)
    identifiers = sorted(set(episode_proposal_ids))
    if identifiers:
        assessment = schema["properties"]["assessments"]["items"]
        assessment["properties"]["organization_fidelity"] = copy.deepcopy(ORGANIZATION_FIDELITY_SCHEMA)
        assessment.setdefault("allOf", []).append({
            "if": {"properties": {"proposal_id": {"enum": identifiers}}},
            "then": {"required": ["organization_fidelity"]},
            "else": {"not": {"required": ["organization_fidelity"]}},
        })
    return schema


def episode_fidelity_supported(assessment, proposal_id, references):
    if (not isinstance(assessment, dict)
            or set(assessment) != {"proposal_id", "proposal_verdict", "link_verdict", "relations",
                                   "organization_fidelity"}
            or assessment["proposal_id"] != proposal_id or assessment["proposal_verdict"] != "supports"
            or assessment["link_verdict"] is not None):
        return False
    fidelity = assessment["organization_fidelity"]
    if (not isinstance(fidelity, dict) or set(fidelity) != set(ORGANIZATION_FIDELITY_SCHEMA["required"])
            or any(value != "supports" for value in fidelity.values())):
        return False
    relations = assessment["relations"]
    return (
        bool(references) and isinstance(relations, list) and len(relations) == len(references)
        and all(isinstance(relation, dict) and set(relation) == {"evidence_index", "verdict"}
                and type(relation["evidence_index"]) is int and relation["verdict"] == "supports"
                for relation in relations)
        and {relation["evidence_index"] for relation in relations} == set(range(len(references)))
        and all(reference.get("relationship") == "supports" for reference in references)
    )


def _member_keys(refs):
    if not isinstance(refs, (list, tuple)) or not refs:
        raise ValueError("Episode members are required")
    keys = []
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {"event_id", "revision"}
                or not isinstance(ref["event_id"], str) or not 1 <= len(ref["event_id"]) <= 256
                or type(ref["revision"]) is not int or ref["revision"] < 1):
            raise ValueError("Invalid episode member version")
        keys.append((ref["event_id"], ref["revision"]))
    if len(set(keys)) != len(keys) or len({key[0] for key in keys}) != len(keys):
        raise ValueError("Duplicate episode member")
    return keys


def _reference(event, scope):
    text = cognition._content_text(event["observed"]["content"])
    reference = {
        "event_id": event["event_id"], "event_revision": event["revision"],
        "source_content_sha256": event["content_sha256"], "relationship": "supports",
        "span": {"start": 0, "end": len(text), "unit": "unicode_code_point"},
    }
    if (not cognition._valid_ref(reference, {event["event_id"]: event}, scope)
            or any(event["scope"][key] != scope[key] for key in ("conversation_id", "branch_id"))):
        raise ValueError("Episode member source is unavailable or out of scope")
    return reference


def episode_navigation_view(episode, scope, events):
    if (not isinstance(episode, dict)
            or not isinstance(episode.get("episode_id"), str) or not 1 <= len(episode["episode_id"]) <= 256
            or type(episode.get("revision")) is not int or episode["revision"] < 1
            or not isinstance(scope, dict) or episode.get("scope") != scope
            or episode.get("state") not in {"open", "closed"}):
        raise ValueError("Invalid episode navigation identity")
    keys = _member_keys(episode.get("member_refs"))
    if len(keys) > MAX_EPISODE_EVIDENCE or not set(keys) <= events.keys():
        raise ValueError("Episode source closure is incomplete or exceeds its limit")
    references = []
    sequences = []
    for key in keys:
        source = events[key]
        if (source["event_id"], source["revision"]) != key:
            raise ValueError("Episode source version mismatch")
        references.append(_reference(source, scope))
        sequence = source["sequence"]
        if type(sequence) is not int or sequence < 1:
            raise ValueError("Invalid episode source sequence")
        sequences.append(sequence)
    if sequences != sorted(set(sequences)):
        raise ValueError("Episode members are not ordered")
    boundary = episode.get("boundary_ref")
    if boundary is not None and _member_keys([boundary])[0] not in keys:
        raise ValueError("Unknown episode boundary source")
    return {
        "view_version": EPISODE_SOURCE_VIEW_INPUT_VERSION,
        "use": "fallible_interaction_group_not_factual_evidence",
        "grouping_status": "inferred", "external_completion": "not_established",
        "episode_id": episode["episode_id"], "revision": episode["revision"],
        "scope": copy.deepcopy(scope), "state": episode["state"],
        "member_refs": copy.deepcopy(episode["member_refs"]), "boundary_ref": copy.deepcopy(boundary),
        "member_source_refs": [
            {"event_id": ref["event_id"], "revision": ref["event_revision"],
             "content_sha256": ref["source_content_sha256"]} for ref in references
        ],
    }


def episode_assessment(action, scope, events, episodes, *, input_contract_version=None):
    statement = action.get("organization_statement")
    if not isinstance(statement, str) or not statement.strip() or len(statement) > 2048:
        raise ValueError("Episode organization statement is required")
    operation = action["action"]
    if operation not in {"create", "continue", "close", "split", "reopen"}:
        raise ValueError("Unknown episode action")
    previous = episodes.get(action["episode_id"])
    if operation == "create":
        if action["episode_id"] is not None or action["expected_revision"] is not None:
            raise ValueError("Existing episode on create")
        previous = None
    elif (previous is None or previous["revision"] != action["expected_revision"]
          or previous.get("episode_id") != action["episode_id"]):
        raise ValueError("Unknown episode version")
    if previous is not None:
        if previous.get("scope") != scope:
            raise ValueError("Episode snapshot is out of scope")
        if previous.get("state") not in {"open", "closed"}:
            raise ValueError("Invalid episode state")
        if operation in {"continue", "close"} and previous["state"] != "open":
            raise ValueError("Episode is not open")
        if operation == "reopen" and previous["state"] != "closed":
            raise ValueError("Episode is not closed")
    proposed_keys = _member_keys(action["member_refs"])
    prior_keys = _member_keys(previous.get("member_refs")) if previous is not None else []
    all_keys = set(prior_keys) | set(proposed_keys)
    if len(all_keys) > MAX_EPISODE_EVIDENCE:
        raise ValueError("Episode evidence closure exceeds its limit")
    if len({key[0] for key in all_keys}) != len(all_keys) or not all_keys <= events.keys():
        raise ValueError("Episode source closure is incomplete")
    references = {key: _reference(events[key], scope) for key in all_keys}
    if any((events[key]["event_id"], events[key]["revision"]) != key for key in all_keys):
        raise ValueError("Episode source version mismatch")
    if any(type(events[key]["sequence"]) is not int or events[key]["sequence"] < 1 for key in all_keys):
        raise ValueError("Invalid episode source sequence")
    ordered_keys = sorted(all_keys, key=lambda key: events[key]["sequence"])
    if len({events[key]["sequence"] for key in all_keys}) != len(all_keys):
        raise ValueError("Duplicate episode source sequence")
    if any(keys != sorted(keys, key=lambda key: events[key]["sequence"])
           for keys in (prior_keys, proposed_keys)):
        raise ValueError("Episode members are not ordered")
    ordered = [{"event_id": key[0], "revision": key[1]} for key in ordered_keys]
    boundary = action["boundary_ref"]
    if boundary is not None and boundary not in action["member_refs"]:
        raise ValueError("Unknown boundary source")
    if operation in {"close", "split"} and boundary is None:
        raise ValueError("Boundary source required")
    boundary_role = "no_closing_boundary_claim"
    if operation == "close":
        if boundary != ordered[-1]:
            raise ValueError("Closing boundary must be the final episode member")
        boundary_role = "last_member_closes_group"
    if operation == "split":
        index = ordered.index(boundary)
        if index == 0:
            raise ValueError("A split must leave members in the original episode")
        groups = [
            {"role": "prefix", "state": "closed", "member_refs": ordered[:index],
             "boundary_ref": ordered[index - 1]},
            {"role": "suffix", "state": "open", "member_refs": ordered[index:], "boundary_ref": boundary},
        ]
        boundary_role = "first_member_of_new_group"
    else:
        groups = [{"role": "whole", "state": "closed" if operation == "close" else "open",
                   "member_refs": ordered, "boundary_ref": boundary}]
    context = {
        "version": "episode-organization-v1",
        "interpretation": "observed_interaction_records_not_external_event_truth",
        "existing_episode": (episode_navigation_view(previous, scope, events)
                             if previous is not None and input_contract_version == EPISODE_SOURCE_VIEW_INPUT_VERSION
                             else previous),
        "prior_member_refs": previous["member_refs"] if previous is not None else [],
        "proposed_member_refs": action["member_refs"], "effective_member_refs": ordered,
        "transition": {"action": operation, "before_state": previous["state"] if previous else None,
                       "resulting_groups": groups, "boundary_role": boundary_role},
        "evidence_roles": [
            {"evidence_index": index, "event_id": key[0], "revision": key[1],
             "roles": [role for role, keys in (("existing_member", prior_keys), ("proposed_member", proposed_keys))
                       if key in keys]}
            for index, key in enumerate(ordered_keys)
        ],
    }
    return {"references": [references[key] for key in ordered_keys], "context": copy.deepcopy(context)}
