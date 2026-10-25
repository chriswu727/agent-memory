import copy
from dataclasses import replace

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_jobs import jobs as jobs
from test_learning import learning as learning
from test_learning_allocations import insert, params, replan_child, reserve, row
from test_learning_recovery import operation_rows, retry, stopped_window
from test_learning_windows import claim, execute

from agent_memory.helpers import DomainError, digest
from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import freeze_input
from agent_memory.learning_windows import recover_windows
from agent_memory.semantic import SemanticProcessor
from agent_memory.service import MemoryService

pytestmark = pytest.mark.integration

MARKER = {"version": "encoding-window-snapshot-v1", "from_sequence": 1,
          "through_sequence": 16, "attempted_through_sequence": 8}


def recover(harness):
    service = MemoryService(harness.database)
    with harness.database.transaction(harness.principal) as conn:
        brain = service._brain(conn, harness.principal, harness.brain)
        recover_windows(conn, service, harness.principal, brain)


def paid_terminal(harness, marker):
    extra = {"through_sequence": 16}
    if marker != "absent":
        extra["encoding_window_snapshot"] = copy.deepcopy(marker)
    identifier = insert(harness, extra=extra)
    _, lease = reserve(harness, identifier)
    harness.store.fail(harness.principal, harness.brain, identifier, lease_token=lease["lease_token"],
                       fence=lease["fence"], failure_code="context_stale", retry=False)
    with harness.database.transaction(harness.principal) as conn:
        conn.execute("UPDATE memory_brains SET durable_sequence=16 WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                     params(harness))
    return identifier


@pytest.mark.parametrize("marker", [
    "absent", None, {}, {**MARKER, "version": "unknown"},
    {**MARKER, "attempted_through_sequence": True}, {**MARKER, "attempted_through_sequence": 8.0},
    {**MARKER, "attempted_through_sequence": 0}, {**MARKER, "attempted_through_sequence": 17},
    {**MARKER, "from_sequence": 2}, {**MARKER, "through_sequence": 17},
    {**MARKER, "unexpected": "must not be accepted"},
])
def test_paid_prefix_without_exact_marker_cannot_mint_untouched_tail(jobs, marker):
    identifier = paid_terminal(jobs, marker)
    before = row(jobs, identifier)
    with jobs.database.transaction(jobs.principal) as conn:
        count = conn.execute("SELECT count(*) AS count FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                             params(jobs)).fetchone()["count"]
        ledger = conn.execute("SELECT * FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                              params(jobs)).fetchall()
    for _ in range(3):
        recover(jobs)
    after = row(jobs, identifier)
    assert after["payload"] == before["payload"] and after["result"] is None
    assert after["model_calls"] == 1 and after["input_bytes"] == before["input_bytes"]
    assert "continuation_job_id" not in after["payload"]
    with jobs.database.transaction(jobs.principal) as conn:
        assert conn.execute("SELECT count(*) AS count FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                            params(jobs)).fetchone()["count"] == count
        assert conn.execute("SELECT * FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                            params(jobs)).fetchall() == ledger


@pytest.mark.parametrize("change", ["remove", "attempted", "bounds", "version"])
def test_database_prevents_rewriting_a_frozen_attempt_boundary(jobs, change):
    identifier = paid_terminal(jobs, MARKER)
    original = row(jobs, identifier)["payload"]
    altered = copy.deepcopy(original)
    if change == "remove":
        del altered["encoding_window_snapshot"]
    elif change == "attempted":
        altered["encoding_window_snapshot"]["attempted_through_sequence"] = 7
    elif change == "bounds":
        altered["encoding_window_snapshot"]["through_sequence"] = 17
    else:
        altered["encoding_window_snapshot"]["version"] = "future"
    with pytest.raises(psycopg.errors.CheckViolation):
        with jobs.database.transaction(jobs.principal) as conn:
            conn.execute("UPDATE memory_jobs SET payload=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(altered), *params(jobs), identifier))
    assert row(jobs, identifier)["payload"] == original


def test_paid_replan_child_scope_cannot_be_changed_to_launder_its_remaining_window(jobs):
    root = insert(jobs, extra={"through_sequence": 16})
    child = replan_child(jobs, root, through_sequence=16, encoding_window_snapshot=copy.deepcopy(MARKER))
    with jobs.database.transaction(jobs.principal) as conn:
        freeze_input(conn, params(jobs), child, input_fingerprint=digest("exact replacement plan"))
    _, lease = reserve(jobs, child)
    jobs.store.fail(jobs.principal, jobs.brain, child, lease_token=lease["lease_token"], fence=lease["fence"],
                    failure_code="context_stale", retry=False)
    before = row(jobs, child)
    with pytest.raises(psycopg.errors.CheckViolation), jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET payload=jsonb_set(payload,'{target,conversation_ref}','\"other\"') "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s", (*params(jobs), child))
    assert row(jobs, child) == before


@pytest.mark.parametrize("invalidation", ["correction", "erasure"])
@pytest.mark.parametrize("freeze_first", [False, True])
def test_source_invalidated_after_explicit_retry_cannot_refresh_policy_or_spend(learning, invalidation, freeze_first):
    parent, receipts = stopped_window(learning, 2)
    child = retry(learning, parent)
    before = operation_rows(learning)
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                        max_wall_time_ms=240000, max_output_tokens=16384)
    if freeze_first:
        selected = claim(learning, child["id"])
        _, snapshot = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
        assert snapshot["semantic_policy"]["limits"]["max_wall_time_ms"] == 240000
        assert learning.job(child["id"])["payload"]["allocation_input_fingerprint"] != parent["payload"][
            "allocation_input_fingerprint"]
    if invalidation == "correction":
        learning.observe("A new corrected source is not the old authorized input.", source_key="input-1", revision=2)
    else:
        learning.memory.forget([receipts[0]["accepted_event_ids"][0]], kind="events", mode="erase_content",
                               expected_scope_generation=1)
    assert learning.job(child["id"])["result"] is None
    selected = claim(learning, child["id"])
    assert selected is None
    assert operation_rows(learning) == before and learning.calls == ["proposal"]
    assert learning.job(parent["id"])["model_calls"] == 1
    assert learning.job(child["id"])["model_calls"] == 0
    with pytest.raises(DomainError):
        retry(learning, learning.job(child["id"]), request_id="cannot-substitute-corrected-input")
    for _ in range(3):
        recover(learning)
    assert "recovery_job_id" not in learning.job(child["id"])["payload"]
    assert operation_rows(learning) == before and learning.calls == ["proposal"]


def test_claimed_explicit_retry_source_race_blocks_before_any_provider_request(learning):
    parent, receipts = stopped_window(learning)
    child = retry(learning, parent)
    selected = claim(learning, child["id"])
    assert selected is not None
    before = operation_rows(learning)
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                        max_wall_time_ms=240000)
    learning.memory.forget([receipts[0]["accepted_event_ids"][0]], kind="events", mode="erase_content",
                           expected_scope_generation=1)
    with pytest.raises(DomainError):
        execute(learning, selected)
    assert operation_rows(learning) == before and learning.calls == ["proposal"]
    retained = learning.job(child["id"])
    assert retained["state"] == "superseded" and retained["result"] is None and retained["model_calls"] == 0
