from __future__ import annotations

import copy
from dataclasses import asdict

from psycopg.types.json import Jsonb

from . import cognition
from .belief_capabilities import RELATION_CAPABILITIES_INPUT_VERSIONS, validate_capability_link
from .citation_coverage import (
    CITATION_COVERAGE_INPUT_VERSION,
    citation_coverage_supported,
    selected_citation_refs,
)
from .contracts import validate
from .episode_semantics import (
    EPISODE_SOURCE_VIEW_INPUT_VERSIONS,
    episode_fidelity_supported,
    episode_navigation_view,
)
from .helpers import DomainError, digest, now
from .history_semantics import integration_coverage, validate_decisions, validate_manifest
from .local_semantics import (
    BOUNDED_MODES,
    HISTORY_INTEGRATION_MODE,
    bounded_assessment_supported,
    validate_bounded_batch,
    validate_bounded_proposal,
)
from .semantic import (
    EPISODE_FIDELITY_INPUT_VERSIONS,
    PROPOSAL_SCHEMA,
    CognitiveBatch,
    SemanticProcessor,
    SemanticResult,
    _complete_instance_verdicts,
    _instance_support,
    belief_capabilities,
    check_result_mode,
    proposal_schema,
    validate_input_contract,
)


def _error():
    raise DomainError("context_stale", "Cognitive source snapshot is unavailable or changed")


def _refs(document):
    return {(ref["event_id"], ref["event_revision"])
            for ref in document.get("evidence_refs", []) + document.get("counterexamples", [])}


def validate_batch(conn, service, principal, brain, batch: CognitiveBatch,
                   result: SemanticResult | None = None):
    current = service._brain(conn, principal, brain["id"])
    brain.update(current)
    scope = service._scope(principal, brain, {"conversation_ref": batch.scope["conversation_id"],
                                             "branch_ref": batch.scope["branch_id"]})
    if scope != batch.scope:
        _error()
    expected = {(event["event_id"], event["revision"]) for event in batch.sources}
    for memory in batch.existing_assertions:
        expected.update(_refs(memory))
    for episode in batch.episodes:
        expected.update((ref["event_id"], ref["revision"]) for ref in episode.get("member_refs", []))
    for group in ("items", "open_loops"):
        for item in batch.working_state.get(group, []):
            expected.update(_refs(item))
    if result is not None:
        observed = {(ref["event_id"], ref["revision"]) for ref in result.observed_event_refs}
        if not expected.issubset(observed):
            _error()
        expected.update(observed)
    if len(expected) > 256:
        raise DomainError("budget_exceeded", "Cognitive source closure exceeds its limit")
    params = service._params(principal, brain["id"])
    rows = conn.execute(
        "SELECT document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND id=ANY(%s::text[]) AND is_current AND NOT retired AND NOT deleted AND NOT suppressed "
        "AND document IS NOT NULL", (*params, sorted({key[0] for key in expected})),
    ).fetchall()
    events = {}
    for row in rows:
        source = service._rebind(row["document"], principal, brain)
        key = (source["event_id"], source["revision"])
        if key not in expected or not cognition._scope_allows(source["scope"], scope):
            _error()
        if digest(source["observed"]["content"]) != source["content_sha256"]:
            _error()
        events[key] = source
    if set(events) != expected:
        _error()
    for previous in batch.sources:
        source = events[(previous["event_id"], previous["revision"])]
        if digest(source) != digest(service._rebind(previous, principal, brain)):
            _error()
    parents = {}
    for previous in batch.existing_assertions:
        row = conn.execute(
            "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND NOT invalidated AND NOT deleted AND NOT suppressed "
            "AND document IS NOT NULL", (*params, previous["memory_id"], previous["revision"]),
        ).fetchone()
        if row is None:
            _error()
        memory = service._rebind(row["document"], principal, brain)
        if (memory["lifecycle"] == "retracted" or not cognition._scope_allows(memory["scope"], scope)
                or digest(memory) != digest(service._rebind(previous, principal, brain))):
            _error()
        parents[(memory["memory_id"], memory["revision"])] = memory
    for previous in batch.episodes:
        row = conn.execute(
            "SELECT document FROM memory_episodes WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND is_current AND NOT invalidated AND NOT deleted "
            "AND document IS NOT NULL", (*params, previous["episode_id"], previous["revision"]),
        ).fetchone()
        if (row is None or digest(service._rebind(row["document"], principal, brain))
                != digest(service._rebind(previous, principal, brain))):
            _error()
    for previous in batch.belief_relations:
        from .belief_store import relation_versions, validate_relation

        validate_relation(previous)
        row = conn.execute(
            "SELECT document FROM memory_belief_relations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND NOT invalidated AND NOT deleted AND document IS NOT NULL",
            (*params, previous["relation_id"], previous["revision"]),
        ).fetchone()
        if (row is None or digest(service._rebind(row["document"], principal, brain))
                != digest(service._rebind(previous, principal, brain))
                or not relation_versions(previous).issubset(parents)
                or not set(previous["root_event_ids"]).issubset(key[0] for key in events)):
            _error()
    superseded = service._superseded_evidence(conn, principal, brain, sorted({key[0] for key in events}))
    return {"events": events, "assertions": parents, "superseded_evidence": superseded,
            "root_event_ids": sorted({key[0] for key in expected})}


def _check_result(batch, result, snapshot, *, current_input_refs=()):
    check_result_mode(batch.proposal_mode, asdict(result))
    validate_bounded_batch(batch)
    try:
        validate_input_contract(batch.input_contract_version)
    except ValueError as error:
        raise DomainError("invalid_evidence", "Unknown cognitive input contract") from error
    if batch.proposal_mode in BOUNDED_MODES and batch.input_contract_version not in EPISODE_FIDELITY_INPUT_VERSIONS:
        raise DomainError("invalid_evidence", "Bounded learning input contract changed")
    if batch.input_contract_version in EPISODE_SOURCE_VIEW_INPUT_VERSIONS:
        try:
            for episode in batch.episodes:
                episode_navigation_view(episode, batch.scope, snapshot["events"])
        except (ValueError, KeyError, TypeError) as error:
            raise DomainError("invalid_evidence", "Episode source view is unavailable") from error
    if batch.proposal_mode == HISTORY_INTEGRATION_MODE:
        validate_manifest(batch.integration_manifest, events=snapshot["events"], assertions=snapshot["assertions"])
        expected = integration_coverage(batch.integration_manifest, result.integration_decisions,
                                        result.belief_links, assessed=result.status in {"completed", "partial"}
                                        and not any(item["reason"] == "invalid_proposal" for item in result.rejected))
        if expected != result.history_integration:
            raise DomainError("invalid_evidence", "Historical integration assessment changed")
    if result.status not in {"completed", "partial"}:
        if result.claims or result.situation_updates or result.episode_actions or result.belief_links:
            raise DomainError("invalid_evidence", "Unverified cognitive result cannot be published")
        return
    if batch.proposal_mode == HISTORY_INTEGRATION_MODE:
        try:
            validate_decisions(batch.integration_manifest, {
                "integration_decisions": list(result.integration_decisions),
                "belief_links": list(result.integration_proposal_links),
            })
            known = {item["proposal_id"] for item in (*result.belief_links, *result.rejected)}
            if any(link["proposal_id"] not in known for link in result.integration_proposal_links):
                raise ValueError("Historical link has no retained outcome")
            fields = set(PROPOSAL_SCHEMA["properties"]["belief_links"]["items"]["properties"])
            originals = {item["proposal_id"]: item for item in result.integration_proposal_links}
            if any({key: value for key, value in item.items() if key in fields} != originals.get(item["proposal_id"])
                   for item in result.belief_links):
                raise ValueError("Historical link differs from its retained proposal")
        except (ValueError, KeyError, TypeError) as error:
            raise DomainError("invalid_evidence", "Historical integration decisions are unsupported") from error
    episodes = {episode["episode_id"]: episode for episode in batch.episodes}
    capabilities = (belief_capabilities(batch.scope, snapshot["events"].values(), snapshot["assertions"].values())
                    if batch.input_contract_version in RELATION_CAPABILITIES_INPUT_VERSIONS else None)
    ids, changed_episodes = set(), set()
    claim_fields = set(PROPOSAL_SCHEMA["properties"]["claims"]["items"]["properties"])
    claims = {item["proposal_id"]: {key: copy.deepcopy(value) for key, value in item.items() if key in claim_fields}
              for item in result.claims}
    abstraction_parents = {}
    link_fields = set(PROPOSAL_SCHEMA["properties"]["belief_links"]["items"]["properties"])
    for final in result.belief_links:
        if final["kind"] != "abstracts":
            continue
        item = {key: copy.deepcopy(value) for key, value in final.items() if key in link_fields}
        try:
            if capabilities is not None:
                validate_capability_link(item, capabilities)
            SemanticProcessor._validate_proposal(
                "belief_links", item, batch.scope, snapshot["events"], snapshot["assertions"], episodes, claims,
                input_contract_version=batch.input_contract_version)
        except (ValueError, KeyError, TypeError) as error:
            raise DomainError("invalid_evidence", "Cognitive abstraction is unsupported") from error
        abstraction_parents.setdefault(item["claim_proposal_id"], set()).update(
            (ref["memory_id"], ref["revision"]) for ref in item["target_memory_versions"])
    for group in ("claims", "situation_updates", "episode_actions", "belief_links"):
        if batch.sampling.get("publication_restricted") and group != "situation_updates" and getattr(result, group):
            raise DomainError("invalid_evidence", "Incomplete history cannot support consolidation")
        schema_fields = set(PROPOSAL_SCHEMA["properties"][group]["items"]["properties"])
        for final in getattr(result, group):
            item = {key: copy.deepcopy(value) for key, value in final.items() if key in schema_fields}
            expected_state = "contested" if item.get("counterexamples") else "supported_inference"
            if (final.get("verification") != {"state": expected_state, "method": "semantic_model",
                                               "not_a_truth_certificate": True}
                    or final["proposal_id"] in ids):
                raise DomainError("invalid_evidence", "Missing semantic verification")
            ids.add(final["proposal_id"])
            if group == "claims" and final.get("epistemic_status") != "derived_inference":
                raise DomainError("invalid_evidence", "Semantic learning remains inferred")
            if group == "situation_updates" and final.get("current_only") is not True:
                raise DomainError("invalid_evidence", "Situation updates are current context only")
            try:
                if group == "belief_links" and capabilities is not None:
                    validate_capability_link(item, capabilities)
                validate_bounded_proposal(
                    batch.proposal_mode, group, item, schema=proposal_schema(
                        batch.proposal_mode, input_contract_version=batch.input_contract_version),
                    events=snapshot["events"], learning_input_refs=batch.learning_input_refs, links=result.belief_links,
                )
                references = SemanticProcessor._validate_proposal(
                    group, item, batch.scope, snapshot["events"], snapshot["assertions"], episodes, claims,
                    abstraction_parents=abstraction_parents.get(item["proposal_id"], ()),
                    current_input_refs=current_input_refs,
                    input_contract_version=batch.input_contract_version,
                )
            except (ValueError, KeyError, TypeError) as error:
                raise DomainError("invalid_evidence", "Cognitive proposal is unsupported") from error
            coverage_required = (batch.input_contract_version == CITATION_COVERAGE_INPUT_VERSION
                                 and group != "episode_actions")
            if batch.proposal_mode in BOUNDED_MODES or coverage_required:
                assessments = [entry for entry in result.verification_assessments
                               if isinstance(entry, dict) and entry.get("proposal_id") == item["proposal_id"]]
                if coverage_required and (len(assessments) != 1 or not citation_coverage_supported(assessments[0])):
                    raise DomainError("invalid_evidence", "Citation coverage assessment is unsupported")
                evidence = references
                abstract = group == "belief_links" and item["kind"] == "abstracts"
                if item["proposal_id"] in abstraction_parents:
                    selections = [entry for entry in result.evidence_selections
                                  if entry.get("proposal_id") == item["proposal_id"]]
                    if len(selections) != 1:
                        raise DomainError("invalid_evidence", "Historical abstraction assessment is incomplete")
                    if not isinstance(selections[0].get("original_evidence_refs"), list):
                        raise DomainError("invalid_evidence", "Historical abstraction evidence is incomplete")
                    evidence = selections[0]["original_evidence_refs"] + item.get("counterexamples", [])
                    abstract = True
                if len(assessments) != 1 or not bounded_assessment_supported(
                        assessments[0], item["proposal_id"], evidence, link=group == "belief_links", abstract=abstract):
                    raise DomainError("invalid_evidence", "Bounded learning assessment is unsupported")
                if coverage_required and abstract:
                    selected = selected_citation_refs(assessments[0], evidence)
                    if not selected or (group == "claims" and (
                            selected != item["evidence_refs"]
                            or selections[0].get("supporting_evidence_indices")
                            != assessments[0].get("supporting_evidence_indices"))):
                        raise DomainError("invalid_evidence", "Citation coverage selection changed")
                    if group == "belief_links":
                        try:
                            instances = _instance_support(item, claims[item["claim_proposal_id"]], snapshot["assertions"])
                            assessment = assessments[0]
                            supported = (_complete_instance_verdicts(assessment, instances)
                                         and all(verdict["verdict"] == "supports"
                                                 for verdict in assessment["instance_verdicts"])
                                         and all(set(instance["evidence_indices"]) & set(assessment["supporting_evidence_indices"])
                                                 for instance in instances))
                        except (ValueError, KeyError, TypeError):
                            supported = False
                        if not supported:
                            raise DomainError("invalid_evidence", "Citation coverage instance support changed")
            if group == "episode_actions" and batch.input_contract_version in EPISODE_FIDELITY_INPUT_VERSIONS:
                assessments = [entry for entry in result.verification_assessments
                               if isinstance(entry, dict) and entry.get("proposal_id") == item["proposal_id"]]
                if (len(assessments) != 1
                        or not episode_fidelity_supported(assessments[0], item["proposal_id"], references)):
                    raise DomainError("invalid_evidence", "Episode fidelity assessment is unsupported")
            if item.get("evidence_use") == "current_request":
                if final.get("request_verification") != {
                    "state": "supported_request", "method": "semantic_model", "not_a_truth_certificate": True,
                }:
                    raise DomainError("invalid_evidence", "Missing current request verification")
                assessments = [entry for entry in result.verification_assessments
                               if isinstance(entry, dict) and entry.get("proposal_id") == item["proposal_id"]]
                if (len(assessments) != 1 or assessments[0].get("proposal_verdict") != "supports"
                        or assessments[0].get("request_verdict") != "supports"
                        or assessments[0].get("link_verdict", "missing") is not None):
                    raise DomainError("invalid_evidence", "Current request assessment is unsupported")
                relations = assessments[0].get("relations")
                if (not isinstance(relations, list) or len(relations) != len(item["evidence_refs"])
                        or any(not isinstance(relation, dict) or type(relation.get("evidence_index")) is not int
                               or relation.get("verdict") != "supports" for relation in relations)
                        or {relation["evidence_index"] for relation in relations} != set(range(len(item["evidence_refs"])))):
                    raise DomainError("invalid_evidence", "Current request evidence assessment is incomplete")
            if group == "episode_actions" and item["episode_id"] is not None:
                if item["episode_id"] in changed_episodes:
                    raise DomainError("invalid_evidence", "An episode may have one action per batch")
                changed_episodes.add(item["episode_id"])
            if any(cognition.evidence_overlaps(ref, old) for ref in references
                   for old in snapshot["superseded_evidence"]):
                _error()


def _meaning(document):
    keys = ("statement", "memory_kind", "world", "representation", "claim_kind", "conditions",
            "valid_time", "procedure", "verification")
    value = {key: document[key] for key in keys}
    value.update({key: document[key] for key in ("valid_during", "condition_registry_fingerprint")
                  if key in document})
    value["evidence_refs"] = sorted(document["evidence_refs"], key=digest)
    value["counterexamples"] = sorted(document["counterexamples"], key=digest)
    return value


def _assertion(item, batch, brain, snapshot):
    identity = copy.deepcopy(item)
    identity["evidence_refs"] += identity["counterexamples"]
    identifier = "semantic-" + digest(["bounded-semantic-v1", _meaning(identity)])[:32]
    timestamp = now()
    support = copy.deepcopy(item["evidence_refs"])
    counterexamples = copy.deepcopy(item["counterexamples"])
    parents = copy.deepcopy(item["source_memory_versions"])
    path_id = "path-" + identifier
    result = {
        "memory_id": identifier, "revision": 1, "scope": copy.deepcopy(batch.scope),
        "memory_kind": item["memory_kind"], "statement": item["statement"],
        "epistemic_status": "derived_inference", "world": item["world"],
        "evidence_refs": support + counterexamples, "valid_time": copy.deepcopy(item["valid_time"]),
        "system_time": {"recorded_at": timestamp, "superseded_at": None},
        "lifecycle": "active", "supersedes_memory_ids": [],
        "derivation": {"method": "aggregation", "rule_version": "bounded-semantic-v1",
                       "model_version": None, "source_memory_versions": parents},
        "dynamics": {"activation": 0.5, "retention_priority": 0.5, "detail_level": "gist",
                     "last_external_reinforcement_at": None, "last_recalled_at": None,
                     "decay_policy_version": "external-use-only-v1", "user_pinned": False,
                     "reasons": ["default_policy"]},
        "disclosure_basis": {
            "dependency_generation": brain["dependency_generation"], "validation_state": "current",
            "support_paths": [{"path_id": path_id, "evidence_refs": support,
                               "source_memory_versions": parents, "permitted_scope": copy.deepcopy(batch.scope),
                               "dependency_generation": brain["dependency_generation"], "state": "current",
                               "independently_supports_complete_statement": True}],
            "selected_path_ids": [path_id], "release_mode": "supported_scope",
        },
        "root_event_ids": snapshot["root_event_ids"],
    }
    for key in ("representation", "claim_kind", "conditions", "counterexamples", "verification", "procedure"):
        result[key] = copy.deepcopy(item[key])
    for key in ("valid_during", "condition_registry_fingerprint"):
        if key in item:
            result[key] = copy.deepcopy(item[key])
    validate("MemoryAssertion", result)
    return result


def _publish_assertion(conn, service, principal, brain, memory):
    params = service._params(principal, brain["id"])
    prior = conn.execute(
        "SELECT document,invalidated,deleted,suppressed FROM memory_assertions "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND revision=%s",
        (*params, memory["memory_id"], memory["revision"]),
    ).fetchone()
    if prior is not None:
        if prior["invalidated"] or prior["deleted"] or prior["suppressed"] or prior["document"] is None:
            _error()
        old = service._rebind(prior["document"], principal, brain)
        if _meaning(old) != _meaning(memory):
            raise DomainError("idempotency_conflict", "Cognitive identity has different semantic content")
        return old
    published = service._publish(conn, principal, brain, memory)
    roots = sorted(set(memory.get("root_event_ids", [])) | {ref["event_id"] for ref in memory["evidence_refs"]})
    conn.execute(
        "UPDATE memory_assertions SET event_ids=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND id=%s AND revision=%s", (roots, *params, memory["memory_id"], memory["revision"]),
    )
    return published


def _load_episode(conn, service, principal, brain, action):
    if action["episode_id"] is None:
        return None
    row = conn.execute(
        "SELECT document FROM memory_episodes WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND id=%s AND revision=%s AND is_current AND NOT invalidated AND NOT deleted "
        "AND document IS NOT NULL FOR UPDATE",
        (*service._params(principal, brain["id"]), action["episode_id"], action["expected_revision"]),
    ).fetchone()
    if row is None:
        _error()
    return service._rebind(row["document"], principal, brain)


def _save_episode(conn, service, principal, brain, batch, document, memories, root_ids):
    params = service._params(principal, brain["id"])
    old = conn.execute(
        "SELECT document,invalidated,deleted FROM memory_episodes WHERE tenant_id=%s AND owner_id=%s "
        "AND brain_id=%s AND id=%s AND revision=%s", (*params, document["episode_id"], document["revision"]),
    ).fetchone()
    if old is not None:
        if old["invalidated"] or old["deleted"] or old["document"] != document:
            raise DomainError("idempotency_conflict", "Episode version changed within one job")
        return
    from .pattern_store import invalidate_patterns

    invalidate_patterns(conn, service, principal, brain, episode_ids=[document["episode_id"]])
    conn.execute(
        "UPDATE memory_episodes SET is_current=false WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND id=%s AND is_current", (*params, document["episode_id"]),
    )
    conn.execute(
        "INSERT INTO memory_episodes(tenant_id,owner_id,brain_id,id,revision,conversation_id,branch_id,state,document,event_ids) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (*params, document["episode_id"], document["revision"], batch.scope["conversation_id"] or "",
         batch.scope["branch_id"] or "", document["state"], Jsonb(document), sorted(root_ids)),
    )
    member_ids = {ref["event_id"] for ref in document["member_refs"]}
    position = 0
    for memory in memories:
        if not {ref["event_id"] for ref in memory["evidence_refs"]}.issubset(member_ids):
            continue
        role = {"episode_outline": "outline", "semantic_anchor": "anchor", "schema": "schema"}.get(
            memory.get("representation"), "fragment",
        )
        conn.execute(
            "INSERT INTO memory_episode_members(tenant_id,owner_id,brain_id,episode_id,episode_revision,"
            "memory_id,memory_revision,role,position) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (*params, document["episode_id"], document["revision"], memory["memory_id"], memory["revision"], role, position),
        )
        position += 1


def _publish_episode(conn, service, principal, brain, batch, action, snapshot, claims, job_id):
    previous = _load_episode(conn, service, principal, brain, action)
    if previous is not None:
        if previous["scope"] != batch.scope:
            _error()
        if action["action"] == "reopen" and previous["state"] != "closed":
            raise DomainError("version_conflict", "Only a closed episode can reopen")
        if action["action"] in {"continue", "close"} and previous["state"] != "open":
            raise DomainError("version_conflict", "Episode is no longer open")
    members = {(ref["event_id"], ref["revision"]): ref
               for ref in (previous["member_refs"] if previous else []) + action["member_refs"]}
    if len(members) > 128:
        raise DomainError("budget_exceeded", "Episode expansion exceeds its source limit")
    current_sources = service._events(conn, principal, brain, event_ids=sorted({key[0] for key in members}), limit=128)
    lookup = {(event["event_id"], event["revision"]): event for event in current_sources}
    if set(lookup) != set(members):
        _error()
    for event in lookup.values():
        if any(event["scope"][key] != batch.scope[key] for key in ("conversation_id", "branch_id")):
            _error()
    ordered = [members[key] for key in sorted(members, key=lambda key: lookup[key]["sequence"])]
    if action["action"] == "close" and action["boundary_ref"] != ordered[-1]:
        raise DomainError("invalid_evidence", "Closing boundary must be the final episode member")
    root_ids = set(snapshot["root_event_ids"]) | {key[0] for key in members}
    if previous:
        root_ids.update(previous.get("root_event_ids", []))
    fragments = []
    for event in sorted(lookup.values(), key=lambda value: value["sequence"]):
        fragments.extend(service._publish(conn, principal, brain, memory)
                         for memory in service._current_captures(conn, principal, brain, event))
    memories = {memory["memory_id"]: memory for memory in fragments + claims}

    def document(identifier, revision, refs, state):
        return {"episode_id": identifier, "revision": revision, "scope": copy.deepcopy(batch.scope),
                "state": state, "member_refs": refs, "boundary_ref": copy.deepcopy(action["boundary_ref"]),
                "organization_status": "inferred", "job_id": job_id,
                "organization_statement": action["organization_statement"],
                "organization_scope": "episode_transition", "organization_action": action["action"],
                "organization_source_version": ({"episode_id": previous["episode_id"], "revision": previous["revision"]}
                                                if previous else None),
                "proposal_id": action["proposal_id"], "root_event_ids": sorted(root_ids)}

    identifier = action["episode_id"] or "episode-" + digest([job_id, action["proposal_id"]])[:32]
    revision = previous["revision"] + 1 if previous else 1
    if action["action"] == "split":
        boundary = ordered.index(action["boundary_ref"])
        if boundary == 0:
            raise DomainError("invalid_evidence", "A split must leave members in the original episode")
        documents = [document(identifier, revision, ordered[:boundary], "closed"),
                     document("episode-" + digest([job_id, action["proposal_id"], "split"])[:32],
                              1, ordered[boundary:], "open")]
        documents[0]["boundary_ref"] = copy.deepcopy(ordered[boundary - 1])
    else:
        documents = [document(identifier, revision, ordered, "closed" if action["action"] == "close" else "open")]
    for value in documents:
        _save_episode(conn, service, principal, brain, batch, value, list(memories.values()), root_ids)
    return [{"episode_id": value["episode_id"], "revision": value["revision"]} for value in documents]


def publish_semantic(conn, service, principal, brain, batch: CognitiveBatch,
                     result: SemanticResult, *, job_id: str) -> dict:
    snapshot = validate_batch(conn, service, principal, brain, batch, result)
    _check_result(batch, result, snapshot)
    memories = []
    for item in result.claims:
        memory = _assertion(item, batch, brain, snapshot)
        memories.append(_publish_assertion(conn, service, principal, brain, memory))
    episodes = []
    for action in result.episode_actions:
        episodes.extend(_publish_episode(conn, service, principal, brain, batch, action, snapshot, memories, job_id))
    params = service._params(principal, brain["id"])
    for memory in memories:
        for parent in memory["derivation"]["source_memory_versions"]:
            relation = "procedure" if memory["memory_kind"] == "procedural" else "schema"
            identifier = "association-" + digest([memory["memory_id"], parent, relation])[:32]
            conn.execute(
                "INSERT INTO memory_associations(tenant_id,owner_id,brain_id,id,source_id,source_revision,"
                "target_id,target_revision,relation,event_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING", (*params, identifier, memory["memory_id"], memory["revision"],
                                            parent["memory_id"], parent["revision"], relation,
                                            snapshot["root_event_ids"]),
            )
    from .pattern_store import refresh_patterns

    pattern_ids = refresh_patterns(conn, service, principal, brain, batch, memories, episodes)
    from .belief_store import publish_links

    links = publish_links(conn, service, principal, brain, batch, result,
                          published_memories=memories, snapshot=snapshot)
    return {"status": result.status, "published_count": len(memories), "pattern_view_ids": pattern_ids,
            "belief_relation_versions": [{"relation_id": link["relation_id"], "revision": link["revision"]} for link in links],
            "memory_versions": [{"memory_id": memory["memory_id"], "revision": memory["revision"]} for memory in memories],
            "episode_versions": episodes, "source_event_ids": snapshot["root_event_ids"],
            "source_memory_ids": sorted({memory["memory_id"] for memory in batch.existing_assertions}),
            "rejected_count": len(result.rejected)}
