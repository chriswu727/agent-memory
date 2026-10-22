import copy
import hashlib
import json
import math
import re

import psycopg
from psycopg.types.json import Jsonb

from . import cognition
from .contracts import CONTRACT_VERSION, validate
from .helpers import DomainError, digest, now, uid


def _vector(text):
    pieces = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", text.casefold())
    pieces += [a + b for a, b in zip(pieces, pieces[1:])]
    values = [0.0] * 256
    for piece in pieces:
        hashed = hashlib.blake2s(piece.encode(), digest_size=4).digest()
        values[int.from_bytes(hashed[:2], "big") % 256] += 1 if hashed[2] % 2 else -1
    norm = math.sqrt(sum(v * v for v in values)) or 1
    return "[" + ",".join(str(v / norm) for v in values) + "]"


class MemoryService:
    def __init__(self, database):
        self.database = database

    def _params(self, principal, brain_id):
        return principal.tenant_id, principal.principal_id, brain_id

    def _brain(self, conn, principal, brain_id):
        row = conn.execute(
            "SELECT * FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s FOR UPDATE",
            self._params(principal, brain_id),
        ).fetchone()
        if row is None:
            raise DomainError("not_found_or_forbidden", "Brain is unavailable.")
        return row

    def _scope(self, principal, brain, target=None):
        target = target or {}
        return {
            "tenant_id": principal.tenant_id,
            "brain_id": brain["id"],
            "principal_binding_id": principal.principal_id,
            "conversation_id": target.get("conversation_ref"),
            "branch_id": target.get("branch_ref"),
            "visibility": "private",
            "access_policy_id": f"owner:{brain['id']}",
            "policy_revision": 1,
            "scope_generation": brain["scope_generation"],
        }

    def _rebind(self, document, principal, brain):
        result = copy.deepcopy(document)
        old_scope = result["scope"]
        scope = self._scope(principal, brain, {
            "conversation_ref": old_scope.get("conversation_id"),
            "branch_ref": old_scope.get("branch_id"),
        })
        result["scope"] = scope
        if "disclosure_basis" in result:
            basis = result["disclosure_basis"]
            basis["dependency_generation"] = brain["dependency_generation"]
            for path in basis["support_paths"]:
                path["permitted_scope"] = copy.deepcopy(scope)
                path["dependency_generation"] = brain["dependency_generation"]
        return result

    def _public_brain(self, brain):
        return {key: brain[key] for key in (
            "id", "name", "scope_generation", "dependency_generation", "durable_sequence", "indexed_sequence"
        )}

    def create_brain(self, principal, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise DomainError("validation_failed", "Brain name must contain 1 to 200 characters.")
        with self.database.transaction(principal) as conn:
            row = conn.execute(
                "INSERT INTO memory_brains(id,tenant_id,owner_id,name) VALUES (%s,%s,%s,%s) RETURNING *",
                (uid(), principal.tenant_id, principal.principal_id, name.strip()),
            ).fetchone()
            return self._public_brain(row)

    def list_brains(self, principal):
        with self.database.transaction(principal) as conn:
            return [self._public_brain(row) for row in conn.execute(
                "SELECT * FROM memory_brains WHERE tenant_id=%s AND owner_id=%s ORDER BY created_at,id",
                (principal.tenant_id, principal.principal_id),
            )]

    def handle(self, principal, request):
        action = request.get("action", "observe")
        envelope = {
            "message_type": "response", "contract_version": CONTRACT_VERSION,
            "request_id": request.get("request_id", uid()), "action": action,
        }
        try:
            validate("ActionRequest", request)
            if request["target"].get("collection_refs"):
                raise DomainError("unsupported_capability", "Collection sharing is not configured.")
            with self.database.transaction(principal) as conn:
                deadline = min(request["payload"].get("budget", {}).get("max_wall_time_ms", 10000), 30000)
                conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(deadline),))
                conn.execute("SELECT set_config('lock_timeout',%s,true)", (str(deadline),))
                brain = self._brain(conn, principal, request["target"]["brain_ref"])
                params = self._params(principal, brain["id"])
                key = request["payload"].get("idempotency_key", request["request_id"])
                fingerprint = digest({"action": action, "target": request["target"], "payload": request["payload"]})
                existing = conn.execute(
                    "SELECT request_hash,response FROM memory_requests "
                    "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND action=%s AND key=%s",
                    (*params, action, key),
                ).fetchone()
                if existing:
                    if existing["request_hash"] != fingerprint:
                        raise DomainError("idempotency_conflict", "Idempotency key has different content.")
                    if existing["response"] is None:
                        raise DomainError("version_conflict", "Cached response was invalidated; use a new request ID.")
                    response = copy.deepcopy(existing["response"])
                    response["request_id"] = request["request_id"]
                    return response
                result = getattr(self, "_" + action)(conn, principal, brain, request)
                envelope.update(outcome="completed", server_scope=self._scope(principal, brain, request["target"]), result=result)
                validate("ActionResponse", envelope)
                conn.execute(
                    "INSERT INTO memory_requests(tenant_id,owner_id,brain_id,action,key,request_hash,response) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (*params, action, key, fingerprint, Jsonb(envelope)),
                )
            return envelope
        except DomainError as error:
            envelope.pop("result", None)
            envelope.pop("server_scope", None)
            envelope.update(outcome="conflict" if "conflict" in error.code else "rejected", error=error.as_dict())
        except (psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable):
            envelope.pop("result", None)
            envelope.pop("server_scope", None)
            envelope.update(outcome="failed", error={"code": "budget_exceeded", "message": "Database deadline exceeded.", "retryable": True})
        except psycopg.OperationalError:
            envelope.update(outcome="failed", error={"code": "dependency_unavailable", "message": "Database is unavailable.", "retryable": True})
        return envelope

    def _advance(self, conn, principal, brain, *, scope=False):
        brain["dependency_generation"] += 1
        if scope:
            brain["scope_generation"] += 1
        conn.execute(
            "UPDATE memory_brains SET dependency_generation=%s,scope_generation=%s "
            "WHERE tenant_id=%s AND owner_id=%s AND id=%s",
            (brain["dependency_generation"], brain["scope_generation"], *self._params(principal, brain["id"])),
        )
        conn.execute(
            "UPDATE memory_requests SET response=NULL WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
            self._params(principal, brain["id"]),
        )

    def _job(self, conn, principal, brain, kind, payload, state="queued"):
        job_id = uid()
        conn.execute(
            "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (*self._params(principal, brain["id"]), job_id, kind, Jsonb(payload), state),
        )
        return {"job_id": job_id, "state": state, "revision": 1, "created_at": now(), "details": []}

    def _invalidate(self, conn, principal, brain, event_ids=(), memory_ids=()):
        rows = conn.execute(
            "WITH RECURSIVE affected(id) AS ("
            "SELECT id FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND (event_ids && %s::text[] OR id=ANY(%s::text[])) "
            "UNION SELECT a.id FROM memory_assertions a JOIN affected b ON b.id=ANY(a.memory_ids) "
            "WHERE a.tenant_id=%s AND a.owner_id=%s AND a.brain_id=%s) "
            "UPDATE memory_assertions SET invalidated=true WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id IN (SELECT id FROM affected) RETURNING id",
            (*self._params(principal, brain["id"]), list(event_ids), list(memory_ids),
             *self._params(principal, brain["id"]), *self._params(principal, brain["id"])),
        ).fetchall()
        return sorted({row["id"] for row in rows})

    def _append_event(self, conn, principal, brain, target, incoming, *, event_id=None, imported=False):
        params = self._params(principal, brain["id"])
        previous = conn.execute(
            "SELECT * FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND producer_ref=%s AND source_key=%s ORDER BY source_revision DESC LIMIT 1",
            (*params, incoming["producer_ref"], incoming["source_event_key"]),
        ).fetchone()
        input_hash = digest(incoming)
        if previous and (previous["conversation_id"] != target.get("conversation_ref") or previous["branch_id"] != target.get("branch_ref")):
            raise DomainError("idempotency_conflict", "Source event key belongs to a different conversation or branch.")
        if len(json.dumps(incoming, ensure_ascii=False).encode()) > 262144:
            raise DomainError("validation_failed", "One event may contain at most 256 KiB.")
        if previous and previous["source_revision"] == incoming["source_revision"]:
            if previous["input_hash"] != input_hash:
                raise DomainError("idempotency_conflict", "Source event version has different content.")
            return previous["id"], True, None
        if previous and (previous["deleted"] or previous["suppressed"]):
            raise DomainError("deletion_in_progress", "A deleted source cannot be reintroduced as an edit.")
        if previous and incoming["source_revision"] < previous["source_revision"]:
            raise DomainError("version_conflict", "Source revision is older than the recorded revision.")
        if previous:
            conn.execute(
                "UPDATE memory_events SET is_current=false WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (*params, previous["id"]),
            )
            self._invalidate(conn, principal, brain, event_ids=[previous["id"]])
            self._advance(conn, principal, brain)
        brain["durable_sequence"] += 1
        document = {
            "event_id": previous["id"] if previous else (event_id or uid()),
            "revision": previous["revision"] + 1 if previous else 1,
            "sequence": brain["durable_sequence"], "scope": self._scope(principal, brain, target),
            "observed": copy.deepcopy(incoming), "system_time": {"recorded_at": now(), "superseded_at": None},
            "content_sha256": digest(incoming["content"]),
            "origin": "document_import" if imported else {
                "user": "user_statement", "assistant": "assistant_output", "tool": "tool_observation",
                "operator": "operator_correction", "system": "system_observation", "external": "external_message",
            }[incoming["actor_role"]],
            "origin_verification": "unverified_import" if imported else "unknown",
            "previous_revision": previous["revision"] if previous else None,
            "utterance_segments": cognition.classify_segments(incoming["content"]),
        }
        validate("Event", document)
        conn.execute(
            "INSERT INTO memory_events(tenant_id,owner_id,brain_id,id,revision,sequence,producer_ref,source_key,"
            "source_revision,conversation_id,branch_id,content_hash,input_hash,document) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*params, document["event_id"], document["revision"], document["sequence"], incoming["producer_ref"],
             incoming["source_event_key"], incoming["source_revision"], target.get("conversation_ref"),
             target.get("branch_ref"), document["content_sha256"], input_hash, Jsonb(document)),
        )
        conn.execute(
            "UPDATE memory_brains SET durable_sequence=%s WHERE tenant_id=%s AND owner_id=%s AND id=%s",
            (brain["durable_sequence"], *params),
        )
        job = self._job(conn, principal, brain, "extract", {"event_id": document["event_id"], "revision": document["revision"]})
        return document["event_id"], False, job["job_id"]

    def _observe(self, conn, principal, brain, request):
        accepted, duplicates, jobs = [], [], []
        if len(request["payload"]["events"]) > 1000:
            raise DomainError("validation_failed", "An observation batch may contain at most 1000 events.")
        for event in request["payload"]["events"]:
            if event["producer_ref"] in ("memory-service", "archive-import") or event["event_kind"] == "source_correction":
                raise DomainError("invalid_evidence", "Correction audit records are created by the service.")
            event_id, duplicate, job_id = self._append_event(conn, principal, brain, request["target"], event)
            (duplicates if duplicate else accepted).append(event_id)
            if job_id:
                jobs.append(job_id)
        return {"accepted_event_ids": accepted, "duplicate_event_ids": duplicates,
                "durable_through_sequence": brain["durable_sequence"], "indexed_through_sequence": brain["indexed_sequence"],
                "consolidation_job_ids": jobs, "scope_generation": brain["scope_generation"]}

    def _events(self, conn, principal, brain, *, include_retired=False, event_ids=None,
                after_sequence=None, limit=512, descending=False):
        clauses, extra = [], []
        if event_ids is not None:
            clauses.append("id=ANY(%s::text[])")
            extra.append(list(event_ids))
        if after_sequence is not None:
            clauses.append("sequence>%s")
            extra.append(after_sequence)
        selection = " AND (" + " OR ".join(clauses) + ")" if clauses else ""
        ordering = " DESC" if descending else " ASC"
        return [self._rebind(row["document"], principal, brain) for row in conn.execute(
            "SELECT document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND is_current AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
            "AND (%s OR NOT retired)" + selection + " ORDER BY sequence" + ordering + " LIMIT %s",
            (*self._params(principal, brain["id"]), include_retired, *extra, limit),
        )]

    def _evidence(self, refs, events):
        lookup = {(e["event_id"], e["revision"]): e for e in events}
        for reference in refs:
            event = lookup.get((reference["event_id"], reference["event_revision"]))
            if not event or event["content_sha256"] != reference["source_content_sha256"]:
                raise DomainError("invalid_evidence", "Evidence is unavailable or stale.")
            span = reference.get("span")
            if span and not (0 <= span["start"] < span["end"] <= len(event["observed"]["content"].get("text", ""))):
                raise DomainError("invalid_evidence", "Evidence span is outside the source.")
        return [lookup[(r["event_id"], r["event_revision"])] for r in refs]

    def _working(self, conn, principal, brain, target, events):
        row = conn.execute(
            "SELECT document FROM memory_working_states WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND conversation_id=%s AND branch_id=%s",
            (*self._params(principal, brain["id"]), target.get("conversation_ref", ""), target.get("branch_ref", "")),
        ).fetchone()
        state = self._rebind(row["document"], principal, brain) if row else {
            "scope": self._scope(principal, brain, target), "revision": 0, "through_sequence": 0,
            "items": [], "open_loops": [], "updated_at": now(),
        }
        for field in ("items", "open_loops") if events is not None else ():
            retained = []
            for item in state[field]:
                try:
                    self._evidence(item["evidence_refs"], events)
                    retained.append(item)
                except DomainError:
                    pass
            state[field] = retained
        return state

    def _working_save(self, conn, principal, brain, target, state):
        conn.execute(
            "INSERT INTO memory_working_states(tenant_id,owner_id,brain_id,conversation_id,branch_id,revision,document) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,brain_id,conversation_id,branch_id) "
            "DO UPDATE SET revision=EXCLUDED.revision,document=EXCLUDED.document",
            (*self._params(principal, brain["id"]), target["conversation_ref"], target["branch_ref"], state["revision"], Jsonb(state)),
        )

    def _state(self, conn, principal, brain, candidate_total):
        rebuilding = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND kind='rebuild' AND state<>'completed') AS value", self._params(principal, brain["id"]),
        ).fetchone()["value"]
        return {"durable_sequence": brain["durable_sequence"], "indexed_sequence": brain["indexed_sequence"],
                "dependency_generation": brain["dependency_generation"], "scope_generation": brain["scope_generation"],
                "candidate_total": candidate_total, "rebuilding": rebuilding}

    def _superseded_evidence(self, conn, principal, brain, event_ids):
        if not event_ids:
            return []
        rows = conn.execute(
            "SELECT reference.value AS reference FROM memory_events e "
            "CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof("
            "e.document #> '{observed,content,structured,superseded_evidence_refs}')='array' THEN "
            "e.document #> '{observed,content,structured,superseded_evidence_refs}' ELSE '[]'::jsonb END) "
            "AS reference(value) WHERE e.tenant_id=%s AND e.owner_id=%s AND e.brain_id=%s "
            "AND e.is_current AND NOT e.deleted AND NOT e.suppressed "
            "AND e.producer_ref IN ('memory-service','archive-import') "
            "AND e.document #>> '{observed,event_kind}'='source_correction' "
            "AND reference.value->>'event_id'=ANY(%s::text[])",
            (*self._params(principal, brain["id"]), list(event_ids)),
        ).fetchall()
        references = []
        for row in rows:
            try:
                validate("EvidenceRef", row["reference"])
                references.append(row["reference"])
            except DomainError:
                continue
        return references

    def _current_captures(self, conn, principal, brain, event):
        superseded = self._superseded_evidence(conn, principal, brain, [event["event_id"]])
        return [memory for memory in cognition.extract_event(event)
                if not any(cognition.evidence_overlaps(reference, old)
                           for reference in memory["evidence_refs"] for old in superseded)]

    def _retrieval(self, conn, principal, brain, target, query):
        params = self._params(principal, brain["id"])
        candidate_limit = min(128, max(4, query["budget"]["max_context_tokens"] // 512))
        total = conn.execute(
            "SELECT count(*) AS value FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND is_current AND NOT invalidated AND NOT deleted AND NOT suppressed", params,
        ).fetchone()["value"]
        rows = conn.execute(
            "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND is_current AND NOT invalidated AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
            "AND embedding_model='lexical-feature-hash' AND embedding_version='v1-d256-blake2s' "
            "ORDER BY embedding <=> %s::vector NULLS LAST,id LIMIT %s", (*params, _vector(query["query"]), candidate_limit),
        ).fetchall()
        memories = [self._rebind(row["document"], principal, brain) for row in rows]
        raw_working = self._working(conn, principal, brain, target, None)
        source_ids = {r["event_id"] for memory in memories for r in memory["evidence_refs"]}
        source_ids.update(r["event_id"] for field in ("items", "open_loops") for item in raw_working[field] for r in item["evidence_refs"])
        source_ids.update(query.get("query_event_ids", []))
        source_ids.update(query["cue_event_ids"])
        if query.get("evidence_cutoff", {}).get("event_id"):
            source_ids.add(query["evidence_cutoff"]["event_id"])
        events = self._events(conn, principal, brain, event_ids=sorted(source_ids)[:1024], limit=1024)
        tail = self._events(conn, principal, brain, after_sequence=brain["indexed_sequence"], limit=129)
        truncated = len(source_ids) > 1024 or len(tail) > 128
        events = list({event["event_id"]: event for event in events + tail[:128]}.values())
        working = self._working(conn, principal, brain, target, events)
        state = self._state(conn, principal, brain, total)
        state["source_truncated"] = truncated
        state["superseded_evidence_refs"] = self._superseded_evidence(
            conn, principal, brain, [event["event_id"] for event in events]
        )
        state["working_event_metadata"] = {
            event["event_id"]: event for event in events
            if not (event["scope"]["conversation_id"] == target.get("conversation_ref")
                    and event["scope"]["branch_id"] is not None
                    and event["scope"]["branch_id"] != target.get("branch_ref"))
        }
        state["verified_working_evidence_refs"] = [r for field in ("items", "open_loops") for item in working[field] for r in item["evidence_refs"]]
        result = cognition.recall(self._scope(principal, brain, target), query, memories, events, state)
        self._save_traces(conn, principal, brain, result.get("recall_traces", []))
        return result, working, state

    def _recall(self, conn, principal, brain, request):
        return self._retrieval(conn, principal, brain, request["target"], request["payload"])[0]

    def _prepare(self, conn, principal, brain, request):
        payload = request["payload"]
        events = self._events(conn, principal, brain, event_ids=payload["latest_input_event_ids"], limit=128)
        lookup = {e["event_id"]: e for e in events}
        if any(i not in lookup for i in payload["latest_input_event_ids"]):
            raise DomainError("invalid_evidence", "Latest input event is unavailable.")
        latest = [lookup[i]["observed"]["content"].get("text", "") for i in payload["latest_input_event_ids"]]
        query = {"query": "\n".join(latest) or "current conversation", "intent": "continue_conversation",
                 "cue_event_ids": payload["latest_input_event_ids"], "query_event_ids": payload["latest_input_event_ids"],
                 "additional_cues": [], "read_fence": payload["read_fence"], "budget": payload["budget"]}
        query["budget"] = {**payload["budget"], "max_context_tokens": 64000}
        for field in ("evidence_mode", "evidence_cutoff"):
            if field in payload:
                query[field] = payload[field]
        result, working, state = self._retrieval(conn, principal, brain, request["target"], query)
        safe_payload = copy.deepcopy(payload)
        capabilities = safe_payload["capabilities"]
        capabilities["verification_state"] = "declared"
        capabilities["verified_at"] = None
        if capabilities["guarantee_level"] == "full_lifecycle":
            capabilities["guarantee_level"] = "partial_automation"
        capabilities["limitations"].append("Lifecycle guarantees have not been independently verified by this service.")
        packet = cognition.prepare(self._scope(principal, brain, request["target"]), safe_payload, working, result, state)
        self._save_traces(conn, principal, brain, packet.get("recall_traces", []))
        return packet

    def _usage(self, text, budget, elapsed=0):
        tokens = len(json.dumps(text, ensure_ascii=False, separators=(",", ":")).encode())
        if tokens > budget["max_context_tokens"]:
            raise DomainError("budget_exceeded", "Conservative context size exceeds the requested budget.")
        return {"context_tokens": tokens, "token_counting": "conservative_estimate", "model_calls": 0,
                "embedding_calls": 0, "expansion_steps": 0, "elapsed_ms": int(elapsed * 1000),
                "actual_cost_microunits": 0, "reserved_background_cost_microunits": 0,
                "currency": budget["currency"], "stopped_by": "completed"}

    def _inspect(self, conn, principal, brain, request):
        payload = request["payload"]
        params = self._params(principal, brain["id"])
        events = self._events(conn, principal, brain, include_retired=True,
                              event_ids=[r["event_id"] for r in payload["evidence_refs"]], limit=1024)
        memories, found_events, unavailable = [], {}, []
        for memory_id in payload["memory_ids"]:
            rows = conn.execute(
                "SELECT * FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
                "AND NOT deleted AND NOT suppressed AND document IS NOT NULL ORDER BY revision DESC",
                (*params, memory_id),
            ).fetchall()
            if not rows:
                unavailable.append({"requested_ref": memory_id, "reason": "not_found_or_forbidden"})
                continue
            for row in (rows if payload["include_corrections"] else rows[:1]):
                document = self._rebind(row["document"], principal, brain)
                if row["invalidated"]:
                    document["disclosure_basis"]["validation_state"] = "invalidated"
                    document["lifecycle"] = "superseded"
                memories.append(document)
        extra = self._events(conn, principal, brain, include_retired=True,
                             event_ids={r["event_id"] for m in memories for r in m["evidence_refs"]}, limit=1024)
        events = list({e["event_id"]: e for e in events + extra}.values())
        for reference in payload["evidence_refs"] + [r for m in memories for r in m["evidence_refs"]]:
            try:
                event = self._evidence([reference], events)[0]
                found_events[event["event_id"]] = event
                if payload["include_successors"]:
                    for following in self._events(conn, principal, brain, include_retired=True,
                                                   after_sequence=event["sequence"], limit=1):
                        found_events[following["event_id"]] = following
            except DomainError:
                unavailable.append({"requested_ref": reference["event_id"], "reason": "not_found_or_forbidden"})
        result = {"memories": memories, "events": list(found_events.values()), "unavailable_refs": unavailable}
        result["usage"] = self._usage(result, payload["budget"])
        return result

    def _publish(self, conn, principal, brain, memory):
        memory = self._rebind(memory, principal, brain)
        validate("MemoryAssertion", memory)
        conn.execute(
            "INSERT INTO memory_assertions(tenant_id,owner_id,brain_id,id,revision,document,event_ids,memory_ids,search_text,embedding) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector) ON CONFLICT(tenant_id,brain_id,id,revision) DO NOTHING",
            (*self._params(principal, brain["id"]), memory["memory_id"], memory["revision"], Jsonb(memory),
             list({r["event_id"] for r in memory["evidence_refs"]}),
             list({r["memory_id"] for r in memory["derivation"]["source_memory_versions"]}),
             memory["statement"], _vector(memory["statement"])),
        )
        return memory

    def _proposal(self, proposal, events):
        sources = self._evidence(proposal["evidence_refs"], events)
        if not sources:
            raise DomainError("invalid_evidence", "A published assertion requires source events.")
        candidates = [m for event in sources for m in cognition.extract_event(event)]
        for memory in candidates:
            source = next(e for e in sources if e["event_id"] == memory["evidence_refs"][0]["event_id"])
            raw = source["observed"]["content"].get("text", "")
            exact_capture = proposal["statement"] == memory["statement"]
            exact_direct_source = (
                proposal["statement"] == raw
                and source["observed"]["actor_role"] in ("user", "operator")
                and all(segment["act"] in ("direct_statement", "correction", "confirmation")
                        for segment in source["utterance_segments"])
            )
            if exact_capture or exact_direct_source:
                if proposal["memory_kind"] != memory["memory_kind"] or proposal["world_hint"] != memory["world"]:
                    raise DomainError("invalid_evidence", "Semantic or world promotion needs a validating provider.")
                return memory
        raise DomainError("invalid_evidence", "The deterministic extractor cannot substantiate this proposal.")

    def _save_traces(self, conn, principal, brain, traces):
        for trace in traces:
            validate("RecallTrace", trace)
            conn.execute(
                "INSERT INTO memory_traces(tenant_id,owner_id,brain_id,id,document) VALUES(%s,%s,%s,%s,%s) "
                "ON CONFLICT(tenant_id,brain_id,id) DO NOTHING",
                (*self._params(principal, brain["id"]), trace["trace_id"], Jsonb(trace)),
            )

    def _feedback(self, conn, principal, brain, target, payload, events):
        traces = []
        lookup = {e["event_id"]: e for e in events}
        for proposal in payload.get("recall_feedback", []):
            kind = proposal["kind"]
            if kind in ("candidate_hit", "context_selected", "quoted"):
                raise DomainError("invalid_evidence", "Internal trace kinds can only be emitted by service instrumentation.")
            memory = conn.execute(
                "SELECT id FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
                "AND is_current AND NOT deleted AND NOT suppressed AND NOT invalidated",
                (*self._params(principal, brain["id"]), proposal["memory_id"]),
            ).fetchone()
            if not memory:
                raise DomainError("invalid_evidence", "Feedback memory is unavailable.")
            source = "model_estimate"
            if kind != "background_influence":
                event = lookup.get(proposal["feedback_event_id"])
                required_role = "tool" if kind == "observed_outcome" else "user"
                if not event or event["observed"]["actor_role"] != required_role:
                    raise DomainError("invalid_evidence", "Feedback requires the actual external source.")
                if required_role == "tool" and event["origin_verification"] != "verified_producer":
                    raise DomainError("invalid_evidence", "Tool outcomes require a registered, verified producer.")
                self._evidence(proposal["evidence_refs"], events)
                if not any(r["event_id"] == event["event_id"] for r in proposal["evidence_refs"]):
                    raise DomainError("invalid_evidence", "Feedback evidence must refer to the external event.")
                text = event["observed"]["content"].get("text", "")
                if kind == "user_confirmed" and not re.search(r"^(yes|correct|confirmed|对|是的|没错|确认)", text, re.I):
                    raise DomainError("invalid_evidence", "The source is not an explicit confirmation.")
                if kind == "user_corrected" and not re.search(r"(不是|更正|改正|不对|actually|incorrect|correction|wrong)", text, re.I):
                    raise DomainError("invalid_evidence", "The source is not an explicit correction.")
                source = "tool_observation" if required_role == "tool" else "user_message"
            trace = {"trace_id": uid(), "recall_id": proposal["recall_id"], "retrieval_snapshot_id": "feedback:" + proposal["recall_id"],
                     "scope": self._scope(principal, brain, target), "turn_id": payload["turn_id"], "memory_id": proposal["memory_id"],
                     "kind": kind, "source": source, "feedback_event_id": proposal["feedback_event_id"] if source != "model_estimate" else None,
                     "evidence_refs": proposal["evidence_refs"], "recorded_at": now(),
                     "allowed_effect": "none" if source == "model_estimate" else "external_reassessment"}
            traces.append(trace)
        self._save_traces(conn, principal, brain, traces)
        return traces

    def _grounded_working_value(self, item, events, target):
        sources = self._evidence(item["evidence_refs"], events)
        for event, reference in zip(sources, item["evidence_refs"], strict=True):
            observed = event["observed"]
            if (observed["actor_role"] not in ("user", "operator")
                    or observed["interpretation_hint"] != "real_world_report"
                    or event["scope"]["conversation_id"] != target.get("conversation_ref")
                    or event["scope"]["branch_id"] != target.get("branch_ref")
                    or not event["utterance_segments"]
                    or any(segment["act"] not in ("direct_statement", "correction", "confirmation")
                           for segment in event["utterance_segments"])):
                continue
            raw = observed["content"].get("text", "")
            span = reference.get("span")
            full_source = span is None or (span["start"] == 0 and span["end"] == len(raw))
            if full_source and item["value"] == raw:
                return "\n".join(memory["statement"] for memory in cognition.extract_event(event))
            for memory in cognition.extract_event(event):
                evidence_span = memory["evidence_refs"][0].get("span")
                covered = span is None or (evidence_span is not None
                    and span["start"] <= evidence_span["start"]
                    and span["end"] >= evidence_span["end"])
                if covered and item["value"] == memory["statement"]:
                    return memory["statement"]
        raise DomainError("invalid_evidence", "Grounded working content must preserve a supported source statement.")

    def _commit(self, conn, principal, brain, request):
        payload, target = request["payload"], request["target"]
        needed = set(payload["delivered_event_ids"])
        needed.update(r["event_id"] for item in payload["proposed_items"] + payload["proposed_open_loops"] + payload["assertion_proposals"] for r in item["evidence_refs"])
        needed.update(r["event_id"] for item in payload.get("recall_feedback", []) for r in item["evidence_refs"])
        if len(needed) > 1024:
            raise DomainError("validation_failed", "A commit may reference at most 1024 source events.")
        events = self._events(conn, principal, brain, event_ids=needed, limit=1024)
        state = self._working(conn, principal, brain, target, events)
        if state["revision"] != payload["expected_working_revision"]:
            raise DomainError("version_conflict", "Working state changed.", current_revision=state["revision"])
        lookup = {e["event_id"]: e for e in events}
        for event_id in payload["delivered_event_ids"]:
            event = lookup.get(event_id)
            if not event or event["observed"]["delivery_state"] not in ("delivered", "partially_delivered"):
                raise DomainError("invalid_evidence", "Only actual delivered events can be committed.")
        for item in payload["proposed_items"] + payload["proposed_open_loops"]:
            self._evidence(item["evidence_refs"], events)
        items = copy.deepcopy(payload["proposed_items"])
        for item in items:
            if item["knowledge_state"] == "grounded":
                item["value"] = self._grounded_working_value(item, events, target)
        if any(loop["state"] != "unknown" for loop in payload["proposed_open_loops"]):
            raise DomainError("unsupported_capability", "Open-loop status validation requires a semantic provider.")
        state.update(revision=state["revision"] + 1, through_sequence=brain["durable_sequence"],
                     items=items, open_loops=payload["proposed_open_loops"], updated_at=now())
        self._working_save(conn, principal, brain, target, state)
        accepted, rejected = 0, 0
        for proposal in payload["assertion_proposals"]:
            try:
                memory = self._proposal(proposal, events)
                memory["memory_id"] = uid()
                self._publish(conn, principal, brain, memory)
                accepted += 1
            except DomainError:
                rejected += 1
        traces = self._feedback(conn, principal, brain, target, payload, events)
        return {"working_state": state, "accepted_proposal_count": accepted, "rejected_proposal_count": rejected,
                "consolidation_job_ids": [], "recall_traces": traces}

    def _coverage(self, brain, through):
        return {"durable_through_sequence": brain["durable_sequence"], "indexed_through_sequence": brain["indexed_sequence"],
                "tail_covered_through_sequence": brain["durable_sequence"], "snapshot_revision": brain["dependency_generation"],
                "read_fence_satisfied": brain["durable_sequence"] >= through, "search_exhaustive": True, "limitations": [],
                "dependency_generation": brain["dependency_generation"], "projection_generation": brain["dependency_generation"], "rebuilding": False}

    def _checkpoint(self, conn, principal, brain, request):
        payload = request["payload"]
        raw = self._working(conn, principal, brain, request["target"], None)
        needed = {r["event_id"] for field in ("items", "open_loops") for item in raw[field] for r in item["evidence_refs"]}
        state = self._working(conn, principal, brain, request["target"],
                              self._events(conn, principal, brain, event_ids=needed, limit=1024))
        if state["revision"] != payload["expected_working_revision"]:
            raise DomainError("version_conflict", "Working state changed.", current_revision=state["revision"])
        self._working_save(conn, principal, brain, request["target"], state)
        safe = payload["through_sequence"] <= brain["durable_sequence"]
        return {"checkpoint_id": uid(), "working_revision": state["revision"], "coverage": self._coverage(brain, payload["through_sequence"]),
                "restore_strategy": ("indexed_state" if brain["indexed_sequence"] >= payload["through_sequence"] else "state_plus_unindexed_tail") if safe else "not_ready",
                "compaction_safe": safe, "created_at": now()}

    def _correct(self, conn, principal, brain, request):
        payload, target = request["payload"], request["target"]
        params = self._params(principal, brain["id"])
        row = conn.execute(
            "SELECT * FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND is_current "
            "AND NOT deleted AND NOT suppressed", (*params, payload["memory_id"]),
        ).fetchone()
        if not row:
            raise DomainError("not_found_or_forbidden", "Memory is unavailable.")
        if row["revision"] != payload["expected_revision"] or brain["dependency_generation"] != payload["expected_dependency_generation"]:
            raise DomainError("version_conflict", "Memory or dependency generation changed.", current_revision=row["revision"])
        needed = set(payload["correction_event_ids"])
        needed.update(r["event_id"] for r in payload.get("replacement", {}).get("evidence_refs", []))
        events = self._events(conn, principal, brain, event_ids=needed, limit=1024)
        lookup = {e["event_id"]: e for e in events}
        if any(i not in lookup for i in payload["correction_event_ids"]):
            raise DomainError("invalid_evidence", "Correction source is unavailable.")
        if any(lookup[i]["observed"]["actor_role"] not in ("user", "operator", "tool") for i in payload["correction_event_ids"]):
            raise DomainError("invalid_evidence", "Correction requires an external source.")
        updated = copy.deepcopy(row["document"])
        if payload["operation"] != "retract":
            updated = self._proposal(payload["replacement"], events)
            if not {r["event_id"] for r in updated["evidence_refs"]}.issubset(set(payload["correction_event_ids"])):
                raise DomainError("invalid_evidence", "Replacement must be supported by the correction sources.")
        old_generation = brain["dependency_generation"]
        self._advance(conn, principal, brain)
        old_evidence = copy.deepcopy(row["document"]["evidence_refs"])
        overlapping = conn.execute(
            "SELECT id,document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s "
            "AND brain_id=%s AND is_current AND NOT deleted AND NOT suppressed "
            "AND document IS NOT NULL AND event_ids && %s::text[]",
            (*params, row["event_ids"]),
        ).fetchall()
        affected_ids = {row["id"]}
        for candidate in overlapping:
            if any(cognition.evidence_overlaps(reference, old)
                   for reference in candidate["document"]["evidence_refs"] for old in old_evidence):
                affected_ids.add(candidate["id"])
        invalidated = self._invalidate(conn, principal, brain, memory_ids=sorted(affected_ids))
        conn.execute(
            "UPDATE memory_assertions SET is_current=false WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, row["id"]),
        )
        updated.update(memory_id=row["id"], revision=row["revision"] + 1,
                       lifecycle="retracted" if payload["operation"] == "retract" else "active",
                       system_time={"recorded_at": now(), "superseded_at": None})
        updated["derivation"]["method"] = "human_correction"
        updated = self._publish(conn, principal, brain, updated)
        if payload["operation"] == "retract":
            conn.execute(
                "UPDATE memory_assertions SET invalidated=true WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (*params, row["id"]),
            )
        audit = {"source_event_key": "correction:" + uid(), "source_revision": 1, "producer_ref": "memory-service",
                 "actor_ref": principal.principal_id, "actor_role": "operator", "event_kind": "source_correction",
                 "content": {"media_type": "application/json", "structured": {"memory_id": row["id"], "operation": payload["operation"], "source_event_ids": payload["correction_event_ids"], "superseded_evidence_refs": old_evidence}},
                 "occurred_time": {"start": now(), "end": None, "precision": "exact", "basis": "source_explicit"},
                 "interpretation_hint": "real_world_report", "delivery_state": "received"}
        correction_id, _, _ = self._append_event(conn, principal, brain, target, audit)
        job = self._job(conn, principal, brain, "rebuild", {"event_ids": payload["correction_event_ids"]})
        return {"updated_memory": updated, "invalidated_derivation_ids": invalidated, "rebuild_job": job,
                "transaction": {"correction_id": correction_id, "committed_at": now(), "correction_event_ids": payload["correction_event_ids"],
                                "durable_through_sequence": brain["durable_sequence"], "previous_dependency_generation": old_generation,
                                "dependency_generation": brain["dependency_generation"], "stale_projection_reads_blocked": True}}

    def _forget(self, conn, principal, brain, request):
        self._archive_guard(conn, principal)
        payload = request["payload"]
        if brain["scope_generation"] != payload["expected_scope_generation"]:
            raise DomainError("version_conflict", "Scope generation changed.", current_revision=brain["scope_generation"])
        params = self._params(principal, brain["id"])
        kind, ids = payload["selector"]["kind"], payload["selector"]["ids"]
        event_ids, memory_ids = set(), set()
        if kind == "memories":
            rows = conn.execute(
                "SELECT id,event_ids FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND id=ANY(%s::text[])", (*params, ids),
            ).fetchall()
            for row in rows:
                memory_ids.add(row["id"])
                event_ids.update(row["event_ids"])
        else:
            condition = {"events": "id=ANY(%s::text[])", "conversation": "conversation_id=ANY(%s::text[])",
                         "all_authorized_scope": "true"}[kind]
            rows = conn.execute(
                "SELECT DISTINCT id FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND " + condition,
                (*params, ids) if kind != "all_authorized_scope" else params,
            ).fetchall()
            event_ids.update(row["id"] for row in rows)
        memory_ids.update(self._invalidate(conn, principal, brain, event_ids, memory_ids))
        self._advance(conn, principal, brain, scope=True)
        for object_kind, object_ids in (("event", event_ids), ("memory", memory_ids)):
            for object_id in object_ids:
                conn.execute(
                    "INSERT INTO memory_tombstones(tenant_id,owner_id,brain_id,object_kind,object_id,mode) "
                    "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,owner_id,brain_id,object_kind,object_id) "
                    "DO UPDATE SET mode=CASE WHEN memory_tombstones.mode='erase_content' THEN 'erase_content' ELSE EXCLUDED.mode END",
                    (*params, object_kind, object_id, payload["mode"]),
                )
        erase = payload["mode"] == "erase_content"
        conn.execute(
            "UPDATE memory_events SET suppressed=true,deleted=deleted OR %s,document=CASE WHEN %s THEN NULL ELSE document END "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s::text[])",
            (erase, erase, *params, list(event_ids)),
        )
        conn.execute(
            "UPDATE memory_assertions SET suppressed=true,deleted=deleted OR %s,"
            "document=CASE WHEN %s THEN NULL ELSE document END,search_text='',embedding=NULL "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s::text[])",
            (erase, erase, *params, list(memory_ids)),
        )
        conn.execute("DELETE FROM memory_working_states WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s", params)
        conn.execute("DELETE FROM memory_traces WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s", params)
        if erase:
            for archive in conn.execute(
                "SELECT id,brain_id,document FROM memory_archives WHERE tenant_id=%s AND owner_id=%s",
                (principal.tenant_id, principal.principal_id),
            ).fetchall():
                data = self._sanitize_archive(conn, principal, archive["document"])
                if data != archive["document"]:
                    conn.execute(
                        "UPDATE memory_archives SET document=%s,manifest_hash=%s "
                        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                        (Jsonb(data), digest(data), principal.tenant_id, principal.principal_id, archive["brain_id"], archive["id"]),
                    )
        return {"deletion_id": uid(), "mode": payload["mode"], "scope_generation": brain["scope_generation"],
                "recall_blocked_at": now(), "purge_state": "completed" if erase else "not_requested",
                "backup_expiry_deadline": None, "tombstone_recorded": True,
                "affected_object_count": len(event_ids) + len(memory_ids)}

    def _archive_guard(self, conn, principal):
        key = digest([principal.tenant_id, principal.principal_id, "archive-erasure"])
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key,))

    def _sanitize_archive(self, conn, principal, document):
        data = copy.deepcopy(document)
        identities = {e["event_id"] for e in data["events"]}
        identities.update(m["memory_id"] for m in data["memories"])
        identities.update(r["event_id"] for m in data["memories"] for r in m["evidence_refs"])
        tombstones = [dict(row) for row in conn.execute(
            "SELECT object_kind,object_id,mode FROM memory_tombstones "
            "WHERE tenant_id=%s AND owner_id=%s AND object_id=ANY(%s::text[])",
            (principal.tenant_id, principal.principal_id, sorted(identities)),
        )] + data["tombstones"]
        erased_events = {t["object_id"] for t in tombstones
                         if t["object_kind"] == "event" and t["mode"] == "erase_content"}
        erased_memories = {t["object_id"] for t in tombstones
                           if t["object_kind"] == "memory" and t["mode"] == "erase_content"}
        removed_events = {e["event_id"] for e in data["events"] if e["event_id"] in erased_events}
        while True:
            invalidated = {
                m["memory_id"] for m in data["memories"]
                if any(r["event_id"] in erased_events for r in m["evidence_refs"])
                or any(r["event_id"] in erased_events for path in m["disclosure_basis"]["support_paths"] for r in path["evidence_refs"])
                or any(r["memory_id"] in erased_memories for r in m["derivation"]["source_memory_versions"])
            }
            if invalidated.issubset(erased_memories):
                break
            erased_memories.update(invalidated)
        original_memory_count = len(data["memories"])
        data["events"] = [e for e in data["events"] if e["event_id"] not in erased_events]
        data["memories"] = [m for m in data["memories"] if m["memory_id"] not in erased_memories]
        if removed_events or len(data["memories"]) != original_memory_count:
            data["working_states"] = []
        data["excluded_deleted_event_ids"] = sorted(set(data.get("excluded_deleted_event_ids", [])) | removed_events)
        data["retired_event_ids"] = [i for i in data.get("retired_event_ids", []) if i not in erased_events]
        data["tombstones"] = list({(t["object_kind"], t["object_id"], t["mode"]): t for t in tombstones}.values())
        return data

    def _archive(self, conn, principal, archive_id):
        self._archive_guard(conn, principal)
        row = conn.execute(
            "SELECT a.* FROM memory_archives a JOIN memory_brains b ON "
            "a.tenant_id=b.tenant_id AND a.owner_id=b.owner_id AND a.brain_id=b.id "
            "WHERE a.tenant_id=%s AND a.owner_id=%s AND a.id=%s",
            (principal.tenant_id, principal.principal_id, archive_id),
        ).fetchone()
        if not row:
            raise DomainError("not_found_or_forbidden", "Archive is unavailable.")
        sanitized = self._sanitize_archive(conn, principal, row["document"])
        if sanitized != row["document"]:
            row["document"], row["manifest_hash"] = sanitized, digest(sanitized)
            conn.execute(
                "UPDATE memory_archives SET document=%s,manifest_hash=%s "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb(sanitized), row["manifest_hash"], principal.tenant_id,
                 principal.principal_id, row["brain_id"], archive_id),
            )
        return row

    def _export(self, conn, principal, brain, request):
        self._archive_guard(conn, principal)
        payload = request["payload"]
        params = self._params(principal, brain["id"])
        memories = [self._rebind(row["document"], principal, brain) for row in conn.execute(
            "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND is_current AND NOT invalidated AND NOT suppressed AND NOT deleted AND document IS NOT NULL", params,
        )] if payload["include_assertions"] else []
        states = [self._rebind(row["document"], principal, brain) for row in conn.execute(
            "SELECT document FROM memory_working_states WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s", params,
        )] if payload["include_working_states"] else []
        tombstones = [dict(row) for row in conn.execute(
            "SELECT object_kind,object_id,mode FROM memory_tombstones WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s", params,
        )]
        data = {"format": "agent-memory-jsonl-bundle", "format_version": CONTRACT_VERSION,
                "events": self._events(conn, principal, brain, include_retired=True, limit=5001) if payload["include_events"] else [],
                "memories": memories, "working_states": states, "tombstones": tombstones,
                "source_brain_id": brain["id"], "created_at": now()}
        data["retired_event_ids"] = [r["id"] for r in conn.execute(
            "SELECT DISTINCT id FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND retired", params)]
        self._validate_archive(data)
        data = self._sanitize_archive(conn, principal, data)
        archive_id, manifest = uid(), digest(data)
        conn.execute(
            "INSERT INTO memory_archives(tenant_id,owner_id,brain_id,id,manifest_hash,document) VALUES(%s,%s,%s,%s,%s,%s)",
            (*params, archive_id, manifest, Jsonb(data)),
        )
        job = self._job(conn, principal, brain, "export", {"archive_id": archive_id}, state="completed")
        includes = ["provenance", "tombstones", "model_versions"]
        includes += [name for name, flag in (("events", "include_events"), ("assertions", "include_assertions"), ("working_states", "include_working_states")) if payload[flag]]
        return {"archive_id": archive_id, "format": data["format"], "format_version": CONTRACT_VERSION,
                "manifest_sha256": manifest, "status": "ready", "download_ref": archive_id,
                "expires_at": None, "includes": includes, "job": job}

    def download_archive(self, principal, archive_id):
        with self.database.transaction(principal) as conn:
            archive = self._archive(conn, principal, archive_id)
            return {"archive_id": archive["id"], "manifest_sha256": archive["manifest_hash"], "document": archive["document"]}

    def _validate_archive(self, document):
        if not isinstance(document, dict) or document.get("format") != "agent-memory-jsonl-bundle" or document.get("format_version") != CONTRACT_VERSION:
            raise DomainError("invalid_archive", "Unsupported archive format.")
        if len(json.dumps(document, ensure_ascii=False).encode()) > 16_000_000:
            raise DomainError("invalid_archive", "Archive exceeds the 16 MB import limit.")
        for field, definition in (("events", "Event"), ("memories", "MemoryAssertion"), ("working_states", "WorkingState")):
            if not isinstance(document.get(field), list) or len(document[field]) > 5000:
                raise DomainError("invalid_archive", "Archive collection is invalid or too large.")
            for item in document[field]:
                validate(definition, item)
        if not isinstance(document.get("tombstones"), list):
            raise DomainError("invalid_archive", "Archive requires a deletion manifest.")
        for tombstone in document["tombstones"]:
            if set(tombstone) != {"object_kind", "object_id", "mode"} or tombstone["object_kind"] not in ("event", "memory") or tombstone["mode"] not in ("suppress_recall", "erase_content") or not isinstance(tombstone["object_id"], str):
                raise DomainError("invalid_archive", "Invalid deletion manifest.")
        for event in document["events"]:
            if digest(event["observed"]["content"]) != event["content_sha256"]:
                raise DomainError("invalid_archive", "Archive event hash does not match its content.")
        if not isinstance(document.get("retired_event_ids", []), list) or not all(isinstance(x, str) for x in document.get("retired_event_ids", [])):
            raise DomainError("invalid_archive", "Invalid retired source manifest.")
        excluded = document.get("excluded_deleted_event_ids", [])
        if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
            raise DomainError("invalid_archive", "Invalid excluded source manifest.")
        by_id = {m["memory_id"]: m for m in document["memories"]}
        visiting, visited = set(), set()
        def visit(memory_id):
            if memory_id in visiting:
                raise DomainError("invalid_archive", "Archive contains circular memory dependencies.")
            if memory_id in visited or memory_id not in by_id:
                return
            visiting.add(memory_id)
            for dependency in by_id[memory_id]["derivation"]["source_memory_versions"]:
                visit(dependency["memory_id"])
            visiting.remove(memory_id)
            visited.add(memory_id)
        for memory_id in by_id:
            visit(memory_id)

    def upload_archive(self, principal, brain_id, document):
        self._validate_archive(document)
        with self.database.transaction(principal) as conn:
            brain = self._brain(conn, principal, brain_id)
            self._archive_guard(conn, principal)
            document = self._sanitize_archive(conn, principal, document)
            archive_id, manifest = uid(), digest(document)
            conn.execute(
                "INSERT INTO memory_archives(tenant_id,owner_id,brain_id,id,manifest_hash,document) VALUES(%s,%s,%s,%s,%s,%s)",
                (*self._params(principal, brain_id), archive_id, manifest, Jsonb(document)),
            )
            return {"archive_id": archive_id, "manifest_sha256": manifest, "brain_id": brain["id"]}

    def _restore(self, conn, principal, brain, request):
        payload = request["payload"]
        if brain["scope_generation"] != payload["expected_scope_generation"]:
            raise DomainError("version_conflict", "Scope generation changed.", current_revision=brain["scope_generation"])
        if payload["mode"] == "new_brain" and brain["durable_sequence"]:
            raise DomainError("version_conflict", "new_brain restore requires an empty target brain.")
        archive = self._archive(conn, principal, payload["uploaded_archive_ref"])
        data = archive["document"]
        if archive["manifest_hash"] != payload["expected_manifest_sha256"] or digest(data) != archive["manifest_hash"]:
            raise DomainError("invalid_archive", "Archive manifest does not match.")
        self._validate_archive(data)
        params = self._params(principal, brain["id"])
        tombstones = [dict(row) for row in conn.execute(
            "SELECT t.object_kind,t.object_id,t.mode FROM memory_tombstones t JOIN memory_brains b ON "
            "t.tenant_id=b.tenant_id AND t.owner_id=b.owner_id AND t.brain_id=b.id "
            "WHERE t.tenant_id=%s AND t.owner_id=%s", (principal.tenant_id, principal.principal_id),
        )] + data["tombstones"]
        blocked_events = {t["object_id"] for t in tombstones if t["object_kind"] == "event"}
        blocked_memories = {t["object_id"] for t in tombstones if t["object_kind"] == "memory"}
        self._advance(conn, principal, brain, scope=True)
        for tombstone in tombstones:
            conn.execute(
                "INSERT INTO memory_tombstones(tenant_id,owner_id,brain_id,object_kind,object_id,mode) VALUES(%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(tenant_id,owner_id,brain_id,object_kind,object_id) DO NOTHING",
                (*params, tombstone["object_kind"], tombstone["object_id"], tombstone["mode"]),
            )
        imported, quarantined = 0, 0
        excluded = len(set(data.get("excluded_deleted_event_ids", [])))
        for event in sorted(data["events"], key=lambda e: e["sequence"]):
            if event["event_id"] in blocked_events:
                excluded += 1
                continue
            existing = conn.execute(
                "SELECT content_hash FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND is_current",
                (*params, event["event_id"]),
            ).fetchone()
            if existing:
                if existing["content_hash"] != event["content_sha256"]:
                    quarantined += 1
                continue
            incoming = copy.deepcopy(event["observed"])
            incoming["producer_ref"] = "archive-import"
            incoming["source_event_key"] = event["event_id"]
            target = {"conversation_ref": event["scope"]["conversation_id"], "branch_ref": event["scope"]["branch_id"]}
            self._append_event(conn, principal, brain, target, incoming, event_id=event["event_id"], imported=True)
            if event["event_id"] in data.get("retired_event_ids", []):
                conn.execute("UPDATE memory_events SET retired=true WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                             (*params, event["event_id"]))
            imported += 1
        available = self._events(conn, principal, brain, event_ids=[e["event_id"] for e in data["events"]], limit=5000)
        lookup = {e["event_id"]: e for e in available}
        for memory in data["memories"]:
            if memory["memory_id"] in blocked_memories or any(r["event_id"] in blocked_events for r in memory["evidence_refs"]):
                continue
            updated = copy.deepcopy(memory)
            try:
                all_refs = updated["evidence_refs"] + [r for path in updated["disclosure_basis"]["support_paths"] for r in path["evidence_refs"]]
                for reference in all_refs:
                    event = lookup.get(reference["event_id"])
                    if not event or reference["source_content_sha256"] != event["content_sha256"]:
                        raise DomainError("invalid_evidence", "Imported source is unavailable.")
                    reference["event_revision"] = event["revision"]
                self._evidence(updated["evidence_refs"], available)
                updated["revision"] = 1
                updated["system_time"] = {"recorded_at": now(), "superseded_at": None}
                self._publish(conn, principal, brain, updated)
            except DomainError:
                quarantined += 1
        for working in data["working_states"]:
            updated = self._rebind(working, principal, brain)
            for field in ("items", "open_loops"):
                kept = []
                for item in updated[field]:
                    try:
                        self._evidence(item["evidence_refs"], available)
                        kept.append(item)
                    except DomainError:
                        pass
                updated[field] = kept
            target = {"conversation_ref": updated["scope"]["conversation_id"], "branch_ref": updated["scope"]["branch_id"]}
            if all(target.values()):
                current = self._working(conn, principal, brain, target, available)
                if current["revision"] == 0:
                    updated.update(revision=1, through_sequence=brain["durable_sequence"], updated_at=now())
                    self._working_save(conn, principal, brain, target, updated)
        job = self._job(conn, principal, brain, "restore", {"archive_id": archive["id"]}, state="completed")
        return {"job": job, "scope_generation": brain["scope_generation"], "imported_event_count": imported,
                "quarantined_event_count": quarantined, "excluded_deleted_count": excluded, "read_enabled": True}

    def work_once(self, principal, brain_id):
        job_id = None
        try:
            with self.database.transaction(principal) as conn:
                brain = self._brain(conn, principal, brain_id)
                params = self._params(principal, brain_id)
                job = conn.execute(
                    "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                    "AND state IN ('queued','failed') AND attempts<3 ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED", params,
                ).fetchone()
                if not job:
                    return 0
                job_id = job["id"]
                if job["kind"] == "extract":
                    row = conn.execute(
                        "SELECT document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
                        "AND revision=%s AND is_current AND NOT retired AND NOT deleted AND NOT suppressed AND document IS NOT NULL",
                        (*params, job["payload"]["event_id"], job["payload"]["revision"]),
                    ).fetchone()
                    if row:
                        event = self._rebind(row["document"], principal, brain)
                        for memory in self._current_captures(conn, principal, brain, event):
                            self._publish(conn, principal, brain, memory)
                elif job["kind"] == "rebuild":
                    event_ids = job["payload"].get("event_ids", [])
                    if len(event_ids) > 128:
                        self._job(conn, principal, brain, "rebuild", {"event_ids": event_ids[128:]})
                    for event in self._events(conn, principal, brain, event_ids=event_ids[:128], limit=128):
                        for memory in self._current_captures(conn, principal, brain, event):
                            self._publish(conn, principal, brain, memory)
                conn.execute(
                    "UPDATE memory_jobs SET state='completed',attempts=attempts+1,last_error=NULL,completed_at=now() "
                    "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s", (*params, job_id),
                )
                pending = conn.execute(
                    "SELECT min(e.sequence) AS value FROM memory_events e JOIN memory_jobs j ON "
                    "j.tenant_id=e.tenant_id AND j.brain_id=e.brain_id AND j.payload->>'event_id'=e.id "
                    "AND (j.payload->>'revision')::bigint=e.revision "
                    "WHERE e.tenant_id=%s AND e.owner_id=%s AND e.brain_id=%s AND j.kind='extract' AND j.state<>'completed'", params,
                ).fetchone()["value"]
                indexed = brain["durable_sequence"] if pending is None else pending - 1
                conn.execute(
                    "UPDATE memory_brains SET indexed_sequence=%s WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                    (indexed, *params),
                )
                return 1
        except Exception as error:
            if job_id:
                with self.database.transaction(principal) as conn:
                    conn.execute(
                        "UPDATE memory_jobs SET state='failed',attempts=attempts+1,last_error=%s "
                        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                        (type(error).__name__, *self._params(principal, brain_id), job_id),
                    )
            raise

    def dashboard(self, principal, brain_id):
        with self.database.transaction(principal) as conn:
            brain = self._brain(conn, principal, brain_id)
            params = self._params(principal, brain_id)
            events = list(reversed(self._events(conn, principal, brain, limit=100, descending=True)))
            memories = [self._rebind(row["document"], principal, brain) for row in conn.execute(
                "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND is_current AND NOT invalidated AND NOT deleted AND NOT suppressed AND document IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 100", params,
            )]
            jobs = [dict(row) for row in conn.execute(
                "SELECT id,kind,state,attempts,last_error FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "ORDER BY created_at DESC LIMIT 100", params,
            )]
            return {"brain": self._public_brain(brain), "events": events[-100:], "memories": memories, "jobs": jobs,
                    "status": {"pending_jobs": sum(j["state"] != "completed" for j in jobs),
                               "index_lag": brain["durable_sequence"] - brain["indexed_sequence"],
                               "extraction": "deterministic_source_capture", "candidate_index": "lexical-feature-hash/v1-d256-blake2s"}}
