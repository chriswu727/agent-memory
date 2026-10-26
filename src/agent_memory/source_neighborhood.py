from __future__ import annotations

import math

from . import cognition
from .contracts import validate
from .helpers import DomainError

SOURCE_NEIGHBORHOOD_VERSION = "bounded-source-neighborhood-v1"


def _superseded_references(conn, params, event_ids, limit):
    rows = conn.execute(
        "SELECT reference.value AS reference FROM memory_events e CROSS JOIN LATERAL "
        "jsonb_array_elements(CASE WHEN jsonb_typeof(e.document #> "
        "'{observed,content,structured,superseded_evidence_refs}')='array' THEN e.document #> "
        "'{observed,content,structured,superseded_evidence_refs}' ELSE '[]'::jsonb END) reference(value) "
        "WHERE e.tenant_id=%s AND e.owner_id=%s AND e.brain_id=%s AND e.is_current "
        "AND NOT e.deleted AND NOT e.suppressed AND e.producer_ref IN ('memory-service','archive-import') "
        "AND e.document #>> '{observed,event_kind}'='source_correction' "
        "AND reference.value->>'event_id'=ANY(%s::text[]) LIMIT %s",
        (*params, list(event_ids), limit + 1),
    ).fetchall()
    if len(rows) > limit:
        return [], True
    references = []
    for row in rows:
        try:
            validate("EvidenceRef", row["reference"])
        except DomainError:
            continue
        references.append(row["reference"])
    return references, False


def source_neighborhood_candidates(conn, service, principal, brain, target, seeds, *,
                                   through_sequence, seed_limit=8, event_limit=16, scan_limit=64,
                                   excluded_versions=(), independent_of_event_ids=()):
    from .candidates import _eligible, _scope_clause
    from .lexical_candidates import historical_support_filter

    for value, maximum in ((seed_limit, 32), (event_limit, 64), (scan_limit, 512)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("Source neighborhood limits must be positive bounded integers")
    if type(through_sequence) is not int or through_sequence < 0:
        raise ValueError("Source neighborhood requires an explicit sequence cutoff")
    scores, truncated = {}, False
    for score, memory in seeds:
        if type(score) not in (int, float) or not math.isfinite(score) or score <= 0:
            raise ValueError("Source neighborhood seeds require finite positive relevance")
        key = (memory["memory_id"], memory["revision"])
        if key not in scores and len(scores) == seed_limit:
            truncated = True
            break
        scores[key] = max(score, scores.get(key, 0))
    if not scores:
        return [], truncated
    params = service._params(principal, brain["id"])
    scope = service._scope(principal, brain, target)
    through_sequence = min(through_sequence, brain["durable_sequence"])
    excluded_events = set(independent_of_event_ids)
    excluded = set(excluded_versions)
    clause, scope_params = _scope_clause("a", target)
    support_clause, support_params = historical_support_filter(excluded_events)
    eligible = (_eligible() + clause + support_clause
                + "AND a.document->>'lifecycle' IN ('active','dormant') "
                "AND NOT EXISTS (SELECT 1 FROM memory_events future WHERE future.tenant_id=a.tenant_id "
                "AND future.owner_id=a.owner_id AND future.brain_id=a.brain_id AND future.id=ANY(a.event_ids) "
                "AND future.is_current AND future.sequence>%s) ")
    eligible_params = (*params, *scope_params, *support_params, through_sequence)
    rows = conn.execute(
        "SELECT a.document FROM memory_assertions a WHERE " + eligible
        + "AND (a.id,a.revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) LIMIT %s",
        (*eligible_params, [key[0] for key in scores], [key[1] for key in scores], seed_limit),
    ).fetchall()
    originals = {(row["document"]["memory_id"], row["document"]["revision"]):
                 service._rebind(row["document"], principal, brain) for row in rows}
    source_scores, source_refs = {}, {}
    for key, score in sorted(scores.items(), key=lambda item: -item[1]):
        memory = originals.get(key)
        if memory is None or key in excluded or not cognition._scope_allows(memory["scope"], scope):
            continue
        for ref in memory["evidence_refs"]:
            identifier = ref["event_id"]
            if ref["relationship"] != "supports" or identifier in excluded_events:
                continue
            if identifier not in source_scores and len(source_scores) == event_limit:
                truncated = True
                continue
            source_scores[identifier] = max(score, source_scores.get(identifier, 0))
            source_refs.setdefault(identifier, []).append((score, ref))
    if not source_scores:
        return [], truncated
    sources = {event["event_id"]: event for event in service._events(
        conn, principal, brain, event_ids=source_scores, limit=event_limit)
        if event["sequence"] <= through_sequence and event["scope"]["conversation_id"] is not None}
    superseded, overflow = _superseded_references(conn, params, source_scores, scan_limit)
    if overflow:
        return [], True

    def valid(reference, events):
        return (reference["relationship"] == "supports" and reference["event_id"] not in excluded_events
                and cognition._valid_ref(reference, events, scope)
                and not any(cognition.evidence_overlaps(reference, old) for old in superseded))

    matched = {}
    for identifier, refs in source_refs.items():
        supported = [score for score, ref in refs if valid(ref, sources)]
        if supported:
            matched[identifier] = max(supported)
    if not matched:
        return [], truncated
    # Validate the nearest raw current records after selection so hidden records remain adjacency barriers.
    neighbors = conn.execute(
        "SELECT seed.id AS seed_id,near.* FROM memory_events seed CROSS JOIN LATERAL ("
        "(SELECT e.* FROM memory_events e WHERE e.tenant_id=seed.tenant_id AND e.owner_id=seed.owner_id "
        "AND e.brain_id=seed.brain_id AND e.conversation_id=seed.conversation_id "
        "AND e.branch_id IS NOT DISTINCT FROM seed.branch_id AND e.is_current AND e.sequence<seed.sequence "
        "ORDER BY e.sequence DESC LIMIT 1) UNION ALL "
        "(SELECT e.* FROM memory_events e WHERE e.tenant_id=seed.tenant_id AND e.owner_id=seed.owner_id "
        "AND e.brain_id=seed.brain_id AND e.conversation_id=seed.conversation_id "
        "AND e.branch_id IS NOT DISTINCT FROM seed.branch_id AND e.is_current AND e.sequence>seed.sequence "
        "ORDER BY e.sequence LIMIT 1)) near "
        "WHERE seed.tenant_id=%s AND seed.owner_id=%s AND seed.brain_id=%s AND seed.is_current "
        "AND seed.id=ANY(%s::text[]) AND seed.conversation_id IS NOT NULL "
        "ORDER BY seed.sequence,near.sequence LIMIT %s",
        (*params, list(matched), 2 * event_limit),
    ).fetchall()
    events, neighbor_scores = {}, {}
    for row in neighbors:
        if (row["document"] is None or row["deleted"] or row["suppressed"] or row["retired"]
                or row["sequence"] > through_sequence or row["id"] in excluded_events):
            continue
        event = service._rebind(row["document"], principal, brain)
        seed = sources[row["seed_id"]]
        if (event["event_id"] != row["id"] or event["revision"] != row["revision"]
                or event["sequence"] != row["sequence"] or event["content_sha256"] != row["content_hash"]
                or event["scope"]["conversation_id"] != row["conversation_id"]
                or event["scope"]["branch_id"] != row["branch_id"]
                or event["scope"]["conversation_id"] != seed["scope"]["conversation_id"]
                or event["scope"]["branch_id"] != seed["scope"]["branch_id"]
                or not cognition._scope_allows(event["scope"], scope)):
            continue
        events[row["id"]] = event
        neighbor_scores[row["id"]] = max(matched[row["seed_id"]], neighbor_scores.get(row["id"], 0))
    if not events:
        return [], truncated
    truncated = truncated or len(events) > event_limit
    events = {identifier: events[identifier] for identifier in sorted(
        events, key=lambda identifier: (-neighbor_scores[identifier], identifier))[:event_limit]}
    superseded, overflow = _superseded_references(conn, params, events, scan_limit)
    if overflow:
        return [], True
    exclusions = excluded | scores.keys()
    rows = conn.execute(
        "SELECT a.document,a.event_ids,a.is_current FROM memory_assertions a CROSS JOIN LATERAL ("
        "SELECT max(hit.score) AS relevance FROM unnest(%s::text[],%s::bigint[],%s::text[],%s::float8[]) "
        "hit(event_id,revision,content_hash,score) JOIN jsonb_array_elements(a.document->'evidence_refs') ref(value) "
        "ON ref.value->>'event_id'=hit.event_id AND ref.value->>'event_revision'=hit.revision::text "
        "AND ref.value->>'source_content_sha256'=hit.content_hash AND ref.value->>'relationship'='supports'"
        ") matched WHERE " + eligible
        + "AND a.event_ids && %s::text[] "
        "AND matched.relevance IS NOT NULL "
        "AND (a.id,a.revision) NOT IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
        "ORDER BY matched.relevance DESC,a.id,a.revision LIMIT %s",
        (list(events), [event["revision"] for event in events.values()],
         [event["content_sha256"] for event in events.values()], [neighbor_scores[identifier] for identifier in events],
         *eligible_params, list(events), [key[0] for key in exclusions], [key[1] for key in exclusions], scan_limit + 1),
    ).fetchall()
    truncated = truncated or len(rows) > scan_limit
    ranked = []
    for row in rows[:scan_limit]:
        memory = service._rebind(row["document"], principal, brain)
        if not cognition._scope_allows(memory["scope"], scope):
            continue
        relevant = [neighbor_scores[ref["event_id"]] for ref in memory["evidence_refs"]
                    if ref["event_id"] in events and valid(ref, events)]
        if relevant:
            ranked.append((max(relevant), row))
    ranked.sort(key=lambda item: (-item[0], item[1]["document"]["memory_id"], item[1]["document"]["revision"]))
    return ranked, truncated
