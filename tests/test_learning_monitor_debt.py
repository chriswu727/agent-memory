import copy
import json
from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_learning_integration import encode, setup_history, setup_legacy_history
from test_learning_phase_monitor import Connection, encoding, exact_rows, history, manifest
from test_learning_progress import AT, summarize, window
from test_learning_settlement_recovery import expire, operations, reserve
from test_learning_windows import claim, execute, observations

from agent_memory.history_semantics import integration_coverage
from agent_memory.jobs import NO_UNSETTLED_RETRY_POLICY, JobStore
from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import HISTORY_PHASE, allocation_metadata
from agent_memory.learning_phase_monitor import history_debt, resolve_history_status


def pending(count=3, *, state="running"):
    value = manifest(count)
    row = history(state=state, model_calls=1, attempts=1, fence=1,
                  lease_expires_at=AT - timedelta(seconds=1), history_manifest=value,
                  has_history_manifest=True, history_coverage=None,
                  settlement_policy=NO_UNSETTLED_RETRY_POLICY, has_settlement_policy=True,
                  has_unsettled_operation=True)
    row["phase_payload"]["history_obligation_manifest"] = copy.deepcopy(value)
    return row


def exact_pending(row):
    parent, child = exact_rows()
    child.update(state=row["state"], model_calls=row["model_calls"], last_error=row.get("last_error"))
    child["payload"].pop("history_obligation_manifest")
    child["payload"] = {**child["payload"], **copy.deepcopy(row["phase_payload"])}
    child["result"] = {"integration_manifest": copy.deepcopy(row.get("history_manifest"))}
    if row.get("history_coverage") is not None:
        child["result"]["history_integration"] = copy.deepcopy(row["history_coverage"])
    if not row.get("has_history_manifest"):
        child["result"].pop("integration_manifest")
    return parent, child


def assessed(value, *, complete=True):
    decisions = [{"objective_id": item["objective_id"], "outcome": "no_change",
                  "reason": "insufficient_shared_meaning", "link_proposal_ids": []}
                 for item in value["objectives"]]
    return integration_coverage(value, decisions, [], assessed=complete)


def test_retained_manifest_survives_an_unknown_provider_outcome_without_invented_coverage():
    row = pending()
    result = summarize(encoding(), row)
    assert result["counts_complete"] and result["learning_phases"]["counts_complete"]
    assert result["runnable_count"] == result["running_count"] == 0
    assert result["blocked_reason_counts"] == {"provider_uncertain": 1}
    assert result["learning_phases"]["history_integration_unassessed_objective_count"] == 3
    assert result["learning_phases"]["history_integration_assessed_count"] == 0
    parent, child = exact_pending(row)
    exact = resolve_history_status(Connection(parent, [child]), parent)
    assert exact["phase_metadata_complete"] and exact["history_integration_open"]
    assert exact["history_integration_unassessed_objective_count"] == 3
    assert "history_integration" not in exact
    assert "private-" not in json.dumps([result, exact])


@pytest.mark.parametrize("damage", ["missing", "null", "fingerprint", "revision", "endpoint",
                                    "other_input", "other_cutoff", "outside_admission", "coverage"])
def test_missing_or_invalid_obligations_remain_explicitly_incomplete_in_both_monitors(damage):
    row = pending()
    if damage == "missing":
        row.pop("history_manifest")
        row["has_history_manifest"] = False
        row["phase_payload"].pop("history_obligation_manifest")
        row["model_calls"] = 0
    elif damage == "null":
        row["history_manifest"] = None
    elif damage == "fingerprint":
        row["history_manifest"]["input_fingerprint"] = "bad"
    elif damage == "revision":
        row["history_manifest"]["input_refs"][0]["revision"] = True
    elif damage == "endpoint":
        row["history_manifest"]["objectives"][0]["target_memory_version"]["memory_id"] = []
    elif damage == "other_input":
        row["phase_payload"]["encoding_input_fingerprint"] = "c" * 64
    elif damage == "other_cutoff":
        row["phase_payload"]["history_cutoff_sequence"] = 9
    elif damage == "outside_admission":
        row["phase_payload"]["history_obligation_manifest"] = manifest(1)
    else:
        row["history_coverage"] = {"objectives": []}
    result = summarize(encoding(), row)
    assert not result["counts_complete"]
    assert not result["learning_phases"]["counts_complete"]
    assert result["learning_phases"]["unknown_count"] == 1
    assert result["learning_phases"]["history_integration_open_count"] == 1
    parent, child = exact_pending(row)
    exact = resolve_history_status(Connection(parent, [child]), parent)
    assert not exact["phase_metadata_complete"]
    assert not exact["history_integration_resolved"]


@pytest.mark.parametrize("state", ["queued", "running", "failed"])
@pytest.mark.parametrize("policy", [None, NO_UNSETTLED_RETRY_POLICY, "unsupported"])
@pytest.mark.parametrize("unsettled", [False, True])
def test_expired_operation_scheduling_preserves_legacy_and_settlement_policy(state, policy, unsettled):
    result = summarize(window(state=state, attempts=1, lease_expires_at=AT,
                              settlement_policy=policy, has_settlement_policy=policy is not None,
                              has_unsettled_operation=unsettled))
    reason = ("dependency_unavailable" if policy == "unsupported" else
              "provider_uncertain" if policy == NO_UNSETTLED_RETRY_POLICY and unsettled else None)
    assert result["runnable_count"] == int(reason is None)
    assert result["blocked_reason_counts"] == ({reason: 1} if reason else {})


def test_an_active_protected_operation_is_in_flight_and_completed_work_stays_completed():
    row = pending()
    row["lease_expires_at"] = AT + timedelta(seconds=1)
    result = summarize(encoding(), row)
    assert result["running_count"] == 1 and result["blocked_count"] == 0
    assert result["learning_phases"]["history_integration_running_count"] == 1
    assert result["learning_phases"]["history_integration_unassessed_objective_count"] == 3
    row.update(state="completed", processing_status="completed", learning_outcome="no_proposals",
               history_coverage=assessed(row["history_manifest"]), has_unsettled_operation=False)
    result = summarize(encoding(), row)
    assert result["open_window_count"] == 0
    assert result["learning_phases"]["history_integration_unassessed_objective_count"] == 0


def test_unattempted_admission_counts_once_when_deferred_work_is_funded():
    first = pending(state="deferred")
    first.update(model_calls=0, input_bytes=0, integration_id="funded", has_integration=True)
    first["phase_payload"]["integration_continuation_job_id"] = "funded"
    funded = copy.deepcopy(first)
    funded.update(id="funded", state="queued", integration_id=None, has_integration=False,
                  integration_parent="history", has_unsettled_operation=False)
    funded["phase_payload"].pop("integration_continuation_job_id")
    funded["phase_payload"].update(integration_continuation_parent_job_id="history",
                                  **allocation_metadata("funded", phase=HISTORY_PHASE, root_job_id="donor"))
    donor = encoding("donor", start=9, end=16, attempted=16, child=None, proposal_mode="general-v1")
    result = summarize(encoding(), first, donor, funded)
    assert result["counts_complete"] and result["open_window_count"] == result["runnable_count"] == 1
    assert result["learning_phases"]["history_integration_unassessed_objective_count"] == 3


def test_selected_failed_debt_and_untouched_remainder_are_each_counted_once():
    first = pending()
    admitted = manifest(4)
    first["phase_payload"]["history_obligation_manifest"] = admitted
    assert history_debt(first) == (4, True)
    first.update(state="cancelled", processing_status="provider_unavailable", last_error="provider_failed",
                 history_coverage=assessed(first["history_manifest"], complete=False),
                 integration_id="tail", has_integration=True)
    first["phase_payload"]["integration_continuation_job_id"] = "tail"
    tail = history("tail", integration_parent="history")
    remaining = {**admitted, "objectives": admitted["objectives"][3:]}
    tail["phase_payload"].update(integration_continuation_parent_job_id="history",
                                 history_obligation_manifest=remaining)
    result = summarize(encoding(), first, tail)
    assert result["counts_complete"]
    assert result["learning_phases"]["history_integration_unassessed_objective_count"] == 4
    tail.update(state="completed", processing_status="completed", learning_outcome="no_proposals",
                history_coverage=assessed(remaining))
    result = summarize(encoding(), first, tail)
    assert result["learning_phases"]["history_integration_unassessed_objective_count"] == 3


def test_explicit_recovery_resolves_the_original_selected_debt_without_rewriting_its_result():
    first = pending()
    first.update(state="cancelled", processing_status="provider_unavailable", last_error="provider_failed",
                 history_coverage=assessed(first["history_manifest"], complete=False), recovery_id="recovered")
    first["phase_payload"]["recovery_job_id"] = "recovered"
    recovered = copy.deepcopy(first)
    recovered.update(id="recovered", state="completed", recovery_id=None, recovery_parent="history",
                     processing_status="completed", learning_outcome="no_proposals",
                     history_coverage=assessed(first["history_manifest"]), has_unsettled_operation=False)
    recovered["phase_payload"].pop("recovery_job_id")
    recovered["phase_payload"].update(recovery_parent_job_id="history",
                                     **allocation_metadata("recovered", phase=HISTORY_PHASE, recovery=True))
    result = summarize(encoding(), first, recovered)
    assert result["counts_complete"] and result["open_window_count"] == 0
    assert result["learning_phases"]["history_integration_unassessed_objective_count"] == 0
    parent, child = exact_pending(first)
    child["result"]["status"] = "provider_unavailable"
    _, recovery = exact_pending(recovered)
    recovery.update(id="recovered", depth=1)
    recovery["result"]["status"] = "completed"
    before = copy.deepcopy(child)
    exact = resolve_history_status(Connection(parent, [child, recovery], [recovery]), parent)
    assert exact["history_integration_resolved"] and not exact["history_integration_open"]
    assert exact["history_integration_unassessed_objective_count"] == 0
    assert child == before


@pytest.mark.integration
def test_database_unknown_provider_snapshot_retains_three_objectives_and_blocks_only_after_expiry(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch, count=3)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    store = JobStore(learning.database)
    queued = learning.job(child_id)
    assert len(queued["result"]["integration_manifest"]["objectives"]) == 3
    assert store.learning_progress(learning.principal, learning.brain, configured=True)["runnable_count"] == 1
    selected = claim(learning, child_id)
    reserve(learning, selected)
    active = store.learning_progress(learning.principal, learning.brain, configured=True)
    assert active["running_count"] == 1 and active["blocked_count"] == 0
    expire(learning, child_id)
    before, before_operations = learning.job(child_id), operations(learning, child_id)
    assert len(before_operations) == 1 and before_operations[0]["outcome"] == "reserved"
    for _ in range(2):
        progress = store.learning_progress(learning.principal, learning.brain, configured=True)
        assert progress["counts_complete"] and progress["blocked_reason_counts"] == {"provider_uncertain": 1}
        assert progress["runnable_count"] == progress["running_count"] == 0
        assert progress["learning_phases"]["history_integration_unassessed_objective_count"] == 3
        for identifier in (parent["id"], child_id):
            summary = store.get(learning.principal, learning.brain, identifier)["learning_summary"]
            assert summary["phase_metadata_complete"] and summary["history_integration_open"]
            assert summary["history_integration_unassessed_objective_count"] == 3
    assert learning.job(child_id) == before
    assert operations(learning, child_id) == before_operations
    assert len(requests) == 2


@pytest.mark.integration
@pytest.mark.parametrize("policy", [None, NO_UNSETTLED_RETRY_POLICY, "unsupported"])
@pytest.mark.parametrize("settlement", [None, "reserved", "completed", "uncertain"])
def test_database_monitoring_does_not_settle_reclaim_or_change_policy_distinctions(learning, policy, settlement):
    observations(learning, 1)
    selected = claim(learning)
    _, snapshot = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    if settlement:
        reserve(learning, selected)
        if settlement != "reserved":
            JobStore(learning.database).settle_operation(
                learning.principal, learning.brain, selected["id"], lease_token=selected["lease_token"],
                fence=selected["fence"], operation_id="interrupted-proposal", outcome=settlement)
    snapshot.pop("operation_settlement_policy")
    if policy is not None:
        snapshot["operation_settlement_policy"] = policy
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(snapshot), *learning.service._params(learning.principal, learning.brain), selected["id"]))
    expire(learning, selected["id"])
    before, before_operations = learning.job(selected["id"]), operations(learning, selected["id"])
    progress = JobStore(learning.database).learning_progress(learning.principal, learning.brain, configured=True)
    reason = ("dependency_unavailable" if policy == "unsupported" else
              "provider_uncertain" if policy == NO_UNSETTLED_RETRY_POLICY and settlement == "reserved" else None)
    assert progress["counts_complete"]
    assert progress["runnable_count"] == int(reason is None)
    assert progress["blocked_reason_counts"] == ({reason: 1} if reason else {})
    assert learning.job(selected["id"]) == before
    assert operations(learning, selected["id"]) == before_operations
    assert learning.calls == []


@pytest.mark.integration
def test_database_completed_history_has_no_unassessed_obligations(learning, monkeypatch):
    setup_history(learning, monkeypatch, count=3)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    execute(learning, claim(learning, child_id))
    store = JobStore(learning.database)
    progress = store.learning_progress(learning.principal, learning.brain, configured=True)
    assert progress["counts_complete"] and progress["open_window_count"] == 0
    assert progress["learning_phases"]["history_integration_unassessed_objective_count"] == 0
    exact = store.get(learning.principal, learning.brain, parent["id"])["learning_summary"]
    assert exact["phase_metadata_complete"] and not exact["history_integration_open"]
    assert exact["history_integration_unassessed_objective_count"] == 0


@pytest.mark.integration
def test_database_explicit_recovery_counts_copied_unassessed_coverage_once_until_completion(learning, monkeypatch):
    setup_history(learning, monkeypatch, count=3)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    original = learning.service.semantic_processor.propose

    def unavailable(_):
        raise TimeoutError("Offline simulated provider failure")

    learning.service.semantic_processor.propose = unavailable
    failed = execute(learning, claim(learning, child_id))
    assert failed["state"] == "cancelled" and failed["result"]["history_integration"]["assessed_count"] == 0
    store = JobStore(learning.database)
    recovery = store.retry_learning(learning.principal, learning.brain, child_id,
                                    request_id="recover-monitor-debt", expected_fence=failed["fence"])
    for identifier in (parent["id"], recovery["id"]):
        exact = store.get(learning.principal, learning.brain, identifier)["learning_summary"]
        assert exact["phase_metadata_complete"] and exact["history_integration_open"]
        assert exact["history_integration_unassessed_objective_count"] == 3
    progress = store.learning_progress(learning.principal, learning.brain, configured=True)
    assert progress["counts_complete"] and progress["open_window_count"] == progress["runnable_count"] == 1
    assert progress["learning_phases"]["history_integration_unassessed_objective_count"] == 3
    learning.service.semantic_processor.propose = original
    assert execute(learning, claim(learning, recovery["id"]))["state"] == "completed"
    progress = store.learning_progress(learning.principal, learning.brain, configured=True)
    assert progress["counts_complete"] and progress["open_window_count"] == 0
    assert progress["learning_phases"]["history_integration_unassessed_objective_count"] == 0
    exact = store.get(learning.principal, learning.brain, parent["id"])["learning_summary"]
    assert exact["phase_metadata_complete"] and not exact["history_integration_open"]
    assert exact["history_integration_unassessed_objective_count"] == 0
    assert learning.job(child_id)["result"] == failed["result"]


@pytest.mark.integration
def test_database_deferred_remainder_keeps_one_debt_when_later_input_funds_it(learning, monkeypatch):
    setup_legacy_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    selected = execute(learning, claim(learning, child_id))
    tail_id = selected["payload"]["integration_continuation_job_id"]
    assert learning.job(tail_id)["state"] == "deferred"
    store = JobStore(learning.database)
    exact = store.get(learning.principal, learning.brain, parent["id"])["learning_summary"]
    assert exact["phase_metadata_complete"] and exact["history_integration_unassessed_objective_count"] == 1
    encode(learning)
    funded_id = learning.job(tail_id)["payload"]["integration_continuation_job_id"]
    exact = store.get(learning.principal, learning.brain, parent["id"])["learning_summary"]
    assert exact["phase_metadata_complete"] and exact["history_integration_unassessed_objective_count"] == 1
    assert execute(learning, claim(learning, funded_id))["state"] == "completed"
    exact = store.get(learning.principal, learning.brain, parent["id"])["learning_summary"]
    assert exact["phase_metadata_complete"] and not exact["history_integration_open"]
    assert exact["history_integration_unassessed_objective_count"] == 0
    assert store.learning_progress(learning.principal, learning.brain, configured=True)["counts_complete"]
