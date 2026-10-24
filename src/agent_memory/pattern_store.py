from __future__ import annotations

from psycopg.types.json import Jsonb

from . import cognition
from .archive_views import memory_references, memory_sources
from .contracts import validate
from .helpers import DomainError, digest
from .retrieval import resolve_dependency_closure

MAX_MEMORIES = 64
MAX_EPISODES = 16
MAX_SOURCES = 256


def invalidate_patterns(conn, service, principal, brain, *, event_ids=(), memory_ids=(),
                        episode_ids=(), deleted=False):
    if not event_ids and not memory_ids and not episode_ids:
        return
    conn.execute(
        "UPDATE memory_pattern_views SET document=NULL,invalidated=true,deleted=deleted OR %s "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND (event_ids && %s::text[] OR memory_ids && %s::text[] OR episode_ids && %s::text[])",
        (deleted, *service._params(principal, brain["id"]),
         sorted(set(event_ids)), sorted(set(memory_ids)), sorted(set(episode_ids))),
    )


def _versions(documents, identifier):
    return sorted({(document[identifier], document["revision"]) for document in documents})


def _memory_rows(conn, service, principal, brain, versions):
    if not versions:
        return []
    return conn.execute(
        "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND NOT invalidated AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
        "AND (id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
        (*service._params(principal, brain["id"]),
         [key[0] for key in versions], [key[1] for key in versions]),
    ).fetchall()


def _validated_inputs(conn, service, principal, brain, scope, memory_versions, episode_versions):
    if len(memory_versions) > MAX_MEMORIES or len(episode_versions) > MAX_EPISODES:
        return None
    params = service._params(principal, brain["id"])
    episodes = conn.execute(
        "SELECT document,event_ids FROM memory_episodes WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND is_current AND NOT invalidated AND NOT deleted AND document IS NOT NULL "
        "AND (id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) ORDER BY id,revision",
        (*params, [key[0] for key in episode_versions], [key[1] for key in episode_versions]),
    ).fetchall()
    if len(episodes) != len(episode_versions) or len(episodes) < 2:
        return None
    member_rows = conn.execute(
        "SELECT DISTINCT memory_id,memory_revision FROM memory_episode_members "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND (episode_id,episode_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
        "ORDER BY memory_id,memory_revision LIMIT %s",
        (*params, [key[0] for key in episode_versions], [key[1] for key in episode_versions], MAX_MEMORIES + 1),
    ).fetchall()
    roots = set(memory_versions) | {(row["memory_id"], row["memory_revision"]) for row in member_rows}
    if len(roots) > MAX_MEMORIES:
        return None

    def load(versions):
        return [service._rebind(row["document"], principal, brain)
                for row in _memory_rows(conn, service, principal, brain, versions)]

    memories = load(sorted(roots))
    if len(memories) != len(roots):
        return None
    closure = resolve_dependency_closure(memories, load, max_nodes=MAX_MEMORIES, max_depth=8)
    if closure.truncated or closure.missing_versions or closure.cycle_versions:
        return None
    memories = list(closure.memories)
    source_ids = {identifier for memory in memories for identifier in memory_sources(memory)}
    documents = []
    for row in episodes:
        document = service._rebind(row["document"], principal, brain)
        if not cognition._scope_allows(document["scope"], scope):
            return None
        source_ids.update(row["event_ids"])
        source_ids.update(document.get("root_event_ids", []))
        source_ids.update(ref["event_id"] for ref in document["member_refs"])
        documents.append(document)
    if len(source_ids) > MAX_SOURCES:
        return None
    events = service._events(conn, principal, brain, event_ids=source_ids, limit=MAX_SOURCES)
    if len(events) != len(source_ids):
        return None
    event_map = {event["event_id"]: event for event in events}
    if any(not cognition._scope_allows(event["scope"], scope)
           or digest(event["observed"]["content"]) != event["content_sha256"] for event in events):
        return None
    superseded = service._superseded_evidence(conn, principal, brain, source_ids)
    if superseded:
        return None
    lookup = {(memory["memory_id"], memory["revision"]): memory for memory in memories}
    for memory in memories:
        validate("MemoryAssertion", memory)
        if memory["lifecycle"] == "retracted" or not cognition._evidence_valid(
            memory, lookup, event_map, scope, brain["dependency_generation"], None, set(), False, "explore",
            valid_dependency_versions=frozenset(lookup),
        ):
            return None
        service._evidence(memory_references(memory), events)
    for document in documents:
        for reference in document["member_refs"]:
            source = event_map.get(reference["event_id"])
            if (source is None or source["revision"] != reference["revision"]
                    or any(source["scope"][key] != document["scope"][key]
                           for key in ("conversation_id", "branch_id"))):
                return None
    return memories, documents, source_ids


def rebuild_patterns(conn, service, principal, brain, *, scope, memory_versions, episode_versions):
    from .patterns import build_patterns

    memory_versions = sorted(set(memory_versions))
    episode_versions = sorted(set(episode_versions))
    try:
        inputs = _validated_inputs(conn, service, principal, brain, scope, memory_versions, episode_versions)
        if inputs is None:
            return []
        memories, episodes, source_ids = inputs
        views = build_patterns(scope, memories, episodes)
    except (DomainError, ValueError, KeyError, TypeError):
        return []
    saved = []
    for view in views:
        row = conn.execute(
            "SELECT document,invalidated,deleted FROM memory_pattern_views "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*service._params(principal, brain["id"]), view["view_id"]),
        ).fetchone()
        if row:
            if row["invalidated"] or row["deleted"] or row["document"] != view:
                continue
        else:
            conn.execute(
                "INSERT INTO memory_pattern_views(tenant_id,owner_id,brain_id,id,document,event_ids,memory_ids,episode_ids) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (*service._params(principal, brain["id"]), view["view_id"], Jsonb(view), sorted(source_ids),
                 sorted({memory["memory_id"] for memory in memories}),
                 sorted({episode["episode_id"] for episode in episodes})),
            )
        saved.append(view["view_id"])
    return saved


def refresh_patterns(conn, service, principal, brain, batch, published_memories, episode_versions):
    versions = {episode["episode_id"]: episode["revision"] for episode in batch.episodes}
    versions.update({episode["episode_id"]: episode["revision"] for episode in episode_versions})
    if len(versions) < 2:
        return []
    return rebuild_patterns(
        conn, service, principal, brain, scope=batch.scope,
        memory_versions=_versions(list(batch.existing_assertions) + list(published_memories), "memory_id"),
        episode_versions=sorted(versions.items()),
    )


def load_patterns(conn, service, principal, brain, *, scope, memory_versions, limit=16):
    from .patterns import validate_pattern

    if type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError("Pattern read limit must be between 1 and 16")
    selected = {(ref["memory_id"], ref["revision"]) if isinstance(ref, dict) else tuple(ref)
                for ref in memory_versions}
    if not selected:
        return []
    rows = conn.execute(
        "SELECT document FROM memory_pattern_views WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND NOT invalidated AND NOT deleted AND document IS NOT NULL AND memory_ids && %s::text[] "
        "ORDER BY created_at DESC,id LIMIT %s",
        (*service._params(principal, brain["id"]), sorted({key[0] for key in selected}), limit * 4),
    ).fetchall()
    result = []
    for row in rows:
        view = row["document"]
        try:
            if not cognition._scope_allows(view["scope"], scope):
                continue
            versions = _versions(view["source_memory_versions"], "memory_id")
            if not set(versions).issubset(selected):
                continue
            episode_versions = _versions([instance["episode_ref"] for instance in view["instances"]], "episode_id")
            inputs = _validated_inputs(conn, service, principal, brain, scope, versions, episode_versions)
            if inputs is None or not validate_pattern(view, inputs[0], inputs[1]):
                continue
        except (DomainError, KeyError, ValueError, TypeError):
            continue
        result.append(view)
        if len(result) == limit:
            break
    return result


def rebuild_restored_patterns(conn, service, principal, brain, data, restored_versions):
    if data.get("archive_origin") != "local_export":
        return []
    episodes = [item["document"] for item in data.get("cognitive_views", {}).get("episodes", [])]
    episodes = sorted(episodes, key=lambda episode: (episode["episode_id"], episode["revision"]))[:MAX_EPISODES]
    if len(episodes) < 2:
        return []
    members = data.get("cognitive_views", {}).get("members", [])
    episode_versions = set(_versions(episodes, "episode_id"))
    roots = {(member["memory_id"], member["memory_revision"]) for member in members
             if (member["episode_id"], member["episode_revision"]) in episode_versions}
    if not roots.issubset(restored_versions):
        return []
    target = {"conversation_ref": episodes[0]["scope"]["conversation_id"],
              "branch_ref": episodes[0]["scope"]["branch_id"]}
    return rebuild_patterns(
        conn, service, principal, brain, scope=service._scope(principal, brain, target),
        memory_versions=sorted(roots), episode_versions=sorted(episode_versions),
    )
