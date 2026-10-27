import json

import pytest
from test_learning import learning as learning
from test_learning_windows import claim as claim_job
from test_learning_windows import drain, jobs
from test_semantic import claim, encoded, proposals, verifier

from membrain import learning as learning_module
from membrain.consolidation_planning import PLANNER_VERSION, plan_consolidation
from membrain.learning import LearningEngine
from membrain.semantic import SemanticPolicy, SemanticProcessor

pytestmark = pytest.mark.integration


def install_instance_learning(learning):
    snapshots = []

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        snapshots.append(data)
        if data.get("proposal_mode") == "orient-v1":
            return encoded({"situation_updates": []})
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


def test_completed_model_attempt_is_durable_and_new_cue_does_not_repeat_it(learning, monkeypatch):
    snapshots = install_instance_learning(learning)
    plans = []

    def counted(*args, **kwargs):
        plans.append(kwargs)
        return plan_consolidation(*args, **kwargs)

    monkeypatch.setattr(learning_module, "plan_consolidation", counted)
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
    assert len(plans) == 2 and all(item["include_all"] for item in plans)


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


def test_completed_v3_comparison_is_not_reauthorized_by_v4_ranking_upgrade(learning, monkeypatch):
    install_instance_learning(learning)
    with monkeypatch.context() as patch:
        patch.setattr(learning_module, "plan_consolidation", lambda *args, **kwargs:
                      plan_consolidation(*args, **kwargs, planner_version="independent-instance-comparison-v3"))
        learning.observe("I recalled those two study sessions.")
        drain(learning)
    previous = jobs(learning)[1]
    assert previous["result"]["consolidation_plan"]["planner_version"] == "independent-instance-comparison-v3"
    assert previous["result"]["consolidation"]["no_change_count"] == 1
    learning.observe("Those old study sessions came to mind again.")
    drain(learning)
    current = jobs(learning)[2]
    assert current["result"]["consolidation_plan"]["planner_version"] == PLANNER_VERSION
    assert current["result"]["consolidation_plan"]["objectives"] == []
    assert current["result"]["consolidation_plan"]["suppressed_count"] == 1
    assert sum(job["model_calls"] for job in jobs(learning)) == 4


def test_frozen_v3_comparison_survives_restart_under_v4_default(learning, monkeypatch):
    install_instance_learning(learning)
    learning.observe("I recalled those two study sessions.")
    selected = claim_job(learning)
    engine = LearningEngine(learning.service)
    with monkeypatch.context() as patch:
        patch.setattr(learning_module, "plan_consolidation", lambda *args, **kwargs:
                      plan_consolidation(*args, **kwargs, planner_version="independent-instance-comparison-v3"))
        _, snapshot = engine._snapshot(learning.principal, learning.brain, selected)
    old_plan = snapshot["consolidation_plan"]
    assert old_plan["planner_version"] == "independent-instance-comparison-v3"
    resumed, frozen = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    assert resumed.consolidation_plan == old_plan and frozen == snapshot
    engine._execute(learning.principal, learning.brain, selected)
    finished = learning.job(selected["id"])
    assert finished["state"] == "completed" and finished["model_calls"] == 1
    assert finished["result"]["consolidation_plan"] == old_plan


def test_published_distinct_fragments_do_not_crowd_out_independent_source_pair(learning):
    repeated = ("I chose a cedar garden fence.", "I planted cedar near the garden fence.",
                "I painted the cedar garden fence.")
    independent = ("I launched a kayak on the ocean beside the blue pier.",
                   "I paddled a kayak on the ocean past a quiet cove.")
    observed = [learning.observe(" ".join(repeated)) for _ in range(2)]
    observed += [learning.observe(text) for text in independent]
    source_ids = [receipt["accepted_event_ids"][0] for receipt in observed]
    snapshots = []

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        snapshots.append(data)
        if len(snapshots) == 1:
            lookup = {source["event_id"]: source for source in data["sources"]}
            items = []
            for index, identifier in enumerate(source_ids):
                statements = repeated if index < 2 else [independent[index - 2]]
                items.extend(claim(lookup[identifier], proposal_id=f"instance-{index}-{fragment}",
                                   statement=text, memory_kind="episodic", representation="episode_outline",
                                   claim_kind="reported_fact", conditions={"op": "always"})
                             for fragment, text in enumerate(statements))
            return encoded(proposals(*items))
        return encoded({**proposals(), "consolidation_decisions": [
            {"objective_id": item["objective_id"], "outcome": "no_change", "reason": "insufficient_shared_meaning",
             "link_proposal_ids": []} for item in data["consolidation_plan"]["objectives"]]})

    learning.service.semantic_processor = SemanticProcessor(propose, verifier)
    drain(learning)
    assert jobs(learning)[0]["result"]["publication"]["published_count"] == 8
    learning.observe("I recalled the cedar garden fence and the ocean kayak trips.")
    drain(learning)
    body = snapshots[-1]
    current = body["consolidation_plan"]
    old = plan_consolidation(body["sources"], body["existing_assertions"], body["belief_relations"], body["sampling"],
                             planner_version="independent-instance-comparison-v3")
    assertions = {item["memory_id"]: item for item in body["existing_assertions"]}

    def pairs(plan):
        return [frozenset(reference["event_id"] for version in objective["instance_memory_versions"]
                          for reference in assertions[version["memory_id"]]["evidence_refs"]
                          if reference["relationship"] == "supports") for objective in plan["objectives"]]

    assert pairs(old) == [frozenset(source_ids[:2])] * 3
    assert frozenset(source_ids[2:]) in pairs(current)
    assert len(current["objectives"]) == 3
    assert jobs(learning)[1]["result"]["consolidation"]["no_change_count"] == 3
    assert jobs(learning)[1]["result"]["consolidation"]["satisfied_count"] == 0


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
    assert "consolidation_plan" not in snapshots[-1]
    assert job["result"]["status"] == "completed"
    assert job["result"]["learning_outcome"] == "no_proposals"


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


@pytest.mark.parametrize("wall_time_ms", [30_000, 120_001, 240_000, 300_000, 600_000])
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
