from __future__ import annotations

import copy
from collections import Counter
from itertools import combinations

from . import cognition
from .helpers import digest

PLANNER_VERSION = "independent-instance-comparison-v3"
_LEGACY_PLANNER_VERSION = "independent-instance-comparison-v1"
_WEAK_PLANNER_VERSION = "independent-instance-comparison-v2"
COMPARISON_POLICY = "minimal-shared-meaning-v1"
MAX_OBJECTIVES = 3
NO_CHANGE_REASONS = (
    "insufficient_shared_meaning", "uncertain_attribution", "incompatible_qualifiers",
    "insufficient_evidence", "already_represented",
)


def _version(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def _key(memory):
    return memory["memory_id"], memory["revision"]


def source_actor(memory, events):
    actors = set()
    for reference in memory["evidence_refs"]:
        if reference["relationship"] != "supports":
            continue
        event = events.get((reference["event_id"], reference["event_revision"]))
        if event is None or event["content_sha256"] != reference["source_content_sha256"]:
            return None
        observed = event["observed"]
        if observed["actor_role"] not in {"user", "operator"}:
            return None
        actors.add((observed["producer_ref"], observed["actor_ref"], observed["actor_role"]))
    return next(iter(actors)) if len(actors) == 1 else None


def _terms(memory):
    words, pairs, _ = cognition._terms(memory["statement"])
    return {term for term in words | pairs if not term.isdecimal()}


def plan_consolidation(sources, assertions, belief_relations=(), sampling=None, *,
                       attempted_ids=(), include_all=False, planner_version=PLANNER_VERSION):
    if planner_version not in {PLANNER_VERSION, _LEGACY_PLANNER_VERSION, _WEAK_PLANNER_VERSION}:
        raise ValueError("Unknown consolidation planner version")
    events = {(source["event_id"], source["revision"]): source for source in sources}
    eligible = []
    excluded = Counter()
    for memory in sorted(assertions, key=_key):
        if (memory["lifecycle"] != "active" or memory.get("representation") in {"schema", "procedure"}
                or memory.get("claim_kind") not in {"reported_fact", "reported_preference"}
                or memory.get("counterexamples") or memory.get("epistemic_status") != "derived_inference"):
            excluded["not_independent_instance"] += 1
            continue
        actor = source_actor(memory, events)
        roots = {ref["event_id"] for ref in memory["evidence_refs"] if ref["relationship"] == "supports"}
        if actor is None or not roots:
            excluded["attribution_unavailable"] += 1
            continue
        eligible.append((memory, actor, roots, _terms(memory)))
    covered = [set((ref["memory_id"], ref["revision"]) for ref in relation["target_memory_versions"])
               for relation in belief_relations if relation["kind"] == "abstracts"]
    related = [{(ref["memory_id"], ref["revision"]) for ref in
                [relation["source_memory_version"], *relation["target_memory_versions"]]}
               for relation in belief_relations if relation["kind"] != "abstracts"]
    candidates = []
    for left, right in combinations(eligible, 2):
        a, actor_a, roots_a, terms_a = left
        b, actor_b, roots_b, terms_b = right
        if (actor_a != actor_b or roots_a & roots_b or a["world"] != b["world"]
                or a.get("conditions") != b.get("conditions") or a.get("claim_kind") != b.get("claim_kind")):
            excluded["incompatible_instances"] += 1
            continue
        if any({_key(a), _key(b)} <= group for group in related):
            excluded["already_related"] += 1
            continue
        if any({_key(a), _key(b)} <= group for group in covered):
            excluded["already_represented"] += 1
            continue
        shared = terms_a & terms_b
        score = len(shared) / max(1, min(len(terms_a), len(terms_b)))
        if not shared or planner_version == _LEGACY_PLANNER_VERSION and score < 0.2:
            excluded["no_lexical_lead"] += 1
            continue
        versions = [_version(a), _version(b)]
        identity = {"planner_version": planner_version, "instances": [
            {key: item.get(key) for key in ("memory_id", "revision", "statement", "world", "conditions",
                                           "claim_kind", "valid_time", "valid_during", "evidence_refs")}
            for item in (a, b)], "source_actor": actor_a}
        candidates.append((score, {"objective_id": "compare-" + digest(identity)[:32],
                                  "operation": "compare_instances", "instance_memory_versions": versions,
                                  "world": a["world"], "conditions": copy.deepcopy(a["conditions"]),
                                  "evidence_policy": "sampled_instances_only_preserve_attribution_and_differences"}))
        if planner_version != _LEGACY_PLANNER_VERSION:
            candidates[-1][1]["selection_basis"] = ("strong_lexical_lead" if score >= 0.2
                                                    else "weak_lexical_exploration")
        if planner_version == PLANNER_VERSION:
            candidates[-1][1]["comparison_policy"] = COMPARISON_POLICY
    candidates.sort(key=lambda value: (-value[0], value[1]["objective_id"]))
    attempted_ids = set(attempted_ids)
    available = [item for _, item in candidates if item["objective_id"] not in attempted_ids]
    if planner_version == _LEGACY_PLANNER_VERSION:
        selected = available[:MAX_OBJECTIVES]
    else:
        strong = [item for item in available if item["selection_basis"] == "strong_lexical_lead"]
        weak = [item for item in available if item["selection_basis"] == "weak_lexical_exploration"]
        selected = strong[:MAX_OBJECTIVES - bool(weak)] + weak[:1]
    restricted = bool((sampling or {}).get("publication_restricted"))
    return {"planner_version": planner_version, "candidate_count": len(candidates),
            "search_exhaustive": False, "limit": MAX_OBJECTIVES,
            "excluded_counts": dict(sorted(excluded.items())), "suppressed_count": len(candidates) - len(available),
            "objectives": [] if restricted else available if include_all else selected}


def consolidation_coverage(plan, decisions=(), links=(), *, attempted=False, verified_objective_ids=()):
    outcomes = []
    decisions = {item["objective_id"]: item for item in decisions}
    for objective in plan["objectives"]:
        identifier = objective["objective_id"]
        targets = {(item["memory_id"], item["revision"]) for item in objective["instance_memory_versions"]}
        matched = [link["proposal_id"] for link in links if link["kind"] == "abstracts" and targets <= {
            (item["memory_id"], item["revision"]) for item in link["target_memory_versions"]}]
        decision = decisions.get(identifier)
        if matched:
            state, reason = "satisfied", "verified_abstraction"
        elif attempted and decision and decision["outcome"] == "no_change":
            state, reason = "no_change", decision["reason"]
        else:
            state, reason = "unresolved", "no_verified_abstraction" if attempted else "not_attempted"
        outcomes.append({"objective_id": identifier, "status": state, "reason": reason,
                         "attempted": attempted,
                         "dedup_eligible": attempted and (state == "no_change" or identifier in verified_objective_ids),
                         "reason_basis": "verified_relationship" if matched else "model_declared_not_verified"
                         if state == "no_change" else "runtime_observed", "verified_link_ids": matched})
    return {"planner_version": plan["planner_version"], "candidate_count": plan["candidate_count"],
            "objective_count": len(outcomes), "suppressed_count": plan["suppressed_count"],
            "satisfied_count": sum(item["status"] == "satisfied" for item in outcomes),
            "no_change_count": sum(item["status"] == "no_change" for item in outcomes),
            "unresolved_count": sum(item["status"] == "unresolved" for item in outcomes),
            "objectives": outcomes}


def validate_consolidation_plan(plan, sources, assertions, belief_relations, sampling):
    if not isinstance(plan, dict):
        raise ValueError("Invalid consolidation plan")
    expected = plan_consolidation(sources, assertions, belief_relations, sampling, include_all=True,
                                  planner_version=plan.get("planner_version"))
    if (set(plan) != set(expected)
            or any(plan[key] != expected[key] for key in set(expected) - {"objectives", "suppressed_count"})
            or type(plan["suppressed_count"]) is not int
            or not 0 <= plan["suppressed_count"] <= expected["candidate_count"]
            or not isinstance(plan["objectives"], list) or len(plan["objectives"]) > MAX_OBJECTIVES):
        raise ValueError("Invalid consolidation plan")
    allowed = {item["objective_id"]: item for item in expected["objectives"]}
    seen = set()
    for objective in plan["objectives"]:
        identifier = objective.get("objective_id") if isinstance(objective, dict) else None
        if not isinstance(identifier, str) or identifier in seen or allowed.get(identifier) != objective:
            raise ValueError("Invalid consolidation objective")
        seen.add(identifier)
    if sum(item.get("selection_basis") == "weak_lexical_exploration" for item in plan["objectives"]) > 1:
        raise ValueError("Consolidation weak exploration exceeds its limit")
