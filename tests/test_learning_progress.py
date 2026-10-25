import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_semantic import claim, encoded, proposals

from agent_memory.config import Settings
from agent_memory.helpers import Principal, uid
from agent_memory.jobs import JobStore, summarize_learning
from agent_memory.learning import LearningEngine
from agent_memory.learning_progress import _summary, learning_progress, sanitize_learning_progress
from agent_memory.semantic import SemanticProcessor
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
                    "attempts,fence,last_error,available_at,lease_expires_at,result) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (principal.tenant_id, principal.principal_id, brain, identifier, kind, state, Jsonb(document),
                     fields.get("created_at", AT), fields.get("attempts", 0), fields.get("fence", 0),
                     fields.get("last_error"), fields.get("available_at", AT), fields.get("lease_expires_at"),
                     Jsonb(fields["result"]) if fields.get("result") is not None else None),
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


def outcome_row(identifier="job", *, status="completed", accepted=0, published=0, reasons=None):
    outcome = ("accepted_proposals" if accepted else "no_accepted_proposals" if status == "partial"
               else "no_proposals" if status == "completed" else "not_evaluated")
    return window(identifier, processed=40, state="completed", has_result=True, result_object=True,
                  has_status=True, processing_status=status, learning_outcome=outcome, publication_object=True,
                  accepted_counts={"claims": accepted, "situation_updates": 0, "episode_actions": 0, "belief_links": 0},
                  published_counts={"claims": published, "episode_versions": 0, "pattern_views": 0, "belief_relations": 0},
                  rejected_count=sum((reasons or {}).values()), rejection_reason_counts=reasons or {})


def semantic_result(*, status="completed", accepted=0, published=0, reasons=()):
    return {
        "status": status, "learning_outcome": "accepted_proposals" if accepted else
        "no_accepted_proposals" if status == "partial" else "no_proposals" if status == "completed" else "not_evaluated",
        "claims": [{"statement": "private-claim"}] * accepted, "situation_updates": [],
        "episode_actions": [], "belief_links": [], "rejected": [{"reason": reason} for reason in reasons],
        "publication": {"memory_versions": [{"memory_id": "private-memory", "revision": 1}] * published,
                        "episode_versions": [], "pattern_view_ids": [], "belief_relation_versions": []},
        "sources": [{"body": "private-source-body"}],
    }


def test_historical_outcomes_distinguish_noop_rejection_partial_and_unpublished_results():
    rows = [outcome_row("noop"), outcome_row("rejected", status="partial",
            reasons={"request_intent_unconfirmed": 1}), outcome_row("published", accepted=2, published=2),
            outcome_row("unpublished", accepted=1), outcome_row("partial", status="partial", accepted=1,
            published=1, reasons={"semantic_support_unconfirmed": 1}), window("pending")]
    summary = summarize(*rows)
    result = summary["semantic_outcomes"]
    assert result["basis"] == "retained_latest_job_results" and result["counts_complete"]
    assert result["job_count"] == 6 and result["evaluated_job_count"] == 5
    assert result["unevaluated_job_count"] == 1 and result["unknown_job_count"] == 0
    assert result["learning_outcome_counts"] == {
        "accepted_proposals": 3, "no_accepted_proposals": 1, "no_proposals": 1, "not_evaluated": 1,
    }
    assert result["accepted_counts"]["claims"] == 4 and result["published_counts"]["claims"] == 3
    assert result["rejection_reason_counts"] == {"request_intent_unconfirmed": 1, "semantic_support_unconfirmed": 1}
    assert {example["reason"] for example in result["examples"]} == {
        "no_accepted_proposals", "partial_acceptance", "accepted_without_publication",
    }
    assert summary["open_window_count"] == 1 and summary["counts_complete"]
    assert sanitize_learning_progress(summary) == summary


def test_old_completed_rejection_remains_visible_after_two_hundred_new_completed_jobs():
    old = outcome_row("old-rejection", status="partial", reasons={"semantic_support_unconfirmed": 1})
    rows = [outcome_row(f"new-{index}", accepted=1, published=1) for index in range(205)]
    for index, row in enumerate(rows):
        row.update(window_start=index + 41, window_end=index + 41, processed=index + 41)
    summary = summarize(*rows, old)
    result = summary["semantic_outcomes"]
    assert summary["open_window_count"] == 0 and summary["counts_complete"]
    assert result["job_count"] == 206 and result["counts_complete"]
    assert result["learning_outcome_counts"]["no_accepted_proposals"] == 1
    assert result["examples"][0]["job_id"] == "old-rejection"


@pytest.mark.parametrize("fields,limitation", [
    ({"has_result": False}, "missing_result"),
    ({"result_object": False}, "invalid_result"),
    ({"processing_status": []}, "invalid_result"),
    ({"learning_outcome": "private-outcome"}, "invalid_result"),
    ({"accepted_counts": {"claims": True}}, "invalid_result"),
    ({"publication_object": False}, "invalid_result"),
    ({"published_counts": {"claims": 2**63}}, "invalid_result"),
    ({"rejected_count": 1, "rejection_reason_counts": {"private-reason": 1}}, "invalid_result"),
    ({"learning_outcome": "accepted_proposals"}, "invalid_result"),
    ({"state": "superseded"}, "invalidated_result"),
    ({"state": "cancelled", "last_error": "source_unavailable"}, "invalidated_result"),
])
def test_missing_malformed_and_invalidated_results_are_unknown(fields, limitation):
    row = outcome_row()
    row.update(fields)
    result = summarize(row)["semantic_outcomes"]
    assert result["unknown_job_count"] == 1 and not result["counts_complete"]
    assert result["limitation_counts"] == {limitation: 1}
    assert result["evaluated_job_count"] == 0
    assert sum(result["accepted_counts"].values()) == sum(result["published_counts"].values()) == 0
    assert "private-" not in json.dumps(result)


def test_recovery_counts_job_results_once_without_claiming_unique_input_or_memory_totals():
    parent = outcome_row("parent", accepted=1, published=1)
    parent.update(processed=8, continuation_id="child")
    child = outcome_row("child", accepted=1, published=1)
    child.update(window_start=9, continuation_parent="parent")
    result = summarize(parent, child)
    assert result["open_window_count"] == 0 and result["semantic_outcomes"]["counts_complete"]
    assert result["semantic_outcomes"]["evaluated_job_count"] == 2
    assert result["semantic_outcomes"]["published_counts"]["claims"] == 2
    parent.update(state="superseded", recovery_id="recovery")
    recovery = outcome_row("recovery", accepted=1, published=1)
    recovery.update(window_end=8, processed=8, recovery_parent="parent")
    result = summarize(parent, child, recovery)
    assert result["open_window_count"] == 0 and result["counts_complete"]
    assert not result["semantic_outcomes"]["counts_complete"]
    assert result["semantic_outcomes"]["unknown_job_count"] == 1
    assert result["semantic_outcomes"]["published_counts"]["claims"] == 2


@pytest.mark.parametrize("field,value", [
    ("job_count", True), ("unknown_job_count", 2**63), ("accepted_counts", {"claims": "private-count"}),
    ("processing_status_counts", {"private-status": 1}), ("examples", [{"body": "private-body"}]),
    ("examples_truncated", []), ("basis", "private-basis"),
])
def test_outcome_sanitizer_rejects_malformed_aggregates_without_leaking_content(field, value):
    summary = summarize(outcome_row())
    summary["semantic_outcomes"][field] = value
    safe = sanitize_learning_progress(summary)
    assert not safe["semantic_outcomes"]["counts_complete"]
    assert safe["counts_complete"] and "private-" not in json.dumps(safe)
    assert sanitize_learning_progress(safe) == safe


@pytest.mark.parametrize("reason", [[], {}, True, "private-reason"])
def test_outcome_examples_require_fixed_reasons(reason):
    summary = summarize(outcome_row(status="partial", reasons={"invalid_proposal": 1}))
    summary["semantic_outcomes"]["examples"][0]["reason"] = reason
    result = sanitize_learning_progress(summary)["semantic_outcomes"]
    assert result["examples"] == [] and not result["counts_complete"]
    assert "private-" not in json.dumps(result)


def test_outcome_examples_are_bounded_ordered_and_private_fields_removed():
    rows = [outcome_row(f"rejection-{index}", status="partial", reasons={"invalid_proposal": 1})
            for index in range(12)]
    for index, row in enumerate(rows):
        row.update(window_start=index + 1, window_end=index + 1, processed=index + 1)
    summary = summarize(*reversed(rows))
    result = summary["semantic_outcomes"]
    assert len(result["examples"]) == 8 and result["examples_truncated"]
    assert result["examples"][0]["job_id"] == "rejection-0"
    result["examples"][0].update(source_id="private-source", body="private-body")
    assert "private-" not in json.dumps(sanitize_learning_progress(summary))


def test_request_intent_rejection_is_not_lost_in_existing_per_job_monitoring():
    result = summarize_learning(semantic_result(status="partial", reasons=["request_intent_unconfirmed"]))
    assert result["rejection_reason_counts"] == {"request_intent_unconfirmed": 1}


@pytest.mark.integration
def test_database_historical_semantic_gap_survives_more_than_two_hundred_new_jobs(stored):
    old = stored.add(start=1, end=1, processed=1, state="completed",
                     result=semantic_result(status="partial", reasons=["request_intent_unconfirmed"]))
    for number in range(205):
        stored.add(start=number + 2, end=number + 2, processed=number + 2, state="completed",
                   result=semantic_result(accepted=1, published=1))
    summary = stored.progress()
    result = summary["semantic_outcomes"]
    assert summary["open_window_count"] == 0 and summary["counts_complete"]
    assert result["counts_complete"] and result["evaluated_job_count"] == 206
    assert result["published_counts"]["claims"] == 205
    assert result["examples"][0]["job_id"] == old
    assert result["rejection_reason_counts"] == {"request_intent_unconfirmed": 1}
    assert "private-" not in json.dumps(summary)


@pytest.mark.integration
@pytest.mark.parametrize("field,value", [
    ("claims", ["private-invalid-claim"]), ("claims", {"body": "private-claim"}),
    ("rejected", [{"reason": {"body": "private-reason"}}]),
    ("rejected", ["private-rejection"]), ("publication", {"memory_versions": None}),
    ("publication", {"pattern_view_ids": [True]}), ("learning_outcome", None),
])
def test_database_projection_marks_malformed_results_unknown(stored, field, value):
    result = semantic_result()
    result[field] = value
    stored.add(processed=40, state="completed", result=result)
    summary = stored.progress()["semantic_outcomes"]
    assert summary["unknown_job_count"] == 1 and not summary["counts_complete"]
    assert "private-" not in json.dumps(summary)


@pytest.mark.integration
def test_worker_rejection_publication_restart_and_source_erasure_have_distinct_outcomes(learning):
    def propose(request):
        sources = json.loads(request.messages[-1]["content"])["sources"]
        source = next((item for item in sources if item["observed"]["content"]["text"]
                       == "I prefer evening walks."), sources[0])
        return encoded(proposals(claim(source, statement=source["observed"]["content"]["text"],
                                       conditions={"op": "always"})))

    def reject(request):
        response = json.loads(learning.verify(request))
        for assessment in response["assessments"]:
            for verdict in assessment["relations"]:
                verdict["verdict"] = "unknown"
        return encoded(response)

    def work():
        job = JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(),
                                               kinds=["consolidate"], lease_seconds=120)
        assert job is not None
        LearningEngine(learning.service)._execute(learning.principal, learning.brain, job)
        return learning.job(job["id"])

    def progress():
        database = Database(Settings.from_env().database_url)
        with database.transaction(learning.principal) as connection:
            return learning_progress(connection, learning.principal, learning.brain, configured=True)

    learning.observe("I prefer early morning walks.")
    learning.service.semantic_processor = SemanticProcessor(propose, reject)
    failed = work()
    assert failed["state"] == "completed" and failed["result"]["publication"]["memory_versions"] == []
    rejected = progress()
    assert rejected["open_window_count"] == 0
    assert rejected["semantic_outcomes"]["learning_outcome_counts"] == {"no_accepted_proposals": 1}
    assert rejected["semantic_outcomes"]["counts_complete"]
    receipt = learning.observe("I prefer evening walks.", conversation="new-conversation")
    learning.service.semantic_processor = SemanticProcessor(propose, learning.verify)
    accepted = work()
    assert accepted["result"]["publication"]["memory_versions"]
    published = progress()["semantic_outcomes"]
    assert published["published_counts"]["claims"] == 1 and published["counts_complete"]
    learning.memory.forget(receipt["accepted_event_ids"], kind="events", mode="erase_content", expected_scope_generation=1)
    erased = progress()["semantic_outcomes"]
    assert not erased["counts_complete"] and erased["unknown_job_count"] >= 1
    assert erased["limitation_counts"]["invalidated_result"] >= 1
    assert erased["published_counts"]["claims"] == 0
