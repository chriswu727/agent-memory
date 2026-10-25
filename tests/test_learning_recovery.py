import copy
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_learning_windows import claim, drain, execute, jobs, observations, processed_refs
from test_semantic import claim as proposed_claim
from test_semantic import encoded, proposals

from agent_memory.helpers import DomainError, Principal, digest, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.learning_windows import enqueue_window, recover_windows
from agent_memory.semantic import SemanticPolicy, SemanticProcessor

pytestmark = pytest.mark.integration


def fail_provider(harness):
    def fail(request):
        harness.calls.append(request.phase)
        raise TimeoutError("private-provider-body")

    harness.service.semantic_processor = SemanticProcessor(fail, harness.verify)


def seed_legacy_window(harness):
    pending = jobs(harness)
    assert len(pending) == 1 and pending[0]["state"] == "queued" and pending[0]["model_calls"] == 0
    parent = pending[0]
    with harness.database.transaction(harness.principal) as conn:
        brain = harness.service._brain(conn, harness.principal, harness.brain)
        conn.execute("DELETE FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*harness.service._params(harness.principal, harness.brain), parent["id"]))
        enqueue_window(conn, harness.service, harness.principal, brain, parent["payload"]["target"],
                       parent["payload"]["from_sequence"], parent["payload"]["through_sequence"])


def stopped_window(harness, count=1, *, legacy=False):
    receipts = observations(harness, count)
    if legacy:
        seed_legacy_window(harness)
    fail_provider(harness)
    parent = execute(harness)
    assert parent["state"] == "cancelled"
    return parent, receipts


def retry(harness, parent, request_id="recovery-request", **changes):
    arguments = {"request_id": request_id, "expected_fence": parent["fence"], **changes}
    return JobStore(harness.database).retry_learning(harness.principal, harness.brain, parent["id"], **arguments)


def update(harness, identifier, **values):
    allowed = {"payload", "result", "state", "attempts"}
    assert values.keys() <= allowed
    with harness.database.transaction(harness.principal) as conn:
        assignments = ",".join(f"{key}=%s" for key in values)
        parameters = [Jsonb(value) if key in {"payload", "result"} else value for key, value in values.items()]
        conn.execute(f"UPDATE memory_jobs SET {assignments} WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (*parameters, harness.principal.tenant_id, harness.brain, identifier))


def operation_rows(harness):
    with harness.database.transaction(harness.principal) as conn:
        return conn.execute("SELECT * FROM memory_cognitive_operations WHERE tenant_id=%s AND brain_id=%s "
                            "ORDER BY created_at,job_id,operation_id",
                            (harness.principal.tenant_id, harness.brain)).fetchall()


def test_explicit_retry_preserves_failed_evidence_and_cumulative_spending(learning):
    parent, receipts = stopped_window(learning)
    operations = operation_rows(learning)
    with learning.database.transaction(learning.principal) as conn:
        budget = conn.execute("SELECT * FROM memory_cognitive_budgets WHERE tenant_id=%s AND brain_id=%s",
                              (learning.principal.tenant_id, learning.brain)).fetchone()
    child = retry(learning, parent)
    retained = learning.job(parent["id"])
    for key in ("result", "state", "attempts", "fence", "model_calls", "input_bytes", "max_model_calls", "max_input_bytes"):
        assert retained[key] == parent[key]
    assert operation_rows(learning) == operations
    assert child["state"] == "queued" and child["model_calls"] == 0
    assert (child["max_model_calls"], child["max_input_bytes"]) == (2, 196608)
    assert retained["payload"]["recovery_job_id"] == child["id"]
    assert "recovery-request" not in json.dumps(retained["payload"])
    assert "private-provider-body" not in json.dumps(child)
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    completed = execute(learning, claim(learning, child["id"]))
    assert completed["state"] == "completed" and completed["model_calls"] == 2
    assert processed_refs(learning) == [(receipts[0]["accepted_event_ids"][0], 1)]
    assert len(operation_rows(learning)) == 3
    assert learning.job(parent["id"])["model_calls"] == 1
    with learning.database.transaction(learning.principal) as conn:
        assert conn.execute("SELECT * FROM memory_cognitive_budgets WHERE tenant_id=%s AND brain_id=%s",
                            (learning.principal.tenant_id, learning.brain)).fetchone() == budget


def test_duplicate_concurrent_requests_and_restart_return_same_latest_child(learning):
    parent, _ = stopped_window(learning)
    barrier = Barrier(2)

    def recover(_):
        barrier.wait()
        return retry(learning, parent)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(recover, range(2)))
    assert responses[0]["id"] == responses[1]["id"]
    assert len(jobs(learning)) == 2
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    execute(learning, claim(learning, responses[0]["id"]))
    replay = retry(learning, parent)
    assert replay["id"] == responses[0]["id"] and replay["state"] == "completed"
    assert len(learning.calls) == 3
    with pytest.raises(DomainError) as conflict:
        retry(learning, parent, request_id="different-request")
    assert conflict.value.code == "idempotency_conflict"
    assert len(jobs(learning)) == 2


def test_stale_fence_and_invalid_request_values_create_no_recovery(learning):
    parent, _ = stopped_window(learning)
    for arguments, code in (({"expected_fence": parent["fence"] + 1}, "version_conflict"),
                            ({"expected_fence": True}, "validation_failed"),
                            ({"request_id": "contains whitespace"}, "validation_failed")):
        with pytest.raises(DomainError) as error:
            retry(learning, parent, **arguments)
        assert error.value.code == code
    assert len(jobs(learning)) == 1


@pytest.mark.parametrize("different_tenant", [False, True])
def test_retry_cannot_access_another_principal_window(learning, different_tenant):
    parent, _ = stopped_window(learning)
    principal = Principal(uid() if different_tenant else learning.principal.tenant_id, uid())
    with pytest.raises(DomainError) as error:
        JobStore(learning.database).retry_learning(principal, learning.brain, parent["id"],
                                                 request_id="request", expected_fence=parent["fence"])
    assert error.value.code == "not_found_or_forbidden"
    assert len(jobs(learning)) == 1


@pytest.mark.parametrize("state,attempts", [("queued", 0), ("running", 1), ("failed", 2)])
def test_live_or_automatically_retryable_jobs_cannot_be_replayed(learning, state, attempts):
    parent, _ = stopped_window(learning)
    update(learning, parent["id"], state=state, attempts=attempts)
    with pytest.raises(DomainError) as error:
        retry(learning, parent)
    assert error.value.code == "stage_conflict"
    assert len(jobs(learning)) == 1


@pytest.mark.parametrize("state,attempts", [("failed", 3), ("superseded", 1)])
def test_exhausted_or_superseded_window_can_be_explicitly_recovered(learning, state, attempts):
    parent, _ = stopped_window(learning)
    update(learning, parent["id"], state=state, attempts=attempts)
    assert retry(learning, parent)["state"] == "queued"
    assert JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(),
                                            job_id=parent["id"], max_attempts=100) is None


def test_recovery_and_continuation_keep_original_range_when_new_input_arrives(learning):
    parent, receipts = stopped_window(learning, 18)
    child = retry(learning, parent)
    newer = learning.observe("A new observation outside the authorized recovery range.")
    assert learning.job(child["id"])["payload"]["through_sequence"] == 18
    assert len(jobs(learning)) == 3
    learning.service.semantic_processor = SemanticProcessor(
        learning.propose, learning.verify,
        policy=replace(SemanticPolicy(), max_model_calls=8, max_input_bytes=1048576, max_request_bytes=524288),
    )
    first = execute(learning, claim(learning, child["id"]))
    continuation = learning.job(first["payload"]["continuation_job_id"])
    assert continuation["payload"]["continuation_parent_job_id"] == child["id"]
    assert continuation["payload"]["recovery_root_job_id"] == child["id"]
    assert (continuation["max_model_calls"], continuation["max_input_bytes"]) == (2, 196608)
    another = learning.observe("Another new observation after the first recovered batch.")
    assert learning.job(continuation["id"])["payload"]["through_sequence"] == 18
    drain(learning)
    chain = [row for row in jobs(learning) if row["payload"].get("recovery_root_job_id") == child["id"]]
    assert len(chain) == 3 and all(row["state"] == "completed" for row in chain)
    assert all(row["payload"]["through_sequence"] == 18 for row in chain)
    assert sum(row["model_calls"] for row in chain) == 6
    expected = [(receipt["accepted_event_ids"][0], 1) for receipt in [*receipts, newer, another]]
    assert Counter(processed_refs(learning)) == Counter(expected)


def test_failed_recovery_does_not_automatically_authorize_another_paid_attempt(learning):
    parent, _ = stopped_window(learning, 10)
    child = retry(learning, parent)
    failed = execute(learning, claim(learning, child["id"]))
    assert failed["state"] == "cancelled" and failed["model_calls"] == 1
    drain(learning)
    assert len(jobs(learning)) == 2 and len(learning.calls) == 2
    assert failed["result"]["learning_window"]["processed_through_sequence"] == 0
    assert "continuation_job_id" not in failed["payload"]


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_recovery_reads_current_sources_without_resurrecting_old_versions(learning, mutation):
    parent, receipts = stopped_window(learning, 2, legacy=True)
    old = receipts[0]["accepted_event_ids"][0]
    if mutation == "edit":
        learning.observe("A corrected observation outside the original range.", source_key="input-1", revision=2)
    else:
        learning.memory.forget([old], kind="events", mode="erase_content", expected_scope_generation=1)
    parent = learning.job(parent["id"])
    child = retry(learning, parent)
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    completed = execute(learning, claim(learning, child["id"]))
    assert completed["state"] == "completed"
    assert (old, 1) not in {(item["event_id"], item["revision"]) for item in completed["result"]["source_snapshot"]}
    assert completed["result"]["learning_input_refs"] == [{"event_id": receipts[1]["accepted_event_ids"][0], "revision": 1}]


def test_completed_partial_range_requires_absent_continuation_and_recovers_only_tail(learning):
    observations(learning, 10)
    seed_legacy_window(learning)
    parent = execute(learning)
    with pytest.raises(DomainError) as error:
        retry(learning, parent)
    assert error.value.code == "stage_conflict"
    continuation = parent["payload"]["continuation_job_id"]
    payload = {key: value for key, value in parent["payload"].items() if key != "continuation_job_id"}
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("DELETE FROM memory_jobs WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, continuation))
    update(learning, parent["id"], payload=payload)
    child = retry(learning, parent)
    row = learning.job(child["id"])
    assert (row["payload"]["from_sequence"], row["payload"]["through_sequence"]) == (9, 10)
    drain(learning)
    assert len(processed_refs(learning)) == 10


def test_complete_or_malformed_window_never_receives_new_budget(learning):
    observations(learning, 1)
    parent = execute(learning)
    with pytest.raises(DomainError) as error:
        retry(learning, parent)
    assert error.value.code == "stage_conflict"
    invalid = copy.deepcopy(parent["result"])
    invalid["learning_window"]["processed_through_sequence"] = -1
    update(learning, parent["id"], result=invalid, state="cancelled")
    with pytest.raises(DomainError) as error:
        retry(learning, parent)
    assert error.value.code == "stage_conflict"
    assert len(jobs(learning)) == 1


def test_crash_on_last_attempt_can_recover_without_replaying_unknown_operation(learning):
    observations(learning, 1)
    selected = claim(learning)
    LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    store = JobStore(learning.database)
    lease = {"lease_token": selected["lease_token"], "fence": selected["fence"]}
    store.reserve_operation(learning.principal, learning.brain, selected["id"], **lease,
                            operation_id="interrupted-operation", request_fingerprint=digest("unknown"),
                            input_bytes=100, max_model_calls=2, max_input_bytes=196608)
    update(learning, selected["id"], attempts=3)
    with pytest.raises(DomainError) as active:
        retry(learning, selected)
    assert active.value.code == "stage_conflict"
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                     "WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, selected["id"]))
    before = learning.job(selected["id"])
    child = retry(learning, before)
    parent = learning.job(before["id"])
    assert parent["state"] == "cancelled" and parent["last_error"] == "provider_uncertain"
    assert parent["fence"] == before["fence"] and parent["model_calls"] == 1
    assert parent["result"] == before["result"] and parent["input_bytes"] == 100
    assert operation_rows(learning)[0]["outcome"] == "uncertain"
    with pytest.raises(DomainError) as fenced:
        store.complete(learning.principal, learning.brain, before["id"], **lease)
    assert fenced.value.code == "lease_conflict"
    assert retry(learning, before)["id"] == child["id"]
    completed = execute(learning, claim(learning, child["id"]))
    assert completed["state"] == "completed" and completed["model_calls"] == 2
    assert len(operation_rows(learning)) == 3


def test_expired_but_reclaimable_lease_remains_in_automatic_recovery(learning):
    observations(learning, 1)
    parent = claim(learning)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                     "WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, parent["id"]))
    with pytest.raises(DomainError) as blocked:
        retry(learning, parent)
    assert blocked.value.code == "stage_conflict"
    assert len(jobs(learning)) == 1


def test_explicit_authorization_does_not_raise_global_rolling_budget(learning):
    parent, _ = stopped_window(learning)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_cognitive_budgets SET max_model_calls=0 "
                     "WHERE tenant_id=%s AND brain_id=%s", (learning.principal.tenant_id, learning.brain))
    child = retry(learning, parent)
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    deferred = execute(learning, claim(learning, child["id"]))
    assert deferred["last_error"] == "budget_exceeded"
    assert deferred["model_calls"] == 0 and len(learning.calls) == 1
    assert deferred["result"]["learning_window"]["processed_through_sequence"] == 0
    assert len(operation_rows(learning)) == 1
    with learning.database.transaction(learning.principal) as conn:
        assert conn.execute("SELECT max_model_calls FROM memory_cognitive_budgets WHERE tenant_id=%s AND brain_id=%s",
                            (learning.principal.tenant_id, learning.brain)).fetchone()["max_model_calls"] == 0


def test_large_configured_policy_splits_recovery_into_fitting_bounded_batches(learning):
    for index in range(8):
        learning.observe(f"Observation {index}: " + ("A detailed factual observation. " * 180))
    fail_provider(learning)
    parent = execute(learning)
    child = retry(learning, parent)

    def propose(request):
        learning.calls.append(request.phase)
        source = json.loads(request.messages[-1]["content"])["sources"][0]
        return encoded(proposals(proposed_claim(source, conditions={"op": "always"})))

    learning.service.semantic_processor = SemanticProcessor(
        propose, learning.verify,
        policy=replace(SemanticPolicy(), max_model_calls=8, max_input_bytes=1048576, max_request_bytes=524288),
    )
    completed = execute(learning, claim(learning, child["id"]))
    assert completed["state"] == "completed"
    assert 0 < completed["result"]["learning_window"]["input_count"] < 8
    assert completed["model_calls"] == 2 and completed["input_bytes"] <= 196608
    continuation = learning.job(completed["payload"]["continuation_job_id"])
    assert (continuation["max_model_calls"], continuation["max_input_bytes"]) == (2, 196608)


@pytest.mark.parametrize("processed", [0, -1, True, "invalid", 3])
def test_source_recovery_handles_unprocessed_or_malformed_prefix_without_stalling(learning, processed):
    parent, _ = stopped_window(learning, 2, legacy=True)
    payload = {**parent["payload"], "processed_through_sequence": processed}
    update(learning, parent["id"], payload=payload, state="superseded")
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    LearningEngine(learning.service).work_once(learning.principal, learning.brain)
    retained = learning.job(parent["id"])
    if type(processed) is int and processed == 0:
        replacement = learning.job(retained["payload"]["recovery_job_id"])
        assert (replacement["payload"]["from_sequence"], replacement["payload"]["through_sequence"]) == (1, 2)
    else:
        assert "recovery_job_id" not in retained["payload"]


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_new_phase_paid_failure_cannot_replan_or_retry_erased_source_identity(learning, mutation):
    parent, receipts = stopped_window(learning, 2)
    before = operation_rows(learning)
    if mutation == "edit":
        learning.observe("A separately authorized corrected report.", source_key="input-1", revision=2)
    else:
        learning.memory.forget([receipts[0]["accepted_event_ids"][0]], kind="events", mode="erase_content",
                               expected_scope_generation=1)
    retained = learning.job(parent["id"])
    assert retained["result"] is None and retained["model_calls"] == 1
    with pytest.raises(DomainError) as error:
        retry(learning, retained)
    assert error.value.code == "context_stale"
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        recover_windows(conn, learning.service, learning.principal, brain)
    assert "recovery_job_id" not in learning.job(parent["id"])["payload"]
    assert operation_rows(learning) == before
