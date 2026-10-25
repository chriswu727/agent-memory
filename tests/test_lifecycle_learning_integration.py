import importlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from test_bounded_learning_semantics import local_claim
from test_learning import delivered
from test_learning import learning as learning
from test_learning_integration import setup_history
from test_memory_lifecycle_evaluation import Meter
from test_semantic import encoded, proposals

from agent_memory.helpers import digest, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.runtime import BrainRuntime

pytestmark = pytest.mark.integration


def test_evaluator_drains_actual_delivered_source_phases_and_retains_unfunded_history(
    learning, monkeypatch, tmp_path,
):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    evaluator = importlib.import_module("examples.evaluate_memory_lifecycle")
    original = learning.service.semantic_processor
    _, semantic_requests = setup_history(learning, monkeypatch, count=4)
    processor = learning.service.semantic_processor
    propose, verify = processor.propose, processor.verify
    jobs, engine = JobStore(learning.database), LearningEngine(learning.service)
    while pending := jobs.claim(learning.principal, learning.brain, worker_id=uid(), kinds=["extract"], max_attempts=1):
        engine._execute(learning.principal, learning.brain, pending)
    learning.service.semantic_processor = original
    runtime = BrainRuntime(learning.memory, lambda request: "I have received this study preference.", deliver=delivered)
    response = runtime.respond("I prefer study conversation in these contexts.", message_id=uid(), conversation_id="chat")
    assert response.stage == "completed"
    sources = runtime.journal.sources(response.turn_id)
    assert sources["output_events"][0]["revision"] == 2
    assert sources["output_events"][0]["observed"]["delivery_state"] == "delivered"
    learning.service.semantic_processor = processor
    planned = evaluator.plan()
    planned["code_fingerprints"] = {}
    report = {"arm": "memory", "plan": planned, "plan_sha256": digest(planned),
              "principal": asdict(learning.principal), "stages": [], "brains": {"trained": learning.brain}}
    meter = Meter()

    class Provider:
        last_call_id = None
        last_usage = None

        def __call__(self, request):
            self.last_call_id = uid()
            self.last_usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
            meter.calls += 1
            if request.phase == "verification":
                return verify(request)
            answer = propose(request)
            body = json.loads(request.messages[-1]["content"])
            if "integration_manifest" not in body:
                user = next(source for source in body["sources"] if source["observed"]["actor_role"] == "user")
                return encoded(proposals(local_claim(user, statement="The user reported a study preference.")))
            return answer

    trial = evaluator.LifecycleTrial(tmp_path, report, learning.database, Provider(), None, meter, None)
    trial.current = {"name": "actual-delivery", "brain_id": learning.brain, "sources": sources,
                     "runtime": runtime.journal.get(response.turn_id)}
    report["stages"].append(trial.current)
    recorder = evaluator.RecordedCognition(trial)
    processor.propose = processor.verify = recorder
    trial.learn(learning.service, learning.brain, {"unrelated-recent-id"})
    stage = trial.current
    assert "learning_stopped_reason" not in stage
    assert [job["learning_phase"] for job in stage["learning_jobs"]] == ["encoding", "history_integration"]
    assert all(job["state"] == "completed" for job in stage["learning_jobs"])
    assert all(stage["learning_phase_checks"].values())
    assert len(stage["literal_jobs"]) == 3
    assert all(job["state"] == "completed" for job in stage["literal_jobs"])
    assert meter.calls == len(semantic_requests) == len(stage["learning_allocation_ledger"]) == 4
    assert all(item["outcome"] == "completed" and item["usage"]["total_tokens"] == 3
               for item in stage["learning_allocation_ledger"])
    assert {item["authorization_root_job_id"] for item in stage["learning_allocation_ledger"]} == {
        stage["learning_job_id"]}
    assert [call["learning_phase"] for call in stage["cognitive_calls"]] == [
        "encoding", "encoding", "history_integration", "history_integration"]
    phases = stage["learning_progress"]["learning_phases"]
    assert phases["counts_complete"] and phases["encoding_open_count"] == 0
    assert phases["history_integration_open_count"] == phases["history_integration_deferred_count"] == 1
    assert phases["history_integration_assessed_count"] == 1
    assert stage["learning_root_status"]["learning_summary"]["history_integration_state"] == "deferred"
    root = learning.job(stage["learning_job_id"])
    assert root["model_calls"] == 2
    user = sources["input_events"][0]
    assert {"event_id": user["event_id"], "revision": user["revision"]} in root["result"]["learning_input_refs"]
    assert root["result"]["publication"]["memory_versions"]
    with learning.database.transaction(learning.principal) as connection:
        brain = learning.service._brain(connection, learning.principal, learning.brain)
        assert brain["indexed_sequence"] == brain["durable_sequence"]
