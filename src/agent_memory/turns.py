import hashlib
import secrets
import unicodedata
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from .helpers import DomainError, digest

STAGES = (
    "started", "captured", "prepared", "generating", "generated", "delivering",
    "delivered", "committed", "completed",
)
FAILURE_CODES = frozenset({
    "capture_failed", "prepare_failed", "generation_failed", "generation_uncertain",
    "delivery_failed", "delivery_uncertain", "commit_failed", "source_unavailable",
    "context_stale", "cancelled", "budget_exceeded", "dependency_unavailable", "internal_error",
})
PUBLIC_FIELDS = (
    "turn_id", "brain_id", "conversation_id", "branch_id", "input_fingerprint", "stage",
    "revision", "fence", "claim_count", "generation_count", "worker_id", "lease_expires_at",
    "recovery_required", "input_event_refs", "context_event_refs", "output_event_refs", "context_fingerprint",
    "context_generation", "working_revision", "durable_sequence", "context_tokens",
    "failure_code", "created_at", "updated_at",
)


def _identifier(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 256
            or any(character.isspace() or unicodedata.category(character).startswith("C")
                   for character in value)):
        raise DomainError("validation_failed", "Identifiers must be bounded and contain no whitespace or controls.")
    return value


def _integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError("validation_failed", "Counter is outside its permitted range.")
    return value


def _hash(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise DomainError("validation_failed", "Fingerprint must be a SHA-256 digest.")
    return value


def _refs(value):
    if not isinstance(value, list) or len(value) > 64:
        raise DomainError("validation_failed", "A turn may reference at most 64 events per group.")
    result = []
    for ref in value:
        if not isinstance(ref, dict) or set(ref) != {"event_id", "revision"}:
            raise DomainError("validation_failed", "Event references contain only an identifier and revision.")
        checked = {"event_id": _identifier(ref["event_id"]), "revision": _integer(ref["revision"], 1)}
        if checked in result:
            raise DomainError("validation_failed", "Event references must be distinct.")
        result.append(checked)
    return result


class TurnStore:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def _transaction(self, principal, brain_id):
        _identifier(brain_id)
        with self.database.transaction(principal) as conn:
            conn.execute("SELECT set_config('statement_timeout','5000',true)")
            conn.execute("SELECT set_config('lock_timeout','2000',true)")
            brain = conn.execute(
                "SELECT * FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s FOR UPDATE",
                (principal.tenant_id, principal.principal_id, brain_id),
            ).fetchone()
            if brain is None:
                raise DomainError("not_found_or_forbidden", "Brain is unavailable.")
            yield conn, brain

    def _params(self, principal, brain_id, turn_id):
        return principal.tenant_id, principal.principal_id, brain_id, _identifier(turn_id)

    def _row(self, conn, principal, brain_id, turn_id):
        row = conn.execute(
            "SELECT *,clock_timestamp() AS db_now FROM memory_runtime_turns "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s FOR UPDATE",
            self._params(principal, brain_id, turn_id),
        ).fetchone()
        if row is None:
            raise DomainError("not_found_or_forbidden", "Turn is unavailable.")
        return row

    def _unavailable(self, conn, principal, row):
        local_refs = row["input_event_refs"] + row["output_event_refs"]
        references = local_refs + row["context_event_refs"]
        if not references:
            return []
        available = {(item["id"], item["revision"]): item for item in conn.execute(
            "SELECT id,revision,conversation_id,branch_id,document #>> '{scope,visibility}' AS visibility "
            "FROM memory_events WHERE tenant_id=%s AND owner_id=%s "
            "AND brain_id=%s AND id=ANY(%s::text[]) "
            "AND is_current AND NOT retired AND NOT suppressed AND NOT deleted AND document IS NOT NULL",
            (principal.tenant_id, principal.principal_id, row["brain_id"],
             [ref["event_id"] for ref in references]),
        )}
        unavailable = []
        for ref in references:
            event = available.get((ref["event_id"], ref["revision"]))
            allowed = event is not None
            if allowed and ref in local_refs:
                allowed = (event["conversation_id"], event["branch_id"]) == (row["conversation_id"], row["branch_id"])
            elif allowed:
                allowed = (event["visibility"] == "private" or (
                    event["visibility"] == "conversation" and event["conversation_id"] == row["conversation_id"]))
                if event["conversation_id"] == row["conversation_id"] and event["branch_id"] is not None:
                    allowed = allowed and event["branch_id"] == row["branch_id"]
            if not allowed and ref not in unavailable:
                unavailable.append(ref)
        return unavailable

    def _public(self, conn, principal, row):
        result = {key: row[key] for key in PUBLIC_FIELDS}
        result["lease_active"] = bool(row["lease_expires_at"] and row["lease_expires_at"] > row["db_now"])
        if not result["lease_active"] and row["stage"] in ("generating", "delivering"):
            result["recovery_required"] = {
                "generating": "generation_uncertain", "delivering": "delivery_uncertain",
            }[row["stage"]]
        for field in ("lease_expires_at", "created_at", "updated_at"):
            if result[field] is not None:
                result[field] = result[field].isoformat()
        result["unavailable_event_refs"] = self._unavailable(conn, principal, row)
        result["sources_available"] = not result["unavailable_event_refs"]
        return result

    def _record(self, conn, row, operation):
        conn.execute(
            "INSERT INTO memory_runtime_turn_transitions(tenant_id,owner_id,brain_id,turn_id,"
            "revision,stage,operation,fence,failure_code) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (row["tenant_id"], row["owner_id"], row["brain_id"], row["turn_id"], row["revision"],
             row["stage"], operation, row["fence"], row["failure_code"]),
        )

    def _lease(self, row, lease_token, expected_revision=None):
        token_digest = hashlib.sha256(_identifier(lease_token).encode()).hexdigest()
        if (not row["lease_token_digest"] or not secrets.compare_digest(row["lease_token_digest"], token_digest)
                or not row["lease_expires_at"] or row["lease_expires_at"] <= row["db_now"]):
            raise DomainError("lease_conflict", "Turn lease is missing, expired, or fenced.", current_revision=row["revision"])
        if expected_revision is not None and _integer(expected_revision, 1) != row["revision"]:
            raise DomainError("version_conflict", "Turn revision has changed.", current_revision=row["revision"])

    def begin(self, principal, brain_id, *, turn_id, conversation_id, branch_id, input_fingerprint):
        _identifier(turn_id)
        _identifier(conversation_id)
        _identifier(branch_id)
        _hash(input_fingerprint)
        with self._transaction(principal, brain_id) as (conn, _):
            inserted = conn.execute(
                "INSERT INTO memory_runtime_turns(tenant_id,owner_id,brain_id,turn_id,conversation_id,"
                "branch_id,input_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING "
                "RETURNING *,clock_timestamp() AS db_now",
                (*self._params(principal, brain_id, turn_id), conversation_id, branch_id, input_fingerprint),
            ).fetchone()
            row = inserted or self._row(conn, principal, brain_id, turn_id)
            if (row["conversation_id"], row["branch_id"], row["input_fingerprint"]) != (
                    conversation_id, branch_id, input_fingerprint):
                raise DomainError("idempotency_conflict", "Turn identifier already binds different input or scope.")
            if inserted:
                self._record(conn, row, "begin")
            return self._public(conn, principal, row)

    def get(self, principal, brain_id, turn_id):
        with self._transaction(principal, brain_id) as (conn, _):
            return self._public(conn, principal, self._row(conn, principal, brain_id, turn_id))

    def sources(self, principal, brain_id, turn_id):
        with self._transaction(principal, brain_id) as (conn, _):
            row = self._row(conn, principal, brain_id, turn_id)
            if self._unavailable(conn, principal, row):
                raise DomainError("source_unavailable", "A turn source is no longer available.")
            refs = row["input_event_refs"] + row["output_event_refs"]
            events = {(event["id"], event["revision"]): event["document"] for event in conn.execute(
                "SELECT id,revision,document FROM memory_events WHERE tenant_id=%s AND owner_id=%s "
                "AND brain_id=%s AND id=ANY(%s::text[]) AND is_current AND NOT suppressed AND NOT deleted",
                (principal.tenant_id, principal.principal_id, brain_id, [ref["event_id"] for ref in refs]),
            )}
            return {field.replace("_refs", "s"): [events[(ref["event_id"], ref["revision"])] for ref in row[field]]
                    for field in ("input_event_refs", "output_event_refs")}

    def list(self, principal, brain_id, *, limit=100):
        _integer(limit, 1, 200)
        with self._transaction(principal, brain_id) as (conn, _):
            rows = conn.execute(
                "SELECT *,clock_timestamp() AS db_now FROM memory_runtime_turns "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s ORDER BY updated_at DESC,turn_id LIMIT %s",
                (principal.tenant_id, principal.principal_id, brain_id, limit),
            ).fetchall()
            return [self._public(conn, principal, row) for row in rows]

    def history(self, principal, brain_id, turn_id, *, limit=100):
        _integer(limit, 1, 200)
        with self._transaction(principal, brain_id) as (conn, _):
            self._row(conn, principal, brain_id, turn_id)
            rows = conn.execute(
                "SELECT revision,stage,operation,fence,failure_code,occurred_at FROM "
                "memory_runtime_turn_transitions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND turn_id=%s ORDER BY revision DESC LIMIT %s",
                (*self._params(principal, brain_id, turn_id), limit),
            ).fetchall()
            return [{**row, "occurred_at": row["occurred_at"].isoformat()} for row in reversed(rows)]

    def status(self, principal, brain_id):
        with self._transaction(principal, brain_id) as (conn, brain):
            params = (principal.tenant_id, principal.principal_id, brain_id)
            counts = conn.execute(
                "SELECT stage,count(*) AS count,count(*) FILTER (WHERE lease_expires_at>clock_timestamp()) "
                "AS active FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s GROUP BY stage",
                params,
            ).fetchall()
            jobs = conn.execute(
                "SELECT count(*) AS count FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND state<>'completed'", params,
            ).fetchone()["count"]
            return {
                "brain_id": brain_id, "durable_sequence": brain["durable_sequence"],
                "indexed_sequence": brain["indexed_sequence"], "dependency_generation": brain["dependency_generation"],
                "scope_generation": brain["scope_generation"], "turn_count": sum(row["count"] for row in counts),
                "active_lease_count": sum(row["active"] for row in counts),
                "stage_counts": {row["stage"]: row["count"] for row in counts}, "pending_job_count": jobs,
            }

    def claim(self, principal, brain_id, turn_id, *, worker_id, lease_seconds=30, expected_revision=None):
        _identifier(worker_id)
        _integer(lease_seconds, 1, 300)
        with self._transaction(principal, brain_id) as (conn, _):
            row = self._row(conn, principal, brain_id, turn_id)
            if expected_revision is not None and _integer(expected_revision, 1) != row["revision"]:
                raise DomainError("version_conflict", "Turn revision has changed.", current_revision=row["revision"])
            if row["stage"] in ("completed", "failed"):
                raise DomainError("turn_terminal", "Turn already reached a terminal state.")
            if row["lease_expires_at"] and row["lease_expires_at"] > row["db_now"]:
                raise DomainError("lease_conflict", "Another worker holds the turn lease.", retryable=True)
            recovery = {"generating": "generation_uncertain", "delivering": "delivery_uncertain"}.get(row["stage"])
            token = secrets.token_urlsafe(32)
            row = conn.execute(
                "UPDATE memory_runtime_turns SET revision=revision+1,fence=fence+1,claim_count=claim_count+1,"
                "worker_id=%s,lease_token_digest=%s,lease_expires_at=clock_timestamp()+(%s*interval '1 second'),"
                "recovery_required=%s,updated_at=clock_timestamp(),last_transition_hash=NULL "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s "
                "RETURNING *,clock_timestamp() AS db_now",
                (worker_id, hashlib.sha256(token.encode()).hexdigest(), lease_seconds, recovery,
                 *self._params(principal, brain_id, turn_id)),
            ).fetchone()
            self._record(conn, row, "claim")
            return {**self._public(conn, principal, row), "lease_token": token}

    def renew(self, principal, brain_id, turn_id, *, lease_token, lease_seconds=30, expected_revision=None):
        _integer(lease_seconds, 1, 300)
        with self._transaction(principal, brain_id) as (conn, _):
            row = self._row(conn, principal, brain_id, turn_id)
            self._lease(row, lease_token, expected_revision)
            row = conn.execute(
                "UPDATE memory_runtime_turns SET lease_expires_at=clock_timestamp()+(%s*interval '1 second') "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s "
                "RETURNING *,clock_timestamp() AS db_now",
                (lease_seconds, *self._params(principal, brain_id, turn_id)),
            ).fetchone()
            return self._public(conn, principal, row)

    def advance(self, principal, brain_id, turn_id, *, lease_token, expected_revision, stage,
                input_event_refs=None, context_event_refs=None, output_event_refs=None, context_fingerprint=None,
                context_generation=None, working_revision=None, durable_sequence=None, context_tokens=None):
        _integer(expected_revision, 1)
        if stage not in STAGES[1:-2]:
            raise DomainError("validation_failed", "Stage is not a valid advance target.")
        changes = {key: value for key, value in {
            "input_event_refs": input_event_refs, "context_event_refs": context_event_refs,
            "output_event_refs": output_event_refs,
            "context_fingerprint": context_fingerprint, "context_generation": context_generation,
            "working_revision": working_revision, "durable_sequence": durable_sequence,
            "context_tokens": context_tokens,
        }.items() if value is not None}
        for key, value in changes.items():
            if key.endswith("_refs"):
                changes[key] = _refs(value)
            elif key.endswith("fingerprint"):
                _hash(value)
            else:
                _integer(value)
        allowed_at = {
            "input_event_refs": {"captured"}, "context_event_refs": {"prepared"},
            "output_event_refs": {"generated", "delivered"},
            "context_fingerprint": {"prepared"}, "context_generation": {"prepared"},
            "context_tokens": {"prepared"}, "working_revision": {"prepared", "committed"},
        }
        if any(stage not in allowed_at[key] for key in changes if key in allowed_at):
            raise DomainError("validation_failed", "Stage cannot replace previously bound turn metadata.")
        fingerprint = digest({"revision": expected_revision, "stage": stage, "changes": changes})
        with self._transaction(principal, brain_id) as (conn, brain):
            row = self._row(conn, principal, brain_id, turn_id)
            self._lease(row, lease_token)
            if row["revision"] == expected_revision + 1 and row["last_transition_hash"] == fingerprint:
                if stage == "delivering":
                    if self._unavailable(conn, principal, row):
                        raise DomainError("source_unavailable", "A turn source changed before delivery retry.")
                    if row["context_generation"] != brain["dependency_generation"]:
                        raise DomainError("context_stale", "Memory changed before delivery retry.")
                    self._working_revision(conn, principal, row)
                return self._public(conn, principal, row)
            self._lease(row, lease_token, expected_revision)
            delivery_retry = stage == row["stage"] == "delivering"
            if not delivery_retry and (
                    row["stage"] not in STAGES[:-1] or STAGES[STAGES.index(row["stage"]) + 1] != stage):
                raise DomainError("stage_conflict", "Turn stages must advance in order.")
            candidate = {**row, **changes}
            if not candidate["input_event_refs"] or (stage in STAGES[4:] and not candidate["output_event_refs"]):
                raise DomainError("validation_failed", "This stage requires durable source references.")
            if self._unavailable(conn, principal, candidate):
                raise DomainError("source_unavailable", "A turn source was changed, erased, or is outside its scope.")
            if stage in ("prepared", "generating") and candidate["context_generation"] != brain["dependency_generation"]:
                raise DomainError("context_stale", "Context generation differs from the current brain.")
            if stage == "delivering":
                if candidate["context_generation"] != brain["dependency_generation"]:
                    raise DomainError("context_stale", "Memory changed before delivery.")
                self._working_revision(conn, principal, candidate)
            if stage == "delivered":
                if {ref["event_id"] for ref in candidate["output_event_refs"]} != {
                        ref["event_id"] for ref in row["output_event_refs"]}:
                    raise DomainError("validation_failed", "Delivery must reference the generated output.")
                self._delivered(conn, principal, candidate)
            if stage in ("generated", "delivered"):
                input_ids = {ref["event_id"] for ref in candidate["input_event_refs"] + candidate["context_event_refs"]}
                output_ids = {ref["event_id"] for ref in candidate["output_event_refs"]}
                if input_ids & output_ids:
                    raise DomainError("invalid_evidence", "Generated output cannot also be its own input evidence.")
                if stage == "generated":
                    states = conn.execute(
                        "SELECT document #>> '{observed,actor_role}' AS role,"
                        "document #>> '{observed,delivery_state}' AS delivery FROM memory_events "
                        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s::text[]) AND is_current",
                        (principal.tenant_id, principal.principal_id, brain_id, sorted(output_ids)),
                    ).fetchall()
                    if any(event["role"] != "assistant" or event["delivery"] != "draft" for event in states):
                        raise DomainError("invalid_evidence", "Generated output requires durable assistant draft sources.")
                conn.execute(
                    "INSERT INTO memory_event_dependencies(tenant_id,owner_id,brain_id,input_event_id,output_event_id) "
                    "SELECT %s,%s,%s,input_id,output_id FROM unnest(%s::text[]) AS sources(input_id) "
                    "CROSS JOIN unnest(%s::text[]) AS outputs(output_id) ON CONFLICT DO NOTHING",
                    (principal.tenant_id, principal.principal_id, brain_id, sorted(input_ids), sorted(output_ids)),
                )
            if stage == "prepared" and (candidate["context_fingerprint"] is None or candidate["working_revision"] is None):
                raise DomainError("validation_failed", "Prepared context requires a fingerprint and working revision.")
            if candidate["durable_sequence"] is not None and candidate["durable_sequence"] > brain["durable_sequence"]:
                raise DomainError("validation_failed", "Turn cannot cover events that are not durable.")
            assignments, values = [], []
            for key, value in changes.items():
                assignments.append(f"{key}=%s")
                values.append(Jsonb(value) if key.endswith("_refs") else value)
            assignments.extend([
                "stage=%s", "revision=revision+1", "updated_at=clock_timestamp()", "last_transition_hash=%s",
                "recovery_required=NULL", "generation_count=generation_count+%s",
            ])
            values.extend([stage, fingerprint, int(stage == "generating")])
            if stage == "completed":
                assignments.append("lease_expires_at=NULL")
            row = conn.execute(
                "UPDATE memory_runtime_turns SET " + ",".join(assignments)
                + " WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s RETURNING *,clock_timestamp() AS db_now",
                (*values, *self._params(principal, brain_id, turn_id)),
            ).fetchone()
            self._record(conn, row, "advance")
            return self._public(conn, principal, row)

    def record_output(self, principal, brain_id, turn_id, *, lease_token, expected_revision,
                      text, delivery_state):
        from .service import MemoryService

        _integer(expected_revision, 1)
        if delivery_state not in ("draft", "delivered", "partially_delivered", "cancelled"):
            raise DomainError("validation_failed", "Output delivery state is not supported.")
        if (not isinstance(text, str) or len(text.encode("utf-8")) > 64000
                or (not text and delivery_state != "cancelled")):
            raise DomainError("validation_failed", "Output must contain bounded text; only cancellation may be empty.")
        source_revision = 1 if delivery_state == "draft" else 2
        incoming = {
            "source_event_key": "runtime-output:" + digest([brain_id, turn_id]),
            "source_revision": source_revision, "producer_ref": "brain-runtime",
            "actor_ref": "assistant", "actor_role": "assistant", "event_kind": "message",
            "content": {"media_type": "text/plain", "text": text},
            "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
            "interpretation_hint": "unknown", "delivery_state": delivery_state,
            "speech_act_hint": "unknown", "parent_source_event_keys": [],
        }
        with self._transaction(principal, brain_id) as (conn, brain):
            row = self._row(conn, principal, brain_id, turn_id)
            existing = conn.execute(
                "SELECT id,input_hash,document FROM memory_events WHERE tenant_id=%s AND owner_id=%s "
                "AND brain_id=%s AND producer_ref=%s AND source_key=%s AND source_revision=%s",
                (principal.tenant_id, principal.principal_id, brain_id, incoming["producer_ref"],
                 incoming["source_event_key"], source_revision),
            ).fetchone()
            replay_stages = {
                "draft": {"generated", "delivering", "delivered", "completed", "failed"},
                "delivered": {"delivered", "completed"},
                "partially_delivered": {"delivered", "completed"}, "cancelled": {"failed"},
            }[delivery_state]
            if existing and row["stage"] in replay_stages and existing["id"] in {
                    ref["event_id"] for ref in row["output_event_refs"]}:
                if self._unavailable(conn, principal, row) or existing["document"] is None:
                    raise DomainError("source_unavailable", "Recorded output sources are no longer available.")
                if existing["input_hash"] != digest(incoming):
                    raise DomainError("idempotency_conflict", "Output capture has different text or delivery state.")
                return self._public(conn, principal, row)
            self._lease(row, lease_token, expected_revision)
            expected_stage = "generating" if delivery_state == "draft" else "delivering"
            if row["stage"] != expected_stage:
                raise DomainError("stage_conflict", "Output capture does not match the current turn stage.")
            if self._unavailable(conn, principal, row):
                raise DomainError("source_unavailable", "A turn source changed before output capture.")
            if row["context_generation"] != brain["dependency_generation"]:
                raise DomainError("context_stale", "Memory changed before output capture.")
            self._working_revision(conn, principal, row)
            if delivery_state != "draft" and len(row["output_event_refs"]) != 1:
                raise DomainError("invalid_evidence", "Delivery requires one previously recorded assistant draft.")
            service = MemoryService(self.database)
            target = {"brain_ref": brain_id, "conversation_ref": row["conversation_id"], "branch_ref": row["branch_id"]}
            event_id, _, _ = service._append_event(conn, principal, brain, target, incoming)
            output_refs = [{"event_id": event_id, "revision": source_revision}]
            candidate = {**row, "output_event_refs": output_refs}
            if delivery_state != "draft" and event_id != row["output_event_refs"][0]["event_id"]:
                raise DomainError("invalid_evidence", "Delivery must revise its own generated draft.")
            if self._unavailable(conn, principal, candidate):
                raise DomainError("source_unavailable", "Output source references are no longer current.")
            input_ids = {ref["event_id"] for ref in row["input_event_refs"] + row["context_event_refs"]}
            if event_id in input_ids:
                raise DomainError("invalid_evidence", "Generated output cannot also be its own input evidence.")
            conn.execute(
                "INSERT INTO memory_event_dependencies(tenant_id,owner_id,brain_id,input_event_id,output_event_id) "
                "SELECT %s,%s,%s,input_id,%s FROM unnest(%s::text[]) AS sources(input_id) ON CONFLICT DO NOTHING",
                (principal.tenant_id, principal.principal_id, brain_id, event_id, sorted(input_ids)),
            )
            stage = "generated" if delivery_state == "draft" else "failed" if delivery_state == "cancelled" else "delivered"
            row = conn.execute(
                "UPDATE memory_runtime_turns SET revision=revision+1,stage=%s,output_event_refs=%s,"
                "durable_sequence=%s,recovery_required=NULL,last_transition_hash=NULL,failure_code=%s,"
                "lease_token_digest=CASE WHEN %s THEN NULL ELSE lease_token_digest END,"
                "lease_expires_at=CASE WHEN %s THEN NULL ELSE lease_expires_at END,updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s "
                "RETURNING *,clock_timestamp() AS db_now",
                (stage, Jsonb(output_refs), brain["durable_sequence"], "cancelled" if stage == "failed" else None,
                 stage == "failed", stage == "failed", *self._params(principal, brain_id, turn_id)),
            ).fetchone()
            self._record(conn, row, "fail" if stage == "failed" else "advance")
            return self._public(conn, principal, row)

    def release(self, principal, brain_id, turn_id, *, lease_token, expected_revision):
        _integer(expected_revision, 1)
        with self._transaction(principal, brain_id) as (conn, _):
            row = self._row(conn, principal, brain_id, turn_id)
            self._lease(row, lease_token, expected_revision)
            row = conn.execute(
                "UPDATE memory_runtime_turns SET revision=revision+1,lease_token_digest=NULL,lease_expires_at=NULL,"
                "last_transition_hash=NULL,updated_at=clock_timestamp() WHERE tenant_id=%s AND owner_id=%s "
                "AND brain_id=%s AND turn_id=%s RETURNING *,clock_timestamp() AS db_now",
                self._params(principal, brain_id, turn_id),
            ).fetchone()
            self._record(conn, row, "release")
            return self._public(conn, principal, row)

    def fail(self, principal, brain_id, turn_id, *, lease_token, expected_revision, failure_code):
        _integer(expected_revision, 1)
        if not isinstance(failure_code, str) or failure_code not in FAILURE_CODES:
            raise DomainError("validation_failed", "Failure code is not a recognized diagnostic category.")
        with self._transaction(principal, brain_id) as (conn, _):
            row = self._row(conn, principal, brain_id, turn_id)
            self._lease(row, lease_token, expected_revision)
            row = conn.execute(
                "UPDATE memory_runtime_turns SET revision=revision+1,stage='failed',failure_code=%s,"
                "lease_token_digest=NULL,lease_expires_at=NULL,last_transition_hash=NULL,"
                "updated_at=clock_timestamp() WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND turn_id=%s RETURNING *,clock_timestamp() AS db_now",
                (failure_code, *self._params(principal, brain_id, turn_id)),
            ).fetchone()
            self._record(conn, row, "fail")
            return self._public(conn, principal, row)

    def _working_revision(self, conn, principal, row):
        current = conn.execute(
            "SELECT revision FROM memory_working_states WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND conversation_id=%s AND branch_id=%s",
            (principal.tenant_id, principal.principal_id, row["brain_id"], row["conversation_id"], row["branch_id"]),
        ).fetchone()
        revision = current["revision"] if current else 0
        if row["working_revision"] != revision:
            raise DomainError("version_conflict", "Working state changed before turn completion.", current_revision=revision)

    def _delivered(self, conn, principal, row):
        if not row["output_event_refs"] or self._unavailable(conn, principal, row):
            raise DomainError("source_unavailable", "Turn sources are unavailable.")
        events = conn.execute(
            "SELECT document #>> '{observed,delivery_state}' AS delivery_state,"
            "document #>> '{observed,actor_role}' AS actor_role FROM memory_events "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s::text[]) AND is_current",
            (principal.tenant_id, principal.principal_id, row["brain_id"],
             [ref["event_id"] for ref in row["output_event_refs"]]),
        ).fetchall()
        if any(event["actor_role"] != "assistant" or event["delivery_state"] not in (
                "delivered", "partially_delivered") for event in events):
            raise DomainError("invalid_evidence", "Delivered turn requires actual delivered assistant source events.")

    def finish(self, principal, brain_id, turn_id, *, lease_token, expected_revision):
        from .service import MemoryService

        _integer(expected_revision, 1)
        with self._transaction(principal, brain_id) as (conn, brain):
            row = self._row(conn, principal, brain_id, turn_id)
            if row["stage"] == "completed":
                return self._public(conn, principal, row)
            self._lease(row, lease_token, expected_revision)
            if row["stage"] != "delivered":
                raise DomainError("stage_conflict", "Only an actually delivered turn can finish.")
            self._working_revision(conn, principal, row)
            self._delivered(conn, principal, row)
            service = MemoryService(self.database)
            target = {"brain_ref": brain_id, "conversation_ref": row["conversation_id"], "branch_ref": row["branch_id"]}
            raw = service._working(conn, principal, brain, target, None)
            event_ids = {ref["event_id"] for field in ("items", "open_loops") for item in raw[field]
                         for ref in item["evidence_refs"]}
            if len(event_ids) > 1024:
                raise DomainError("budget_exceeded", "Working source validation exceeds the bounded commit budget.")
            state = service._working(conn, principal, brain, target,
                                     service._events(conn, principal, brain, event_ids=event_ids, limit=1024))
            committed = service._commit(conn, principal, brain, {"target": target, "payload": {
                "turn_id": turn_id, "expected_working_revision": row["working_revision"],
                "delivered_event_ids": [ref["event_id"] for ref in row["output_event_refs"]],
                "proposed_items": state["items"], "proposed_open_loops": state["open_loops"],
                "assertion_proposals": [],
            }})
            working_revision = committed["working_state"]["revision"]
            checkpoint = service._checkpoint(conn, principal, brain, {"target": target, "payload": {
                "expected_working_revision": working_revision, "through_sequence": brain["durable_sequence"],
                "reason": "compaction",
            }})
            if not checkpoint["compaction_safe"]:
                raise DomainError("dependency_unavailable", "Durable turn checkpoint is unavailable.")
            row = conn.execute(
                "UPDATE memory_runtime_turns SET revision=revision+1,stage='committed',working_revision=%s,"
                "durable_sequence=%s,updated_at=clock_timestamp(),last_transition_hash=NULL "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s "
                "RETURNING *,clock_timestamp() AS db_now",
                (working_revision, brain["durable_sequence"], *self._params(principal, brain_id, turn_id)),
            ).fetchone()
            self._record(conn, row, "advance")
            row = conn.execute(
                "UPDATE memory_runtime_turns SET revision=revision+1,stage='completed',lease_token_digest=NULL,"
                "lease_expires_at=NULL,recovery_required=NULL,updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s "
                "RETURNING *,clock_timestamp() AS db_now", self._params(principal, brain_id, turn_id),
            ).fetchone()
            self._record(conn, row, "advance")
            return self._public(conn, principal, row)
