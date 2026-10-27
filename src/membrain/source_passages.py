from __future__ import annotations

from . import cognition

SOURCE_PASSAGE_VERSION = "bounded-adjacent-source-passages-v1"


def source_passage_candidates(conn, service, principal, brain, target, seeds, *, through_sequence):
    from .candidates import _eligible, _scope_clause

    scope = service._scope(principal, brain, target)
    seeds = seeds[:4]
    source_ids = list(
        dict.fromkeys(
            ref["event_id"]
            for _, memory in seeds
            for ref in memory["evidence_refs"]
            if ref["relationship"] == "supports"
        )
    )
    truncated = len(source_ids) > 4
    sources = {
        source["event_id"]: source
        for source in service._events(conn, principal, brain, event_ids=source_ids[:4], limit=4)
        if source["sequence"] <= through_sequence
        and cognition._scope_allows(source["scope"], scope)
        and source["observed"]["actor_role"] in {"user", "operator"}
        and source["observed"]["content"]["media_type"] == "text/plain"
    }
    superseded = service._superseded_evidence(conn, principal, brain, list(sources))
    anchors = {}
    for score, memory in seeds:
        for ref in memory["evidence_refs"]:
            if (
                ref["relationship"] != "supports"
                or ref["event_id"] not in sources
                or "span" not in ref
                or not cognition._valid_ref(ref, sources, scope)
                or any(cognition.evidence_overlaps(ref, old) for old in superseded)
            ):
                continue
            source = sources[ref["event_id"]]
            segments = cognition._segments(source)
            for index, segment in enumerate(segments):
                if segment.get("span") != ref["span"]:
                    continue
                for neighbor in (index - 1, index + 1):
                    if not 0 <= neighbor < len(segments) or "span" not in segments[neighbor]:
                        continue
                    span = segments[neighbor]["span"]
                    key = (
                        source["event_id"],
                        source["revision"],
                        source["content_sha256"],
                        span["start"],
                        span["end"],
                    )
                    anchors[key] = max(score * 0.8, anchors.get(key, 0))
    if not anchors:
        return [], truncated
    clause, scope_params = _scope_clause("a", target)
    rows = conn.execute(
        "SELECT a.document,a.event_ids,a.is_current FROM memory_assertions a WHERE "
        + _eligible()
        + clause
        + "AND a.document #>> '{derivation,method}'='direct_capture' "
        + "AND a.event_ids && %s::text[] AND EXISTS ("
        "SELECT 1 FROM unnest(%s::text[],%s::bigint[],%s::text[],%s::bigint[],%s::bigint[]) "
        "wanted(event_id,revision,content_hash,start_pos,end_pos) "
        "JOIN jsonb_array_elements(a.document->'evidence_refs') ref(value) ON "
        "ref.value->>'event_id'=wanted.event_id AND ref.value->>'event_revision'=wanted.revision::text "
        "AND ref.value->>'source_content_sha256'=wanted.content_hash "
        "AND ref.value #>> '{span,start}'=wanted.start_pos::text "
        "AND ref.value #>> '{span,end}'=wanted.end_pos::text) ORDER BY a.id,a.revision LIMIT 33",
        (
            *service._params(principal, brain["id"]),
            *scope_params,
            list(sources),
            *[list(values) for values in zip(*anchors, strict=True)],
        ),
    ).fetchall()
    truncated = truncated or len(rows) > 32
    candidates = []
    for row in rows[:32]:
        memory = service._rebind(row["document"], principal, brain)
        if memory["lifecycle"] not in {"active", "dormant"} or not cognition._scope_allows(
            memory["scope"], scope
        ):
            continue
        scores = []
        for ref in memory["evidence_refs"]:
            span = ref.get("span")
            if (
                ref["relationship"] != "supports"
                or not span
                or not cognition._valid_ref(ref, sources, scope)
                or any(cognition.evidence_overlaps(ref, old) for old in superseded)
            ):
                continue
            key = (
                ref["event_id"],
                ref["event_revision"],
                ref["source_content_sha256"],
                span["start"],
                span["end"],
            )
            if key in anchors:
                scores.append(anchors[key])
        if scores:
            candidates.append((max(scores), row))
    return candidates, truncated
