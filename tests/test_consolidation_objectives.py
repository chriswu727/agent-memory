import json

import pytest
from test_learning import learning as learning
from test_learning_windows import claim as claim_job
from test_learning_windows import drain, jobs
from test_semantic import claim, encoded, proposals, verifier

from agent_memory.learning import LearningEngine
from agent_memory.semantic import SemanticPolicy, SemanticProcessor

pytestmark = pytest.mark.integration


def install_instance_learning(learning):
    snapshots = []

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        snapshots.append(data)
        if len(snapshots) == 1:
            items = [claim(source, proposal_id=f"instance-{index}",
                           statement=source["observed"]["content"]["text"], memory_kind="episodic",
                           representation="episode_outline", claim_kind="reported_fact", conditions={"op": "always"})
                     for index, source in enumerate(data["sources"])]
            return encoded(proposals(*items))
        decisions = [{"objective_id": item["objective_id"], "outcome": "no_change",
                      "reason": "insufficient_shared_meaning", "link_proposal_ids": []}
                     for item in data["consolidation_plan"]["objectives"]]
        return encoded({**proposals(), "consolidation_decisions": decisions})

    learning.service.semantic_processor = SemanticProcessor(propose, verifier)
    learning.observe("I talked with a friend before studying algebra.")
    learning.observe("I talked with a colleague before studying biology.")
    drain(learning)
    assert jobs(learning)[0]["result"]["publication"]["published_count"] == 2
    return snapshots


def test_completed_model_attempt_is_durable_and_new_cue_does_not_repeat_it(learning):
    snapshots = install_instance_learning(learning)
    learning.observe("I recalled those two study sessions.")
    drain(learning)
    second = jobs(learning)[1]
    assert second["state"] == "completed"
    assert second["model_calls"] == 1
    assert second["result"]["consolidation"]["no_change_count"] == 1
    assert second["result"]["consolidation"]["satisfied_count"] == 0
    assert len(second["result"]["consolidation_plan"]["objectives"]) == 1
    assert second["result"]["learning_window"]["processing_succeeded"] is True
    learning.observe("Those old study sessions came to mind again.")
    drain(learning)
    third = jobs(learning)[2]
    assert third["state"] == "completed"
    assert third["result"]["consolidation_plan"]["objectives"] == []
    assert third["result"]["consolidation_plan"]["suppressed_count"] == 1
    assert snapshots[-1]["consolidation_plan"] == third["result"]["consolidation_plan"]
    assert sum(job["model_calls"] for job in jobs(learning)) == 4


def test_frozen_plan_survives_learning_engine_restart_before_execution(learning):
    install_instance_learning(learning)
    learning.observe("I recalled those two study sessions.")
    selected = claim_job(learning)
    engine = LearningEngine(learning.service)
    batch, snapshot = engine._snapshot(learning.principal, learning.brain, selected)
    assert len(batch.consolidation_plan["objectives"]) == 1
    recovered, recovered_snapshot = LearningEngine(learning.service)._snapshot(
        learning.principal, learning.brain, selected)
    assert recovered.consolidation_plan == batch.consolidation_plan == snapshot["consolidation_plan"]
    assert recovered_snapshot == snapshot
    engine._execute(learning.principal, learning.brain, selected)
    completed = learning.job(selected["id"])
    assert completed["state"] == "completed" and completed["model_calls"] == 1
    assert completed["result"]["consolidation_plan"] == snapshot["consolidation_plan"]


def test_model_ignored_goal_is_reported_unresolved_and_can_be_attempted_later(learning):
    install_instance_learning(learning)
    learning.service.semantic_processor = SemanticProcessor(lambda _: encoded(proposals()), verifier)
    learning.observe("I recalled those study sessions.")
    drain(learning)
    result = jobs(learning)[1]["result"]
    assert result["consolidation"]["unresolved_count"] == 1
    assert result["consolidation"]["satisfied_count"] == 0
    learning.observe("I thought about those study sessions again.")
    drain(learning)
    assert jobs(learning)[2]["result"]["consolidation_plan"]["suppressed_count"] == 0
    assert len(jobs(learning)[2]["result"]["consolidation_plan"]["objectives"]) == 1


def test_orientation_does_not_schedule_cross_instance_organization(learning):
    snapshots = install_instance_learning(learning)
    journal, row, token = learning.capture("I recalled those study sessions.")
    oriented = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    job = learning.job(oriented["orientation_job_id"])
    assert job["state"] == "completed"
    assert job["result"]["consolidation_plan"]["candidate_count"] == 1
    assert job["result"]["consolidation_plan"]["objectives"] == []
    assert snapshots[-1]["consolidation_plan"]["objectives"] == []


def test_completed_nonwindow_job_with_failed_verifier_does_not_suppress_retry(learning):
    install_instance_learning(learning)
    receipt = learning.observe("I recalled those study sessions.")

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        objective = data["consolidation_plan"]["objectives"][0]
        lookup = {(item["memory_id"], item["revision"]): item for item in data["existing_assertions"]}
        instances = [lookup[(item["memory_id"], item["revision"])]
                     for item in objective["instance_memory_versions"]]
        references = [reference for item in instances for reference in item["evidence_refs"]]
        proposed = claim(data["sources"][0], statement="In these two recorded sessions, talking preceded study.",
                         memory_kind="semantic", representation="schema", claim_kind="inferred_pattern",
                         conditions={"op": "always"}, evidence_refs=references,
                         source_memory_versions=objective["instance_memory_versions"])
        link = {"proposal_id": "comparison-link", "kind": "abstracts", "claim_proposal_id": proposed["proposal_id"],
                "target_memory_versions": objective["instance_memory_versions"], "evidence_refs": references,
                "world": "real_world", "conditions": {"op": "always"}, "valid_during": {"kind": "unknown"}}
        return encoded({**proposals(proposed), "belief_links": [link], "consolidation_decisions": [{
            "objective_id": objective["objective_id"], "outcome": "proposed", "reason": None,
            "link_proposal_ids": [link["proposal_id"]]}]})

    def unavailable(_):
        raise TimeoutError()

    learning.service.semantic_processor = SemanticProcessor(propose, unavailable)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        queued = learning.service._job(conn, learning.principal, brain, "consolidate", {
            "event_id": receipt["accepted_event_ids"][0], "revision": 1,
            "through_sequence": receipt["durable_through_sequence"],
            "target": {"conversation_ref": "chat", "branch_ref": "main"},
        })
    selected = claim_job(learning, queued["job_id"])
    LearningEngine(learning.service)._execute(learning.principal, learning.brain, selected)
    completed = learning.job(selected["id"])
    assert completed["state"] == "completed"
    assert completed["result"]["status"] == "provider_unavailable"
    objective = completed["result"]["consolidation"]["objectives"][0]
    assert objective["attempted"] is True and objective["dedup_eligible"] is False
    next_job = claim_job(learning)
    recovered, _ = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, next_job)
    assert recovered.consolidation_plan["suppressed_count"] == 0
    assert len(recovered.consolidation_plan["objectives"]) == 1


@pytest.mark.parametrize("wall_time_ms", [30_000, 120_001, 240_000, 300_000])
def test_model_call_lease_covers_the_configured_deadline_without_sleep(learning, wall_time_ms):
    inspected = []

    def inspect(request):
        with learning.database.transaction(learning.principal) as conn:
            remaining = conn.execute(
                "SELECT extract(epoch FROM lease_expires_at-clock_timestamp()) AS remaining "
                "FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND state='running'",
                learning.service._params(learning.principal, learning.brain),
            ).fetchone()["remaining"]
        assert remaining > request.timeout_seconds
        assert remaining > max(120, (wall_time_ms + 999) // 1000 + 5) - 3
        inspected.append(request.phase)

    def propose(request):
        inspect(request)
        return learning.propose(request)

    def verify(request):
        inspect(request)
        return learning.verify(request)

    learning.service.semantic_processor = SemanticProcessor(
        propose, verify, SemanticPolicy(max_wall_time_ms=wall_time_ms),
    )
    learning.observe("I am making a study plan.")
    drain(learning)
    assert inspected == ["proposal", "verification"]
    completed = jobs(learning)[0]
    assert completed["state"] == "completed" and completed["model_calls"] == 2
