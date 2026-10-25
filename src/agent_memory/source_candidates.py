from . import cognition

SOURCE_ROUTE_VERSION = "exact-word-hash-and-source-span-v3"


def _matching_references(memory, events, scope, query, superseded):
    matches = []
    if memory["lifecycle"] not in {"active", "dormant"} or not cognition._scope_allows(memory["scope"], scope):
        return matches
    for reference in memory["evidence_refs"]:
        if (reference["relationship"] != "supports"
                or not cognition._valid_ref(reference, events, scope)
                or any(cognition.evidence_overlaps(reference, old) for old in superseded)):
            continue
        event = events[reference["event_id"]]
        text = cognition._content_text(event["observed"]["content"])
        if "span" in reference:
            text = text[reference["span"]["start"]:reference["span"]["end"]]
        score = cognition._lexical_score(query, text)
        if score > 0:
            matches.append((score, reference))
    return matches


def source_candidates(conn, service, principal, brain, target, query, seeds, *,
                      source_limit, scan_limit, through_sequence, excluded_versions=(), independent_of_event_ids=()):
    from .candidates import _eligible, _scope_clause
    from .lexical_candidates import historical_support_filter

    scope = service._scope(principal, brain, target)
    support_clause, support_params = historical_support_filter(independent_of_event_ids)
    event_ids = list(dict.fromkeys(reference["event_id"] for memory in seeds
                                  for reference in memory["evidence_refs"]
                                  if reference["relationship"] == "supports"))
    if excluded_versions and event_ids:
        clause, scope_params = _scope_clause("a", target)
        available = conn.execute(
            "SELECT DISTINCT source.event_id FROM memory_assertions a "
            "CROSS JOIN LATERAL unnest(a.event_ids) AS source(event_id) WHERE "
            + _eligible() + clause + support_clause
            + "AND a.document->>'epistemic_status'='derived_inference' "
            "AND a.document->>'lifecycle' IN ('active','dormant') "
            "AND a.event_ids && %s::text[] AND source.event_id=ANY(%s::text[]) "
            "AND (a.id,a.revision) NOT IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
            "AND NOT EXISTS (SELECT 1 FROM memory_events future WHERE future.tenant_id=a.tenant_id "
            "AND future.owner_id=a.owner_id AND future.brain_id=a.brain_id AND future.id=ANY(a.event_ids) "
            "AND future.is_current AND future.sequence>%s) ORDER BY source.event_id LIMIT %s",
            (*service._params(principal, brain["id"]), *scope_params, *support_params, event_ids, event_ids,
             [key[0] for key in excluded_versions], [key[1] for key in excluded_versions],
             through_sequence, source_limit + 1),
        ).fetchall()
        event_ids = [row["event_id"] for row in available]
    truncated = len(event_ids) > source_limit
    event_ids = event_ids[:source_limit]
    if not event_ids:
        return [], truncated
    events = {event["event_id"]: event for event in service._events(
        conn, principal, brain, event_ids=event_ids, limit=source_limit)
        if event["sequence"] <= through_sequence and cognition._scope_allows(event["scope"], scope)}
    superseded = service._superseded_evidence(conn, principal, brain, event_ids)
    seed_refs = [reference for memory in seeds
                 for _, reference in _matching_references(memory, events, scope, query, superseded)]
    if not seed_refs:
        return [], truncated
    clause, scope_params = _scope_clause("a", target)
    exclusions, excluded_params = "", ()
    if excluded_versions:
        exclusions = "AND (a.id,a.revision) NOT IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
        excluded_params = ([key[0] for key in excluded_versions], [key[1] for key in excluded_versions])
    rows = conn.execute(
        "SELECT a.document,a.event_ids,a.is_current FROM memory_assertions a WHERE "
        + _eligible() + clause + support_clause + exclusions
        + "AND a.document->>'epistemic_status'='derived_inference' "
        "AND a.document->>'lifecycle' IN ('active','dormant') "
        "AND a.event_ids && %s::text[] "
        "AND NOT EXISTS (SELECT 1 FROM memory_events future WHERE future.tenant_id=a.tenant_id "
        "AND future.owner_id=a.owner_id AND future.brain_id=a.brain_id AND future.id=ANY(a.event_ids) "
        "AND future.is_current AND future.sequence>%s) "
        "ORDER BY a.id,a.revision LIMIT %s",
        (*service._params(principal, brain["id"]), *scope_params, *support_params, *excluded_params,
         sorted({reference["event_id"] for reference in seed_refs}), through_sequence, scan_limit + 1),
    ).fetchall()
    truncated = truncated or len(rows) > scan_limit
    ranked = []
    for row in rows[:scan_limit]:
        matches = _matching_references(service._rebind(row["document"], principal, brain),
                                       events, scope, query, superseded)
        scores = [score for score, reference in matches
                  if any(cognition.evidence_overlaps(reference, seed) for seed in seed_refs)]
        if scores:
            ranked.append((max(scores), row))
    ranked.sort(key=lambda item: (-item[0], item[1]["document"]["memory_id"], item[1]["document"]["revision"]))
    return ranked, truncated
