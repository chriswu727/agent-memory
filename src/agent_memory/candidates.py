from __future__ import annotations

import math

from . import cognition
from .context_views import assistant_speech
from .dynamics import access_state, rank_weight
from .embeddings import EmbeddingResult
from .helpers import DomainError, digest, now
from .retrieval import fuse_rankings, resolve_dependency_closure


def _scope_clause(alias, target):
    return (
        f"AND ({alias}.scope_visibility<>'conversation' "
        f"OR {alias}.scope_conversation_id IS NOT DISTINCT FROM %s) "
        f"AND ({alias}.scope_branch_id IS NULL "
        f"OR {alias}.scope_conversation_id IS DISTINCT FROM %s "
        f"OR {alias}.scope_branch_id IS NOT DISTINCT FROM %s) ",
        (target.get("conversation_ref"), target.get("conversation_ref"), target.get("branch_ref")),
    )


def _eligible(alias="a", *, current=True):
    return (f"{alias}.tenant_id=%s AND {alias}.owner_id=%s AND {alias}.brain_id=%s "
            + (f"AND {alias}.is_current " if current else "")
            + f"AND NOT {alias}.invalidated AND NOT {alias}.deleted AND NOT {alias}.suppressed "
            f"AND {alias}.document IS NOT NULL ")


def merge_belief_candidates(memories, candidate_state, package, *, selected=False):
    from .belief_store import relation_versions

    known = {(memory["memory_id"], memory["revision"]): memory for memory in memories}
    known.update({(memory["memory_id"], memory["revision"]): memory for memory in package["memories"]})
    endpoints = set().union(*(relation_versions(relation) for relation in package["relations"]))
    candidate_state["valid_dependency_versions"] = set(candidate_state["valid_dependency_versions"]) | known.keys()
    if not selected:
        candidate_state["candidate_versions"] = list(set(candidate_state["candidate_versions"]) | endpoints)
        for key in endpoints:
            candidate_state["rank_signals"].setdefault(key, [{
                "method": "association", "score": 1 / 61,
                "meaning": "ranking_only_not_probability", "model_or_index_version": "belief-neighborhood-v1",
            }])
    return list(known.values())


def _direct_candidates(rankings, versions, lexical_scores, *, limit):
    ranked = fuse_rankings(rankings, index_versions=versions, limit=512)
    maximum = max(lexical_scores.values(), default=0.0)
    for item in ranked:
        key = (item["memory_id"], item["revision"])
        for signal in item["rank_signals"]:
            if signal["method"] == "keyword":
                signal["score"] *= lexical_scores.get(key, 0.0) / maximum if maximum else 0.0
        item["score"] = sum(signal["score"] for signal in item["rank_signals"])
    ranked.sort(key=lambda item: (-item["score"], item["memory_id"], item["revision"]))
    return ranked[:limit]


def collect_candidates(conn, service, principal, brain, target, query, recall_plan=None,
                       query_embedding=None, *, access_at=None):
    params = service._params(principal, brain["id"])
    scope_sql, scope_params = _scope_clause("a", target)
    candidate_limit = min(128, max(4, query["budget"]["max_context_tokens"] // 512))
    scan_limit = min(512, candidate_limit * 4)
    text = " ".join([query["query"], *query.get("additional_cues", [])])
    documents, rankings, versions = {}, {}, {}
    truncated = False

    def remember(rows, *, dependencies=False):
        result = []
        for row in rows:
            document = service._rebind(row["document"], principal, brain)
            document["scope"]["visibility"] = row["document"]["scope"]["visibility"]
            for old_path, path in zip(row["document"]["disclosure_basis"]["support_paths"],
                                      document["disclosure_basis"]["support_paths"], strict=True):
                path["permitted_scope"]["visibility"] = old_path["permitted_scope"]["visibility"]
            if dependencies and not row["is_current"]:
                document["lifecycle"] = "superseded"
            key = (document["memory_id"], document["revision"])
            documents[key] = document
            result.append(key)
        return result

    if recall_plan is not None:
        requested = [(ref["memory_id"], ref["revision"]) for ref in recall_plan["memory_versions"]]
        rows = conn.execute(
            "SELECT a.document FROM memory_assertions a WHERE " + _eligible() + scope_sql
            + "AND (a.id,a.revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (*params, *scope_params, [key[0] for key in requested], [key[1] for key in requested]),
        ).fetchall()
        remember(rows)
        roots = [key for key in requested if key in documents]
        signals = {}
    else:
        from .lexical_candidates import LexicalScorer, lexical_rows
        from .source_candidates import SOURCE_ROUTE_VERSION, source_candidates

        rows, truncated = lexical_rows(conn, params, target, text, limit=scan_limit)
        if not cognition.speech_requested({**query, "query": text}):
            rows = [row for row in rows if not assistant_speech(row["document"])]
        score_text = LexicalScorer(text, [row["document"]["statement"] for row in rows])
        lexical = [(score_text(text, row["document"]["statement"]), row)
                   for row in rows]
        lexical = [(score, row) for score, row in lexical if score > 0]
        lexical.sort(key=lambda value: (-value[0], value[1]["document"]["memory_id"]))
        bridged, source_truncated = source_candidates(
            conn, service, principal, brain, target, text,
            [service._rebind(row["document"], principal, brain) for _, row in lexical[:candidate_limit]],
            source_limit=min(64, candidate_limit), scan_limit=scan_limit,
            through_sequence=brain["durable_sequence"],
            score_text=score_text,
        )
        truncated = truncated or source_truncated
        lexical.extend(bridged)
        lexical.sort(key=lambda value: (-value[0], value[1]["document"]["memory_id"]))
        rankings["keyword"] = remember([row for _, row in lexical])
        lexical_scores = {}
        for score, row in lexical:
            memory = row["document"]
            key = (memory["memory_id"], memory["revision"])
            lexical_scores[key] = max(score, lexical_scores.get(key, 0.0))
        versions["keyword"] = SOURCE_ROUTE_VERSION
        if query_embedding is not None:
            if (not isinstance(query_embedding, EmbeddingResult) or len(query_embedding.vectors) != 1
                    or len(query_embedding.vectors[0]) != query_embedding.space.dimensions
                    or not any(query_embedding.vectors[0])
                    or any(type(value) not in (float, int) or not math.isfinite(value)
                           for value in query_embedding.vectors[0])):
                raise DomainError("validation_failed", "A query embedding must contain one finite vector in its declared space.")
            space = query_embedding.space
            if service.embedding_provider and service.embedding_provider.space != space:
                raise DomainError("validation_failed", "Query embedding space differs from the configured space.")
            vector = "[" + ",".join(str(value) for value in query_embedding.vectors[0]) + "]"
            rows = conn.execute(
                "SELECT a.document,v.content_hash,(v.embedding <=> %s::vector) AS distance "
                "FROM memory_assertions a JOIN memory_embeddings v ON v.tenant_id=a.tenant_id "
                "AND v.owner_id=a.owner_id AND v.brain_id=a.brain_id AND v.memory_id=a.id "
                "AND v.memory_revision=a.revision WHERE " + _eligible() + scope_sql
                + "AND v.model=%s AND v.model_version=%s AND v.dimensions=%s "
                "ORDER BY distance,a.id LIMIT %s",
                (vector, *params, *scope_params, space.model, space.model_version, space.dimensions, scan_limit + 1),
            ).fetchall()
            truncated = truncated or len(rows) > scan_limit
            rows = [row for row in rows[:scan_limit] if row["distance"] is not None and math.isfinite(row["distance"])
                    and row["distance"] < 1 and row["content_hash"] == digest(row["document"]["statement"])]
            rankings["vector"] = remember(rows)
            versions["vector"] = "embedding-space:" + space.fingerprint
        direct_ranked = _direct_candidates(rankings, versions, lexical_scores, limit=512)
        seeds = direct_ranked[:candidate_limit]
        seed_ids = [item["memory_id"] for item in seeds]
        seed_revisions = [item["revision"] for item in seeds]
        seed_scores = [item["score"] for item in seeds]
        neighbors = {}

        def expand(rows, version):
            keys = remember(rows)
            for key, row in zip(keys, rows, strict=True):
                score = row["relevance"] * 0.5
                if score > neighbors.get(key, (0.0, ""))[0]:
                    neighbors[key] = (score, version)

        if seeds:
            from .source_passages import SOURCE_PASSAGE_VERSION, source_passage_candidates

            passages, passage_truncated = source_passage_candidates(
                conn, service, principal, brain, target,
                [(item["score"], documents[(item["memory_id"], item["revision"])]) for item in seeds],
                through_sequence=brain["durable_sequence"],
            )
            truncated = truncated or passage_truncated
            for key, (score, _) in zip(remember([row for _, row in passages]), passages, strict=True):
                neighbors[key] = (score, SOURCE_PASSAGE_VERSION)
            association_rows = conn.execute(
                "WITH seeds AS (SELECT * FROM unnest(%s::text[],%s::bigint[],%s::double precision[]) AS s(id,revision,score)) "
                "SELECT a.id,a.revision,a.document,max(s.score) AS relevance FROM memory_associations e JOIN seeds s "
                "ON (e.source_id=s.id AND e.source_revision=s.revision) "
                "OR (e.target_id=s.id AND e.target_revision=s.revision) "
                "JOIN memory_assertions a ON a.tenant_id=e.tenant_id AND a.owner_id=e.owner_id AND a.brain_id=e.brain_id "
                "AND ((e.source_id=s.id AND e.source_revision=s.revision AND a.id=e.target_id AND a.revision=e.target_revision) "
                "OR (e.target_id=s.id AND e.target_revision=s.revision AND a.id=e.source_id AND a.revision=e.source_revision)) "
                "WHERE " + _eligible() + scope_sql + "AND NOT e.invalidated "
                "AND NOT EXISTS (SELECT 1 FROM seeds own WHERE own.id=a.id AND own.revision=a.revision) "
                "GROUP BY a.id,a.revision,a.document ORDER BY relevance DESC,a.id,a.revision LIMIT %s",
                (seed_ids, seed_revisions, seed_scores, *params, *scope_params, candidate_limit + 1),
            ).fetchall()
            episode_rows = conn.execute(
                "WITH seeds AS (SELECT * FROM unnest(%s::text[],%s::bigint[],%s::double precision[]) AS s(id,revision,score)), "
                "matched AS MATERIALIZED (SELECT e.id,e.revision,max(s.score) AS score FROM memory_episode_members seed "
                "JOIN seeds s ON seed.memory_id=s.id AND seed.memory_revision=s.revision "
                "JOIN memory_episodes e ON e.tenant_id=seed.tenant_id AND e.owner_id=seed.owner_id AND e.brain_id=seed.brain_id "
                "AND e.id=seed.episode_id AND e.revision=seed.episode_revision "
                "WHERE seed.tenant_id=%s AND seed.owner_id=%s AND seed.brain_id=%s "
                "AND e.is_current AND NOT e.invalidated AND NOT e.deleted AND e.document IS NOT NULL "
                "AND (e.conversation_id IS DISTINCT FROM %s OR e.branch_id IS NOT DISTINCT FROM %s) "
                "GROUP BY e.id,e.revision) "
                "SELECT a.id,a.revision,a.document,max(matched.score) AS relevance FROM matched "
                "JOIN memory_episode_members member ON member.tenant_id=%s AND member.owner_id=%s "
                "AND member.brain_id=%s AND member.episode_id=matched.id AND member.episode_revision=matched.revision "
                "JOIN memory_assertions a ON a.tenant_id=member.tenant_id AND a.owner_id=member.owner_id "
                "AND a.brain_id=member.brain_id AND a.id=member.memory_id AND a.revision=member.memory_revision "
                "WHERE " + _eligible() + scope_sql
                + "AND NOT EXISTS (SELECT 1 FROM seeds own WHERE own.id=a.id AND own.revision=a.revision) "
                "GROUP BY a.id,a.revision,a.document ORDER BY relevance DESC,a.id,a.revision LIMIT %s",
                (seed_ids, seed_revisions, seed_scores, *params, target.get("conversation_ref"),
                 target.get("branch_ref"), *params, *params, *scope_params, candidate_limit + 1),
            ).fetchall()
            truncated = truncated or len(association_rows) > candidate_limit or len(episode_rows) > candidate_limit
            expand(association_rows[:candidate_limit] + episode_rows[:candidate_limit], "bounded-seed-neighborhood-v2")
            if query.get("intent") == "continue_conversation":
                from .source_neighborhood import (
                    SOURCE_NEIGHBORHOOD_VERSION,
                    source_neighborhood_candidates,
                )

                adjacent, adjacent_truncated = source_neighborhood_candidates(
                    conn, service, principal, brain, target,
                    [(item["score"], documents[(item["memory_id"], item["revision"])]) for item in seeds],
                    through_sequence=brain["durable_sequence"],
                )
                truncated = truncated or adjacent_truncated
                expand([{**row, "relevance": score} for score, row in adjacent], SOURCE_NEIGHBORHOOD_VERSION)
        combined = {(item["memory_id"], item["revision"]): item for item in direct_ranked}
        for key, (score, version) in neighbors.items():
            item = combined.setdefault(key, {"memory_id": key[0], "revision": key[1], "score": 0.0, "rank_signals": []})
            item["score"] += score
            item["rank_signals"].append({"method": "association", "score": score,
                                        "meaning": "ranking_only_not_probability", "model_or_index_version": version})
        ranked = list(combined.values())
        if not cognition.speech_requested({**query, "query": text}):
            ranked = [item for item in ranked if not assistant_speech(documents[(item["memory_id"], item["revision"])])]
        usage_rows = conn.execute(
            "SELECT memory_id,memory_revision,external_uses,successes,failures,last_external_use_at,last_reinforcement_at "
            "FROM memory_access_state WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND (memory_id,memory_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (*params, [item["memory_id"] for item in ranked], [item["revision"] for item in ranked]),
        ).fetchall()
        usage = {(row["memory_id"], row["memory_revision"]): row for row in usage_rows}
        timestamp = now() if access_at is None else access_at
        direct = query.get("intent") in {"exact_quote", "episode", "timeline", "cue_recovery"}
        for item in ranked:
            key = (item["memory_id"], item["revision"])
            weight = rank_weight(access_state(documents[key], usage.get(key), at=timestamp), direct=direct)
            item["score"] *= weight
            for signal in item["rank_signals"]:
                signal["score"] *= weight
        ranked.sort(key=lambda item: (-item["score"], item["memory_id"], item["revision"]))
        truncated = truncated or len(ranked) > candidate_limit
        ranked = ranked[:candidate_limit]
        roots = [(item["memory_id"], item["revision"]) for item in ranked]
        signals = {(item["memory_id"], item["revision"]): item["rank_signals"] for item in ranked}

    def fetch_dependencies(keys):
        rows = conn.execute(
            "SELECT a.document,a.is_current FROM memory_assertions a WHERE " + _eligible(current=False) + scope_sql
            + "AND (a.id,a.revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (*params, *scope_params, [key[0] for key in keys], [key[1] for key in keys]),
        ).fetchall()
        return [documents[key] for key in remember(rows, dependencies=True)]

    closure = resolve_dependency_closure([documents[key] for key in roots], fetch_dependencies, max_nodes=256, max_depth=8)
    root_set = set(roots)
    dependencies = {(memory["memory_id"], memory["revision"]) for memory in closure.memories} - root_set
    return list(closure.memories), {
        "candidate_versions": roots, "rank_signals": signals, "valid_dependency_versions": dependencies,
        "candidate_truncated": truncated,
        "source_truncated": closure.truncated or bool(closure.missing_versions) or bool(closure.cycle_versions),
    }
