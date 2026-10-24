import copy

from psycopg.types.json import Jsonb

from .contracts import validate
from .helpers import DomainError

REBUILD_POLICY = {
    "embeddings": "rebuild_from_available_assertions",
    "access_state": "reset_requires_new_external_feedback",
    "jobs": "execution_and_leases_not_restored",
    "cognitive_views": "restore_only_exact_versions_with_available_sources",
}


def _invalid():
    raise DomainError("invalid_archive", "Cognitive archive structure or source manifest is invalid.")


def _identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        _invalid()


def _revision(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        _invalid()


def _ids(values, maximum=512):
    if not isinstance(values, list) or len(values) > maximum:
        _invalid()
    for value in values:
        _identifier(value)
    if len(set(values)) != len(values):
        _invalid()


def memory_references(memory):
    return (memory["evidence_refs"] + memory.get("counterexamples", [])
            + [ref for path in memory["disclosure_basis"]["support_paths"] for ref in path["evidence_refs"]])


def memory_sources(memory):
    return set(memory.get("root_event_ids", [])) | {ref["event_id"] for ref in memory_references(memory)}


def memory_parents(memory):
    references = (memory["derivation"]["source_memory_versions"]
                  + [ref for path in memory["disclosure_basis"]["support_paths"] for ref in path["source_memory_versions"]])
    return {(ref["memory_id"], ref["revision"]) for ref in references}


def all_memories(data):
    return data["memories"] + data.get("dependency_memories", [])


def archive_identities(data):
    from .belief_store import relation_sources, relation_versions

    identities = set()
    for memory in all_memories(data):
        identities.add(memory["memory_id"])
        identities.update(memory_sources(memory))
        identities.update(key[0] for key in memory_parents(memory))
    views = data.get("cognitive_views", {})
    for episode in views.get("episodes", []):
        identities.update(episode["event_ids"])
        identities.update(episode["document"]["root_event_ids"])
        identities.update(ref["event_id"] for ref in episode["document"]["member_refs"])
    for member in views.get("members", []):
        identities.add(member["memory_id"])
    for association in views.get("associations", []):
        identities.update(association["event_ids"])
        identities.update((association["source_id"], association["target_id"]))
    for relation in data.get("belief_relations", []):
        identities.update(relation_sources(relation))
        identities.update(key[0] for key in relation_versions(relation))
    return identities


def validate_extensions(data):
    if set(data) - {"format", "format_version", "events", "memories", "working_states", "tombstones",
                    "source_brain_id", "created_at", "retired_event_ids", "event_dependencies",
                    "excluded_deleted_event_ids", "dependency_memories", "cognitive_views", "rebuild_policy", "archive_origin",
                    "belief_relations"}:
        _invalid()
    if data.get("archive_origin", "legacy_unverified") not in {"local_export", "untrusted_upload", "legacy_unverified"}:
        _invalid()
    for event in data["events"]:
        _revision(event["revision"])
        _revision(event["observed"]["source_revision"])
    dependencies = data.get("dependency_memories", [])
    if not isinstance(dependencies, list) or len(dependencies) + len(data["memories"]) > 5000:
        _invalid()
    for memory in dependencies:
        validate("MemoryAssertion", memory)
    memories = {}
    for memory in all_memories(data):
        _revision(memory["revision"])
        key = (memory["memory_id"], memory["revision"])
        if key in memories:
            _invalid()
        memories[key] = memory
        for identifier, revision in memory_parents(memory):
            _identifier(identifier)
            _revision(revision)
    edges = {key: memory_parents(memory) for key, memory in memories.items()}
    completed = set()
    for start in memories:
        stack, visiting = [(start, False)], set()
        while stack:
            key, leaving = stack.pop()
            if leaving:
                visiting.remove(key)
                completed.add(key)
            elif key in visiting:
                _invalid()
            elif key in memories and key not in completed:
                visiting.add(key)
                stack.append((key, True))
                stack.extend((parent, False) for parent in edges[key])
    if "rebuild_policy" in data and data["rebuild_policy"] != REBUILD_POLICY:
        _invalid()
    from .belief_store import validate_relation

    relations = data.get("belief_relations", [])
    if not isinstance(relations, list) or len(relations) > 5000:
        _invalid()
    relation_ids = set()
    for relation in relations:
        validate_relation(relation)
        if relation["relation_id"] in relation_ids:
            _invalid()
        relation_ids.add(relation["relation_id"])
    views = data.get("cognitive_views", {"version": 1, "episodes": [], "members": [], "associations": []})
    if (not isinstance(views, dict) or set(views) != {"version", "episodes", "members", "associations"}
            or type(views["version"]) is not int or views["version"] != 1):
        _invalid()
    for name, maximum in (("episodes", 5000), ("members", 20000), ("associations", 20000)):
        if not isinstance(views[name], list) or len(views[name]) > maximum:
            _invalid()
    episode_versions = set()
    for episode in views["episodes"]:
        if not isinstance(episode, dict) or set(episode) != {"document", "event_ids"}:
            _invalid()
        document = episode["document"]
        if not isinstance(document, dict) or set(document) != {
            "episode_id", "revision", "scope", "state", "member_refs", "boundary_ref",
            "organization_status", "job_id", "proposal_id", "root_event_ids",
        }:
            _invalid()
        for field in ("episode_id", "job_id", "proposal_id"):
            _identifier(document[field])
        _revision(document["revision"])
        validate("BoundScope", document["scope"])
        if document["state"] not in {"open", "closed"} or document["organization_status"] != "inferred":
            _invalid()
        _ids(episode["event_ids"])
        _ids(document["root_event_ids"], 256)
        members = document["member_refs"]
        if not isinstance(members, list) or not 1 <= len(members) <= 512:
            _invalid()
        for reference in members + ([document["boundary_ref"]] if document["boundary_ref"] is not None else []):
            if not isinstance(reference, dict) or set(reference) != {"event_id", "revision"}:
                _invalid()
            _identifier(reference["event_id"])
            _revision(reference["revision"])
        if len({(ref["event_id"], ref["revision"]) for ref in members}) != len(members):
            _invalid()
        if not set(document["root_event_ids"]).union(ref["event_id"] for ref in members).issubset(episode["event_ids"]):
            _invalid()
        key = (document["episode_id"], document["revision"])
        if key in episode_versions:
            _invalid()
        episode_versions.add(key)
    memberships = set()
    for member in views["members"]:
        if not isinstance(member, dict) or set(member) != {
            "episode_id", "episode_revision", "memory_id", "memory_revision", "role", "position",
        }:
            _invalid()
        for field in ("episode_id", "memory_id"):
            _identifier(member[field])
        for field in ("episode_revision", "memory_revision"):
            _revision(member[field])
        if (member["role"] not in {"fragment", "outline", "anchor", "residual", "schema"}
                or type(member["position"]) is not int or not 0 <= member["position"] <= 20000):
            _invalid()
        identity = tuple(member[key] for key in ("episode_id", "episode_revision", "memory_id", "memory_revision"))
        if identity in memberships:
            _invalid()
        memberships.add(identity)
    association_ids = set()
    for association in views["associations"]:
        if not isinstance(association, dict) or set(association) != {
            "id", "source_id", "source_revision", "target_id", "target_revision", "relation", "event_ids",
        }:
            _invalid()
        for field in ("id", "source_id", "target_id"):
            _identifier(association[field])
        for field in ("source_revision", "target_revision"):
            _revision(association[field])
        if association["relation"] not in {"same_episode", "context", "schema", "exception", "procedure"}:
            _invalid()
        _ids(association["event_ids"])
        if association["id"] in association_ids:
            _invalid()
        association_ids.add(association["id"])


def export_views(conn, service, principal, brain, data, *, include_views):
    from .belief_store import relation_versions

    params = service._params(principal, brain["id"])
    data["rebuild_policy"] = copy.deepcopy(REBUILD_POLICY)
    data["dependency_memories"] = []
    views = {"version": 1, "episodes": [], "members": [], "associations": []}
    data["cognitive_views"] = views
    data["belief_relations"] = []
    if not data["memories"]:
        return
    if include_views:
        views["episodes"] = [dict(row) for row in conn.execute(
            "SELECT document,event_ids FROM memory_episodes WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND is_current AND NOT invalidated AND NOT deleted AND document IS NOT NULL LIMIT 5001", params)]
        views["members"] = [dict(row) for row in conn.execute(
            "SELECT m.episode_id,m.episode_revision,m.memory_id,m.memory_revision,m.role,m.position "
            "FROM memory_episode_members m JOIN memory_episodes e ON e.tenant_id=m.tenant_id AND e.brain_id=m.brain_id "
            "AND e.id=m.episode_id AND e.revision=m.episode_revision WHERE e.tenant_id=%s AND e.owner_id=%s "
            "AND e.brain_id=%s AND e.is_current AND NOT e.invalidated AND NOT e.deleted AND e.document IS NOT NULL LIMIT 20001", params)]
        views["associations"] = [dict(row) for row in conn.execute(
            "SELECT id,source_id,source_revision,target_id,target_revision,relation,event_ids FROM memory_associations "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND NOT invalidated LIMIT 20001", params)]
        data["belief_relations"] = [row["document"] for row in conn.execute(
            "SELECT document FROM memory_belief_relations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND NOT invalidated AND NOT deleted AND document IS NOT NULL ORDER BY id LIMIT 5001", params)]
    documents = {(memory["memory_id"], memory["revision"]): memory for memory in data["memories"]}
    needed = {key for memory in documents.values() for key in memory_parents(memory)}
    needed.update((member["memory_id"], member["memory_revision"]) for member in views["members"])
    needed.update((association[side + "_id"], association[side + "_revision"])
                  for association in views["associations"] for side in ("source", "target"))
    needed.update(key for relation in data["belief_relations"] for key in relation_versions(relation))
    attempted = set(documents)
    while needed - attempted:
        keys = sorted(needed - attempted)
        if len(attempted) + len(keys) > 5000:
            _invalid()
        attempted.update(keys)
        rows = conn.execute(
            "SELECT document,is_current FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND NOT invalidated AND NOT suppressed AND NOT deleted AND document IS NOT NULL "
            "AND (id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (*params, [key[0] for key in keys], [key[1] for key in keys]),
        )
        for row in rows:
            memory = service._rebind(row["document"], principal, brain)
            if not row["is_current"]:
                memory["lifecycle"] = "superseded"
            documents[(memory["memory_id"], memory["revision"])] = memory
            data["dependency_memories"].append(memory)
            needed.update(memory_parents(memory))
    views["members"] = [member for member in views["members"] if (member["memory_id"], member["memory_revision"]) in documents]
    views["associations"] = [association for association in views["associations"]
                             if all((association[side + "_id"], association[side + "_revision"]) in documents for side in ("source", "target"))]


def sanitize_views(data, erased_events, erased_memories):
    from .belief_store import relation_sources, relation_versions

    data["dependency_memories"] = [memory for memory in data.get("dependency_memories", []) if memory["memory_id"] not in erased_memories]
    if "belief_relations" in data:
        data["belief_relations"] = [relation for relation in data["belief_relations"]
                                    if not erased_events.intersection(relation_sources(relation))
                                    and not erased_memories.intersection(key[0] for key in relation_versions(relation))]
    views = data.get("cognitive_views")
    if views is None:
        return
    views["episodes"] = [episode for episode in views["episodes"] if not erased_events.intersection(episode["event_ids"])]
    episodes = {(episode["document"]["episode_id"], episode["document"]["revision"]) for episode in views["episodes"]}
    views["members"] = [member for member in views["members"]
                        if (member["episode_id"], member["episode_revision"]) in episodes and member["memory_id"] not in erased_memories]
    views["associations"] = [association for association in views["associations"]
                             if not erased_events.intersection(association["event_ids"])
                             and association["source_id"] not in erased_memories and association["target_id"] not in erased_memories]


def event_version_mapping(data, available):
    targets = {event["event_id"]: event for event in available}
    mapping = {}
    for source in data["events"]:
        target = targets.get(source["event_id"])
        if target is None or source["content_sha256"] != target["content_sha256"]:
            continue
        if any(source["scope"][key] != target["scope"][key] for key in ("conversation_id", "branch_id")):
            continue
        ignored = {"producer_ref", "source_event_key", "source_revision"}
        if ({key: value for key, value in source["observed"].items() if key not in ignored}
                != {key: value for key, value in target["observed"].items() if key not in ignored}):
            continue
        mapping[(source["event_id"], source["revision"])] = target
    return mapping


def restore_memories(conn, service, principal, brain, data, available, blocked_events, blocked_memories):
    params = service._params(principal, brain["id"])
    mapping = event_version_mapping(data, available)
    roots = {event["event_id"] for event in mapping.values()}
    pending = {(memory["memory_id"], memory["revision"]): copy.deepcopy(memory) for memory in all_memories(data)}
    current = {(memory["memory_id"], memory["revision"]) for memory in data["memories"]}
    restored, rejected = set(), set()
    event_lookup = {event["event_id"]: event for event in available}
    capture_cache = {}
    while pending:
        progressed = False
        for key, memory in list(pending.items()):
            parents = memory_parents(memory)
            if parents.intersection(pending):
                continue
            progressed = True
            del pending[key]
            if (key[0] in blocked_memories or memory_sources(memory).intersection(blocked_events)
                    or not memory_sources(memory).issubset(roots) or not parents.issubset(restored)):
                rejected.add(key)
                continue
            try:
                seen = set()
                for reference in memory_references(memory):
                    if id(reference) in seen:
                        continue
                    seen.add(id(reference))
                    source = mapping.get((reference["event_id"], reference["event_revision"]))
                    if source is None or source["content_sha256"] != reference["source_content_sha256"]:
                        raise DomainError("invalid_evidence", "An exact imported source version is unavailable.")
                    reference["event_revision"] = source["revision"]
                service._evidence(memory_references(memory), available)
                if data.get("archive_origin") != "local_export":
                    if (memory["derivation"]["method"] != "direct_capture" or parents
                            or any(field in memory for field in ("verification", "procedure", "conditions", "claim_kind"))):
                        raise DomainError("invalid_evidence", "Imported semantic interpretations require local reverification.")
                    for event_id in {ref["event_id"] for ref in memory["evidence_refs"]}:
                        if event_id not in capture_cache:
                            capture_cache[event_id] = service._current_captures(conn, principal, brain, event_lookup[event_id])
                    captures = [capture for event_id in {ref["event_id"] for ref in memory["evidence_refs"]}
                                for capture in capture_cache[event_id]
                                if capture["statement"] == memory["statement"]
                                and capture["memory_kind"] == memory["memory_kind"] and capture["world"] == memory["world"]
                                and all(ref in memory["evidence_refs"] for ref in capture["evidence_refs"])]
                    if not captures:
                        raise DomainError("invalid_evidence", "An imported literal assertion must match its source.")
                    memory["epistemic_status"] = captures[0]["epistemic_status"]
                memory = service._rebind(memory, principal, brain)
                prior = conn.execute(
                    "SELECT document,invalidated,deleted,suppressed FROM memory_assertions WHERE tenant_id=%s "
                    "AND owner_id=%s AND brain_id=%s AND id=%s AND revision=%s", (*params, *key),
                ).fetchone()
                if prior is not None:
                    if (prior["document"] is None or prior["invalidated"] or prior["deleted"] or prior["suppressed"]
                            or service._rebind(prior["document"], principal, brain) != memory):
                        raise DomainError("invalid_evidence", "An imported memory version conflicts with the target.")
                else:
                    existing_current = conn.execute(
                        "SELECT revision FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                        "AND id=%s AND is_current", (*params, key[0]),
                    ).fetchone()
                    service._publish(conn, principal, brain, memory)
                    if key not in current or existing_current is not None:
                        conn.execute("UPDATE memory_assertions SET is_current=false WHERE tenant_id=%s AND owner_id=%s "
                                     "AND brain_id=%s AND id=%s AND revision=%s", (*params, *key))
                restored.add(key)
            except DomainError:
                rejected.add(key)
        if not progressed:
            rejected.update(pending)
            break
    return restored, len(rejected), mapping


def restore_views(conn, service, principal, brain, data, restored, mapping, blocked_events):
    from .belief_store import restore_beliefs

    params = service._params(principal, brain["id"])
    views = data.get("cognitive_views", {"episodes": [], "members": [], "associations": []})
    available_ids = {event["event_id"] for event in mapping.values()}
    counts = {"episodes": 0, "members": 0, "associations": 0, "quarantined_views": 0}
    counts.update(restore_beliefs(conn, service, principal, brain, data, restored, mapping, blocked_events))
    if data.get("archive_origin") != "local_export":
        counts["quarantined_views"] = sum(len(views[field]) for field in ("episodes", "members", "associations"))
        return counts
    episodes = set()
    for item in views["episodes"]:
        document = copy.deepcopy(item["document"])
        key = (document["episode_id"], document["revision"])
        if set(item["event_ids"]).intersection(blocked_events) or not set(item["event_ids"]).issubset(available_ids):
            counts["quarantined_views"] += 1
            continue
        try:
            for reference in document["member_refs"] + ([document["boundary_ref"]] if document["boundary_ref"] else []):
                event = mapping.get((reference["event_id"], reference["revision"]))
                if event is None or any(event["scope"][field] != document["scope"][field] for field in ("conversation_id", "branch_id")):
                    raise DomainError("invalid_evidence", "Imported episode source is unavailable.")
                reference["revision"] = event["revision"]
            document = service._rebind(document, principal, brain)
            previous = conn.execute("SELECT revision,document,invalidated,deleted,is_current FROM memory_episodes "
                                    "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
                                    "AND (is_current OR revision=%s) ORDER BY is_current DESC LIMIT 1", (*params, *key)).fetchone()
            if previous:
                if (previous["invalidated"] or previous["deleted"] or previous["document"] is None
                        or not previous["is_current"] or previous["revision"] != key[1]
                        or service._rebind(previous["document"], principal, brain) != document):
                    raise DomainError("invalid_evidence", "Imported episode conflicts with the target.")
            else:
                conn.execute("INSERT INTO memory_episodes(tenant_id,owner_id,brain_id,id,revision,conversation_id,branch_id,"
                             "state,document,event_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             (*params, *key, document["scope"]["conversation_id"], document["scope"]["branch_id"],
                              document["state"], Jsonb(document), item["event_ids"]))
            episodes.add(key)
            counts["episodes"] += 1
        except DomainError:
            counts["quarantined_views"] += 1
    for member in views["members"]:
        if ((member["episode_id"], member["episode_revision"]) not in episodes
                or (member["memory_id"], member["memory_revision"]) not in restored):
            counts["quarantined_views"] += 1
            continue
        previous = conn.execute(
            "SELECT role,position FROM memory_episode_members WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND episode_id=%s AND episode_revision=%s AND memory_id=%s AND memory_revision=%s",
            (*params, *(member[key] for key in ("episode_id", "episode_revision", "memory_id", "memory_revision"))),
        ).fetchone()
        if previous is not None and previous != {"role": member["role"], "position": member["position"]}:
            counts["quarantined_views"] += 1
            continue
        conn.execute("INSERT INTO memory_episode_members(tenant_id,owner_id,brain_id,episode_id,episode_revision,"
                     "memory_id,memory_revision,role,position) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (*params, *(member[key] for key in ("episode_id", "episode_revision", "memory_id", "memory_revision", "role", "position"))))
        counts["members"] += 1
    for association in views["associations"]:
        if (not set(association["event_ids"]).issubset(available_ids) or set(association["event_ids"]).intersection(blocked_events)
                or any((association[side + "_id"], association[side + "_revision"]) not in restored for side in ("source", "target"))):
            counts["quarantined_views"] += 1
            continue
        previous = conn.execute(
            "SELECT id,source_id,source_revision,target_id,target_revision,relation,event_ids,invalidated "
            "FROM memory_associations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, association["id"]),
        ).fetchone()
        if previous is not None and (previous["invalidated"] or {key: value for key, value in previous.items() if key != "invalidated"} != association):
            counts["quarantined_views"] += 1
            continue
        conn.execute("INSERT INTO memory_associations(tenant_id,owner_id,brain_id,id,source_id,source_revision,target_id,"
                     "target_revision,relation,event_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (*params, *(association[key] for key in ("id", "source_id", "source_revision", "target_id", "target_revision", "relation", "event_ids"))))
        counts["associations"] += 1
    return counts
