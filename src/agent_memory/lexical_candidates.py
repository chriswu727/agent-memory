from . import cognition

MAX_QUERY_TERMS = 128
MAX_TERM_LENGTH = 256


def lexical_rows(conn, params, target, text, *, limit, through_sequence=None):
    from .candidates import _eligible, _scope_clause
    from .service import _vector

    clause, scope_params = _scope_clause("a", target)
    where = _eligible() + clause
    where += "AND a.embedding_model='lexical-feature-hash' AND a.embedding_version='v1-d256-blake2s' "
    values = (*params, *scope_params)
    if through_sequence is not None:
        where += (
            "AND NOT EXISTS (SELECT 1 FROM memory_events future WHERE future.tenant_id=a.tenant_id "
            "AND future.owner_id=a.owner_id AND future.brain_id=a.brain_id AND future.id=ANY(a.event_ids) "
            "AND future.is_current AND future.sequence>%s) "
        )
        values += (through_sequence,)
    select = "SELECT a.document,a.event_ids,a.is_current FROM memory_assertions a WHERE " + where
    order = "ORDER BY a.embedding <=> %s::vector NULLS LAST,a.id,a.revision LIMIT %s"
    vector = _vector(text)
    hashed = conn.execute(select + order, (*values, vector, limit + 1)).fetchall()
    words = sorted(cognition._terms(text)[0])
    terms = [word for word in words if len(word) <= MAX_TERM_LENGTH][:MAX_QUERY_TERMS]
    truncated = len(hashed) > limit or len(terms) != len(words)
    exact = []
    if terms:
        expression = " | ".join("'" + word + "'" for word in terms)
        exact = conn.execute(
            select + "AND to_tsvector('simple',a.search_text) @@ to_tsquery('simple',%s) " + order,
            (*values, expression, vector, limit + 1),
        ).fetchall()
        truncated = truncated or len(exact) > limit
    unique = {}
    for row in exact[:limit] + hashed[:limit]:
        memory = row["document"]
        unique.setdefault((memory["memory_id"], memory["revision"]), row)
    return list(unique.values()), truncated
