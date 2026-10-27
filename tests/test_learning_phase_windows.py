import pytest
from psycopg.types.json import Jsonb
from test_jobs import jobs as jobs
from test_learning_allocations import insert, params, row

from membrain.helpers import DomainError, digest
from membrain.learning_allocations import (
    CONTRACT_VERSION,
    ENCODING_PHASE,
    HISTORY_PHASE,
    freeze_input,
    phase,
)
from membrain.learning_windows import (
    enqueue_window,
    pending_window,
    record_progress,
    recover_windows,
)
from membrain.service import MemoryService

pytestmark = pytest.mark.integration
TARGET = {"conversation_ref": "chat", "branch_ref": "main"}


def enqueue(harness, start, end, *, new=True, coalesce=True):
    service = MemoryService(harness.database)
    with harness.database.transaction(harness.principal) as conn:
        brain = service._brain(conn, harness.principal, harness.brain)
        return enqueue_window(conn, service, harness.principal, brain, TARGET, start, end,
                              coalesce=coalesce, learning_contract_version=CONTRACT_VERSION if new else None)


def progress(harness, identifier, attempted):
    service = MemoryService(harness.database)
    job = row(harness, identifier)
    window = {**pending_window(job["payload"]), "attempted_through_sequence": attempted}
    with harness.database.transaction(harness.principal) as conn:
        brain = service._brain(conn, harness.principal, harness.brain)
        return record_progress(conn, service, harness.principal, brain, job, window, succeeded=True)


def test_new_encoding_coalesces_only_before_input_binding_and_never_with_legacy(jobs):
    legacy = enqueue(jobs, 1, 2, new=False)
    new = enqueue(jobs, 3, 4)
    assert legacy != new and phase(row(jobs, legacy)["payload"]) is None
    assert phase(row(jobs, new)["payload"]) == ENCODING_PHASE
    assert enqueue(jobs, 5, 6) == new
    assert row(jobs, new)["payload"]["through_sequence"] == 6
    with jobs.database.transaction(jobs.principal) as conn:
        freeze_input(conn, params(jobs), new, input_fingerprint=digest("frozen inputs"))
    next_job = enqueue(jobs, 7, 8)
    assert next_job != new
    assert row(jobs, new)["payload"]["through_sequence"] == 6
    assert enqueue(jobs, 9, 10, new=False) != next_job


def test_new_contract_and_legacy_same_range_have_distinct_durable_identity(jobs):
    legacy = enqueue(jobs, 1, 8, new=False, coalesce=False)
    new = enqueue(jobs, 1, 8, coalesce=False)
    assert legacy != new
    assert enqueue(jobs, 1, 8, coalesce=False) == new
    assert row(jobs, new)["max_model_calls"] == 2


def test_each_distinct_encoding_continuation_has_its_own_frozen_allocation(jobs):
    parent = enqueue(jobs, 1, 16)
    progress(jobs, parent, 8)
    child = row(jobs, row(jobs, parent)["payload"]["continuation_job_id"])
    assert child["payload"]["from_sequence"] == 9
    assert child["payload"]["through_sequence"] == 16
    assert child["payload"]["continuation_parent_job_id"] == parent
    assert child["payload"]["authorization_root_job_id"] == child["id"]
    assert phase(child["payload"]) == ENCODING_PHASE
    assert child["payload"]["allocation_budget"]["max_model_calls"] == 4
    assert "allocation_input_fingerprint" not in child["payload"]


def test_history_cannot_mint_encoding_continuation_or_merge_new_input(jobs):
    history = insert(jobs, selected_phase=HISTORY_PHASE, recovery=True, frozen=False)
    with pytest.raises(DomainError) as error:
        progress(jobs, history, 4)
    assert error.value.code == "stage_conflict"
    assert "continuation_job_id" not in row(jobs, history)["payload"]
    fresh = enqueue(jobs, 9, 10)
    assert fresh != history
    assert row(jobs, history)["payload"]["through_sequence"] == 8


def test_legacy_continuation_and_automatic_source_recovery_remain_legacy(jobs):
    parent = enqueue(jobs, 1, 16, new=False)
    progress(jobs, parent, 8)
    child = row(jobs, parent)["payload"]["continuation_job_id"]
    assert phase(row(jobs, child)["payload"]) is None
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET state='superseded' WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*params(jobs), parent))
        service = MemoryService(jobs.database)
        brain = service._brain(conn, jobs.principal, jobs.brain)
        recover_windows(conn, service, jobs.principal, brain)
    replacement = row(jobs, parent)["payload"]["recovery_job_id"]
    assert phase(row(jobs, replacement)["payload"]) is None


@pytest.mark.parametrize("selected_phase", [ENCODING_PHASE, HISTORY_PHASE])
def test_new_phases_never_enter_legacy_automatic_recovery(jobs, selected_phase):
    parent = insert(jobs, selected_phase=selected_phase, recovery=True, state="superseded")
    with jobs.database.transaction(jobs.principal) as conn:
        service = MemoryService(jobs.database)
        brain = service._brain(conn, jobs.principal, jobs.brain)
        recover_windows(conn, service, jobs.principal, brain)
    saved = row(jobs, parent)
    if selected_phase == ENCODING_PHASE:
        child = row(jobs, saved["payload"]["recovery_job_id"])
        assert phase(child["payload"]) == ENCODING_PHASE
        assert child["payload"]["authorization_root_job_id"] == parent
        assert child["payload"]["allocation_policy"] == saved["payload"]["allocation_policy"]
    else:
        assert "recovery_job_id" not in saved["payload"]


def test_completed_new_encoding_is_not_mistaken_for_failed_phase_recovery(jobs):
    parent = insert(jobs, state="completed")
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb({"learning_window": {**pending_window(row(jobs, parent)["payload"]),
                                                "attempted_through_sequence": 4,
                                                "processed_through_sequence": 4,
                                                "processing_succeeded": True}}), *params(jobs), parent))
    with pytest.raises(DomainError) as error:
        jobs.store.retry_learning(jobs.principal, jobs.brain, parent, request_id="recovery", expected_fence=0)
    assert error.value.code == "stage_conflict"


@pytest.mark.parametrize("processed", [False, True, -1, "0", 1])
def test_unpaid_replan_requires_strict_unprocessed_prefix(jobs, processed):
    parent = insert(jobs, state="superseded", extra={"processed_through_sequence": processed})
    with jobs.database.transaction(jobs.principal) as conn:
        service = MemoryService(jobs.database)
        brain = service._brain(conn, jobs.principal, jobs.brain)
        recover_windows(conn, service, jobs.principal, brain)
    assert "recovery_job_id" not in row(jobs, parent)["payload"]
