from __future__ import annotations

import copy
import re

from psycopg.types.json import Jsonb

from . import cognition
from .applicability import validate_condition
from .archive_views import memory_parents, memory_references, memory_sources
from .contracts import validate
from .helpers import DomainError, digest
from .ontology import CORE_REGISTRY
from .temporal import validate_valid_during

MAX_MEMORIES = 128
MAX_SOURCES = 256
MAX_RELATIONS = 32
RELATION_KINDS = frozenset({"abstracts", "equivalent", "exception", "supersedes", "contradicts"})
VERIFICATION = {"state": "supported_inference", "method": "semantic_model", "not_a_truth_certificate": True}
_FIELDS = frozenset({
    "relation_id", "revision", "scope", "kind", "source_memory_version", "target_memory_versions",
    "evidence_refs", "conditions", "valid_during", "world", "condition_registry_fingerprint",
    "verification", "root_event_ids", "endpoint_snapshots",
})


def _unavailable(message="Belief relation evidence or endpoint is unavailable"):
    raise DomainError("source_unavailable", message)


def _version(reference):
    if (not isinstance(reference, dict) or set(reference) != {"memory_id", "revision"}
            or not isinstance(reference["memory_id"], str) or not 1 <= len(reference["memory_id"]) <= 256
            or type(reference["revision"]) is not int or not 1 <= reference["revision"] <= 2**63 - 1):
        raise ValueError("An exact memory version is required")
    return reference["memory_id"], reference["revision"]


def _ref(key):
    return {"memory_id": key[0], "revision": key[1]}


def _stable_scope(scope):
    return {key: value for key, value in scope.items() if key != "scope_generation"}


def assertion_fingerprint(memory):
    value = {key: copy.deepcopy(item) for key, item in memory.items()
             if key not in {"dynamics", "system_time", "disclosure_basis", "scope", "lifecycle"}}
    value["scope"] = _stable_scope(memory["scope"])
    basis = copy.deepcopy(memory["disclosure_basis"])
    basis.pop("dependency_generation", None)
    for path in basis["support_paths"]:
        path.pop("dependency_generation", None)
        path["permitted_scope"] = _stable_scope(path["permitted_scope"])
    value["disclosure_basis"] = basis
    return digest(value)


endpoint_hash = assertion_fingerprint


def relation_ref(document):
    return {"relation_id": document["relation_id"], "revision": document["revision"],
            "content_sha256": digest(document)}


def relation_versions(document):
    return {_version(document["source_memory_version"]),
            *(_version(ref) for ref in document["target_memory_versions"])}


def relation_sources(document):
    return set(document["root_event_ids"]) | {ref["event_id"] for ref in document["evidence_refs"]}


def _relation_id(document):
    value = {key: copy.deepcopy(item) for key, item in document.items()
             if key not in {"relation_id", "root_event_ids"}}
    value["scope"] = _stable_scope(value["scope"])
    return "belief-" + digest(["evidence-belief-relation-v1", value])[:32]


def validate_relation(document):
    validate("BeliefRelation", document)
    if not isinstance(document, dict) or set(document) != _FIELDS:
        raise ValueError("Invalid belief relation structure")
    if document["revision"] != 1 or type(document["revision"]) is not int or document["kind"] not in RELATION_KINDS:
        raise ValueError("Invalid belief relation version or kind")
    validate("BoundScope", document["scope"])
    source = _version(document["source_memory_version"])
    targets = document["target_memory_versions"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
        raise ValueError("Belief targets exceed their limit")
    keys = [_version(ref) for ref in targets]
    if keys != sorted(set(keys)) or source in keys or document["kind"] == "abstracts" and len(keys) < 2:
        raise ValueError("Belief relation endpoints must be distinct exact versions")
    references = document["evidence_refs"]
    if not isinstance(references, list) or not 1 <= len(references) <= 16:
        raise ValueError("Belief evidence exceeds its limit")
    for reference in references:
        validate("EvidenceRef", reference)
        if reference["relationship"] != "supports":
            raise ValueError("Belief relation requires supporting evidence")
    validate_condition(document["conditions"])
    validate_valid_during(document["valid_during"])
    if document["world"] not in {"real_world", "quoted", "roleplay", "hypothetical", "dream", "unknown"}:
        raise ValueError("Invalid belief relation world")
    if document["verification"] != VERIFICATION:
        raise ValueError("Belief relations remain verified inferences")
    if document["condition_registry_fingerprint"] != CORE_REGISTRY.fingerprint:
        raise ValueError("Belief relation registry is unavailable")
    roots = document["root_event_ids"]
    if (not isinstance(roots, list) or not 1 <= len(roots) <= MAX_SOURCES
            or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in roots)
            or roots != sorted(set(roots))):
        raise ValueError("Invalid belief source closure")
    if not {ref["event_id"] for ref in references}.issubset(roots):
        raise ValueError("Belief source closure omits evidence")
    snapshots = document["endpoint_snapshots"]
    if not isinstance(snapshots, list) or len(snapshots) != len(keys) + 1:
        raise ValueError("Belief endpoint snapshots are incomplete")
    observed = []
    for snapshot in snapshots:
        if (not isinstance(snapshot, dict) or set(snapshot) != {"memory_version", "content_sha256"}
                or not isinstance(snapshot["content_sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", snapshot["content_sha256"]) is None):
            raise ValueError("Invalid belief endpoint snapshot")
        observed.append(_version(snapshot["memory_version"]))
    if observed != sorted({source, *keys}) or document["relation_id"] != _relation_id(document):
        raise ValueError("Belief relation identity or endpoints changed")


def _load_memories(conn, service, principal, brain, versions):
    if not versions:
        return []
    return [service._rebind(row["document"], principal, brain) for row in conn.execute(
        "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND NOT invalidated AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
        "AND (id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
        (*service._params(principal, brain["id"]), [key[0] for key in versions], [key[1] for key in versions]),
    )]


def _package(conn, service, principal, brain, scope, versions, *, source_ids=(), references=()):
    memories, pending, depth = {}, set(versions), 0
    while pending:
        if depth > 8 or len(memories) + len(pending) > MAX_MEMORIES:
            _unavailable("Belief memory closure exceeds its limit")
        rows = _load_memories(conn, service, principal, brain, sorted(pending))
        if {(memory["memory_id"], memory["revision"]) for memory in rows} != pending:
            _unavailable()
        for memory in rows:
            memories[(memory["memory_id"], memory["revision"])] = memory
        pending = {key for memory in rows for key in memory_parents(memory)} - memories.keys()
        depth += 1
    depths = {}

    def check(key, path):
        if key in path or len(path) > 8:
            _unavailable("Belief parent closure is cyclic or exceeds its depth limit")
        if key not in depths:
            depths[key] = max((1 + check(parent, path | {key}) for parent in memory_parents(memories[key])), default=0)
        if depths[key] > 8:
            _unavailable("Belief parent closure exceeds its depth limit")
        return depths[key]

    for key in memories:
        check(key, set())
    source_ids = set(source_ids) | {identifier for memory in memories.values() for identifier in memory_sources(memory)}
    references = list(references) + [ref for memory in memories.values() for ref in memory_references(memory)]
    source_ids.update(ref["event_id"] for ref in references)
    if len(source_ids) > MAX_SOURCES:
        _unavailable("Belief source closure exceeds its limit")
    events = service._events(conn, principal, brain, event_ids=source_ids, limit=MAX_SOURCES)
    if {event["event_id"] for event in events} != source_ids:
        _unavailable()
    if any(not cognition._scope_allows(event["scope"], scope)
           or event["content_sha256"] != digest(event["observed"]["content"]) for event in events):
        _unavailable()
    superseded = service._superseded_evidence(conn, principal, brain, source_ids)
    if superseded:
        _unavailable("Belief source was corrected")
    service._evidence(references, events)
    event_map = {event["event_id"]: event for event in events}
    for memory in memories.values():
        validate("MemoryAssertion", memory)
        if memory["lifecycle"] == "retracted" or not cognition._evidence_valid(
            memory, memories, event_map, scope, brain["dependency_generation"], None, set(), False, "explore",
            valid_dependency_versions=frozenset(memories),
        ):
            _unavailable()
    return memories, event_map


def _validate_package(conn, service, principal, brain, scope, documents, *, required_versions=()):
    versions, sources, references = set(required_versions), set(), []
    for document in documents:
        validate_relation(document)
        if not cognition._scope_allows(document["scope"], scope):
            _unavailable()
        versions.update(relation_versions(document))
        sources.update(relation_sources(document))
        references.extend(document["evidence_refs"])
    memories, events = _package(conn, service, principal, brain, scope, versions,
                               source_ids=sources, references=references)
    for document in documents:
        if any(snapshot["content_sha256"] != assertion_fingerprint(memories[_version(snapshot["memory_version"])])
               for snapshot in document["endpoint_snapshots"]):
            _unavailable("Belief endpoint content changed under the recorded version")
        if any(memories[key]["world"] != document["world"] for key in relation_versions(document)):
            _unavailable("Belief endpoints belong to different worlds")
        closure, pending = set(), relation_versions(document)
        while pending:
            closure.update(pending)
            pending = {parent for key in pending for parent in memory_parents(memories[key])} - closure
        roots = {identifier for key in closure for identifier in memory_sources(memories[key])}
        if not roots.issubset(document["root_event_ids"]):
            _unavailable("Belief source closure omits an endpoint dependency")
    return memories, events


def _insert(conn, service, principal, brain, document, memories):
    params = service._params(principal, brain["id"])
    previous = conn.execute(
        "SELECT document,invalidated,deleted FROM memory_belief_relations "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s", (*params, document["relation_id"]),
    ).fetchone()
    if previous is not None:
        if (previous["invalidated"] or previous["deleted"] or previous["document"] is None
                or _relation_id(previous["document"]) != _relation_id(document)):
            _unavailable("A revoked belief relation cannot be reintroduced")
        saved = service._rebind(previous["document"], principal, brain)
        _validate_package(conn, service, principal, brain, document["scope"], [saved])
        return saved, False
    endpoints = sorted(relation_versions(document))
    conn.execute(
        "INSERT INTO memory_belief_relations(tenant_id,owner_id,brain_id,id,revision,document,event_ids,"
        "memory_ids,endpoint_versions,endpoint_keys) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (*params, document["relation_id"], document["revision"], Jsonb(document), sorted(relation_sources(document)),
         sorted({key[0] for key in memories}), Jsonb([_ref(key) for key in endpoints]),
         [digest(_ref(key)) for key in endpoints]),
    )
    return document, True


def publish_links(conn, service, principal, brain, batch, result, *, published_memories, snapshot):
    links = getattr(result, "belief_links", ())
    if not links:
        return []
    if len(links) > MAX_RELATIONS:
        raise DomainError("budget_exceeded", "Belief relation publication exceeds its limit")
    claims = {item["proposal_id"]: memory for item, memory in zip(result.claims, published_memories, strict=True)}
    existing = {(memory["memory_id"], memory["revision"]) for memory in batch.existing_assertions}
    documents = []
    for item in links:
        if item["claim_proposal_id"] not in claims:
            _unavailable("Belief source must be a claim accepted in the same batch")
        source = claims[item["claim_proposal_id"]]
        targets = {_version(ref) for ref in item["target_memory_versions"]}
        if not targets.issubset(existing):
            _unavailable("Belief target must be an observed exact assertion version")
        versions = targets | {(source["memory_id"], source["revision"])}
        memories, events = _package(conn, service, principal, brain, batch.scope, versions,
                                   source_ids=snapshot["root_event_ids"], references=item["evidence_refs"])
        document = {key: copy.deepcopy(item[key]) for key in (
            "kind", "evidence_refs", "conditions", "valid_during", "world",
            "condition_registry_fingerprint", "verification")}
        document.update(
            revision=1, scope=copy.deepcopy(batch.scope), source_memory_version=_ref((source["memory_id"], source["revision"])),
            target_memory_versions=[_ref(key) for key in sorted(targets)],
            root_event_ids=sorted(events), endpoint_snapshots=[
                {"memory_version": _ref(key), "content_sha256": assertion_fingerprint(memories[key])}
                for key in sorted(versions)],
        )
        document["evidence_refs"].sort(key=digest)
        document["relation_id"] = _relation_id(document)
        validate_relation(document)
        _validate_package(conn, service, principal, brain, batch.scope, [document])
        documents.append((document, memories))
    saved, inserted = {}, False
    for document, memories in documents:
        previous, created = _insert(conn, service, principal, brain, document, memories)
        saved[previous["relation_id"]] = previous
        inserted = inserted or created
    if inserted:
        service._advance(conn, principal, brain)
    return [service._rebind(document, principal, brain) for document in saved.values()]


def invalidate_beliefs(conn, service, principal, brain, *, event_ids=(), memory_ids=(), deleted=False):
    if event_ids or memory_ids:
        conn.execute(
            "UPDATE memory_belief_relations SET document=NULL,invalidated=true,deleted=deleted OR %s "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND (event_ids && %s::text[] OR memory_ids && %s::text[])",
            (deleted, *service._params(principal, brain["id"]), sorted(set(event_ids)), sorted(set(memory_ids))),
        )


def load_belief_relations(conn, service, principal, brain, scope, memory_versions, max_relations=32):
    if type(max_relations) is not int or not 1 <= max_relations <= MAX_RELATIONS:
        raise ValueError("Belief read limit must be between 1 and 32")
    selected = {_version(ref) if isinstance(ref, dict) else _version(_ref(ref)) for ref in memory_versions}
    if len(selected) > MAX_MEMORIES:
        return {"relations": [], "memories": [], "events": [], "incomplete_versions": [_ref(key) for key in sorted(selected)]}
    completed, incomplete, relations, memories, events = set(), set(), {}, {}, {}
    memory_cache = {key: None for key in selected}
    memory_cache.update({(memory["memory_id"], memory["revision"]): memory
                         for memory in _load_memories(conn, service, principal, brain, sorted(selected))})
    for seed in sorted(selected):
        if seed in completed:
            continue
        found, pending, scanned, package, failed = {seed}, {seed}, set(), {}, False
        parent_depths = {seed: 0}
        while pending and not failed:
            scanned.update(pending)
            missing = pending - memory_cache.keys()
            memory_cache.update({key: None for key in missing})
            memory_cache.update({(memory["memory_id"], memory["revision"]): memory
                                 for memory in _load_memories(conn, service, principal, brain, sorted(missing))})
            following = set()
            for key in pending:
                try:
                    memory = memory_cache[key]
                    if memory is None or memory["lifecycle"] == "retracted" or not cognition._scope_allows(memory["scope"], scope):
                        _unavailable()
                    parents = memory_parents(memory)
                    found.update(parents)
                    following.update(parents)
                    for parent in parents:
                        parent_depths[parent] = max(parent_depths.get(parent, 0), parent_depths[key] + 1)
                        if parent_depths[parent] > 8:
                            _unavailable("Belief parent closure exceeds its depth limit")
                except (DomainError, ValueError, KeyError, TypeError):
                    failed = True
            rows = conn.execute(
                "SELECT id,document,endpoint_versions FROM memory_belief_relations "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND NOT invalidated AND NOT deleted "
                "AND endpoint_keys && %s::text[] ORDER BY id LIMIT %s",
                (*service._params(principal, brain["id"]), [digest(_ref(key)) for key in sorted(pending)], max_relations + 1),
            ).fetchall()
            if len(rows) > max_relations:
                failed = True
            for row in rows:
                document = row["document"]
                try:
                    if document is not None:
                        document = service._rebind(document, principal, brain)
                    endpoints = {_version(ref) for ref in row["endpoint_versions"]}
                    if not endpoints.intersection(pending):
                        raise ValueError("Belief endpoint index mismatch")
                    if document is not None and not cognition._scope_allows(document["scope"], scope):
                        continue
                    found.update(endpoints)
                    following.update(endpoints)
                    for key in endpoints:
                        parent_depths.setdefault(key, 0)
                    if document is None or row["id"] != document["relation_id"] or endpoints != relation_versions(document):
                        raise ValueError("Belief endpoint index mismatch")
                    validate_relation(document)
                    package[row["id"]] = document
                except (DomainError, ValueError, KeyError, TypeError):
                    failed = True
            pending = following - scanned
            if len(package) > max_relations or len(found) > MAX_MEMORIES:
                failed = True
        completed.update(found)
        if not package and not failed and len(found) == 1 and not memory_parents(memory_cache[seed]):
            continue
        try:
            if failed or len(set(relations) | set(package)) > max_relations:
                _unavailable("Belief relation neighborhood exceeds its limit")
            package_memories, package_events = _validate_package(
                conn, service, principal, brain, scope, list(package.values()), required_versions=found,
            )
            if len(set(memories) | set(package_memories)) > MAX_MEMORIES or len(set(events) | set(package_events)) > MAX_SOURCES:
                _unavailable("Belief relation closure exceeds its limit")
            relations.update(package)
            memories.update(package_memories)
            events.update(package_events)
        except (DomainError, ValueError, KeyError, TypeError):
            incomplete.update(found)
    return {"relations": [relations[key] for key in sorted(relations)],
            "memories": [memories[key] for key in sorted(memories)],
            "events": [events[key] for key in sorted(events)],
            "incomplete_versions": [_ref(key) for key in sorted(incomplete)]}


def restore_beliefs(conn, service, principal, brain, data, restored, mapping, blocked_events):
    documents = data.get("belief_relations", [])
    if data.get("archive_origin") != "local_export":
        return {"belief_relations": 0, "quarantined_belief_relations": len(documents)}
    originals = {(memory["memory_id"], memory["revision"]): memory
                 for memory in data["memories"] + data.get("dependency_memories", [])}
    available = {event["event_id"] for event in mapping.values()}
    counts = {"belief_relations": 0, "quarantined_belief_relations": 0}
    for original in documents:
        try:
            validate_relation(original)
            endpoints = relation_versions(original)
            if (not endpoints.issubset(restored) or not relation_sources(original).issubset(available)
                    or relation_sources(original).intersection(blocked_events)):
                _unavailable()
            if any(snapshot["content_sha256"] != assertion_fingerprint(originals[_version(snapshot["memory_version"])])
                   for snapshot in original["endpoint_snapshots"]):
                _unavailable("Archived belief endpoint does not match its snapshot")
            document = service._rebind(original, principal, brain)
            for reference in document["evidence_refs"]:
                source = mapping.get((reference["event_id"], reference["event_revision"]))
                if source is None or source["content_sha256"] != reference["source_content_sha256"]:
                    _unavailable()
                reference["event_revision"] = source["revision"]
            document["evidence_refs"].sort(key=digest)
            memories, _ = _package(conn, service, principal, brain, document["scope"], endpoints,
                                   source_ids=relation_sources(document), references=document["evidence_refs"])
            document["endpoint_snapshots"] = [
                {"memory_version": _ref(key), "content_sha256": assertion_fingerprint(memories[key])}
                for key in sorted(endpoints)]
            document["relation_id"] = _relation_id(document)
            _validate_package(conn, service, principal, brain, document["scope"], [document])
        except (DomainError, ValueError, KeyError, TypeError):
            counts["quarantined_belief_relations"] += 1
            continue
        try:
            _insert(conn, service, principal, brain, document, memories)
            counts["belief_relations"] += 1
        except DomainError:
            counts["quarantined_belief_relations"] += 1
    return counts
