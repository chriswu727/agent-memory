from __future__ import annotations

import copy

from .belief_store import assertion_fingerprint
from .helpers import DomainError, digest
from .local_semantics import (
    HISTORY_INTEGRATION_MODE,
    LOCAL_OBSERVATION_MODE,
    bounded_verification_context,
)
from .neighborhood import Neighborhood, _Incomplete, _Selector

MANIFEST_VERSION = "bounded-history-manifest-v1"
MAX_COMPARISONS = 32
MAX_PACKAGE_COMPARISONS = 3
_DISCOVERY_LIMITS = frozenset({"cue_limit", "candidate_scan_limit", "candidate_limit", "source_candidate_limit"})


def empty_working(state):
    return {**{key: copy.deepcopy(state[key]) for key in (
        "scope", "revision", "through_sequence", "updated_at",
    )}, "items": [], "open_loops": []}


def input_fingerprint(sources):
    return digest([{"event_id": item["event_id"], "revision": item["revision"],
                    "content_sha256": item["content_sha256"]}
                   for item in sorted(sources, key=lambda item: (item["sequence"], item["event_id"]))])


def snapshot_fingerprint(snapshot):
    frozen = copy.deepcopy(snapshot)
    if "learning_window" in frozen:
        frozen["learning_window"] = {key: value for key, value in frozen["learning_window"].items() if key in {
            "from_sequence", "through_sequence", "attempted_through_sequence", "input_count", "skipped_unavailable_count",
        }}
    return digest(frozen)


def _required_sources(selector, references):
    try:
        events = selector.source_package(ref["event_id"] for ref in references)
        if (not references or len(events) != len(references)
                or any(events[ref["event_id"]]["revision"] != ref["revision"] for ref in references)
                or any(any(event["scope"][field] != selector.scope[field]
                           for field in ("conversation_id", "branch_id")) for event in events.values())):
            raise _Incomplete("source_version_mismatch")
        return events
    except (_Incomplete, KeyError, TypeError) as error:
        raise DomainError("source_unavailable", "Required learning inputs are unavailable or exceed their budget.") from error


def _request_size(selector, mode, events, memories, sampling, *, input_refs=(), manifest=None):
    from .semantic import proposal_data, proposal_schema, request_payload

    schema = proposal_schema(mode, input_contract_version=selector.input_contract_version)
    data = proposal_data(
        events.values(), (selector.bind(value) for value in memories.values()), empty_working(selector.working),
        (), sampling, selector.relations_for(events, memories), scope=selector.scope,
        learning_input_refs=input_refs, proposal_mode=mode, input_contract_version=selector.input_contract_version,
        integration_manifest=manifest,
    )
    return request_payload("proposal", data, schema, selector.policy.max_output_tokens, proposal_mode=mode,
                           input_contract_version=selector.input_contract_version)[1]


def local_neighborhood(conn, service, principal, brain, target, *, through_sequence, required_event_refs,
                       policy, input_contract_version):
    selector = _Selector(conn, service, principal, brain, target, through_sequence, policy,
                         learning_input_refs=required_event_refs, input_contract_version=input_contract_version)
    events = _required_sources(selector, required_event_refs)
    sampling = {
        "strategy": LOCAL_OBSERVATION_MODE, "through_sequence": through_sequence,
        "indexed_through_sequence": min(brain["indexed_sequence"], through_sequence),
        "search_exhaustive": False, "publication_restricted": False,
        "source_count": len(events), "assertion_count": 0, "episode_count": 0,
        "omitted_packages": 0, "limitations": [],
        "selected_min_sequence": min(event["sequence"] for event in events.values()),
        "selected_max_sequence": max(event["sequence"] for event in events.values()),
        "limits": {"sources": policy.max_sources, "assertions": policy.max_assertions, "episodes": policy.max_episodes},
    }
    size = _request_size(selector, LOCAL_OBSERVATION_MODE, events, {}, sampling, input_refs=required_event_refs)
    if size > min(policy.max_request_bytes, policy.max_input_bytes):
        raise DomainError("budget_exceeded", "Complete local learning inputs exceed their request budget.")
    return Neighborhood(tuple(sorted(events.values(), key=lambda event: event["sequence"])), (), (), sampling)


def _minimum_verification_size(selector, events, memories, manifest):
    from .semantic import VERIFICATION_SCHEMA, request_payload

    context = bounded_verification_context(
        HISTORY_INTEGRATION_MODE, events.values(), (selector.bind(value) for value in memories.values()),
        selector.relations_for(events, memories), manifest=manifest,
    )
    return request_payload("verification", {"proposals": [], **context}, VERIFICATION_SCHEMA,
                           selector.policy.max_output_tokens, proposal_mode=HISTORY_INTEGRATION_MODE,
                           input_contract_version=selector.input_contract_version)[1]


def discover_manifest(conn, service, principal, brain, target, *, through_sequence, required_event_refs,
                      policy, input_contract_version, assessed_ids=(), excluded_versions=(), attention_policy=None):
    from .history_attention import POLICY, admit

    if attention_policy not in (None, POLICY):
        raise DomainError("dependency_unavailable", "Historical attention policy is unavailable.")
    selector = _Selector(conn, service, principal, brain, target, through_sequence, policy,
                         input_contract_version=input_contract_version)
    sources = _required_sources(selector, required_event_refs)
    fingerprint = input_fingerprint(sources.values())
    cue = "\n".join(event["observed"]["content"].get("text", "")
                    for event in sorted(sources.values(), key=lambda item: item["sequence"]))
    if len(cue) > 8192:
        cue = cue[:8192]
        selector.reasons.add("cue_limit")
    candidates = selector.candidates(cue, excluded_versions=excluded_versions, independent_of_event_ids=tuple(sources))
    objectives = []
    for memory in candidates:
        supported = {ref["event_id"] for ref in memory["evidence_refs"] if ref["relationship"] == "supports"}
        if not supported - sources.keys():
            continue
        version = {"memory_id": memory["memory_id"], "revision": memory["revision"],
                   "content_sha256": assertion_fingerprint(selector.bind(memory))}
        identifier = "comparison-" + digest([MANIFEST_VERSION, fingerprint, version])
        if identifier not in assessed_ids:
            objectives.append({"objective_id": identifier, "target_memory_version": version})
    truncated = len(objectives) > MAX_COMPARISONS or bool(selector.reasons & _DISCOVERY_LIMITS)
    manifest = {
        "version": MANIFEST_VERSION, "input_fingerprint": fingerprint,
        "input_refs": [{"event_id": item["event_id"], "revision": item["revision"],
                        "content_sha256": item["content_sha256"]}
                       for item in sorted(sources.values(), key=lambda item: item["sequence"])],
        "through_sequence": through_sequence, "objectives": objectives[:MAX_COMPARISONS],
        "unresolved_discovery": truncated, "discovery_limitations": sorted(selector.reasons & _DISCOVERY_LIMITS),
    }
    if attention_policy == POLICY:
        return admit({**manifest, "objectives": objectives,
                      "unresolved_discovery": bool(selector.reasons & _DISCOVERY_LIMITS)},
                     candidates, sources.values())
    return manifest


def complete_history_package(selector, root, *, expansions=None):
    expansions = {} if expansions is None else expansions
    pending = {(root["memory_id"], root["revision"]): root}
    expanded, sources, memories = set(), {}, {}
    while pending:
        key = next(iter(pending))
        candidate = pending.pop(key)
        if key in expanded:
            continue
        expansion_key = (key, digest(candidate))
        if expansion_key not in expansions:
            expansions[expansion_key] = selector.assertion_package(candidate)
        events, dependencies = expansions[expansion_key]
        sources.update(events)
        memories.update(dependencies)
        if len(sources) > selector.policy.max_sources or len(memories) > selector.policy.max_assertions:
            raise _Incomplete("dependency_incomplete")
        expanded.add(key)
        pending.update({version: memory for version, memory in dependencies.items() if version not in expanded})
    relations = selector.relations_for(sources, memories)
    if len(relations) > selector.policy.max_assertions:
        raise _Incomplete("belief_relation_budget")
    return sources, memories


def integration_package(conn, service, principal, brain, target, *, manifest, policy, input_contract_version):
    from .history_semantics import validate_manifest

    validate_manifest(manifest)
    selector = _Selector(conn, service, principal, brain, target, manifest["through_sequence"], policy,
                         input_contract_version=input_contract_version)
    refs = [{"event_id": ref["event_id"], "revision": ref["revision"]} for ref in manifest["input_refs"]]
    required = _required_sources(selector, refs)
    if input_fingerprint(required.values()) != manifest["input_fingerprint"]:
        raise DomainError("context_stale", "Historical integration inputs changed.")
    selected_events, selected_memories, selected, deferred = dict(required), {}, [], []
    # Reuse ends with this scope/cutoff snapshot; later calls must recheck source invalidation.
    expansions = {}
    sampling = {"strategy": HISTORY_INTEGRATION_MODE, "through_sequence": manifest["through_sequence"],
                "search_exhaustive": False, "publication_restricted": False,
                "omitted_packages": 0, "source_count": len(required), "assertion_count": 0,
                "episode_count": 0, "limitations": []}
    for objective in manifest["objectives"]:
        if len(selected) >= MAX_PACKAGE_COMPARISONS:
            deferred.append(copy.deepcopy(objective))
            continue
        reference = objective["target_memory_version"]
        key = (reference["memory_id"], reference["revision"])
        found = selector.load_memories([key])
        if (len(found) != 1 or assertion_fingerprint(selector.bind(selector.memory_cache[key]["document"]))
                != reference["content_sha256"]):
            raise DomainError("context_stale", "Historical comparison endpoint changed.")
        try:
            events, memories = complete_history_package(
                selector, selector.memory_cache[key]["document"], expansions=expansions)
            combined_events, combined_memories = selected_events | events, selected_memories | memories
            if len(combined_events) > policy.max_sources or len(combined_memories) > policy.max_assertions:
                raise _Incomplete("source_budget")
            candidate = {**manifest, "objectives": [*selected, objective]}
            candidate_sampling = {**sampling, "source_count": len(combined_events),
                                  "assertion_count": len(combined_memories)}
            proposal_size = _request_size(selector, HISTORY_INTEGRATION_MODE, combined_events, combined_memories,
                                          candidate_sampling, manifest=candidate)
            verification_minimum = _minimum_verification_size(selector, combined_events, combined_memories, candidate)
            if (proposal_size + 2048 > policy.max_request_bytes
                    or verification_minimum + 2048 > policy.max_request_bytes
                    or proposal_size + verification_minimum + 2048 > policy.max_input_bytes):
                raise _Incomplete("request_bytes_budget")
            selected_events, selected_memories = combined_events, combined_memories
            selected.append(copy.deepcopy(objective))
        except _Incomplete:
            deferred.append(copy.deepcopy(objective))
    sampling.update(source_count=len(selected_events), assertion_count=len(selected_memories))
    package = Neighborhood(
        tuple(sorted(selected_events.values(), key=lambda event: event["sequence"])),
        tuple(selected_memories[key] for key in sorted(selected_memories)), (), sampling,
        tuple(selector.relations_for(selected_events, selected_memories)),
    )
    return package, {**manifest, "objectives": selected}, {**manifest, "objectives": deferred}
