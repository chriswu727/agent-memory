from .helpers import DomainError


def _scope(principal, row):
    return (principal.tenant_id, principal.principal_id, row["brain_id"],
            row["conversation_id"], row["branch_id"])


def require_new_scope(conn, principal, brain_id, conversation_id, branch_id):
    pending = conn.execute(
        "SELECT turn_id FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND conversation_id=%s AND branch_id=%s AND stage NOT IN ('completed','failed') LIMIT 1",
        (principal.tenant_id, principal.principal_id, brain_id, conversation_id, branch_id),
    ).fetchone()
    if pending is not None:
        raise DomainError("scope_conflict", "Another unfinished turn owns this conversation branch.")


def reserve_existing_scope(conn, principal, row):
    if row["scope_reserved"]:
        return
    reserved = conn.execute(
        "SELECT turn_id FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND conversation_id=%s AND branch_id=%s AND scope_reserved LIMIT 1",
        _scope(principal, row),
    ).fetchone()
    oldest = conn.execute(
        "SELECT turn_id FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND conversation_id=%s AND branch_id=%s AND stage NOT IN ('completed','failed') "
        "ORDER BY created_at,turn_id LIMIT 1",
        _scope(principal, row),
    ).fetchone()
    if reserved is not None or oldest is None or oldest["turn_id"] != row["turn_id"]:
        raise DomainError("scope_conflict", "Another unfinished turn owns this conversation branch.")
    conn.execute(
        "UPDATE memory_runtime_turns SET scope_reserved=true "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s",
        (*_scope(principal, row)[:3], row["turn_id"]),
    )
