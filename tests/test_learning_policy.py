import copy
import json
from dataclasses import asdict

import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_learning_recovery import seed_legacy_window
from test_learning_windows import claim
from test_semantic import encoded, proposals

from membrain.helpers import DomainError, digest
from membrain.jobs import JobStore
from membrain.learning import LearningEngine, _snapshot_policy, validate_snapshot
from membrain.semantic import SemanticPolicy, SemanticProcessor

ORIGINAL = SemanticPolicy(max_wall_time_ms=240_000, max_output_tokens=16384, max_response_bytes=65536)


def saved_policy(policy=ORIGINAL):
    return {"version": "semantic-policy-v1", "limits": asdict(policy)}


@pytest.mark.parametrize("change", [
    None, [], {}, {"version": "unknown", "limits": asdict(ORIGINAL)},
    {"version": "semantic-policy-v1", "limits": {}},
    {"version": "semantic-policy-v1", "limits": {**asdict(ORIGINAL), "max_output_tokens": True}},
    {"version": "semantic-policy-v1", "limits": {**asdict(ORIGINAL), "max_wall_time_ms": 600001}},
    {"version": "semantic-policy-v1", "limits": {**asdict(ORIGINAL), "private": "private-content"}},
    {"version": "semantic-policy-v1", "limits": asdict(ORIGINAL), "extra": 1},
])
def test_present_malformed_snapshot_policy_fails_closed(change):
    with pytest.raises(DomainError) as caught:
        _snapshot_policy({"semantic_policy": change}, SemanticPolicy())
    assert caught.value.code == "context_stale"
    assert "private-content" not in str(caught.value)


def test_absent_legacy_policy_does_not_infer_historical_configuration():
    current = SemanticPolicy(max_output_tokens=1000)
    assert _snapshot_policy({}, current) is current
    assert _snapshot_policy({}) is None
    assert _snapshot_policy({"semantic_policy": saved_policy()}) == ORIGINAL


def prepare(learning, monkeypatch, mode, *, legacy=False):
    engine = LearningEngine(learning.service)
    if mode == "learning":
        learning.observe("I chose a blue mug for the pottery class.")
        if legacy:
            seed_legacy_window(learning)
        job = claim(learning)
        _, snapshot = engine._snapshot(learning.principal, learning.brain, job)
        return job, snapshot
    _, row, token = learning.capture("Please help me plan a pottery class.")
    captured = []

    def pause(self, principal, brain_id, job):
        _, snapshot = self._snapshot(principal, brain_id, job)
        captured.append((job, snapshot))
        raise DomainError("context_stale", "Paused after durable snapshot.")

    with monkeypatch.context() as patch:
        patch.setattr(LearningEngine, "_execute", pause)
        with pytest.raises(DomainError, match="Paused"):
            engine.orient(learning.principal, learning.brain, row["turn_id"],
                          lease_token=token, expected_revision=row["revision"])
    return captured[0]


def save_snapshot(learning, job, snapshot):
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(snapshot), *learning.service._params(learning.principal, learning.brain), job["id"]))


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["learning", "orientation"])
@pytest.mark.parametrize("wall_time_ms", [240_000, 600_000])
def test_new_snapshot_keeps_exact_effective_limits_after_engine_restart(learning, monkeypatch, mode, wall_time_ms):
    policy = SemanticPolicy(**{**asdict(ORIGINAL), "max_wall_time_ms": wall_time_ms})
    calls = []

    def propose(request):
        calls.append(request)
        with learning.database.transaction(learning.principal) as conn:
            remaining = conn.execute(
                "SELECT extract(epoch FROM lease_expires_at-clock_timestamp()) AS remaining FROM memory_jobs "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (*learning.service._params(learning.principal, learning.brain), job["id"]),
            ).fetchone()["remaining"]
        assert remaining > wall_time_ms / 1000
        return encoded({"situation_updates": []} if mode == "orientation" else proposals())

    learning.service.semantic_processor = SemanticProcessor(propose, policy=policy)
    job, snapshot = prepare(learning, monkeypatch, mode)
    assert snapshot["semantic_policy"] == saved_policy(policy)
    changed = SemanticPolicy(max_model_calls=0, max_output_tokens=1, max_wall_time_ms=1, max_response_bytes=1)
    learning.service.semantic_processor = SemanticProcessor(propose, policy=changed)
    engine = LearningEngine(learning.service)
    _, resumed = engine._snapshot(learning.principal, learning.brain, job)
    assert resumed == snapshot
    engine._execute(learning.principal, learning.brain, job)
    completed = learning.job(job["id"])
    assert completed["state"] == "completed" and completed["model_calls"] == 1
    assert completed["result"]["semantic_policy"] == saved_policy(policy)
    assert len(calls) == 1 and calls[0].max_output_tokens == ORIGINAL.max_output_tokens
    assert wall_time_ms / 1000 - 1 < calls[0].timeout_seconds <= wall_time_ms / 1000
    assert learning.service.semantic_processor.policy == changed


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["learning", "orientation"])
def test_existing_snapshot_without_policy_keeps_legacy_service_behavior(learning, monkeypatch, mode):
    calls = []

    def propose(request):
        calls.append(request)
        return encoded({"situation_updates": []} if mode == "orientation" else proposals())

    learning.service.semantic_processor = SemanticProcessor(propose, policy=ORIGINAL)
    job, snapshot = prepare(learning, monkeypatch, mode, legacy=True)
    del snapshot["semantic_policy"]
    save_snapshot(learning, job, snapshot)
    learning.service.semantic_processor.policy = SemanticPolicy(max_output_tokens=1000)
    engine = LearningEngine(learning.service)
    _, resumed = engine._snapshot(learning.principal, learning.brain, job)
    assert resumed == snapshot and "semantic_policy" not in resumed
    engine._execute(learning.principal, learning.brain, job)
    completed = learning.job(job["id"])
    assert completed["state"] == "completed"
    assert "semantic_policy" not in completed["result"]
    assert calls[0].max_output_tokens == 1000 and calls[0].timeout_seconds <= 30


@pytest.mark.integration
@pytest.mark.parametrize("field", [
    "semantic_policy", "operation_settlement_policy", "input_contract_version", "learning_input_refs",
])
def test_new_phase_cannot_be_downgraded_by_removing_frozen_metadata(learning, monkeypatch, field):
    learning.service.semantic_processor = SemanticProcessor(lambda _: pytest.fail("No model call authorized"))
    job, snapshot = prepare(learning, monkeypatch, "learning")
    del snapshot[field]
    save_snapshot(learning, job, snapshot)
    with pytest.raises(DomainError) as error:
        LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, job)
    assert error.value.code == "idempotency_conflict"
    assert learning.job(job["id"])["model_calls"] == 0


@pytest.mark.integration
def test_invalid_durable_policy_blocks_execution_and_publication_before_provider_calls(learning, monkeypatch):
    learning.service.semantic_processor = SemanticProcessor(lambda _: pytest.fail("No model call authorized"))
    job, snapshot = prepare(learning, monkeypatch, "learning")
    invalid = copy.deepcopy(snapshot)
    invalid["semantic_policy"]["limits"]["max_output_tokens"] = "private-invalid"
    save_snapshot(learning, job, invalid)
    with pytest.raises(DomainError) as caught:
        LearningEngine(learning.service)._execute(learning.principal, learning.brain, job)
    assert caught.value.code == "context_stale"
    assert learning.job(job["id"])["model_calls"] == 0
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        with pytest.raises(DomainError, match="policy snapshot"):
            validate_snapshot(conn, learning.service, learning.principal, brain, invalid)


@pytest.mark.integration
def test_frozen_policy_does_not_reset_reserved_usage_or_authorize_extra_calls(learning, monkeypatch):
    learning.service.semantic_processor = SemanticProcessor(lambda _: pytest.fail("No model call authorized"),
                                                           policy=ORIGINAL)
    job, snapshot = prepare(learning, monkeypatch, "learning")
    store = JobStore(learning.database)
    operation = store.reserve_operation(
        learning.principal, learning.brain, job["id"], lease_token=job["lease_token"], fence=job["fence"],
        operation_id="prior-reservation", request_fingerprint=digest("original request"), input_bytes=100,
        max_model_calls=2, max_input_bytes=ORIGINAL.max_input_bytes,
    )
    job.update(model_calls=operation["job_model_calls"], input_bytes=operation["job_input_bytes"])
    learning.service.semantic_processor.policy = SemanticPolicy(max_model_calls=8)
    LearningEngine(learning.service)._execute(learning.principal, learning.brain, job)
    completed = learning.job(job["id"])
    assert completed["state"] == "cancelled" and completed["last_error"] == "budget_exceeded"
    assert completed["model_calls"] == 1 and completed["input_bytes"] == 100
    assert completed["max_model_calls"] == 2
    assert completed["result"]["semantic_policy"] == snapshot["semantic_policy"]
    kept = store.get_operation(learning.principal, learning.brain, job["id"], "prior-reservation")
    assert kept["request_fingerprint"] == digest("original request") and kept["outcome"] == "reserved"
    assert "private" not in json.dumps(kept)
