"""Bounded, one-attempt phase execution for the opt-in lifecycle evaluator."""

from __future__ import annotations

import copy

from agent_memory.helpers import uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import CONTRACT_VERSION, ENCODING_PHASE, HISTORY_PHASE, phase
from agent_memory.learning_integration import reconsider
from examples.evaluate_deepseek import Trial

PHASE_POLICY = {
    "version": "lifecycle-phase-execution-v1",
    "fresh_encoding_roots_per_learning_stage": 1,
    "max_calls_per_fresh_root": 4,
    "max_calls_per_phase": 2,
    "max_input_bytes_per_fresh_root": 393216,
    "max_input_bytes_per_phase": 196608,
    "max_ready_history_jobs_per_stage": 8,
    "max_literal_jobs_per_stage": 4,
    "attempt_policy": "Only queued jobs with zero attempts, counters and operation rows; no retries or new recovery roots.",
    "debt_policy": "Deferred, unassessed and undispatched history remains open evidence; it is not successful learning.",
    "conversation_scope": "One conversation per fixture, unchanged from the original runner; later stages do not fund earlier scopes.",
}


def repair_selection(seed):
    from examples.memory_benchmark_cases import long_history

    source = long_history(seed, 100)
    return copy.deepcopy(source["history"][:16]), {
        "version": "lifecycle-repair-diagnostic-v1", "seed": seed,
        "source_dataset_sha256": source["sha256"], "source_history_turns": 100,
        "selected_history_turns": 16, "selection": "First 16 original history turns in original order",
        "scope": "Memory arm only; no held-out probes, baseline comparison, or same-conversation donor repayment claim",
    }


def semantic_allowance(turns, learning_turns, policy):
    return {"max_model_calls": 2 * turns + PHASE_POLICY["max_calls_per_fresh_root"] * learning_turns,
            "max_input_bytes": turns * policy.max_input_bytes
            + learning_turns * min(PHASE_POLICY["max_input_bytes_per_fresh_root"], 2 * policy.max_input_bytes)}


def eligible(row):
    return (row.get("state") == "queued" and row.get("available") is True
            and all(type(row.get(key)) is int and row[key] == 0
                    for key in ("attempts", "model_calls", "input_bytes"))
            and row.get("has_operations") is False)


def window_metadata(row):
    payload = row["payload"]
    return {"id": row["id"], "state": row["state"], "learning_phase": phase(payload),
            "learning_contract_version": payload.get("learning_contract_version"),
            "authorization_root_job_id": payload.get("authorization_root_job_id"),
            "from_sequence": payload.get("from_sequence"), "through_sequence": payload.get("through_sequence")}


def source_sequences(stage):
    sources = stage.get("sources", {})
    rows = sources.get("input_events", []) + sources.get("output_events", [])
    if (not rows or not sources.get("input_events") or not sources.get("output_events")
            or any(type(row.get("sequence")) is not int or row["sequence"] < 1 for row in rows)):
        raise RuntimeError("Learning requires exact persisted input and delivered output sequences")
    return rows


def select_encoding(rows, stage):
    sources = source_sequences(stage)
    sequences = [row["sequence"] for row in sources]
    candidates = [row for row in rows if phase(row["payload"]) == ENCODING_PHASE
                  and row["payload"].get("authorization_root_job_id") == row["id"]
                  and type(row["payload"].get("from_sequence")) is int
                  and type(row["payload"].get("through_sequence")) is int
                  and row["payload"]["from_sequence"] <= min(sequences)
                  and row["payload"]["through_sequence"] >= max(sequences)]
    if len(candidates) != 1 or not eligible(candidates[0]):
        raise RuntimeError("Expected one untouched encoding allocation for the exact delivered exchange")
    return candidates[0]


def _rows(service, principal, brain_id, clause, arguments, *, limit=None):
    with service.database.transaction(principal) as conn:
        service._brain(conn, principal, brain_id)
        return [dict(row) for row in conn.execute(
            "SELECT j.id,j.kind,j.state,j.payload,j.attempts,j.model_calls,j.input_bytes,"
            "j.available_at<=clock_timestamp() AS available,EXISTS ("
            "SELECT 1 FROM memory_cognitive_operations o WHERE o.tenant_id=j.tenant_id "
            "AND o.owner_id=j.owner_id AND o.brain_id=j.brain_id AND o.job_id=j.id) AS has_operations "
            "FROM memory_jobs j WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s "
            + clause + " ORDER BY j.created_at,j.id" + (" LIMIT %s" if limit is not None else ""),
            (*service._params(principal, brain_id), *arguments, *((limit,) if limit is not None else ())),
        )]


def scoped_windows(service, principal, brain_id, stage):
    runtime = stage["runtime"]
    return _rows(service, principal, brain_id,
                 "AND j.kind='consolidate' AND j.payload #>> '{target,conversation_ref}'=%s "
                 "AND j.payload #>> '{target,branch_ref}'=%s",
                 (runtime["conversation_id"], runtime["branch_id"]))


def literal_jobs(service, principal, brain_id, stage, limit):
    sources = source_sequences(stage)
    expected = {row["event_id"]: row["revision"] for row in sources}
    rows = _rows(service, principal, brain_id,
                 "AND j.kind='extract' AND j.payload->>'event_id'=ANY(%s::text[])",
                 ([row["event_id"] for row in sources],), limit=limit + 1)
    # Delivery revises the draft event; its earlier no-op extraction must also settle the index watermark.
    if len(rows) > limit or any(row["payload"].get("event_id") not in expected
                                or type(row["payload"].get("revision")) is not int
                                or not 1 <= row["payload"]["revision"] <= expected[row["payload"]["event_id"]]
                                for row in rows):
        raise RuntimeError("Literal work exceeds the exact current-source boundary")
    if any(not eligible(row) for row in rows):
        raise RuntimeError("Literal work has already been attempted")
    return rows


def ready_history(service, principal, brain_id, roots, limit):
    rows = _rows(service, principal, brain_id,
                 "AND j.kind='consolidate' AND j.state='queued' AND j.attempts=0 "
                 "AND j.model_calls=0 AND j.input_bytes=0 AND j.available_at<=clock_timestamp() "
                 "AND j.payload->>'learning_contract_version'=%s AND j.payload->>'learning_phase'=%s "
                 "AND j.payload->>'authorization_root_job_id'=ANY(%s::text[])",
                 (CONTRACT_VERSION, HISTORY_PHASE, list(roots)), limit=limit)
    if any(not eligible(row) or phase(row["payload"]) != HISTORY_PHASE for row in rows):
        raise RuntimeError("Ready history has prior paid work or invalid phase authority")
    return rows


def allocation_ledger(service, principal, brain_id, roots):
    with service.database.transaction(principal) as conn:
        service._brain(conn, principal, brain_id)
        return [dict(row) for row in conn.execute(
            "SELECT j.id AS job_id,j.payload->>'authorization_root_job_id' AS authorization_root_job_id,"
            "j.payload->>'learning_phase' AS learning_phase,o.operation_id,o.outcome,o.input_bytes,o.usage "
            "FROM memory_cognitive_operations o JOIN memory_jobs j "
            "ON j.tenant_id=o.tenant_id AND j.owner_id=o.owner_id AND j.brain_id=o.brain_id AND j.id=o.job_id "
            "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s "
            "AND j.payload->>'authorization_root_job_id'=ANY(%s::text[]) ORDER BY o.created_at,j.id,o.operation_id",
            (*service._params(principal, brain_id), list(roots)),
        )]


def execution_failure(stage):
    for job in stage.get("learning_jobs", []):
        if job.get("last_error") == "provider_uncertain":
            return "unknown_provider_usage"
    for job in stage.get("learning_jobs", []):
        result = job.get("result") or {}
        if result.get("status") in {"budget_exhausted", "provider_unavailable", "invalid_response", "verification_unavailable"}:
            return "cognitive_processing_unavailable"
    for job in stage.get("learning_jobs", []):
        result = job.get("result") or {}
        if (job.get("execution_error") or job.get("state") != "completed"
                or result.get("sampling", {}).get("publication_restricted") is True
                or result.get("learning_window", {}).get("processing_succeeded") is False):
            return "learning_processing_incomplete"
    return None


def phase_checks(stage):
    jobs = stage.get("learning_jobs", [])
    encoding = [job for job in jobs if job.get("learning_phase") == ENCODING_PHASE]
    progress = stage.get("learning_progress", {})
    phases = progress.get("learning_phases", {})
    return {
        "one_exact_encoding_allocation_executed": len(encoding) == 1,
        "local_encoding_processed_successfully": len(encoding) == 1 and encoding[0].get("state") == "completed"
        and encoding[0].get("result", {}).get("learning_window", {}).get("processing_succeeded") is True,
        "phase_monitoring_complete": phases.get("counts_complete") is True,
        "no_learning_execution_failure": execution_failure(stage) is None,
    }


class PhaseLearning:
    def __init__(self, trial, service, brain_id, *, boundary):
        self.trial, self.service, self.brain_id = trial, service, brain_id
        self.principal, self.stage = trial.principal, trial.current
        self.jobs, self.engine = JobStore(service.database), LearningEngine(service)
        self.boundary = boundary
        self.policy = trial.report["plan"]["learning_phase_policy"]
        if self.policy != PHASE_POLICY:
            raise RuntimeError("Unsupported frozen learning phase execution policy")

    def execute(self, candidate, *, cognitive):
        self.boundary()
        job = self.jobs.claim(self.principal, self.brain_id, worker_id=uid(), job_id=candidate["id"],
                              kinds=[candidate["kind"]], max_attempts=1, lease_seconds=120)
        if job is None or job["payload"] != candidate["payload"]:
            raise RuntimeError("The selected untouched job changed before execution")
        self.trial.role = "consolidate"
        self.trial.learning_call_context = ({"learning_job_id": job["id"],
                                            "learning_phase": phase(job["payload"]),
                                            "authorization_root_job_id": job["payload"]["authorization_root_job_id"]}
                                           if cognitive else None)
        failure = None
        try:
            self.engine._execute(self.principal, self.brain_id, job)
        except Exception as error:
            failure = {"type": type(error).__name__, "code": getattr(error, "code", None)}
        finally:
            result = Trial(self.service, self.principal, self.brain_id).job_result(job["id"], failure)
            if cognitive:
                result.update(self.trial.learning_call_context)
                self.stage.setdefault("learning_jobs", []).append(result)
            else:
                self.stage.setdefault("literal_jobs", []).append(result)
            self.trial.learning_call_context = None
            self.trial.save()
        if not cognitive and (failure or result["state"] != "completed"):
            raise RuntimeError("Literal extraction did not complete")
        self.boundary()

    def run(self):
        self.boundary()
        encoding = select_encoding(scoped_windows(self.service, self.principal, self.brain_id, self.stage), self.stage)
        registry = self.trial.report.setdefault("learning_authorizations", {}).setdefault(self.brain_id, [])
        if encoding["id"] in registry:
            raise RuntimeError("An encoding authorization will not be executed again")
        registry.append(encoding["id"])
        self.stage["learning_job_id"] = encoding["id"]
        self.stage["learning_phase_policy"] = copy.deepcopy(self.policy)
        self.trial.save()
        for item in literal_jobs(self.service, self.principal, self.brain_id, self.stage,
                                 self.policy["max_literal_jobs_per_stage"]):
            self.execute(item, cognitive=False)
        self.execute(encoding, cognitive=True)
        dispatched = 0
        while dispatched < self.policy["max_ready_history_jobs_per_stage"]:
            self.boundary()
            with self.service.database.transaction(self.principal) as conn:
                brain = self.service._brain(conn, self.principal, self.brain_id)
                reconsider(conn, self.service, self.principal, brain)
            candidates = ready_history(self.service, self.principal, self.brain_id, registry, 1)
            if not candidates:
                break
            if candidates[0]["payload"]["authorization_root_job_id"] not in registry:
                raise RuntimeError("Historical work has no recorded encoding authorization")
            self.execute(candidates[0], cognitive=True)
            dispatched += 1
        self.stage["history_dispatch_limit_reached"] = dispatched == self.policy["max_ready_history_jobs_per_stage"]

    def capture(self):
        self.stage["learning_progress"] = self.jobs.learning_progress(self.principal, self.brain_id, configured=True)
        root = self.stage.get("learning_job_id")
        if root is not None:
            self.stage["learning_root_status"] = self.jobs.get(self.principal, self.brain_id, root)
        roots = self.trial.report.get("learning_authorizations", {}).get(self.brain_id, [])
        self.stage["learning_allocation_ledger"] = allocation_ledger(self.service, self.principal, self.brain_id, roots)
        self.stage["learning_phase_checks"] = phase_checks(self.stage)
        self.trial.save()
