import json
from dataclasses import replace

import httpx
import pytest
from psycopg import sql
from test_learning import learning as learning
from test_learning_settlement_recovery import expire, operations
from test_learning_windows import claim, observations

from agent_memory.helpers import DomainError, uid
from agent_memory.jobs import NO_UNSETTLED_RETRY_POLICY, JobStore
from agent_memory.learning import LearningEngine
from agent_memory.semantic import SemanticPolicy, SemanticProcessor
from agent_memory.semantic_provider import SemanticProviderError
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


def published_documents(harness):
    with harness.database.transaction(harness.principal) as conn:
        return {
            table: conn.execute(
                sql.SQL("SELECT document FROM {} WHERE tenant_id=%s AND owner_id=%s "
                        "AND brain_id=%s ORDER BY document::text").format(sql.Identifier(table)),
                harness.service._params(harness.principal, harness.brain),
            ).fetchall()
            for table in ("memory_events", "memory_assertions", "memory_episodes",
                          "memory_belief_relations", "memory_working_states")
        }


@pytest.mark.parametrize("kind", ["orient", "consolidate"])
@pytest.mark.parametrize("timeout_phase", ["proposal", "verification"])
def test_timeout_after_expiry_cannot_settle_publish_or_repeat(learning, monkeypatch, kind, timeout_phase):
    engine = LearningEngine(learning.service)
    if kind == "orient":
        journal, turn, token = learning.capture()

        def job_id():
            return journal.get(turn["turn_id"])["orientation_job_id"]

        def run():
            return engine.orient(learning.principal, learning.brain, turn["turn_id"],
                                 lease_token=token, expected_revision=turn["revision"])
    else:
        observations(learning, 1)
        selected = claim(learning)

        def job_id():
            return selected["id"]

        def run():
            return engine._execute(learning.principal, learning.brain, selected)

    before_publication = published_documents(learning)
    at_timeout = {}

    def provider(request):
        if request.phase != timeout_phase:
            return learning.propose(request)
        learning.calls.append(request.phase)
        expire(learning, job_id())
        at_timeout["job"] = learning.job(job_id())
        at_timeout["operations"] = operations(learning, job_id())
        raise SemanticProviderError("timeout")

    learning.service.semantic_processor = SemanticProcessor(
        provider, provider, policy=replace(SemanticPolicy(), max_wall_time_ms=240_000),
    )
    failures = []
    original_settle, original_fail = JobStore.settle_operation, JobStore.fail

    def settle(store, *args, **kwargs):
        try:
            return original_settle(store, *args, **kwargs)
        except DomainError as error:
            failures.append(("settlement", error.code))
            raise

    def fail(store, *args, **kwargs):
        try:
            return original_fail(store, *args, **kwargs)
        except DomainError as error:
            failures.append(("cleanup", error.code))
            raise

    monkeypatch.setattr(JobStore, "settle_operation", settle)
    monkeypatch.setattr(JobStore, "fail", fail)
    with pytest.raises(DomainError) as stopped:
        run()
    assert stopped.value.as_dict() == {
        "code": "provider_uncertain",
        "message": "Provider settlement requires explicit reconciliation (lease_conflict).",
        "retryable": False,
    }
    assert failures == [("settlement", "lease_conflict"), ("cleanup", "lease_conflict")]
    identifier = job_id()
    pending = learning.job(identifier)
    assert pending == at_timeout["job"]
    assert pending["state"] == "running" and pending["last_error"] is None
    assert pending["result"]["operation_settlement_policy"] == NO_UNSETTLED_RETRY_POLICY
    assert "status" not in pending["result"] and "publication" not in pending["result"]
    expected_calls = ["proposal"] if timeout_phase == "proposal" else ["proposal", "verification"]
    assert learning.calls == expected_calls
    assert pending["model_calls"] == len(expected_calls)
    before_operations = operations(learning, identifier)
    assert before_operations == at_timeout["operations"]
    unresolved = [item for item in before_operations if item["outcome"] == "reserved"]
    assert len(unresolved) == 1 and unresolved[0]["operation_id"] == f"1:{timeout_phase}"
    assert unresolved[0]["usage"] is unresolved[0]["response_fingerprint"] is None
    assert unresolved[0]["completed_at"] is None
    assert pending["input_bytes"] == sum(item["input_bytes"] for item in before_operations)
    assert published_documents(learning) == before_publication

    fresh = JobStore(Database(learning.database.dsn))
    assert fresh.claim(learning.principal, learning.brain, worker_id=uid(), job_id=identifier) is None
    reconciled = learning.job(identifier)
    assert reconciled["state"] == "cancelled" and reconciled["last_error"] == "provider_uncertain"
    assert reconciled["lease_token_digest"] is reconciled["lease_expires_at"] is reconciled["worker_id"] is None
    for field in ("result", "payload", "model_calls", "input_bytes", "fence", "attempts", "created_at"):
        assert reconciled[field] == pending[field]
    assert operations(learning, identifier) == [
        {**item, "outcome": "uncertain" if item["outcome"] == "reserved" else item["outcome"]}
        for item in before_operations
    ]
    assert fresh.claim(learning.principal, learning.brain, worker_id=uid(),
                       job_id=identifier, max_attempts=100) is None
    assert learning.calls == expected_calls
    assert published_documents(learning) == before_publication
    usage = fresh.cognitive_usage(learning.principal, learning.brain)
    assert usage["reservation_count"] == len(expected_calls)
    assert usage["outcome_counts"]["uncertain"] == 1
    assert not usage["counts_complete"]


@pytest.mark.parametrize("error", [
    RuntimeError("Private database details and payload"),
    DomainError("private_failure_code", "Private database details and payload"),
    DomainError("lease_conflict", "Private database details and payload"),
])
def test_public_settlement_diagnostic_never_exposes_exception_text(learning, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(JobStore, "settle_operation", fail)
    journal, turn, token = learning.capture()
    with pytest.raises(httpx.HTTPStatusError) as stopped:
        journal.orient(turn["turn_id"], lease_token=token, expected_revision=turn["revision"])
    assert stopped.value.response.status_code == 409
    expected_message = (
        "Provider settlement requires explicit reconciliation (lease_conflict)."
        if isinstance(error, DomainError) and error.code == "lease_conflict"
        else "Provider settlement requires explicit reconciliation."
    )
    assert stopped.value.response.json() == {"error": {
        "code": "provider_uncertain", "message": expected_message, "retryable": False,
    }}
    job = learning.job(journal.get(turn["turn_id"])["orientation_job_id"])
    assert job["state"] == "cancelled" and job["last_error"] == "provider_uncertain"
    assert job["model_calls"] == 1 and learning.calls == ["proposal"]
    assert "Private" not in json.dumps(job["result"])
    store = JobStore(learning.database)
    assert store.claim(learning.principal, learning.brain, worker_id=uid(), job_id=job["id"]) is None
    assert store.cognitive_usage(learning.principal, learning.brain)["outcome_counts"]["uncertain"] == 1
