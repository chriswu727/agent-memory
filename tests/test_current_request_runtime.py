import copy
import json
from dataclasses import asdict

import pytest
from psycopg.types.json import Jsonb
from test_current_input_binding import MUTATIONS, damaged_authorization
from test_learning import delivered
from test_learning import learning as learning
from test_semantic import encoded, proposals, situation

from agent_memory.helpers import DomainError, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine, orientation_result, validate_snapshot
from agent_memory.runtime import BrainRuntime, RuntimePolicy
from agent_memory.semantic import SemanticProcessor, SemanticResult
from agent_memory.turns import TurnStore

pytestmark = pytest.mark.integration


def request_processor(learning):
    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        learning.calls.append(request.phase)
        learning.snapshots.append(data)
        source = data["sources"][-1]
        return encoded(proposals(updates=[situation(
            source, value="核实是否存在过去的海边约定。", evidence_use="current_request",
        )]))

    return SemanticProcessor(propose, learning.verify)


def orientation_job(learning):
    _, turn, token = learning.capture("你还记得我们约好去海边吗？")
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        payload = {"turn_id": turn["turn_id"], "target": {"conversation_ref": "chat", "branch_ref": "main"},
                   "through_sequence": turn["durable_sequence"]}
        job_id = learning.service._job(conn, learning.principal, brain, "orient", payload)["job_id"]
    turn = TurnStore(learning.database).bind_orientation(
        learning.principal, learning.brain, turn["turn_id"], lease_token=token,
        expected_revision=turn["revision"], job_id=job_id,
    )
    job = JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id)
    return LearningEngine(learning.service), turn, job


def alter_request(result, mutation):
    item = result["situation_updates"][0]
    assessments = result["verification_assessments"]
    if mutation == "missing_verification":
        item.pop("request_verification")
    elif mutation == "changed_verification":
        item["request_verification"]["state"] = "unknown"
    elif mutation == "empty_evidence":
        item["evidence_refs"] = []
        assessments[0]["relations"] = []
    elif mutation == "missing_refs":
        item.pop("evidence_refs")
    elif mutation == "null_refs":
        item["evidence_refs"] = None
    elif mutation == "malformed_refs":
        item["evidence_refs"] = [{}]
    elif mutation == "unknown_request":
        assessments[0]["request_verdict"] = "unknown"
    elif mutation == "unknown_proposal":
        assessments[0]["proposal_verdict"] = "unknown"
    elif mutation == "missing_assessment":
        result["verification_assessments"] = []
    elif mutation == "duplicate_assessment":
        result["verification_assessments"] = list(assessments) * 2
    elif mutation == "missing_evidence":
        assessments[0]["relations"] = []
    elif mutation == "duplicate_evidence":
        assessments[0]["relations"] *= 2
    elif mutation == "unsupported_evidence":
        assessments[0]["relations"][0]["verdict"] = "unknown"
    elif mutation == "invalid_index":
        assessments[0]["relations"][0]["evidence_index"] = 1
    else:
        raise AssertionError(mutation)


def test_current_request_reaches_host_without_turning_question_presupposition_into_history(learning):
    learning.service.semantic_processor = request_processor(learning)
    prompts = []
    runtime = BrainRuntime(learning.memory, lambda request: prompts.append(request) or "没有找到过去的约定。",
                           deliver=delivered, policy=RuntimePolicy(recent_context_events=0))
    result = runtime.respond("你还记得我们约好去海边吗？", message_id="question", conversation_id="chat")
    assert result.stage == "completed"
    view = json.loads(prompts[0].memory_context)
    current = [block["content"] for block in view["blocks"] if block["section"] == "current_situation"]
    assert len(current) == 1
    assert current[0]["evidence_use"] == "current_request"
    assert current[0]["use"] == "current_request_not_historical_evidence"
    assert current[0]["request_verification"]["state"] == "supported_request"
    assert view["memory_versions"] == {}
    turn = runtime.journal.get(result.turn_id)
    snapshot = learning.job(turn["orientation_job_id"])["result"]
    assert snapshot["current_input_refs"] == turn["input_event_refs"]
    assert snapshot["current_input_binding"] == {"turn_id": result.turn_id, "job_id": turn["orientation_job_id"]}
    saved = learning.working()["items"][0]
    assert saved["value"]["evidence_use"] == "current_request"
    assert "observation" not in saved["value"]
    learning.service.semantic_processor = None
    runtime.respond("今天聊什么？", message_id="later", conversation_id="chat")
    later = json.loads(prompts[1].memory_context)
    assert not any(block["section"] in {"current_situation", "working_state"} for block in later["blocks"])
    assert later["memory_versions"] == {}
    assert learning.calls == ["proposal", "verification"]


def test_current_input_authority_is_bound_to_turn_and_frozen_across_snapshot_retry(learning):
    learning.observe("较早的另一条问题，去哪里？")
    engine, turn, job = orientation_job(learning)
    batch, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert batch.current_input_refs == tuple(turn["input_event_refs"])
    assert len(batch.sources) > len(batch.current_input_refs)
    resumed, retried = engine._snapshot(learning.principal, learning.brain, job)
    assert resumed.current_input_refs == batch.current_input_refs
    assert retried == snapshot


def test_current_request_generation_recovery_reuses_exact_prompt_and_frozen_authority(learning):
    learning.service.semantic_processor = request_processor(learning)
    requests = []

    def uncertain(request):
        requests.append(request)
        raise TimeoutError("Generation outcome is unknown")

    policy = RuntimePolicy(recent_context_events=0)
    runtime = BrainRuntime(learning.memory, uncertain, policy=policy)
    with pytest.raises(TimeoutError):
        runtime.respond("你还记得我们约好去海边吗？", message_id="retry", conversation_id="chat")
    turn = runtime.journal.list()[0]
    frozen = copy.deepcopy(learning.job(turn["orientation_job_id"])["result"])

    def recover(request):
        assert request.messages == requests[0].messages
        assert request.request_id == requests[0].request_id
        assert learning.job(turn["orientation_job_id"])["result"] == frozen
        return "没有找到过去的约定。"

    resumed = BrainRuntime(learning.memory, recover, deliver=delivered, policy=policy, generation_is_idempotent=True)
    assert resumed.resume(turn["turn_id"]).stage == "completed"
    assert learning.calls == ["proposal", "verification"]
    completed = learning.job(turn["orientation_job_id"])["result"]
    for field in ("current_input_refs", "current_input_binding", "source_snapshot", "situation_updates"):
        assert completed[field] == frozen[field]


def test_legacy_frozen_orientation_does_not_gain_current_request_authority(learning):
    engine, _, job = orientation_job(learning)
    _, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    snapshot.pop("current_input_refs")
    snapshot.pop("current_input_binding")
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(snapshot), learning.principal.tenant_id, learning.brain, job["id"]))
    resumed, retried = engine._snapshot(learning.principal, learning.brain, job)
    assert resumed.current_input_refs == ()
    assert retried == snapshot


@pytest.mark.parametrize("mutation", ["input_refs", "job_binding", "context"])
def test_changed_current_input_binding_invalidates_frozen_orientation(learning, mutation):
    engine, turn, job = orientation_job(learning)
    _, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    with learning.database.transaction(learning.principal) as conn:
        if mutation == "input_refs":
            conn.execute("UPDATE memory_runtime_turns SET input_event_refs='[]'::jsonb "
                         "WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
                         (learning.principal.tenant_id, learning.brain, turn["turn_id"]))
        elif mutation == "job_binding":
            snapshot["current_input_binding"]["job_id"] = "another-job"
        else:
            snapshot["scope"]["conversation_id"] = "another-chat"
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        with pytest.raises(DomainError) as error:
            validate_snapshot(conn, learning.service, learning.principal, brain, snapshot)
        assert error.value.code == "context_stale"


def test_malformed_authority_cannot_survive_snapshot_retry_or_completed_reuse(learning):
    learning.service.semantic_processor = request_processor(learning)
    engine, turn, job = orientation_job(learning)
    engine.semantic(learning.principal, learning.brain, job)
    original = copy.deepcopy(learning.job(job["id"])["result"])
    for mutation in MUTATIONS:
        damaged = damaged_authorization(original, mutation)
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            with pytest.raises(DomainError) as error:
                validate_snapshot(conn, learning.service, learning.principal, brain, damaged)
            assert error.value.code == "context_stale", mutation
            conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(damaged), learning.principal.tenant_id, learning.brain, job["id"]))
            with pytest.raises(DomainError) as error:
                orientation_result(conn, learning.service, learning.principal, brain, turn)
            assert error.value.code == "context_stale", mutation
        with pytest.raises(DomainError) as error:
            engine._snapshot(learning.principal, learning.brain, job)
        assert error.value.code == "context_stale", mutation
    assert learning.calls == ["proposal", "verification"]


def test_consolidation_cannot_gain_current_input_authority_from_a_turn_payload(learning):
    engine, _, orient = orientation_job(learning)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        job_id = learning.service._job(conn, learning.principal, brain, "consolidate", orient["payload"])["job_id"]
    job = JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id)
    batch, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert batch.current_input_refs == ()
    assert "current_input_binding" not in snapshot
    processed = request_processor(learning).process(batch, reserve=lambda **_: None)
    assert processed.situation_updates == ()
    assert processed.rejected[0]["reason"] == "invalid_proposal"
    assert learning.calls == ["proposal"]


@pytest.mark.parametrize("mutation", [
    "missing_verification", "changed_verification", "missing_authority", "empty_evidence", "unknown_request",
    "unknown_proposal", "missing_assessment", "duplicate_assessment", "missing_evidence", "duplicate_evidence",
    "unsupported_evidence", "invalid_index", "missing_refs", "null_refs", "malformed_refs",
])
def test_completed_orientation_rechecks_current_request_certificate_before_reuse(learning, mutation):
    learning.service.semantic_processor = request_processor(learning)
    engine, turn, job = orientation_job(learning)
    engine.semantic(learning.principal, learning.brain, job)
    result = copy.deepcopy(learning.job(job["id"])["result"])
    if mutation == "missing_authority":
        result.pop("current_input_refs")
        result.pop("current_input_binding")
    else:
        alter_request(result, mutation)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(result), learning.principal.tenant_id, learning.brain, job["id"]))
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        with pytest.raises(DomainError) as error:
            orientation_result(conn, learning.service, learning.principal, brain, turn)
        assert error.value.code == "invalid_evidence"


@pytest.mark.parametrize("mutation", [
    "missing_verification", "changed_verification", "empty_evidence", "unknown_request", "unknown_proposal",
    "missing_assessment", "duplicate_assessment", "missing_evidence", "duplicate_evidence", "unsupported_evidence",
    "invalid_index", "missing_refs", "null_refs", "malformed_refs",
])
def test_orientation_publication_rejects_an_unsupported_processor_result(learning, mutation):
    processor = request_processor(learning)
    process = processor.process

    def unsupported(*args, **kwargs):
        result = asdict(process(*args, **kwargs))
        alter_request(result, mutation)
        return SemanticResult(**result)

    processor.process = unsupported
    learning.service.semantic_processor = processor
    engine, _, job = orientation_job(learning)
    with pytest.raises(DomainError) as error:
        engine.semantic(learning.principal, learning.brain, job)
    assert error.value.code == "invalid_evidence"
    assert "situation_updates" not in learning.job(job["id"])["result"]
    assert learning.working() is None
