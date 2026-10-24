from dataclasses import dataclass

from . import cognition
from .candidates import _eligible, _scope_clause
from .helpers import DomainError, digest
from .retrieval import resolve_dependency_closure


@dataclass(frozen=True)
class Neighborhood:
    sources: tuple[dict, ...]
    assertions: tuple[dict, ...]
    episodes: tuple[dict, ...]
    sampling: dict


class _Incomplete(Exception):
    pass


def _key(memory):
    return memory["memory_id"], memory["revision"]


def _references(memory):
    references = list(memory["evidence_refs"]) + memory.get("counterexamples", [])
    basis = memory["disclosure_basis"]
    for path in basis["support_paths"]:
        if path["path_id"] in basis["selected_path_ids"]:
            references.extend(path["evidence_refs"])
    return references


class _Selector:
    def __init__(self, conn, service, principal, brain, target, through, policy):
        self.conn, self.service, self.principal, self.brain = conn, service, principal, brain
        self.target, self.through, self.policy = target, through, policy
        self.params = service._params(principal, brain["id"])
        self.scope = service._scope(principal, brain, target)
        self.source_cache, self.memory_cache = {}, {}
        self.selected_sources, self.selected_memories, self.selected_episodes = {}, {}, {}
        self.reasons = set()
        if brain["indexed_sequence"] < through:
            self.reasons.add("index_lag")
        self.sampling = {
            "strategy": "bounded-cue-neighborhood-v1", "through_sequence": through,
            "indexed_through_sequence": min(brain["indexed_sequence"], through),
            "search_exhaustive": False, "publication_restricted": False,
            "recent_window_limit": min(24, policy.max_sources),
            "candidate_scan_limit": min(256, policy.max_assertions * 4),
            "candidate_limit": policy.max_assertions, "association_depth": 1,
            "dependency_depth": 8, "candidate_count": 0, "omitted_packages": 0,
            "limits": {"sources": policy.max_sources, "assertions": policy.max_assertions,
                       "episodes": policy.max_episodes},
        }

    def bind(self, memory):
        return self.service._rebind(memory, self.principal, self.brain)

    def source_package(self, ids, references=()):
        ids = set(ids) | {ref["event_id"] for ref in references}
        if len(ids) > self.policy.max_sources:
            raise _Incomplete("source_budget")
        missing = ids - self.source_cache.keys()
        if missing:
            found = self.service._events(self.conn, self.principal, self.brain,
                                         event_ids=missing, limit=self.policy.max_sources)
            blocked = {ref["event_id"] for ref in self.service._superseded_evidence(
                self.conn, self.principal, self.brain, missing)}
            self.source_cache.update({identifier: None for identifier in missing})
            for event in found:
                if (event["event_id"] not in blocked and event["sequence"] <= self.through
                        and cognition._scope_allows(event["scope"], self.scope)
                        and event["content_sha256"] == digest(event["observed"]["content"])
                        and event["observed"]["delivery_state"] in {"received", "delivered", "partially_delivered"}):
                    self.source_cache[event["event_id"]] = event
        events = [self.source_cache[identifier] for identifier in sorted(ids)]
        if any(event is None for event in events):
            raise _Incomplete("source_unavailable")
        try:
            self.service._evidence(references, events)
        except DomainError as error:
            raise _Incomplete("source_version_mismatch") from error
        return {event["event_id"]: event for event in events}

    def remember(self, rows):
        for row in rows:
            self.memory_cache[_key(row["document"])] = row

    def load_memories(self, keys):
        missing = set(keys) - self.memory_cache.keys()
        if missing:
            rows = self.conn.execute(
                "SELECT a.document,a.event_ids,a.is_current FROM memory_assertions a WHERE "
                + _eligible(current=False)
                + "AND (a.id,a.revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
                (*self.params, [key[0] for key in missing], [key[1] for key in missing]),
            ).fetchall()
            self.memory_cache.update({key: None for key in missing})
            self.remember(rows)
        memories = []
        for key in keys:
            row = self.memory_cache.get(key)
            if row:
                memory = self.bind(row["document"])
                if memory["lifecycle"] != "retracted" and cognition._scope_allows(memory["scope"], self.scope):
                    memories.append(memory)
        return memories

    def assertion_package(self, root, *, exceptions=True):
        if root["lifecycle"] == "retracted":
            raise _Incomplete("assertion_retracted")
        roots = [self.bind(root)]
        extra_source_ids = set()
        if exceptions:
            rows = self.conn.execute(
                "SELECT a.document,a.event_ids,a.is_current,a.invalidated,a.deleted,a.suppressed,e.event_ids AS edge_event_ids "
                "FROM memory_associations e LEFT JOIN memory_assertions a "
                "ON a.tenant_id=e.tenant_id AND a.owner_id=e.owner_id AND a.brain_id=e.brain_id "
                "AND ((e.source_id=%s AND e.source_revision=%s AND a.id=e.target_id AND a.revision=e.target_revision) "
                "OR (e.target_id=%s AND e.target_revision=%s AND a.id=e.source_id AND a.revision=e.source_revision)) "
                "WHERE e.tenant_id=%s AND e.owner_id=%s AND e.brain_id=%s "
                "AND ((e.source_id=%s AND e.source_revision=%s) OR (e.target_id=%s AND e.target_revision=%s)) "
                "AND NOT e.invalidated AND e.relation='exception' ORDER BY e.id LIMIT %s",
                (*_key(root), *_key(root), *self.params, *_key(root), *_key(root), self.policy.max_assertions + 1),
            ).fetchall()
            if len(rows) > self.policy.max_assertions:
                raise _Incomplete("exception_budget")
            if any(row["document"] is None or row["invalidated"] or row["deleted"] or row["suppressed"] for row in rows):
                raise _Incomplete("exception_unavailable")
            self.remember(rows)
            keys = {_key(row["document"]) for row in rows}
            neighbors = self.load_memories(sorted(keys))
            if len(neighbors) != len(keys):
                raise _Incomplete("exception_unavailable")
            roots.extend(memory for memory in neighbors if _key(memory) != _key(root))
            extra_source_ids.update(identifier for row in rows for identifier in row["edge_event_ids"])
        if len(roots) > self.policy.max_assertions:
            raise _Incomplete("assertion_budget")
        closure = resolve_dependency_closure(roots, self.load_memories,
                                             max_nodes=self.policy.max_assertions, max_depth=8)
        if closure.truncated or closure.missing_versions or closure.cycle_versions:
            raise _Incomplete("dependency_incomplete")
        memories = {_key(memory): memory for memory in closure.memories}
        ids, references = set(extra_source_ids), []
        for key, memory in memories.items():
            ids.update(self.memory_cache[key]["event_ids"])
            ids.update(memory.get("root_event_ids", []))
            references.extend(_references(memory))
        events = self.source_package(ids, references)
        if not all(cognition._evidence_valid(
            memory, memories, events, self.scope, self.brain["dependency_generation"],
            self.through, set(), False, "explore", valid_dependency_versions=frozenset(memories),
        ) for memory in memories.values()):
            raise _Incomplete("unsupported_assertion")
        return events, {key: self.memory_cache[key]["document"] for key in memories}

    def include(self, events, memories=None):
        memories = memories or {}
        if len(self.selected_sources.keys() | events.keys()) > self.policy.max_sources:
            raise _Incomplete("source_budget")
        if len(self.selected_memories.keys() | memories.keys()) > self.policy.max_assertions:
            raise _Incomplete("assertion_budget")
        self.selected_sources.update(events)
        self.selected_memories.update(memories)

    def omit(self, reason, *, relevant=False):
        self.reasons.add(reason)
        self.sampling["omitted_packages"] += 1
        if relevant:
            self.sampling["publication_restricted"] = True

    def candidates(self, cue):
        from .service import _vector

        if not cue.strip():
            return []
        clause, scope_params = _scope_clause("a", self.target)
        rows = self.conn.execute(
            "SELECT a.document,a.event_ids,a.is_current FROM memory_assertions a WHERE "
            + _eligible() + clause
            + "AND a.embedding_model='lexical-feature-hash' AND a.embedding_version='v1-d256-blake2s' "
            "AND NOT EXISTS (SELECT 1 FROM memory_events future WHERE future.tenant_id=a.tenant_id "
            "AND future.owner_id=a.owner_id AND future.brain_id=a.brain_id AND future.id=ANY(a.event_ids) "
            "AND future.is_current AND future.sequence>%s) "
            "ORDER BY a.embedding <=> %s::vector NULLS LAST,a.id,a.revision LIMIT %s",
            (*self.params, *scope_params, self.through, _vector(cue), self.sampling["candidate_scan_limit"] + 1),
        ).fetchall()
        if len(rows) > self.sampling["candidate_scan_limit"]:
            self.reasons.add("candidate_scan_limit")
        ranked = [(cognition._lexical_score(cue, row["document"]["statement"]), row)
                  for row in rows[:self.sampling["candidate_scan_limit"]]]
        ranked = [(score, row) for score, row in ranked if score > 0]
        ranked.sort(key=lambda item: (-item[0], -bool(item[1]["document"].get("counterexamples")),
                                      *_key(item[1]["document"])))
        if len(ranked) > self.policy.max_assertions:
            self.reasons.add("candidate_limit")
        rows = [row for _, row in ranked[:self.policy.max_assertions]]
        self.remember(rows)
        self.sampling["candidate_count"] = len(rows)
        return [row["document"] for row in rows]

    def episodes(self):
        rows = self.conn.execute(
            "SELECT document,event_ids FROM memory_episodes WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND conversation_id=%s AND branch_id=%s AND is_current AND NOT invalidated AND NOT deleted "
            "AND document IS NOT NULL AND event_ids && %s::text[] ORDER BY created_at DESC,id LIMIT %s",
            (*self.params, self.target.get("conversation_ref"), self.target.get("branch_ref"),
             list(self.selected_sources), self.policy.max_episodes + 1),
        ).fetchall()
        if len(rows) > self.policy.max_episodes:
            self.reasons.add("episode_limit")
        for row in rows[:self.policy.max_episodes]:
            episode = row["document"]
            ids = set(row["event_ids"]) | set(episode.get("root_event_ids", []))
            ids.update(ref["event_id"] for ref in episode["member_refs"])
            try:
                events = self.source_package(ids)
                if any(events[ref["event_id"]]["revision"] != ref["revision"] for ref in episode["member_refs"]):
                    raise _Incomplete("episode_source_changed")
                if not cognition._scope_allows(self.bind(episode)["scope"], self.scope):
                    raise _Incomplete("episode_scope")
                self.include(events)
                self.selected_episodes[episode["episode_id"]] = episode
            except _Incomplete as error:
                self.omit(str(error), relevant=True)


def select_neighborhood(conn, service, principal, brain, target, *, through_sequence,
                        required_event_refs, policy):
    selector = _Selector(conn, service, principal, brain, target, through_sequence, policy)
    rows = conn.execute(
        "SELECT document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND conversation_id=%s AND branch_id=%s AND sequence<=%s AND is_current AND NOT retired "
        "AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
        "AND document #>> '{observed,delivery_state}' IN ('received','delivered','partially_delivered') "
        "ORDER BY sequence DESC LIMIT %s",
        (*selector.params, target.get("conversation_ref"), target.get("branch_ref"), through_sequence,
         selector.sampling["recent_window_limit"] + 1),
    ).fetchall()
    if len(rows) > selector.sampling["recent_window_limit"]:
        selector.reasons.add("recent_window_limit")
    recent = [row["document"] for row in rows[:selector.sampling["recent_window_limit"]]]
    required = list(required_event_refs) or ([{"event_id": recent[0]["event_id"], "revision": recent[0]["revision"]}]
                                            if recent else [])
    try:
        events = selector.source_package(ref["event_id"] for ref in required)
        if any(events[ref["event_id"]]["revision"] != ref["revision"] for ref in required):
            raise _Incomplete("source_version_mismatch")
        if any(event["scope"]["conversation_id"] != target.get("conversation_ref")
               or event["scope"]["branch_id"] != target.get("branch_ref") for event in events.values()):
            raise _Incomplete("input_scope")
        selector.include(events)
    except _Incomplete as error:
        raise DomainError("source_unavailable", "Required cognitive input is unavailable or exceeds its budget") from error
    cue = "\n".join(event["observed"]["content"].get("text", "") for event in sorted(events.values(), key=lambda item: item["sequence"]))
    if len(cue) > 8192:
        cue = cue[:8192]
        selector.reasons.add("cue_limit")
    for memory in selector.candidates(cue):
        try:
            selector.include(*selector.assertion_package(memory))
        except _Incomplete as error:
            selector.omit(str(error), relevant=True)
    for event in recent:
        try:
            selector.include(selector.source_package([event["event_id"]]))
        except _Incomplete as error:
            selector.omit(str(error))
    selector.episodes()
    context_ids = {event["event_id"] for event in recent if event["event_id"] in selector.selected_sources}
    context_ids.update(ref["event_id"] for episode in selector.selected_episodes.values()
                       for ref in episode["member_refs"])
    clause, scope_params = _scope_clause("a", target)
    rows = conn.execute(
        "SELECT a.document,a.event_ids,a.is_current FROM memory_assertions a WHERE " + _eligible() + clause
        + "AND cardinality(a.event_ids)>0 AND a.event_ids <@ %s::text[] "
        "ORDER BY a.created_at DESC,a.id,a.revision LIMIT %s",
        (*selector.params, *scope_params, sorted(context_ids), policy.max_assertions + 1),
    ).fetchall()
    if len(rows) > policy.max_assertions:
        selector.reasons.add("recent_assertion_limit")
    selector.remember(rows[:policy.max_assertions])
    for row in rows[:policy.max_assertions]:
        if _key(row["document"]) not in selector.selected_memories:
            try:
                selector.include(*selector.assertion_package(row["document"]))
            except _Incomplete as error:
                selector.omit(str(error), relevant=cognition._lexical_score(cue, row["document"]["statement"]) > 0)
    selector.sampling.update(
        source_count=len(selector.selected_sources), assertion_count=len(selector.selected_memories),
        episode_count=len(selector.selected_episodes), limitations=sorted(selector.reasons),
        selected_min_sequence=min((event["sequence"] for event in selector.selected_sources.values()), default=None),
        selected_max_sequence=max((event["sequence"] for event in selector.selected_sources.values()), default=None),
    )
    return Neighborhood(
        tuple(sorted(selector.selected_sources.values(), key=lambda event: event["sequence"])),
        tuple(selector.selected_memories[key] for key in sorted(selector.selected_memories)),
        tuple(selector.selected_episodes[key] for key in sorted(selector.selected_episodes)), selector.sampling,
    )
