import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from . import cognition
from .consolidation_planning import plan_consolidation
from .helpers import DomainError, digest, now, uid
from .jobs import JobStore
from .learning_windows import record_progress, recover_windows, select_window
from .semantic import CognitiveBatch, SemanticResult
from .turns import TurnStore


def validate_snapshot(conn, service, principal, brain, result):
    scope = result.get("scope", {})
    if scope.get("scope_generation") != brain["scope_generation"]:
        raise DomainError("context_stale", "Cognitive scope changed.")
    if "dependency_generation" in result and result["dependency_generation"] != brain["dependency_generation"]:
        raise DomainError("context_stale", "Cognitive dependency generation changed.")
    refs = result.get("source_snapshot", [])
    events = service._events(conn, principal, brain, event_ids=[r["event_id"] for r in refs], limit=128)
    lookup = {(e["event_id"], e["revision"]): e for e in events}
    for ref in refs:
        event = lookup.get((ref["event_id"], ref["revision"]))
        if (event is None or event["content_sha256"] != ref["content_sha256"]
                or not cognition._scope_allows(event["scope"], scope)):
            raise DomainError("source_unavailable", "Cognitive source changed.")
    if service._superseded_evidence(conn, principal, brain, [r["event_id"] for r in refs]):
        raise DomainError("context_stale", "Cognitive source was corrected.")
    for ref in result.get("memory_snapshot", []):
        row = conn.execute(
            "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND NOT invalidated AND NOT deleted AND NOT suppressed",
            (*service._params(principal, brain["id"]), ref["memory_id"], ref["revision"]),
        ).fetchone()
        if (not row or row["document"] is None or row["document"]["lifecycle"] == "retracted"
                or digest(row["document"]) != ref["content_sha256"]
                or not cognition._scope_allows(service._rebind(row["document"], principal, brain)["scope"], scope)):
            raise DomainError("source_unavailable", "Cognitive parent changed.")
    for ref in result.get("episode_snapshot", []):
        row = conn.execute(
            "SELECT document FROM memory_episodes WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND is_current AND NOT invalidated AND NOT deleted "
            "AND document IS NOT NULL",
            (*service._params(principal, brain["id"]), ref["episode_id"], ref["revision"]),
        ).fetchone()
        if (row is None or digest(row["document"]) != ref["content_sha256"]
                or not cognition._scope_allows(service._rebind(row["document"], principal, brain)["scope"], scope)):
            raise DomainError("context_stale", "Cognitive episode changed.")
    for ref in result.get("belief_relation_snapshot", []):
        row = conn.execute(
            "SELECT document FROM memory_belief_relations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND NOT invalidated AND NOT deleted AND document IS NOT NULL",
            (*service._params(principal, brain["id"]), ref["relation_id"], ref["revision"]),
        ).fetchone()
        if row is None:
            raise DomainError("context_stale", "Cognitive belief relationship changed.")
        document = service._rebind(row["document"], principal, brain)
        if digest(document) != ref["content_sha256"] or not cognition._scope_allows(document["scope"], scope):
            raise DomainError("context_stale", "Cognitive belief relationship changed.")
    return events


def orientation_result(conn, service, principal, brain, row):
    if not row.get("orientation_job_id"):
        return None
    job = conn.execute(
        "SELECT kind,payload,state,result FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
        (*service._params(principal, brain["id"]), row["orientation_job_id"]),
    ).fetchone()
    if not job or job["state"] in ("cancelled", "superseded"):
        raise DomainError("context_stale", "Orientation was invalidated.")
    if job["kind"] != "orient" or job["payload"].get("turn_id") != row["turn_id"]:
        raise DomainError("invalid_evidence", "Orientation belongs to another turn.")
    if job["state"] != "completed" or not job["result"]:
        return None
    result = job["result"]
    scope = result.get("scope", {})
    if (scope.get("conversation_id"), scope.get("branch_id")) != (row["conversation_id"], row["branch_id"]):
        raise DomainError("invalid_evidence", "Orientation belongs to another context.")
    validate_snapshot(conn, service, principal, brain, result)
    return result


def orientation_updates(conn, service, principal, brain, row):
    result = orientation_result(conn, service, principal, brain, row)
    if not result:
        return []
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    return [{"key": "situation:" + item["key"],
             "value": {"orientation_job_id": row["orientation_job_id"],
                       **{key: item[key] for key in ("kind", "value", "state", "world", "conditions", "verification", "observation")
                          if key in item}},
             "knowledge_state": "inferred", "evidence_refs": item["evidence_refs"], "expires_at": expires,
             "updated_at": now()}
            for item in result.get("situation_updates", [])]


class LearningEngine:
    def __init__(self, service):
        self.service = service
        self.jobs = JobStore(service.database)

    def _resume_snapshot(self, conn, principal, brain, job, snapshot):
        service = self.service
        events = validate_snapshot(conn, service, principal, brain, snapshot)
        documents = []
        for field, table, identifier in (("memory_snapshot", "memory_assertions", "memory_id"),
                                         ("episode_snapshot", "memory_episodes", "episode_id")):
            current = []
            for reference in snapshot.get(field, []):
                row = conn.execute(
                    "SELECT document FROM " + table + " WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                    "AND id=%s AND revision=%s",
                    (*service._params(principal, brain["id"]), reference[identifier], reference["revision"]),
                ).fetchone()
                current.append(service._rebind(row["document"], principal, brain))
            documents.append(tuple(current))
        return CognitiveBatch(
            job["id"], snapshot["scope"], tuple(events), documents[0], snapshot["working_snapshot"], documents[1],
            sampling=snapshot["sampling"], belief_relations=tuple(snapshot.get("belief_relations", ())),
            consolidation_plan=snapshot.get("consolidation_plan"),
        ), snapshot

    def _snapshot(self, principal, brain_id, job):
        service = self.service
        with service.database.transaction(principal) as conn:
            brain = service._brain(conn, principal, brain_id)
            params = service._params(principal, brain_id)
            payload = job["payload"]
            prior = conn.execute("SELECT result FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                                 (*params, job["id"])).fetchone()["result"]
            windowed = job["kind"] == "consolidate" and "from_sequence" in payload
            if windowed and prior and "working_snapshot" in prior:
                snapshot = {key: copy.deepcopy(value) for key, value in prior.items() if key in {
                    "scope", "source_snapshot", "memory_snapshot", "episode_snapshot", "working_revision",
                    "working_snapshot", "sampling", "learning_window", "learning_input_refs", "dependency_generation",
                    "belief_relations", "belief_relation_snapshot", "consolidation_plan",
                }}
                return self._resume_snapshot(conn, principal, brain, job, snapshot)
            if "event_id" in payload:
                source = service._events(conn, principal, brain, event_ids=[payload["event_id"]], limit=1)
                if not source or source[0]["revision"] != payload["revision"]:
                    raise DomainError("source_unavailable", "Consolidation trigger changed.")
            target = payload.get("target", {})
            scope = service._scope(principal, brain, target)
            from .neighborhood import Neighborhood, select_neighborhood

            required = []
            window = None
            through = payload["through_sequence"]
            if windowed:
                required, window = select_window(conn, service, principal, brain, payload,
                                                policy=service.semantic_processor.policy)
                through = window["attempted_through_sequence"]
            elif "event_id" in payload:
                required = [{"event_id": payload["event_id"], "revision": payload["revision"]}]
            elif "turn_id" in payload:
                turn = conn.execute(
                    "SELECT input_event_refs FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s "
                    "AND brain_id=%s AND turn_id=%s AND conversation_id=%s AND branch_id=%s",
                    (*params, payload["turn_id"], target.get("conversation_ref"), target.get("branch_ref")),
                ).fetchone()
                if turn is None:
                    raise DomainError("source_unavailable", "Orientation input turn is unavailable.")
                required = turn["input_event_refs"]
            neighborhood = (select_neighborhood(
                conn, service, principal, brain, target, through_sequence=through,
                required_event_refs=required, policy=service.semantic_processor.policy,
            ) if required or not windowed else Neighborhood((), (), (), {"source_count": 0}))
            sources, assertion_documents, episode_documents = (
                neighborhood.sources, neighborhood.assertions, neighborhood.episodes,
            )
            source_ids = [event["event_id"] for event in sources]
            assertions = tuple(service._rebind(document, principal, brain) for document in assertion_documents)
            working = service._working(conn, principal, brain, target, sources)
            episodes = tuple(service._rebind(document, principal, brain) for document in episode_documents)
            snapshot = {"scope": scope, "dependency_generation": brain["dependency_generation"], "source_snapshot": [
                {"event_id": e["event_id"], "revision": e["revision"], "content_sha256": e["content_sha256"]}
                for e in sources], "memory_snapshot": [
                    {"memory_id": m["memory_id"], "revision": m["revision"], "content_sha256": digest(m)}
                    for m in assertion_documents],
                "episode_snapshot": [
                    {"episode_id": e["episode_id"], "revision": e["revision"], "content_sha256": digest(e)}
                    for e in episode_documents], "working_revision": working["revision"],
                        "sampling": neighborhood.sampling,
                "belief_relations": list(neighborhood.belief_relations),
                "belief_relation_snapshot": [{"relation_id": item["relation_id"], "revision": item["revision"],
                                              "content_sha256": digest(item)}
                                             for item in neighborhood.belief_relations]}
            candidate_plan = plan_consolidation(sources, assertions, neighborhood.belief_relations,
                                                neighborhood.sampling, include_all=True)
            identifiers = [item["objective_id"] for item in candidate_plan["objectives"]]
            attempted = []
            if identifiers and job["kind"] == "consolidate":
                attempted = [row["objective_id"] for row in conn.execute(
                    "SELECT DISTINCT objective->>'objective_id' AS objective_id FROM memory_jobs j "
                    "CROSS JOIN LATERAL jsonb_array_elements(CASE "
                    "WHEN jsonb_typeof(j.result #> '{consolidation,objectives}')='array' "
                    "THEN j.result #> '{consolidation,objectives}' ELSE '[]'::jsonb END) objective "
                    "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.kind='consolidate' "
                    "AND j.state='completed' AND objective->>'dedup_eligible'='true' "
                    "AND objective->>'objective_id'=ANY(%s::text[]) LIMIT %s",
                    (*params, identifiers, len(identifiers)),
                ).fetchall()]
            snapshot["consolidation_plan"] = plan_consolidation(
                sources, assertions, neighborhood.belief_relations, neighborhood.sampling,
                attempted_ids=attempted,
            )
            if job["kind"] == "orient":
                snapshot["consolidation_plan"]["objectives"] = []
            if window is not None:
                snapshot.update(learning_window=window, learning_input_refs=required,
                                working_snapshot=working)
            if prior and "consolidation_plan" in prior:
                snapshot["consolidation_plan"] = prior["consolidation_plan"]
            if prior and prior.get("source_snapshot") != snapshot["source_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry sources changed.")
            if prior and prior.get("episode_snapshot", []) != snapshot["episode_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry episodes changed.")
            if prior and prior.get("belief_relation_snapshot", []) != snapshot["belief_relation_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry relationships changed.")
            if prior and prior.get("memory_snapshot", []) != snapshot["memory_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry parents changed.")
            if prior and prior.get("sampling"):
                def sampling_identity(value):
                    return {**{key: item for key, item in value.items()
                               if key not in {"indexed_through_sequence", "limitations"}},
                            "limitations": [reason for reason in value.get("limitations", []) if reason != "index_lag"]}

                if sampling_identity(prior["sampling"]) != sampling_identity(snapshot["sampling"]):
                    raise DomainError("context_stale", "Cognitive retry sampling changed.")
                snapshot["sampling"] = prior["sampling"]
            conn.execute(
                "UPDATE memory_jobs SET result=%s,source_event_ids=%s,source_memory_ids=%s "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND fence=%s AND state='running'",
                (Jsonb(snapshot), source_ids, [m["memory_id"] for m in assertions], *params, job["id"], job["fence"]),
            )
            batch = CognitiveBatch(job["id"], scope, sources, assertions, working, episodes,
                                   sampling=snapshot["sampling"], belief_relations=neighborhood.belief_relations,
                                   consolidation_plan=snapshot["consolidation_plan"])
            return batch, snapshot

    def semantic(self, principal, brain_id, job):
        service = self.service
        batch, snapshot = self._snapshot(principal, brain_id, job)
        lease = {"lease_token": job["lease_token"], "fence": job["fence"]}
        operations = []
        renewable_budget = False
        # Synchronous provider calls cannot renew their lease while waiting for a response.
        lease_seconds = max(120, (service.semantic_processor.policy.max_wall_time_ms + 999) // 1000 + 5)

        def check():
            self.jobs.renew(principal, brain_id, job["id"], **lease, lease_seconds=lease_seconds)

        def reserve(**request):
            operation_id = f"{job['fence']}:{request['phase']}"
            policy = service.semantic_processor.policy
            reservation = self.jobs.reserve_operation(
                principal, brain_id, job["id"], **lease, operation_id=operation_id,
                request_fingerprint=request["request_fingerprint"], input_bytes=request["input_bytes"],
                max_model_calls=policy.max_model_calls, max_input_bytes=policy.max_input_bytes,
            )
            if not reservation["newly_reserved"]:
                raise DomainError("budget_exceeded", "Cognitive operation already reserved.")
            operations.append(operation_id)

        try:
            policy = service.semantic_processor.policy
            call_limit = min(policy.max_model_calls, job["max_model_calls"] if job["max_model_calls"] is not None
                             else policy.max_model_calls)
            if "learning_window" in snapshot and batch.sources and call_limit - job["model_calls"] < 2:
                raise DomainError("budget_exceeded", "Remaining learning budget cannot cover proposal and verification.")
            result = service.semantic_processor.process(
                batch, reserve=reserve, check=check, prior_model_calls=job["model_calls"], prior_input_bytes=job["input_bytes"],
            )
        except DomainError as error:
            if error.code != "budget_exceeded":
                raise
            renewable_budget = error.retryable
            usage = self.jobs.get(principal, brain_id, job["id"])
            result = SemanticResult("budget_exhausted", observed_event_refs=tuple(
                {"event_id": e["event_id"], "revision": e["revision"]} for e in batch.sources),
                model_calls=usage["model_calls"], input_bytes=usage["input_bytes"])
        for operation_id in operations:
            self.jobs.settle_operation(principal, brain_id, job["id"], **lease, operation_id=operation_id,
                                       outcome="uncertain" if result.status == "provider_unavailable" else "completed")
        window = snapshot.get("learning_window")
        only_invalid_proposals = (result.rejected and all(item.get("reason") == "invalid_proposal" for item in result.rejected)
                                  and not any((result.claims, result.situation_updates, result.episode_actions, result.belief_links)))
        succeeded = (result.status in {"completed", "partial"} and not only_invalid_proposals
                     and not batch.sampling.get("publication_restricted")
                     or window is not None and not snapshot["learning_input_refs"] and result.status == "no_sources")

        def publish(conn, _):
            brain = service._brain(conn, principal, brain_id)
            validate_snapshot(conn, service, principal, brain, snapshot)
            if job["kind"] == "orient":
                target = job["payload"]["target"]
                working = service._working(conn, principal, brain, target, None)
                if working["revision"] != snapshot["working_revision"]:
                    raise DomainError("context_stale", "Working context changed during orientation.")
                publication = {}
            else:
                from .consolidation import publish_semantic

                publication = publish_semantic(conn, service, principal, brain, batch, result, job_id=job["id"])
            data = {**snapshot, **asdict(result), "publication": publication}
            if window is not None:
                data["learning_window"] = record_progress(
                    conn, service, principal, brain, job, window, succeeded=succeeded,
                )
            conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(data), *service._params(principal, brain_id), job["id"]))
            return data

        if window is not None and not succeeded:
            with service.database.transaction(principal) as conn:
                service._brain(conn, principal, brain_id)
                self.jobs._lease(self.jobs._row(conn, principal, brain_id, job["id"]), **lease)
                data = {**snapshot, **asdict(result), "publication": {},
                        "learning_window": {**window, "status": "deferred", "processing_succeeded": False}}
                conn.execute(
                    "UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                    (Jsonb(data), *service._params(principal, brain_id), job["id"]),
                )
                failure = ("budget_exceeded" if result.status == "budget_exhausted"
                           else "provider_failed" if result.status in {"provider_unavailable", "verification_unavailable"}
                           else "dependency_unavailable" if batch.sampling.get("publication_restricted")
                           else "invalid_proposal")
                current = self.jobs._row(conn, principal, brain_id, job["id"])
                policy = service.semantic_processor.policy
                call_limit = min(policy.max_model_calls, current["max_model_calls"] or policy.max_model_calls)
                byte_limit = min(policy.max_input_bytes, current["max_input_bytes"] or policy.max_input_bytes)
                remaining = call_limit - current["model_calls"]
                can_retry = (job["attempts"] < 3 and remaining >= 2
                             and current["input_bytes"] < byte_limit
                             and failure != "dependency_unavailable"
                             and (failure != "budget_exceeded" or renewable_budget))
                delay = 3600 if failure == "budget_exceeded" else min(3600, 60 * 2 ** (job["attempts"] - 1))
                conn.execute(
                    "UPDATE memory_jobs SET state=%s,last_error=%s,available_at=clock_timestamp()+make_interval(secs=>%s),"
                    "lease_token_digest=NULL,lease_expires_at=NULL,worker_id=NULL,updated_at=clock_timestamp() "
                    "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                    ("failed" if can_retry else "cancelled", failure, delay,
                     *service._params(principal, brain_id), job["id"]),
                )
            return data
        return self.jobs.complete(principal, brain_id, job["id"], **lease, publish=publish)

    def orient(self, principal, brain_id, turn_id, *, lease_token, expected_revision):
        service = self.service
        turns = TurnStore(service.database)
        with turns._transaction(principal, brain_id) as (conn, brain):
            row = turns._row(conn, principal, brain_id, turn_id)
            turns._lease(row, lease_token, expected_revision)
            if row["stage"] != "captured":
                raise DomainError("stage_conflict", "Orientation requires a captured turn.")
            if service.semantic_processor is None:
                return turns._public(conn, principal, row)
            job_id = row["orientation_job_id"]
            if job_id is None:
                payload = {"turn_id": turn_id, "target": {"conversation_ref": row["conversation_id"],
                           "branch_ref": row["branch_id"]}, "through_sequence": row["durable_sequence"] or brain["durable_sequence"]}
                job_id = "orient:" + digest([brain_id, turn_id])
                conn.execute(
                    "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state) "
                    "VALUES(%s,%s,%s,%s,'orient',%s,'queued') ON CONFLICT DO NOTHING",
                    (*service._params(principal, brain_id), job_id, Jsonb(payload)),
                )
        row = turns.bind_orientation(principal, brain_id, turn_id, lease_token=lease_token,
                                     expected_revision=expected_revision, job_id=job_id)
        job = self.jobs.claim(principal, brain_id, worker_id=uid(), job_id=job_id, kinds=["orient"], lease_seconds=120)
        if job:
            self._execute(principal, brain_id, job)
        with turns._transaction(principal, brain_id) as (conn, brain):
            current = turns._row(conn, principal, brain_id, turn_id)
            turns._lease(current, lease_token, row["revision"])
            result = orientation_result(conn, service, principal, brain, current)
            status = conn.execute("SELECT state FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                                  (*service._params(principal, brain_id), job_id)).fetchone()["state"]
            if status == "running":
                raise DomainError("lease_conflict", "Orientation is already running.", True)
            generation = brain["dependency_generation"]
        if result:
            row = turns.record_orientation_sources(
                principal, brain_id, turn_id, lease_token=lease_token, expected_revision=row["revision"],
                job_id=job_id, source_refs=result["observed_event_refs"], context_generation=generation,
            )
        return row

    def _literal(self, principal, brain_id, job):
        service = self.service
        with service.database.transaction(principal) as conn:
            brain = service._brain(conn, principal, brain_id)
            event_ids = ([job["payload"]["event_id"]] if job["kind"] == "extract"
                         else job["payload"].get("event_ids", []))
            events = service._events(conn, principal, brain, event_ids=event_ids[:128], limit=128)
            if job["kind"] == "extract":
                events = [e for e in events if e["revision"] == job["payload"]["revision"]]
            memories = [(event, service._current_captures(conn, principal, brain, event)) for event in events]

        def publish(conn, _):
            brain = service._brain(conn, principal, brain_id)
            current = {(e["event_id"], e["revision"]) for e in service._events(
                conn, principal, brain, event_ids=event_ids[:128], limit=128)}
            for event, captures in memories:
                if (event["event_id"], event["revision"]) in current:
                    blocked = service._superseded_evidence(conn, principal, brain, [event["event_id"]])
                    for memory in captures:
                        if not any(cognition.evidence_overlaps(ref, old) for ref in memory["evidence_refs"] for old in blocked):
                            service._publish(conn, principal, brain, memory)
            if len(event_ids) > 128:
                service._job(conn, principal, brain, "rebuild", {"event_ids": event_ids[128:]})
            pending = conn.execute(
                "SELECT min(e.sequence) AS value FROM memory_events e JOIN memory_jobs j ON "
                "j.tenant_id=e.tenant_id AND j.brain_id=e.brain_id AND j.payload->>'event_id'=e.id "
                "AND (j.payload->>'revision')::bigint=e.revision "
                "WHERE e.tenant_id=%s AND e.owner_id=%s AND e.brain_id=%s AND j.kind='extract' "
                "AND j.state<>'completed' AND j.id<>%s", (*service._params(principal, brain_id), job["id"]),
            ).fetchone()["value"]
            indexed = brain["durable_sequence"] if pending is None else pending - 1
            conn.execute("UPDATE memory_brains SET indexed_sequence=%s WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                         (indexed, *service._params(principal, brain_id)))

        self.jobs.complete(principal, brain_id, job["id"], lease_token=job["lease_token"], fence=job["fence"], publish=publish)

    def _execute(self, principal, brain_id, job):
        try:
            if job["kind"] in ("extract", "rebuild"):
                self._literal(principal, brain_id, job)
            elif job["kind"] in ("orient", "consolidate"):
                self.semantic(principal, brain_id, job)
            elif job["kind"] == "embed":
                from .vector_jobs import embed_memory

                embed_memory(self.service, principal, brain_id, job)
        except Exception as error:
            try:
                self.jobs.fail(principal, brain_id, job["id"], lease_token=job["lease_token"], fence=job["fence"],
                               failure_code=error.code if isinstance(error, DomainError) and error.code in {
                                   "source_unavailable", "context_stale", "budget_exceeded"} else "internal_error",
                               retry=not isinstance(error, DomainError),
                               retry_delay_seconds=(60 * 2 ** min(job["attempts"] - 1, 5)
                                                    if job["kind"] == "consolidate" else 0))
            except DomainError:
                pass
            raise

    def work_once(self, principal, brain_id):
        kinds = ["extract", "rebuild"]
        if self.service.semantic_processor:
            with self.service.database.transaction(principal) as conn:
                brain = self.service._brain(conn, principal, brain_id)
                recover_windows(conn, self.service, principal, brain)
            kinds.append("consolidate")
        if self.service.embedding_provider:
            kinds.append("embed")
        job = self.jobs.claim(principal, brain_id, worker_id=uid(), kinds=kinds, lease_seconds=120)
        if job is None:
            return 0
        self._execute(principal, brain_id, job)
        return 1
