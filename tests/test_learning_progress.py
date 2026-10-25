import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from agent_memory.config import Settings
from agent_memory.helpers import Principal, uid
from agent_memory.learning_progress import _summary, learning_progress, sanitize_learning_progress
from agent_memory.service import MemoryService
from agent_memory.storage import Database

AT = datetime(2026, 10, 24, tzinfo=UTC)


def window(identifier="job", start=1, end=40, *, processed=None, **updates):
    return {
        "id": identifier, "state": "queued", "attempts": 0, "fence": 0, "last_error": None,
        "created_at": AT, "available_at": AT, "lease_expires_at": None, "observed_at": AT,
        "window_start": start, "window_end": end, "processed": processed,
        "has_processed": processed is not None, "conversation": "private-conversation", "branch": "main",
        "continuation_id": None, "recovery_id": None, "continuation_parent": None,
        "recovery_parent": None, **updates,
    }


def summarize(*rows, configured=True):
    return _summary(iter(rows), configured=configured)


def test_empty_and_unconfigured_progress_are_distinct_from_failed_learning():
    empty = summarize()
    assert empty["window_count"] == empty["open_window_count"] == 0
    assert empty["counts_complete"] and empty["provider_state"] == "configured"
    queued = summarize(window(), configured=False)
    assert queued["provider_state"] == "not_configured"
    assert queued["open_window_count"] == queued["runnable_count"] == 1
    assert queued["counts_complete"]


@pytest.mark.parametrize("fields,category,reason", [
    ({}, "runnable_count", None),
    ({"state": "failed", "attempts": 1}, "runnable_count", None),
    ({"state": "failed", "available_at": AT + timedelta(seconds=60)}, "waiting_retry_count", None),
    ({"state": "running", "lease_expires_at": AT + timedelta(seconds=60), "attempts": 3},
     "running_count", None),
    ({"state": "running", "lease_expires_at": AT - timedelta(seconds=1), "attempts": 2},
     "runnable_count", None),
    ({"state": "running", "lease_expires_at": AT - timedelta(seconds=1), "attempts": 3},
     "blocked_count", "attempts_exhausted"),
    ({"state": "failed", "attempts": 3}, "blocked_count", "attempts_exhausted"),
    ({"state": "cancelled", "last_error": "provider_uncertain"}, "blocked_count", "provider_uncertain"),
    ({"state": "superseded"}, "blocked_count", "terminal_window"),
    ({"state": "completed", "processed": 8, "has_processed": True}, "blocked_count", "missing_continuation"),
])
def test_scheduling_counts_match_claim_eligibility_without_guessing_model_budget(fields, category, reason):
    result = summarize(window(**fields))
    assert result[category] == result["open_window_count"] == 1
    assert result["counts_complete"]
    assert result["blocked_reason_counts"] == ({reason: 1} if reason else {})
    if category == "waiting_retry_count":
        assert result["next_retry_at"] == (AT + timedelta(seconds=60)).isoformat()


def test_completed_prefix_delegates_the_tail_once_even_if_child_coalesced_new_input():
    parent = window("parent", processed=8, state="completed", continuation_id="child")
    child = window("child", 9, 48, continuation_parent="parent")
    result = summarize(parent, child)
    assert result["window_count"] == 2
    assert result["open_window_count"] == result["runnable_count"] == 1
    assert result["oldest_pending_window"] == {
        "job_id": "child", "from_sequence": 9, "through_sequence": 48,
        "processed_through_sequence": 8, "created_at": AT.isoformat(),
    }
    assert result["counts_complete"]


@pytest.mark.parametrize("back_reference", [True, False])
def test_legacy_and_current_lineage_allow_fully_completed_window_chains(back_reference):
    parent = window("parent", processed=8, state="completed", continuation_id="child")
    child = window("child", 9, 40, processed=40, state="completed",
                   continuation_parent="parent" if back_reference else None)
    result = summarize(parent, child)
    assert result["open_window_count"] == 0 and result["counts_complete"]


def test_superseded_processed_prefix_and_continuation_have_separate_coverage():
    parent = window("parent", processed=8, state="superseded", recovery_id="repair", continuation_id="tail")
    repair = window("repair", 1, 8, recovery_parent="parent")
    tail = window("tail", 9, 40, processed=40, state="completed", continuation_parent="parent")
    pending = summarize(parent, repair, tail)
    assert pending["window_count"] == 3 and pending["open_window_count"] == 1
    assert pending["oldest_pending_window"]["job_id"] == "repair"
    assert pending["counts_complete"]
    repair.update(processed=8, has_processed=True, state="completed")
    finished = summarize(parent, repair, tail)
    assert finished["open_window_count"] == 0 and finished["counts_complete"]


def test_manual_recovery_delegates_only_the_unprocessed_completed_tail():
    parent = window("parent", processed=8, state="completed", recovery_id="repair")
    repair = window("repair", 9, 40, recovery_parent="parent")
    result = summarize(parent, repair)
    assert result["open_window_count"] == result["runnable_count"] == 1
    assert result["counts_complete"]


@pytest.mark.parametrize("damage", ["missing", "wrong_scope", "wrong_start", "short_end", "wrong_parent", "non_id"])
def test_invalid_or_missing_child_never_hides_the_unprocessed_parent(damage):
    parent = window("parent", processed=8, state="completed", continuation_id="child")
    child = window("child", 9, 40, continuation_parent="parent")
    if damage == "wrong_scope":
        child["conversation"] = "different"
    elif damage == "wrong_start":
        child["window_start"] = 10
    elif damage == "short_end":
        child["window_end"] = 39
    elif damage == "wrong_parent":
        child["continuation_parent"] = "other-parent"
    elif damage == "non_id":
        parent["continuation_id"] = {"text": "private-content"}
    result = summarize(parent, *([] if damage == "missing" else [child]))
    assert result["unknown_count"] >= 1 and result["open_window_count"] >= 1
    assert not result["counts_complete"]
    assert "private-content" not in json.dumps(result)


def test_recovery_cycles_and_orphan_reverse_links_report_incomplete_metadata():
    left = window("left", state="cancelled", recovery_id="right")
    right = window("right", state="cancelled", recovery_id="left")
    result = summarize(left, right)
    assert result["unknown_count"] == 2
    assert result["limitation_counts"]["lineage_cycle"] == 2
    assert not result["counts_complete"]
    orphan = summarize(window(continuation_parent="missing"))
    assert orphan["unknown_count"] == 1 and not orphan["counts_complete"]


def test_two_legacy_parents_cannot_share_one_child_to_hide_blocked_windows():
    first = window("first", 1, 8, state="cancelled", recovery_id="child")
    second = window("second", 1, 8, state="cancelled", recovery_id="child")
    child = window("child", 1, 8)
    result = summarize(first, second, child)
    assert result["window_count"] == result["unknown_count"] == result["open_window_count"] == 3
    assert result["limitation_counts"]["invalid_lineage"] == 3
    assert not result["counts_complete"]


def test_recovery_cannot_expand_over_a_separate_continuation_range():
    parent = window("parent", processed=8, state="superseded", recovery_id="repair", continuation_id="tail")
    repair = window("repair", 1, 40, recovery_parent="parent")
    tail = window("tail", 9, 40, continuation_parent="parent")
    result = summarize(parent, repair, tail)
    assert result["unknown_count"] >= 1 and not result["counts_complete"]


@pytest.mark.parametrize("relation", ["continuation", "recovery"])
def test_explicit_null_lineage_is_not_mistaken_for_an_absent_claim_guard(relation):
    result = summarize(window(**{"has_" + relation: True, relation + "_id": None}))
    assert result["runnable_count"] == 0 and result["unknown_count"] == 1
    assert result["limitation_counts"] == {"invalid_lineage": 1}
    assert not result["counts_complete"]


@pytest.mark.parametrize("fields", [
    {"window_start": True}, {"window_start": 0}, {"window_end": 2**63},
    {"processed": 45, "has_processed": True}, {"processed": [], "has_processed": True},
    {"state": "running"}, {"created_at": "private-invalid-time"}, {"attempts": True},
])
def test_malformed_metadata_is_unknown_not_idle(fields):
    result = summarize(window(**fields))
    assert result["window_count"] == result["open_window_count"] == result["unknown_count"] == 1
    assert not result["counts_complete"]
    assert "private-" not in json.dumps(result)


def test_oldest_gap_and_bounded_examples_do_not_depend_on_recent_jobs():
    rows = [window("oldest", 1, 8, state="cancelled", last_error="provider_failed",
                   created_at=AT - timedelta(days=100))]
    rows += [window(f"closed-{number}", number + 20, number + 20,
                    processed=number + 20, state="completed") for number in range(250)]
    rows += [window(f"blocked-{number}", number + 1000, number + 1000,
                    state="cancelled", last_error="private-error") for number in range(12)]
    result = summarize(*reversed(rows))
    assert result["window_count"] == 263 and result["blocked_count"] == 13
    assert result["oldest_pending_window"]["job_id"] == "oldest"
    assert len(result["blocked_examples"]) == 8 and result["blocked_examples_truncated"]
    assert result["blocked_examples"][0]["job_id"] == "oldest"
    assert result["blocked_reason_counts"] == {"provider_failed": 1, "internal_error": 12}
    assert "private-" not in json.dumps(result) and result["counts_complete"]


def test_recovery_of_old_input_remains_first_even_when_child_job_is_recent():
    parent = window("parent", 1, 8, state="cancelled", recovery_id="repair",
                    created_at=AT - timedelta(days=10))
    repair = window("repair", 1, 8, state="cancelled", recovery_parent="parent",
                    last_error="provider_failed", created_at=AT)
    newer_input = window("newer", 9, 16, state="cancelled", last_error="budget_exceeded",
                         created_at=AT - timedelta(days=1))
    result = summarize(parent, repair, newer_input)
    assert result["oldest_pending_window"]["job_id"] == "repair"
    assert [item["job_id"] for item in result["blocked_examples"]] == ["repair", "newer"]
    assert result["counts_complete"]


@pytest.mark.parametrize("value", [[], {}, True, 12, None, "private-error"])
def test_malformed_blocked_reason_is_redacted_without_crashing(value):
    summary = summarize(window(state="cancelled", last_error="provider_failed"))
    summary["blocked_examples"][0]["reason"] = value
    safe = sanitize_learning_progress(summary)
    assert safe["blocked_examples"] == [] and not safe["counts_complete"]
    assert "private-error" not in json.dumps(safe)


@pytest.mark.parametrize("field,value", [
    ("window_count", True), ("blocked_count", 2**63), ("provider_state", {"text": "private-provider"}),
    ("next_retry_at", "private-date"), ("blocked_examples", {"text": "private-example"}),
    ("blocked_examples_truncated", "private-flag"),
])
def test_malformed_summary_fields_cannot_claim_complete_counts(field, value):
    summary = summarize()
    summary[field] = value
    safe = sanitize_learning_progress(summary)
    assert not safe["counts_complete"] and "private-" not in json.dumps(safe)


def test_summary_sanitizer_is_idempotent_and_never_copies_extra_bodies_or_targets():
    summary = summarize(window(state="cancelled", last_error="budget_exceeded"))
    summary["body"] = "private-body"
    summary["oldest_pending_window"]["source_id"] = "private-source"
    summary["blocked_examples"][0]["target"] = {"conversation": "private-target"}
    original = copy.deepcopy(summary)
    safe = sanitize_learning_progress(summary)
    assert "private-" not in json.dumps(safe)
    assert sanitize_learning_progress(safe) == safe
    assert summary == original and safe["counts_complete"]


@pytest.fixture
def stored():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    database = Database(settings.database_url)
    principal = Principal(uid(), uid())
    brain = MemoryService(database).create_brain(principal, "Learning progress integration")["id"]

    class Stored:
        def add(self, identifier=None, *, start=1, end=40, processed=None, state="queued",
                kind="consolidate", payload=None, **fields):
            identifier = identifier or uid()
            document = {"from_sequence": start, "through_sequence": end,
                        "target": {"conversation_ref": "private-conversation", "branch_ref": "main"},
                        "private": "private-body"}
            if processed is not None:
                document["processed_through_sequence"] = processed
            document.update(payload or {})
            with database.transaction(principal) as connection:
                connection.execute(
                    "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,state,payload,created_at,"
                    "attempts,fence,last_error,available_at,lease_expires_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (principal.tenant_id, principal.principal_id, brain, identifier, kind, state, Jsonb(document),
                     fields.get("created_at", AT), fields.get("attempts", 0), fields.get("fence", 0),
                     fields.get("last_error"), fields.get("available_at", AT), fields.get("lease_expires_at")),
                )
            return identifier

        def progress(self, configured=True, identity=principal, brain_id=brain):
            with database.transaction(identity) as connection:
                return learning_progress(connection, identity, brain_id, configured=configured)

    instance = Stored()
    instance.database, instance.principal, instance.brain = database, principal, brain
    return instance


@pytest.mark.integration
def test_database_old_gap_survives_more_than_two_hundred_new_jobs(stored):
    old = stored.add(start=1, end=8, state="cancelled", last_error="provider_failed",
                     created_at=AT - timedelta(days=100))
    for number in range(205):
        stored.add(start=number + 10, end=number + 10, processed=number + 10, state="completed")
    result = stored.progress()
    assert result["window_count"] == 206 and result["blocked_count"] == 1
    assert result["oldest_pending_window"]["job_id"] == old
    assert result["counts_complete"] and "private-" not in json.dumps(result)


@pytest.mark.integration
def test_database_recovery_prefix_and_continuation_are_not_double_counted(stored):
    parent, recovery, tail = uid(), uid(), uid()
    stored.add(parent, processed=8, state="superseded", payload={
        "recovery_job_id": recovery, "continuation_job_id": tail,
    })
    stored.add(recovery, start=1, end=8, processed=8, state="completed", payload={"recovery_parent_job_id": parent})
    stored.add(tail, start=9, end=41, payload={"continuation_parent_job_id": parent})
    result = stored.progress()
    assert result["window_count"] == 3 and result["open_window_count"] == result["runnable_count"] == 1
    assert result["oldest_pending_window"]["job_id"] == tail and result["counts_complete"]
    with stored.database.transaction(stored.principal) as connection:
        connection.execute("UPDATE memory_jobs SET state='completed',payload=payload || %s WHERE id=%s",
                           (Jsonb({"processed_through_sequence": 41}), tail))
    assert stored.progress()["open_window_count"] == 0


@pytest.mark.integration
def test_database_missing_or_invalid_child_is_incomplete_and_legacy_jobs_are_excluded(stored):
    stored.add(processed=8, state="completed", payload={"continuation_job_id": "missing"})
    invalid = uid()
    stored.add(invalid, start=9, end=40, payload={"target": {"conversation_ref": "other", "branch_ref": "main"}})
    stored.add(processed=8, state="completed", payload={"continuation_job_id": invalid})
    legacy = stored.add()
    with stored.database.transaction(stored.principal) as connection:
        connection.execute("UPDATE memory_jobs SET payload=%s WHERE id=%s",
                           (Jsonb({"event_id": "private-source", "revision": 1, "through_sequence": 40}), legacy))
    result = stored.progress()
    assert result["window_count"] == 3 and result["unknown_count"] == 2
    assert not result["counts_complete"] and "private-" not in json.dumps(result)


@pytest.mark.integration
def test_database_explicit_null_recovery_pointer_is_not_reported_runnable(stored):
    stored.add(state="failed", payload={"recovery_job_id": None})
    result = stored.progress()
    assert result["unknown_count"] == 1 and result["runnable_count"] == 0
    assert not result["counts_complete"]


@pytest.mark.integration
def test_database_counts_are_tenant_owner_and_brain_scoped(stored):
    stored.add()
    for identity in (Principal(uid(), uid()), Principal(stored.principal.tenant_id, uid())):
        result = stored.progress(identity=identity)
        assert result["window_count"] == result["open_window_count"] == 0
    assert stored.progress(brain_id=uid())["window_count"] == 0
    assert stored.progress()["window_count"] == 1


def test_configuration_validation_precedes_database_access():
    with pytest.raises(ValueError, match="boolean"):
        learning_progress(None, None, None, configured=1)
