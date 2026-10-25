from .helpers import DomainError, digest
from .turns import _identifier, _integer

DEFAULT_POLICY = {"window_seconds": 3600, "max_model_calls": 100, "max_input_bytes": 2_000_000}
POLICY_LIMITS = {"window_seconds": (1, 2_592_000), "max_model_calls": (0, 1_000_000),
                 "max_input_bytes": (0, 1_000_000_000_000)}


def validate_change(request_id, expected_revision, policy):
    _identifier(request_id)
    _integer(expected_revision, 0, 2**63 - 2)
    if not isinstance(policy, dict) or set(policy) != set(DEFAULT_POLICY):
        raise DomainError("validation_failed", "Provide the complete cognitive budget policy.")
    for field, (minimum, maximum) in POLICY_LIMITS.items():
        _integer(policy[field], minimum, maximum)


def _params(principal, brain_id):
    return principal.tenant_id, principal.principal_id, brain_id


def _configuration(row):
    return {"revision": row["revision"], "is_explicit": row["is_explicit"],
            "policy": {key: row[key] for key in DEFAULT_POLICY}}


def _current(conn, principal, brain_id):
    row = conn.execute(
        "SELECT revision,is_explicit,window_seconds,max_model_calls,max_input_bytes "
        "FROM memory_cognitive_budgets WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
        _params(principal, brain_id),
    ).fetchone()
    return row if row is not None else {**DEFAULT_POLICY, "revision": 0, "is_explicit": False}


def window_usage(conn, principal, brain_id, window_seconds):
    row = conn.execute(
        "SELECT count(*) AS calls,coalesce(sum(input_bytes),0) AS bytes FROM memory_cognitive_operations "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND created_at>clock_timestamp()-make_interval(secs=>%s)",
        (*_params(principal, brain_id), window_seconds),
    ).fetchone()
    return {"model_calls": row["calls"], "input_bytes": int(row["bytes"])}


def cognitive_budget(conn, principal, brain_id):
    row = _current(conn, principal, brain_id)
    usage = window_usage(conn, principal, brain_id, row["window_seconds"])
    return {**_configuration(row), "scope": "cognition_and_embeddings",
            "basis": "retained_cognitive_operation_reservations", "used": usage,
            "remaining": {key: max(0, row["max_" + key] - value) for key, value in usage.items()}}


def _receipt(row):
    return {"request_id": row["request_id"], "expected_revision": row["expected_revision"],
            "configuration": _configuration({**row, "is_explicit": True})}


def configure_cognitive_budget(conn, principal, brain_id, *, request_id, expected_revision, policy):
    validate_change(request_id, expected_revision, policy)
    fingerprint = digest({"expected_revision": expected_revision, "policy": policy})
    params = _params(principal, brain_id)
    prior = conn.execute(
        "SELECT * FROM memory_cognitive_budget_changes WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND request_id=%s", (*params, request_id),
    ).fetchone()
    if prior is not None:
        if prior["request_fingerprint"] != fingerprint:
            raise DomainError("idempotency_conflict", "Budget request identifier already binds another change.")
        return _receipt(prior)
    current = _current(conn, principal, brain_id)
    if current["revision"] != expected_revision:
        raise DomainError("version_conflict", "Cognitive budget changed. Read its current revision before updating.")
    values = tuple(policy[key] for key in DEFAULT_POLICY)
    conn.execute(
        "INSERT INTO memory_cognitive_budgets(tenant_id,owner_id,brain_id,window_seconds,max_model_calls,"
        "max_input_bytes,revision,is_explicit) VALUES(%s,%s,%s,%s,%s,%s,%s,true) "
        "ON CONFLICT (tenant_id,brain_id) DO UPDATE SET window_seconds=excluded.window_seconds,"
        "max_model_calls=excluded.max_model_calls,max_input_bytes=excluded.max_input_bytes,"
        "revision=excluded.revision,is_explicit=true",
        (*params, *values, expected_revision + 1),
    )
    receipt = conn.execute(
        "INSERT INTO memory_cognitive_budget_changes(tenant_id,owner_id,brain_id,request_id,request_fingerprint,"
        "expected_revision,revision,window_seconds,max_model_calls,max_input_bytes) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (*params, request_id, fingerprint, expected_revision, expected_revision + 1, *values),
    ).fetchone()
    return _receipt(receipt)


def reservation_policy(conn, principal, brain_id, *, window_seconds, max_model_calls, max_input_bytes):
    offered = {"window_seconds": window_seconds, "max_model_calls": max_model_calls,
               "max_input_bytes": max_input_bytes}
    conn.execute(
        "INSERT INTO memory_cognitive_budgets(tenant_id,owner_id,brain_id,window_seconds,max_model_calls,"
        "max_input_bytes,revision) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,brain_id) DO NOTHING",
        (*_params(principal, brain_id), window_seconds, max_model_calls, max_input_bytes,
         0 if offered == DEFAULT_POLICY else 1),
    )
    current = _current(conn, principal, brain_id)
    if not current["is_explicit"]:
        tightened = {"window_seconds": max(current["window_seconds"], window_seconds),
                     "max_model_calls": min(current["max_model_calls"], max_model_calls),
                     "max_input_bytes": min(current["max_input_bytes"], max_input_bytes)}
        if any(current[key] != value for key, value in tightened.items()):
            current = conn.execute(
                "UPDATE memory_cognitive_budgets SET window_seconds=%s,max_model_calls=%s,max_input_bytes=%s,"
                "revision=revision+1 WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s RETURNING *",
                (*tightened.values(), *_params(principal, brain_id)),
            ).fetchone()
    return current
