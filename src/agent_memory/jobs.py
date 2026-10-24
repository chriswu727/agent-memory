import hashlib
import secrets
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from .helpers import DomainError
from .turns import _hash, _identifier, _integer

FAILURE_CODES = frozenset({
    "provider_failed", "provider_uncertain", "invalid_proposal", "source_unavailable",
    "context_stale", "superseded", "budget_exceeded", "cancelled", "dependency_unavailable", "internal_error",
})
PUBLIC_FIELDS = (
    "id", "brain_id", "kind", "state", "attempts", "fence", "lease_expires_at", "available_at",
    "model_calls", "input_bytes", "max_model_calls", "max_input_bytes", "last_error",
    "created_at", "updated_at", "completed_at",
)
PROCESSING_STATUSES = frozenset({
    "completed", "partial", "budget_exhausted", "provider_unavailable", "invalid_response",
    "no_sources", "verification_unavailable", "not_evaluated",
})
LEARNING_OUTCOMES = frozenset({
    "no_proposals", "no_accepted_proposals", "accepted_proposals", "not_evaluated",
})
LEARNING_LIMITATIONS = frozenset({
    "index_lag", "source_budget", "source_unavailable", "source_version_mismatch",
    "exception_budget", "exception_unavailable", "assertion_budget", "dependency_incomplete",
    "unsupported_assertion", "assertion_retracted", "candidate_scan_limit", "candidate_limit",
    "episode_limit", "episode_source_changed", "episode_scope", "recent_window_limit",
    "cue_limit", "recent_assertion_limit",
})
REJECTION_REASONS = frozenset({
    "incomplete_history_package", "invalid_proposal", "verification_unavailable",
    "semantic_support_unconfirmed",
})
_SAMPLING_COUNTS = (
    "recent_window_limit", "candidate_scan_limit", "candidate_limit", "association_depth",
    "dependency_depth", "candidate_count", "omitted_packages", "source_count",
    "assertion_count", "episode_count",
)
_SAMPLING_SEQUENCES = (
    "through_sequence", "indexed_through_sequence", "selected_min_sequence", "selected_max_sequence",
)


def _monitor_count(value, maximum=1_000_000):
    return value if type(value) is int and 0 <= value <= maximum else None


def _monitor_enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "not_evaluated"


def sanitize_learning_summary(summary):
    if not isinstance(summary, dict):
        return None
    result = {
        "processing_status": _monitor_enum(summary.get("processing_status"), PROCESSING_STATUSES),
        "learning_outcome": _monitor_enum(summary.get("learning_outcome"), LEARNING_OUTCOMES),
    }
    for group, fields in (("accepted_counts", ("claims", "situation_updates", "episode_actions")),
                          ("published_counts", ("claims", "episode_versions"))):
        values = summary.get(group)
        values = values if isinstance(values, dict) else {}
        result[group] = {key: _monitor_count(values.get(key)) or 0 for key in fields}
        if group == "published_counts" and "pattern_views" in values:
            result[group]["pattern_views"] = _monitor_count(values["pattern_views"]) or 0
    sampling = summary.get("sampling")
    safe_sampling = {}
    if isinstance(sampling, dict):
        for keys, maximum in ((_SAMPLING_COUNTS, 1_000_000), (_SAMPLING_SEQUENCES, 2**63 - 1)):
            for key in keys:
                value = _monitor_count(sampling.get(key), maximum)
                if value is not None:
                    safe_sampling[key] = value
        for key in ("search_exhaustive", "publication_restricted"):
            if type(sampling.get(key)) is bool:
                safe_sampling[key] = sampling[key]
        limits = sampling.get("limits")
        if isinstance(limits, dict):
            safe_sampling["limits"] = {
                key: value for key in ("sources", "assertions", "episodes")
                if (value := _monitor_count(limits.get(key))) is not None
            }
        limitations = sampling.get("limitations")
        if isinstance(limitations, (list, tuple)):
            safe_sampling["limitations"] = sorted({
                reason for reason in limitations[:64]
                if isinstance(reason, str) and reason in LEARNING_LIMITATIONS
            })
    result["sampling"] = safe_sampling
    reasons = summary.get("rejection_reason_counts")
    result["rejection_reason_counts"] = {
        key: value for key in sorted(REJECTION_REASONS)
        if isinstance(reasons, dict) and (value := _monitor_count(reasons.get(key))) is not None
    }
    return result


def summarize_learning(result):
    if not isinstance(result, dict):
        return None

    def count(values):
        return len(values) if isinstance(values, (list, tuple)) else 0

    publication = result.get("publication")
    publication = publication if isinstance(publication, dict) else {}
    reasons = {}
    rejected = result.get("rejected")
    if isinstance(rejected, (list, tuple)):
        for item in rejected[:72]:
            reason = item.get("reason") if isinstance(item, dict) else None
            if isinstance(reason, str) and reason in REJECTION_REASONS:
                reasons[reason] = reasons.get(reason, 0) + 1
    evaluated = _monitor_enum(result.get("status"), PROCESSING_STATUSES) in {"completed", "partial"}
    return sanitize_learning_summary({
        "processing_status": result.get("status"), "learning_outcome": result.get("learning_outcome"),
        "accepted_counts": {key: count(result.get(key)) if evaluated else 0
                            for key in ("claims", "situation_updates", "episode_actions")},
        "published_counts": {"claims": count(publication.get("memory_versions")),
                             "episode_versions": count(publication.get("episode_versions")),
                             **({"pattern_views": count(publication["pattern_view_ids"])}
                                if "pattern_view_ids" in publication else {})},
        "sampling": result.get("sampling"), "rejection_reason_counts": reasons,
    })


class JobStore:
    def __init__(self, database):
        self.database = database

    def _params(self, principal, brain_id):
        return principal.tenant_id, principal.principal_id, _identifier(brain_id)

    @contextmanager
    def _transaction(self, principal, brain_id):
        with self.database.transaction(principal) as conn:
            conn.execute("SELECT set_config('statement_timeout','5000',true)")
            conn.execute("SELECT set_config('lock_timeout','2000',true)")
            brain = conn.execute(
                "SELECT id FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s FOR UPDATE",
                self._params(principal, brain_id),
            ).fetchone()
            if brain is None:
                raise DomainError("not_found_or_forbidden", "Brain is unavailable.")
            yield conn

    def _row(self, conn, principal, brain_id, job_id):
        row = conn.execute(
            "SELECT *,clock_timestamp() AS db_now FROM memory_jobs "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s FOR UPDATE",
            (*self._params(principal, brain_id), _identifier(job_id)),
        ).fetchone()
        if row is None:
            raise DomainError("not_found_or_forbidden", "Job is unavailable.")
        return row

    def _lease(self, row, lease_token, fence):
        token_digest = hashlib.sha256(_identifier(lease_token).encode()).hexdigest()
        if (row["state"] != "running" or row["fence"] != _integer(fence, 1)
                or not row["lease_token_digest"]
                or not secrets.compare_digest(row["lease_token_digest"], token_digest)
                or not row["lease_expires_at"] or row["lease_expires_at"] <= row["db_now"]):
            raise DomainError("lease_conflict", "Job lease is missing, expired, or fenced.")

    def _public(self, row):
        result = {key: row[key] for key in PUBLIC_FIELDS}
        result["lease_active"] = bool(row["state"] == "running" and row["lease_expires_at"]
                                      and row["lease_expires_at"] > row["db_now"])
        if result["last_error"] not in FAILURE_CODES:
            result["last_error"] = "internal_error" if result["last_error"] else None
        for key in ("lease_expires_at", "available_at", "created_at", "updated_at", "completed_at"):
            if result[key] is not None:
                result[key] = result[key].isoformat()
        if row["kind"] in {"orient", "consolidate"}:
            summary = summarize_learning(row.get("result"))
            if summary is not None:
                result["learning_summary"] = summary
        return result

    def get(self, principal, brain_id, job_id):
        with self._transaction(principal, brain_id) as conn:
            return self._public(self._row(conn, principal, brain_id, job_id))

    def list(self, principal, brain_id, *, limit=100):
        _integer(limit, 1, 200)
        with self._transaction(principal, brain_id) as conn:
            rows = conn.execute(
                "SELECT *,clock_timestamp() AS db_now FROM memory_jobs "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "ORDER BY updated_at DESC,id LIMIT %s",
                (*self._params(principal, brain_id), limit),
            )
            return [self._public(row) for row in rows]

    def claim(self, principal, brain_id, *, worker_id, lease_seconds=60, kinds=None, max_attempts=3, job_id=None):
        _identifier(worker_id)
        _integer(lease_seconds, 1, 3600)
        _integer(max_attempts, 1, 100)
        if job_id is not None:
            _identifier(job_id)
        if kinds is not None:
            if not isinstance(kinds, (list, tuple)) or not 1 <= len(kinds) <= 32:
                raise DomainError("validation_failed", "Job kinds must be a bounded nonempty list.")
            kinds = [_identifier(kind) for kind in kinds]
        with self._transaction(principal, brain_id) as conn:
            params = self._params(principal, brain_id)
            row = conn.execute(
                "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND attempts<%s AND available_at<=clock_timestamp() "
                "AND (state IN ('queued','failed') OR (state='running' AND lease_expires_at<=clock_timestamp())) "
                "AND (%s::text[] IS NULL OR kind=ANY(%s::text[])) "
                "AND (%s::text IS NULL OR id=%s) "
                "ORDER BY CASE kind WHEN 'extract' THEN 0 WHEN 'rebuild' THEN 1 ELSE 2 END,created_at,id "
                "LIMIT 1 FOR UPDATE SKIP LOCKED",
                (*params, max_attempts, kinds, kinds, job_id, job_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE memory_cognitive_operations SET outcome='uncertain' "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND job_id=%s AND outcome='reserved'",
                (*params, row["id"]),
            )
            token = secrets.token_urlsafe(32)
            row = conn.execute(
                "UPDATE memory_jobs SET state='running',attempts=attempts+1,fence=fence+1,worker_id=%s,"
                "lease_token_digest=%s,lease_expires_at=clock_timestamp()+make_interval(secs=>%s),"
                "updated_at=clock_timestamp(),completed_at=NULL WHERE tenant_id=%s AND owner_id=%s "
                "AND brain_id=%s AND id=%s RETURNING *,clock_timestamp() AS db_now",
                (worker_id, hashlib.sha256(token.encode()).hexdigest(), lease_seconds, *params, row["id"]),
            ).fetchone()
            result = self._public(row)
            result.update(payload=row["payload"], worker_id=worker_id, lease_token=token)
            return result

    def renew(self, principal, brain_id, job_id, *, lease_token, fence, lease_seconds=60):
        _integer(lease_seconds, 1, 3600)
        with self._transaction(principal, brain_id) as conn:
            self._lease(self._row(conn, principal, brain_id, job_id), lease_token, fence)
            row = conn.execute(
                "UPDATE memory_jobs SET lease_expires_at=clock_timestamp()+make_interval(secs=>%s),"
                "updated_at=clock_timestamp() WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
                "RETURNING *,clock_timestamp() AS db_now",
                (lease_seconds, *self._params(principal, brain_id), job_id),
            ).fetchone()
            return self._public(row)

    def reserve_operation(self, principal, brain_id, job_id, *, lease_token, fence, operation_id,
                          request_fingerprint, input_bytes, max_model_calls, max_input_bytes,
                          window_seconds=3600, window_max_model_calls=100, window_max_input_bytes=2_000_000):
        _identifier(operation_id)
        _hash(request_fingerprint)
        for value in (input_bytes, max_model_calls, max_input_bytes, window_max_model_calls, window_max_input_bytes):
            _integer(value)
        _integer(window_seconds, 1, 2_592_000)
        with self._transaction(principal, brain_id) as conn:
            params = self._params(principal, brain_id)
            row = self._row(conn, principal, brain_id, job_id)
            self._lease(row, lease_token, fence)
            existing = conn.execute(
                "SELECT * FROM memory_cognitive_operations "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND job_id=%s AND operation_id=%s",
                (*params, job_id, operation_id),
            ).fetchone()
            if existing:
                if (existing["request_fingerprint"], existing["input_bytes"]) != (request_fingerprint, input_bytes):
                    raise DomainError("idempotency_conflict", "Operation identifier already binds another request.")
                return self._operation(existing, row, False)
            call_limit = min(max_model_calls, row["max_model_calls"]) if row["max_model_calls"] is not None else max_model_calls
            byte_limit = min(max_input_bytes, row["max_input_bytes"]) if row["max_input_bytes"] is not None else max_input_bytes
            if row["model_calls"] + 1 > call_limit or row["input_bytes"] + input_bytes > byte_limit:
                raise DomainError("budget_exceeded", "Durable job budget is exhausted.")
            budget = conn.execute(
                "INSERT INTO memory_cognitive_budgets(tenant_id,owner_id,brain_id,window_seconds,max_model_calls,max_input_bytes) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,brain_id) DO UPDATE SET "
                "window_seconds=greatest(memory_cognitive_budgets.window_seconds,excluded.window_seconds),"
                "max_model_calls=least(memory_cognitive_budgets.max_model_calls,excluded.max_model_calls),"
                "max_input_bytes=least(memory_cognitive_budgets.max_input_bytes,excluded.max_input_bytes) RETURNING *",
                (*params, window_seconds, window_max_model_calls, window_max_input_bytes),
            ).fetchone()
            usage = conn.execute(
                "SELECT count(*) AS calls,coalesce(sum(input_bytes),0) AS bytes FROM memory_cognitive_operations "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND created_at>clock_timestamp()-make_interval(secs=>%s)",
                (*params, budget["window_seconds"]),
            ).fetchone()
            if (usage["calls"] + 1 > budget["max_model_calls"]
                    or usage["bytes"] + input_bytes > budget["max_input_bytes"]):
                raise DomainError("budget_exceeded", "Brain background budget window is exhausted.", retryable=True)
            operation = conn.execute(
                "INSERT INTO memory_cognitive_operations(tenant_id,owner_id,brain_id,job_id,operation_id,"
                "request_fingerprint,input_bytes,fence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (*params, job_id, operation_id, request_fingerprint, input_bytes, fence),
            ).fetchone()
            row = conn.execute(
                "UPDATE memory_jobs SET model_calls=model_calls+1,input_bytes=input_bytes+%s,"
                "max_model_calls=%s,max_input_bytes=%s,updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s RETURNING *",
                (input_bytes, call_limit, byte_limit, *params, job_id),
            ).fetchone()
            self._lease(self._row(conn, principal, brain_id, job_id), lease_token, fence)
            return self._operation(operation, row, True)

    def _operation(self, operation, job, newly_reserved):
        return {"operation_id": operation["operation_id"], "request_fingerprint": operation["request_fingerprint"],
                "fence": operation["fence"], "outcome": operation["outcome"], "input_bytes": operation["input_bytes"],
                "usage": operation["usage"], "response_fingerprint": operation["response_fingerprint"],
                "newly_reserved": newly_reserved, "job_model_calls": job["model_calls"],
                "job_input_bytes": job["input_bytes"]}

    def get_operation(self, principal, brain_id, job_id, operation_id):
        _identifier(operation_id)
        with self._transaction(principal, brain_id) as conn:
            job = self._row(conn, principal, brain_id, job_id)
            operation = conn.execute(
                "SELECT * FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s "
                "AND brain_id=%s AND job_id=%s AND operation_id=%s",
                (*self._params(principal, brain_id), job_id, operation_id),
            ).fetchone()
            if operation is None:
                raise DomainError("not_found_or_forbidden", "Operation is unavailable.")
            return self._operation(operation, job, False)

    def settle_operation(self, principal, brain_id, job_id, *, lease_token, fence, operation_id, outcome,
                         usage=None, response_fingerprint=None):
        _identifier(operation_id)
        if outcome not in ("completed", "failed", "uncertain"):
            raise DomainError("validation_failed", "Operation outcome is not supported.")
        if usage is not None:
            if not isinstance(usage, dict) or not usage or set(usage) - {"input_tokens", "output_tokens", "total_tokens"}:
                raise DomainError("validation_failed", "Operation usage accepts only bounded token counters.")
            for value in usage.values():
                if value is not None:
                    _integer(value)
        if response_fingerprint is not None:
            _hash(response_fingerprint)
        with self._transaction(principal, brain_id) as conn:
            job = self._row(conn, principal, brain_id, job_id)
            self._lease(job, lease_token, fence)
            params = (*self._params(principal, brain_id), job_id, operation_id)
            operation = conn.execute(
                "SELECT * FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s "
                "AND brain_id=%s AND job_id=%s AND operation_id=%s FOR UPDATE", params,
            ).fetchone()
            if operation is None or operation["fence"] != fence:
                raise DomainError("lease_conflict", "Operation belongs to an unavailable job attempt.")
            if operation["outcome"] not in ("reserved", outcome):
                raise DomainError("version_conflict", "Operation outcome is already recorded.")
            if operation["outcome"] != "reserved":
                if (operation["usage"], operation["response_fingerprint"]) != (usage, response_fingerprint):
                    raise DomainError("idempotency_conflict", "Operation settlement already binds another result.")
                return self._operation(operation, job, False)
            operation = conn.execute(
                "UPDATE memory_cognitive_operations SET outcome=%s,usage=%s,response_fingerprint=%s,"
                "completed_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND job_id=%s AND operation_id=%s RETURNING *",
                (outcome, Jsonb(usage) if usage is not None else None, response_fingerprint, *params),
            ).fetchone()
            return self._operation(operation, job, False)

    def complete(self, principal, brain_id, job_id, *, lease_token, fence, publish=None):
        with self._transaction(principal, brain_id) as conn:
            row = self._row(conn, principal, brain_id, job_id)
            self._lease(row, lease_token, fence)
            result = publish(conn, row) if publish is not None else None
            self._lease(self._row(conn, principal, brain_id, job_id), lease_token, fence)
            conn.execute(
                "UPDATE memory_cognitive_operations SET outcome='completed',completed_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND job_id=%s AND fence=%s AND outcome='reserved'",
                (*self._params(principal, brain_id), job_id, fence),
            )
            completed = conn.execute(
                "UPDATE memory_jobs SET state='completed',lease_token_digest=NULL,lease_expires_at=NULL,"
                "worker_id=NULL,last_error=NULL,completed_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
                "AND lease_expires_at>clock_timestamp() RETURNING id",
                (*self._params(principal, brain_id), job_id),
            ).fetchone()
            if completed is None:
                raise DomainError("lease_conflict", "Job lease expired before publication completed.")
            return result

    def fail(self, principal, brain_id, job_id, *, lease_token, fence, failure_code,
             retry=True, retry_delay_seconds=0):
        if failure_code not in FAILURE_CODES or type(retry) is not bool:
            raise DomainError("validation_failed", "Job failure must use a redacted failure code.")
        _integer(retry_delay_seconds, 0, 2_592_000)
        with self._transaction(principal, brain_id) as conn:
            self._lease(self._row(conn, principal, brain_id, job_id), lease_token, fence)
            params = (*self._params(principal, brain_id), job_id)
            conn.execute(
                "UPDATE memory_cognitive_operations SET outcome='uncertain' "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND job_id=%s AND outcome='reserved'", params,
            )
            row = conn.execute(
                "UPDATE memory_jobs SET state=%s,lease_token_digest=NULL,lease_expires_at=NULL,worker_id=NULL,"
                "last_error=%s,available_at=clock_timestamp()+make_interval(secs=>%s),updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s RETURNING *,clock_timestamp() AS db_now",
                ("failed" if retry else ("superseded" if failure_code == "superseded" else "cancelled"),
                 failure_code, retry_delay_seconds, *params),
            ).fetchone()
            return self._public(row)
