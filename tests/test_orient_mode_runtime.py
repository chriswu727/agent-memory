import copy
import json
from dataclasses import replace

import pytest
from psycopg.types.json import Jsonb
from test_current_request_runtime import orientation_job, request_processor
from test_learning import learning as learning
from test_semantic import encoded

from membrain.helpers import DomainError, uid
from membrain.jobs import JobStore
from membrain.learning import LearningEngine, orientation_result
from membrain.semantic import GENERAL_PROPOSAL_MODE, ORIENT_PROPOSAL_MODE, SemanticProcessor

pytestmark = pytest.mark.integration


def save_result(learning, job, result):
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(result), learning.principal.tenant_id, learning.brain, job["id"]))


def test_new_orientation_mode_is_persisted_and_exactly_reused(learning):
    learning.service.semantic_processor = request_processor(learning)
    engine, turn, job = orientation_job(learning)
    batch, frozen = engine._snapshot(learning.principal, learning.brain, job)
    assert batch.proposal_mode == frozen["proposal_mode"] == ORIENT_PROPOSAL_MODE
    reconstructed, resumed = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, job)
    assert reconstructed == batch and resumed == frozen
    assert batch.working_state == frozen["working_snapshot"]
    calls = []
    processor = SemanticProcessor(lambda request: calls.append(request) or encoded({"situation_updates": []}))
    for value in (batch, reconstructed):
        assert processor.process(value, reserve=lambda **_: None).status == "completed"
    assert calls[0].messages == calls[1].messages and calls[0].request_id == calls[1].request_id
    engine.semantic(learning.principal, learning.brain, job)
    completed = learning.job(job["id"])["result"]
    assert completed["proposal_mode"] == ORIENT_PROPOSAL_MODE
    assert completed["claims"] == completed["episode_actions"] == completed["belief_links"] == []
    assert len(completed["situation_updates"]) == 1
    assert completed["source_snapshot"] == frozen["source_snapshot"]
    assert completed["current_input_refs"] == turn["input_event_refs"]
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        assert orientation_result(conn, learning.service, learning.principal, brain, turn) == completed
    assert learning.calls == ["proposal", "verification"]


def test_legacy_frozen_orientation_keeps_general_contract_without_mode_migration(learning):
    learning.service.semantic_processor = request_processor(learning)
    engine, turn, job = orientation_job(learning)
    _, frozen = engine._snapshot(learning.principal, learning.brain, job)
    frozen.pop("proposal_mode")
    frozen.pop("working_snapshot")
    save_result(learning, job, frozen)
    resumed_batch, resumed = engine._snapshot(learning.principal, learning.brain, job)
    assert resumed == frozen and "proposal_mode" not in resumed
    assert resumed_batch.proposal_mode == GENERAL_PROPOSAL_MODE
    engine.semantic(learning.principal, learning.brain, job)
    completed = learning.job(job["id"])["result"]
    assert completed["status"] == "completed" and len(completed["situation_updates"]) == 1
    assert "proposal_mode" not in completed
    assert "episodes" in learning.snapshots[-1] and "consolidation_plan" in learning.snapshots[-1]
    assert "proposal_mode" not in learning.snapshots[-1]
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        assert orientation_result(conn, learning.service, learning.principal, brain, turn) == completed


@pytest.mark.parametrize("field", ["claims", "episode_actions", "belief_links", "consolidation"])
def test_custom_processor_cannot_publish_unused_foreground_products(learning, field):
    processor = request_processor(learning)
    process = processor.process
    unsupported = {"objectives": [{"objective_id": "background-work"}]} if field == "consolidation" else ({"fake": True},)

    def invalid(*args, **kwargs):
        return replace(process(*args, **kwargs), **{field: unsupported})

    processor.process = invalid
    learning.service.semantic_processor = processor
    engine, _, job = orientation_job(learning)
    with pytest.raises(DomainError, match="only current-situation"):
        engine.semantic(learning.principal, learning.brain, job)
    saved = learning.job(job["id"])["result"]
    assert saved["proposal_mode"] == ORIENT_PROPOSAL_MODE
    assert "situation_updates" not in saved and learning.working() is None


@pytest.mark.parametrize("field", ["claims", "episode_actions", "belief_links", "consolidation", "proposal_mode"])
def test_completed_orientation_rechecks_mode_contract_on_reuse(learning, field):
    learning.service.semantic_processor = request_processor(learning)
    engine, turn, job = orientation_job(learning)
    engine.semantic(learning.principal, learning.brain, job)
    result = copy.deepcopy(learning.job(job["id"])["result"])
    result[field] = ("unknown-v9" if field == "proposal_mode" else {"objectives": [{"fake": True}]}
                     if field == "consolidation" else [{"fake": True}])
    save_result(learning, job, result)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        with pytest.raises(DomainError) as error:
            orientation_result(conn, learning.service, learning.principal, brain, turn)
        assert error.value.code == ("context_stale" if field == "proposal_mode" else "invalid_evidence")


def test_invalid_frozen_mode_fails_before_provider_on_retry(learning):
    engine, _, job = orientation_job(learning)
    _, frozen = engine._snapshot(learning.principal, learning.brain, job)
    frozen["proposal_mode"] = "unsupported-v2"
    save_result(learning, job, frozen)
    with pytest.raises(DomainError, match="proposal mode"):
        engine.semantic(learning.principal, learning.brain, job)
    assert learning.calls == []


def test_changed_working_revision_rejects_new_mode_retry_before_spending(learning):
    engine, _, job = orientation_job(learning)
    _, frozen = engine._snapshot(learning.principal, learning.brain, job)
    state = copy.deepcopy(frozen["working_snapshot"])
    state["revision"] = 1
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        learning.service._working_save(conn, learning.principal, brain, job["payload"]["target"], state)
    with pytest.raises(DomainError, match="Working context changed"):
        engine.semantic(learning.principal, learning.brain, job)
    assert learning.calls == [] and learning.job(job["id"])["model_calls"] == 0


def test_consolidation_cannot_resume_an_orientation_contract(learning):
    engine, _, orient = orientation_job(learning)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        job_id = learning.service._job(conn, learning.principal, brain, "consolidate", orient["payload"])["job_id"]
    job = JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id)
    batch, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert snapshot["proposal_mode"] == batch.proposal_mode == GENERAL_PROPOSAL_MODE
    snapshot["proposal_mode"] = ORIENT_PROPOSAL_MODE
    save_result(learning, job, snapshot)
    with pytest.raises(DomainError, match="proposal mode"):
        engine._snapshot(learning.principal, learning.brain, job)
    assert learning.calls == []


def test_no_inference_foreground_finishes_without_background_proposals_or_verification(learning):
    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        assert data["proposal_mode"] == ORIENT_PROPOSAL_MODE
        assert "consolidation_plan" not in data and "episodes" not in data
        return encoded({"situation_updates": []})

    learning.service.semantic_processor = SemanticProcessor(propose, lambda _: pytest.fail("No update needs verification"))
    engine, _, job = orientation_job(learning)
    engine.semantic(learning.principal, learning.brain, job)
    completed = learning.job(job["id"])
    assert completed["state"] == "completed" and completed["model_calls"] == 1
    assert completed["result"]["learning_outcome"] == "no_proposals"
    assert completed["result"]["publication"] == {}
    assert completed["result"]["situation_updates"] == []
