import importlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from test_bounded_learning_semantics import local_claim
from test_learning import delivered
from test_learning import learning as learning
from test_learning_integration import setup_history, setup_legacy_history
from test_learning_windows import jobs as recorded_jobs
from test_memory_lifecycle_evaluation import Meter
from test_semantic import encoded, proposals

from agent_memory.helpers import digest, uid
from agent_memory.history_attention import POLICY, attention_summary
from agent_memory.jobs import JobStore
from agent_memory.runtime import BrainRuntime

pytestmark = pytest.mark.integration


def settle_already_published_seed_extraction(learning, memories):
    """Directly published seed assertions do not need a second literal fixture population."""
    references = {(ref["event_id"], ref["event_revision"]) for memory in memories for ref in memory["evidence_refs"]}
    store = JobStore(learning.database)
    settled = set()
    while pending := store.claim(learning.principal, learning.brain, worker_id=uid(), kinds=["extract"], max_attempts=1):
        reference = (pending["payload"]["event_id"], pending["payload"]["revision"])
        assert reference in references and reference not in settled
        store.complete(learning.principal, learning.brain, pending["id"],
                       lease_token=pending["lease_token"], fence=pending["fence"])
        settled.add(reference)
    assert settled == references


@pytest.mark.parametrize("issuance", ["legacy", "latest"])
def test_evaluator_drains_actual_delivered_phases_and_distinguishes_debt_from_unselected_history(
    learning, monkeypatch, tmp_path, issuance,
):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    evaluator = importlib.import_module("examples.evaluate_memory_lifecycle")
    original = learning.service.semantic_processor
    setup = setup_legacy_history if issuance == "legacy" else setup_history
    memories, semantic_requests = setup(learning, monkeypatch, count=4)
    processor = learning.service.semantic_processor
    propose, verify = processor.propose, processor.verify
    jobs = JobStore(learning.database)
    settle_already_published_seed_extraction(learning, memories)
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
    assert [entry["learning_phase"] for entry in stage["learning_allocation_ledger"]] == [
        "encoding", "encoding", "history_integration", "history_integration"]
    snapshots = stage["learning_authority_snapshots"]
    assert [snapshot["job_id"] for snapshot in snapshots] == [job["job_id"] for job in stage["learning_jobs"]]
    assert all(snapshot["payload_sha256"] == digest(snapshot["payload"]) for snapshot in snapshots)
    assert all(snapshot["payload"]["authorization_root_job_id"] == stage["learning_job_id"]
               for snapshot in snapshots)
    assert all(snapshot["payload"]["target"] == {"conversation_ref": "chat", "branch_ref": "main"}
               for snapshot in snapshots)
    assert all(job["model_calls"] == 2 for job in stage["learning_jobs"])
    history = learning.job(stage["learning_jobs"][1]["job_id"])
    manifest = history["payload"]["history_obligation_manifest"]
    selected = history["result"]["integration_manifest"]["objectives"]
    coverage = history["result"]["history_integration"]
    assert coverage["assessed_count"] == coverage["published_count"] == len(selected) == 3
    assert {item["objective_id"] for item in coverage["objectives"]} == {
        item["objective_id"] for item in selected}
    assert all(item["assessed"] and item["status"] == "published_relation" for item in coverage["objectives"])
    phases = stage["learning_progress"]["learning_phases"]
    assert phases["counts_complete"] and phases["encoding_open_count"] == 0
    expected_debt = 1 if issuance == "legacy" else 0
    assert phases["history_integration_open_count"] == phases["history_integration_deferred_count"] == expected_debt
    assert phases["history_integration_assessed_count"] == 1
    current = stage["learning_root_status"]["learning_summary"]
    assert current["history_integration_state"] == ("deferred" if issuance == "legacy" else "completed")
    assert current["history_integration_resolved"] is True
    assert current["history_integration_open"] is bool(expected_debt)
    root = learning.job(stage["learning_job_id"])
    assert root["model_calls"] == 2
    history_rows = [row for row in recorded_jobs(learning) if row["payload"].get("learning_phase") == "history_integration"]
    if issuance == "legacy":
        assert "history_attention_policy" not in root["payload"]
        assert "history_funding_permission" not in root["payload"]
        assert "attention" not in manifest and len(manifest["objectives"]) == 4
        assert len(history_rows) == 2
        tail = learning.job(history["payload"]["integration_continuation_job_id"])
        assert tail["state"] == "deferred" and tail["model_calls"] == tail["input_bytes"] == 0
        assert tail["result"]["integration_manifest"] == history["result"]["remaining_manifest"]
        assert len(tail["result"]["integration_manifest"]["objectives"]) == 1
        assert tail["id"] not in {item["job_id"] for item in stage["learning_allocation_ledger"]}
    else:
        assert root["payload"]["history_attention_policy"] == POLICY
        assert "history_funding_permission" in snapshots[0]["payload"]
        summary = attention_summary(manifest)
        assert summary["candidate_count"] == 4
        assert summary["admitted_count"] == 3 and summary["not_selected_count"] == 1
        assert summary["search_exhaustive"] is False
        assert len(history_rows) == 1 and not history["result"]["remaining_manifest"]["objectives"]
        candidates = manifest["attention"]["candidates"]
        assert {item["target_memory_version"]["memory_id"] for item in candidates} == {
            memory["memory_id"] for memory in memories}
        omitted = {item["objective_id"] for item in candidates if item["basis"] == "not_selected"}
        assert len(omitted) == 1
        assert omitted.isdisjoint(item["objective_id"] for item in manifest["objectives"])
        assert omitted.isdisjoint(item["objective_id"] for item in coverage["objectives"])
        assert omitted.isdisjoint(item["objective_id"] for item in history["result"]["remaining_manifest"]["objectives"])
        public = jobs.get(learning.principal, learning.brain, history["id"])["learning_summary"]
        assert public["history_attention"] == summary
    user = sources["input_events"][0]
    assert {"event_id": user["event_id"], "revision": user["revision"]} in root["result"]["learning_input_refs"]
    assert root["result"]["publication"]["memory_versions"]
    with learning.database.transaction(learning.principal) as connection:
        brain = learning.service._brain(connection, learning.principal, learning.brain)
        assert brain["indexed_sequence"] == brain["durable_sequence"]
