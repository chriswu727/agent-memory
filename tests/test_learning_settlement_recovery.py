from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_learning_windows import claim, execute, observations

from agent_memory.helpers import DomainError, Principal, digest, uid
from agent_memory.jobs import NO_UNSETTLED_RETRY_POLICY, JobStore
from agent_memory.learning import LearningEngine
from agent_memory.semantic import SemanticPolicy, SemanticProcessor
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


class ProcessInterrupted(BaseException):
    pass


def expire(harness, job_id):
    with harness.database.transaction(harness.principal) as conn:
        conn.execute(
            "UPDATE memory_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*harness.service._params(harness.principal, harness.brain), job_id),
        )


def operations(harness, job_id):
    with harness.database.transaction(harness.principal) as conn:
        return conn.execute(
            "SELECT * FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s "
            "AND brain_id=%s AND job_id=%s ORDER BY operation_id",
            (*harness.service._params(harness.principal, harness.brain), job_id),
        ).fetchall()


def reserve(harness, job, operation_id="interrupted-proposal"):
    return JobStore(harness.database).reserve_operation(
        harness.principal, harness.brain, job["id"], lease_token=job["lease_token"], fence=job["fence"],
        operation_id=operation_id, request_fingerprint=digest("request"), input_bytes=200,
        max_model_calls=4, max_input_bytes=196608,
    )


@pytest.mark.parametrize("kind", ["orient", "consolidate"])
@pytest.mark.parametrize("phase", ["proposal", "verification"])
def test_crash_after_provider_response_blocks_reclaim_and_preserves_ledger(learning, monkeypatch, kind, phase):
    learning.service.semantic_processor = SemanticProcessor(
        learning.propose, learning.verify, policy=replace(SemanticPolicy(), max_model_calls=4),
    )
    original_settle = JobStore.settle_operation

    def interrupt(store, *args, **kwargs):
        if kwargs["operation_id"].endswith(":" + phase):
            raise ProcessInterrupted()
        return original_settle(store, *args, **kwargs)

    engine = LearningEngine(learning.service)
    with monkeypatch.context() as patch:
        patch.setattr(JobStore, "settle_operation", interrupt)
        if kind == "orient":
            journal, turn, token = learning.capture()
            with pytest.raises(ProcessInterrupted):
                engine.orient(learning.principal, learning.brain, turn["turn_id"],
                              lease_token=token, expected_revision=turn["revision"])
            job_id = journal.get(turn["turn_id"])["orientation_job_id"]
        else:
            observations(learning, 1)
            selected = claim(learning)
            job_id = selected["id"]
            with pytest.raises(ProcessInterrupted):
                engine._execute(learning.principal, learning.brain, selected)
    interrupted = learning.job(job_id)
    before_operations = operations(learning, job_id)
    assert interrupted["state"] == "running"
    assert interrupted["result"]["operation_settlement_policy"] == NO_UNSETTLED_RETRY_POLICY
    expected_calls = ["proposal"] if phase == "proposal" else ["proposal", "verification"]
    assert learning.calls == expected_calls
    assert interrupted["model_calls"] == len(expected_calls)
    assert sum(item["outcome"] == "reserved" for item in before_operations) == 1

    fresh = JobStore(Database(learning.database.dsn))
    assert fresh.claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id) is None
    assert learning.job(job_id)["state"] == "running"
    expire(learning, job_id)
    assert fresh.claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id) is None
    stopped = learning.job(job_id)
    assert stopped["state"] == "cancelled" and stopped["last_error"] == "provider_uncertain"
    assert stopped["worker_id"] is stopped["lease_token_digest"] is stopped["lease_expires_at"] is None
    for field in ("result", "payload", "model_calls", "input_bytes", "max_model_calls", "max_input_bytes",
                  "fence", "attempts", "created_at", "completed_at"):
        assert stopped[field] == interrupted[field]
    expected_operations = [{**item, "outcome": "uncertain" if item["outcome"] == "reserved" else item["outcome"]}
                           for item in before_operations]
    assert operations(learning, job_id) == expected_operations
    assert fresh.get(learning.principal, learning.brain, job_id)["last_error"] == "provider_uncertain"
    assert fresh.claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id,
                       max_attempts=100) is None
    assert learning.calls == expected_calls
    usage = fresh.cognitive_usage(learning.principal, learning.brain)
    assert usage["reservation_count"] == len(expected_calls)
    assert usage["outcome_counts"]["uncertain"] == 1
    if kind == "consolidate":
        progress = fresh.learning_progress(learning.principal, learning.brain, configured=True)
        assert progress["blocked_count"] == 1
        assert progress["blocked_reason_counts"] == {"provider_uncertain": 1}
        with pytest.raises(DomainError, match="expired, or fenced"):
            fresh.complete(learning.principal, learning.brain, job_id,
                           lease_token=selected["lease_token"], fence=selected["fence"])


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("settlement", [None, "completed", "uncertain"])
def test_reclaim_without_unsettled_operations_preserves_snapshot_policy(learning, legacy, settlement):
    observations(learning, 1)
    selected = claim(learning)
    engine = LearningEngine(learning.service)
    _, snapshot = engine._snapshot(learning.principal, learning.brain, selected)
    if legacy:
        snapshot.pop("operation_settlement_policy")
        with learning.database.transaction(learning.principal) as conn:
            conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(snapshot), learning.principal.tenant_id, learning.brain, selected["id"]))
    if settlement:
        reserve(learning, selected)
        JobStore(learning.database).settle_operation(
            learning.principal, learning.brain, selected["id"], lease_token=selected["lease_token"],
            fence=selected["fence"], operation_id="interrupted-proposal", outcome=settlement,
        )
    expire(learning, selected["id"])
    reclaimed = claim(learning, selected["id"])
    assert reclaimed["fence"] == selected["fence"] + 1
    _, resumed = engine._snapshot(learning.principal, learning.brain, reclaimed)
    assert resumed == snapshot
    assert ("operation_settlement_policy" in resumed) is not legacy
    assert learning.calls == []


def test_concurrent_reclaim_fences_once_and_explicit_retry_uses_fresh_budget(learning):
    observations(learning, 1)
    selected = claim(learning)
    LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    reserve(learning, selected)
    expire(learning, selected["id"])
    barrier = Barrier(2)

    def reclaim(_):
        store = JobStore(Database(learning.database.dsn))
        barrier.wait()
        return store.claim(learning.principal, learning.brain, worker_id=uid(), job_id=selected["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(reclaim, range(2))) == [None, None]
    store = JobStore(learning.database)
    stopped = learning.job(selected["id"])
    assert stopped["attempts"] == stopped["fence"] == 1
    assert stopped["model_calls"] == 1 and learning.calls == []
    child = store.retry_learning(learning.principal, learning.brain, selected["id"],
                                request_id="explicit-recovery", expected_fence=selected["fence"])
    completed = execute(learning, claim(learning, child["id"]))
    assert completed["state"] == "completed"
    assert completed["model_calls"] == completed["max_model_calls"] == 2
    assert completed["result"]["operation_settlement_policy"] == NO_UNSETTLED_RETRY_POLICY
    assert learning.job(selected["id"])["model_calls"] == 1
    assert operations(learning, selected["id"])[0]["outcome"] == "uncertain"
    assert learning.calls == ["proposal", "verification"]


def test_unauthorized_claim_cannot_terminalize_an_unsettled_job(learning):
    observations(learning, 1)
    selected = claim(learning)
    LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    reserve(learning, selected)
    expire(learning, selected["id"])
    with pytest.raises(DomainError, match="Brain is unavailable"):
        JobStore(learning.database).claim(Principal(uid(), uid()), learning.brain,
                                         worker_id=uid(), job_id=selected["id"])
    assert learning.job(selected["id"])["state"] == "running"
    assert operations(learning, selected["id"])[0]["outcome"] == "reserved"


@pytest.mark.parametrize("invalid", [None, False, {}, "future-policy-v2"])
def test_unknown_policy_cannot_enable_legacy_reclaim(learning, invalid):
    observations(learning, 1)
    selected = claim(learning)
    _, snapshot = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    snapshot["operation_settlement_policy"] = invalid
    reserve(learning, selected)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(snapshot), learning.principal.tenant_id, learning.brain, selected["id"]))
    expire(learning, selected["id"])
    assert claim(learning, selected["id"]) is None
    stopped = learning.job(selected["id"])
    assert stopped["state"] == "cancelled" and stopped["last_error"] == "dependency_unavailable"
    assert stopped["model_calls"] == 1 and stopped["result"] == snapshot
    assert operations(learning, selected["id"])[0]["outcome"] == "uncertain"
    assert learning.calls == []


def test_nonobject_snapshot_fails_closed_without_hiding_other_jobs(learning):
    observations(learning, 1)
    selected = claim(learning)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(["invalid"]), learning.principal.tenant_id, learning.brain, selected["id"]))
    expire(learning, selected["id"])
    assert claim(learning, selected["id"]) is None
    assert learning.job(selected["id"])["last_error"] == "dependency_unavailable"
    learning.observe("A separate later observation.")
    other = claim(learning)
    assert other["id"] != selected["id"]
    assert learning.calls == []


def test_erasure_clears_snapshot_without_reauthorizing_the_original_job(learning):
    receipt = observations(learning, 1)[0]
    selected = claim(learning)
    LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    reserve(learning, selected)
    learning.memory.forget(receipt["accepted_event_ids"], kind="events", mode="erase_content",
                           expected_scope_generation=1)
    stopped = learning.job(selected["id"])
    assert stopped["result"] is None and stopped["state"] == "superseded"
    assert claim(learning, selected["id"]) is None
    assert stopped["model_calls"] == 1
    assert learning.calls == []
