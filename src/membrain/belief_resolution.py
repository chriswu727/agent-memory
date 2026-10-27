from __future__ import annotations

import copy

from .belief_store import assertion_fingerprint, relation_ref, relation_versions, validate_relation
from .helpers import DomainError, digest


def version(value):
    return value["memory_id"], value["revision"]


def relation_assessments(resolution, table=()):
    references = {(value["relation_id"], value["revision"]) for value in resolution.get("relation_refs", [])}
    return list(resolution.get("relations", [])) + [value for value in table
        if (value["relation"]["relation_id"], value["relation"]["revision"]) in references]


def factor_relations(result):
    assessments = {}
    for item in result["items"]:
        resolution = item.get("belief_resolution")
        if resolution is None:
            continue
        values = relation_assessments(resolution, result.get("belief_relations", []))
        resolution["relation_refs"] = [relation_ref(value["relation"]) for value in values]
        resolution["evidence_refs"] = resolution_evidence(resolution)
        resolution.pop("relations", None)
        for value in values:
            assessments[(value["relation"]["relation_id"], value["relation"]["revision"])] = value
    result["belief_relations"] = [assessments[key] for key in sorted(assessments)]


def frozen_relations(items):
    references = {}
    for item in items:
        for reference in item.get("belief_resolution", {}).get("relation_refs", []):
            references[(reference["relation_id"], reference["revision"])] = copy.deepcopy(reference)
        for assessment in item.get("belief_resolution", {}).get("relations", []):
            reference = relation_ref(assessment["relation"])
            references[(reference["relation_id"], reference["revision"])] = reference
    return [references[key] for key in sorted(references)]


def resolution_evidence(resolution):
    references = {digest(ref): ref for ref in resolution.get("evidence_refs", [])}
    for assessment in resolution.get("relations", []):
        for ref in assessment["relation"]["evidence_refs"] + assessment["applicability"]["evidence_refs"]:
            references[digest(ref)] = ref
    return list(references.values())


def _cyclic_nodes(edges):
    cyclic = set()
    for start in edges:
        pending, visited = list(edges[start]), {start}
        while pending:
            node = pending.pop()
            if node == start:
                cyclic.add(start)
                break
            if node not in visited:
                visited.add(node)
                pending.extend(edges.get(node, ()))
    return cyclic


def resolve(items, memories, events, scope, state, snapshot, timestamp):
    from . import cognition

    selected = {version(item["memory"]) for item in items}
    known = {version(memory): memory for memory in memories}
    incomplete = {version(value) for value in state.get("belief_incomplete_versions", [])}
    historical = snapshot["historical_evidence_status"] != "not_requested"
    cutoff = snapshot["resolved_evidence_through_sequence"]
    excluded = cognition._cue_descendants(set(snapshot["excluded_cue_event_ids"]), events)
    superseded = tuple(state.get("superseded_evidence_refs", ()))
    validated, adjacency, own = [], {}, {}
    cache = {}

    def applicability(memory):
        key = version(memory)
        if key not in own:
            assessment = cognition._memory_applicability(memory, state, scope, events, timestamp)
            start, end = cognition._range(memory["valid_time"])
            at = cognition._stamp(state.get("applicability_at", timestamp))
            if memory["memory_kind"] != "episodic" and (start and start > at or end and end < at):
                assessment.update(value="false", status="false", conflict=False)
            own[key] = assessment
        return own[key]

    for relation in state.get("belief_relations", [])[:32]:
        try:
            keys = relation_versions(relation)
            roots = relation["root_event_ids"]
            if historical and any(identifier in excluded or (
                    identifier in events and (cutoff is None or events[identifier]["sequence"] > cutoff))
                    for identifier in roots):
                continue
            validate_relation(relation)
            if not cognition._scope_allows(relation["scope"], scope):
                raise ValueError("Unavailable relation scope")
            if any(key not in known for key in keys):
                raise ValueError("Incomplete endpoints")
            for endpoint in relation["endpoint_snapshots"]:
                memory = known[version(endpoint["memory_version"])]
                if (assertion_fingerprint(memory) != endpoint["content_sha256"]
                        or memory["world"] != relation["world"]
                        or not cognition._evidence_valid(
                            memory, known, events, scope, state["dependency_generation"], cutoff,
                            excluded, historical, "episode", validation_cache=cache,
                            valid_dependency_versions=frozenset(state.get("valid_dependency_versions", ())),
                            superseded_evidence_refs=superseded)):
                    raise ValueError("Unavailable endpoint")
            references = copy.deepcopy(relation["evidence_refs"])
            references.extend({"event_id": identifier, "event_revision": events[identifier]["revision"],
                               "source_content_sha256": events[identifier]["content_sha256"],
                               "relationship": "context_only"} for identifier in roots)
            if any(not cognition._valid_ref(ref, events, scope)
                   or any(cognition.evidence_overlaps(ref, old) for old in superseded)
                   for ref in references):
                raise ValueError("Unavailable relation source")
            assessment = cognition._memory_applicability(relation, state, scope, events, timestamp)
            assessment["evidence_refs"] = list({digest(ref): ref for ref in (
                assessment["evidence_refs"] + references)}.values())
            source = known[version(relation["source_memory_version"])]
            source_assessment = applicability(source)
            if source_assessment["value"] == "false" or assessment["value"] == "false":
                assessment.update(value="false", status="false", conflict=False)
            elif source_assessment["value"] != "true" or source_assessment["conflict"]:
                assessment.update(value="unknown", status="conflict" if source_assessment["conflict"] else "unknown",
                                  conflict=assessment["conflict"] or source_assessment["conflict"])
            if (source["epistemic_status"] in {"disputed", "unknown"}
                    or source.get("verification", {}).get("state") == "contested") and assessment["value"] != "false":
                assessment.update(value="unknown", status="conflict", conflict=True)
            for ref in source_assessment["evidence_refs"]:
                if ref not in assessment["evidence_refs"]:
                    assessment["evidence_refs"].append(copy.deepcopy(ref))
            for field in ("supporting_observation_ids", "opposing_observation_ids", "unresolved_observation_ids", "reasons"):
                if field in assessment or field in source_assessment:
                    assessment[field] = sorted(set(assessment.get(field, [])) | set(source_assessment.get(field, [])))
            validated.append({"relation": copy.deepcopy(relation), "applicability": assessment})
            for key in keys:
                adjacency.setdefault(key, set()).update(keys - {key})
        except (ValueError, TypeError, KeyError, DomainError):
            try:
                incomplete.update(relation_versions(relation))
            except (ValueError, TypeError, KeyError):
                incomplete.update(selected)

    components = {}
    parents = {}
    for key, memory in known.items():
        references = list(memory["derivation"]["source_memory_versions"])
        references.extend(reference for path in memory["disclosure_basis"]["support_paths"]
                          if path["path_id"] in memory["disclosure_basis"]["selected_path_ids"]
                          for reference in path["source_memory_versions"])
        parents[key] = {version(reference) for reference in references}
        for parent in parents[key]:
            adjacency.setdefault(key, set()).add(parent)
            adjacency.setdefault(parent, set()).add(key)
    for key in selected | adjacency.keys():
        pending, connected = [key], set()
        while pending:
            node = pending.pop()
            if node in connected:
                continue
            connected.add(node)
            pending.extend(adjacency.get(node, set()) - connected)
        components[key] = connected
    active_edges = {}
    states = {key: "unchanged" for key in known}
    states.update({key: "uncertain" for key in incomplete if key in known})
    for assessment in validated:
        relation, applies = assessment["relation"], assessment["applicability"]
        if relation["kind"] in {"abstracts", "equivalent"} or applies["value"] == "false":
            continue
        source = version(relation["source_memory_version"])
        targets = {version(value) for value in relation["target_memory_versions"]}
        applicable_targets = {key for key in targets if applicability(known[key])["value"] != "false"}
        affected = applicable_targets | ({source} if relation["kind"] == "contradicts" and applicable_targets else set())
        for key in affected:
            if applicability(known[key])["value"] == "false":
                continue
            desired = ("conflicted" if applies["conflict"] or relation["kind"] == "contradicts" else
                       "uncertain" if applies["value"] != "true" else
                       "exception_applies" if relation["kind"] == "exception" else "superseded")
            precedence = {"unchanged": 0, "superseded": 1, "exception_applies": 2, "uncertain": 3, "conflicted": 4}
            if precedence[desired] > precedence[states[key]]:
                states[key] = desired
        if applies["value"] == "true" and relation["kind"] in {"exception", "supersedes"}:
            active_edges.setdefault(source, set()).update(targets)

    for key in _cyclic_nodes(active_edges):
        states[key] = "conflicted"
    for _ in range(len(known)):
        changed = False
        for child, dependencies in parents.items():
            if (states[child] not in {"uncertain", "conflicted"}
                    and any(states.get(parent) in {"superseded", "exception_applies", "uncertain", "conflicted"}
                            for parent in dependencies)
                    and applicability(known[child])["value"] != "false"):
                states[child] = "uncertain"
                changed = True
        for source, targets in active_edges.items():
            if states[source] not in {"uncertain", "conflicted"}:
                continue
            for target in targets:
                if states[target] not in {"uncertain", "conflicted"} and applicability(known[target])["value"] != "false":
                    states[target] = "uncertain"
                    changed = True
        if not changed:
            break

    def apply(selected_items):
        available = {version(item["memory"]) for item in selected_items}
        for item in selected_items:
            key = version(item["memory"])
            connected = components.get(key, {key})
            assessments = [assessment for assessment in validated
                           if relation_versions(assessment["relation"]) & connected]
            required = set().union(*(relation_versions(value["relation"]) for value in assessments
                                    if value["applicability"]["value"] != "false")) if assessments else set()
            complete = not connected & incomplete and required <= available and connected <= available
            if not assessments and complete:
                item.pop("belief_resolution", None)
                continue
            item["belief_resolution"] = {
                "state": states.get(key, "unchanged") if complete else "uncertain",
                "complete": complete, "relations": copy.deepcopy(assessments),
            }

    apply(items)
    return apply


def atomic_units(units, membership):
    groups = []
    for unit in units:
        relations = set()
        for (section, text, refs), provenance in unit:
            for memory, _ in provenance:
                relations.update(membership.get(version(memory), ()))
        overlapping = [index for index, (_, identifiers) in enumerate(groups) if identifiers & relations]
        if not overlapping:
            groups.append((list(unit), relations))
            continue
        first = overlapping[0]
        groups[first][0].extend(unit)
        groups[first][1].update(relations)
        for index in reversed(overlapping[1:]):
            other, identifiers = groups.pop(index)
            groups[first][0].extend(other)
            groups[first][1].update(identifiers)
    return [unit for unit, _ in groups]


def connected_item_groups(items):
    groups = []
    for item in items:
        memory = item["memory"]
        keys = {version(memory)}
        if "deferred_by" in item:
            keys.add(version(item["deferred_by"]))
        keys.update(version(parent) for parent in memory["derivation"]["source_memory_versions"])
        keys.update(version(parent) for path in memory["disclosure_basis"]["support_paths"]
                    if path["path_id"] in memory["disclosure_basis"]["selected_path_ids"]
                    for parent in path["source_memory_versions"])
        for assessment in item.get("belief_resolution", {}).get("relations", []):
            keys.update(relation_versions(assessment["relation"]))
        overlaps = [index for index, (_, members) in enumerate(groups) if members & keys]
        if not overlaps:
            groups.append(([item], keys))
            continue
        first = overlaps[0]
        groups[first][0].append(item)
        groups[first][1].update(keys)
        for index in reversed(overlaps[1:]):
            other, members = groups.pop(index)
            groups[first][0].extend(other)
            groups[first][1].update(members)
    return [group for group, _ in groups]
