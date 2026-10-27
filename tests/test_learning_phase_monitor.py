import copy
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_learning_monitoring import job_row
from test_learning_progress import AT, outcome_row, semantic_result, summarize, window
from test_learning_progress import stored as stored

from membrain.api import create_app
from membrain.config import Settings
from membrain.helpers import Principal, digest
from membrain.history_semantics import MANIFEST_VERSION
from membrain.jobs import JobStore, sanitize_learning_summary
from membrain.learning_allocations import HISTORY_PHASE, allocation_metadata
from membrain.learning_phase_monitor import (
    DEFERRED_REASON,
    classify,
    coverage_summary,
    resolve_history_status,
    sanitize_phase_counts,
    sanitize_phase_metadata,
)


def manifest(count=1):
    inputs = [{"event_id": "private-source", "revision": 1, "content_sha256": "a" * 64}]
    fingerprint = digest(inputs)
    targets = [{"memory_id": f"private-memory-{index}", "revision": 1, "content_sha256": "b" * 64}
               for index in range(count)]
    return {"version": MANIFEST_VERSION, "input_fingerprint": fingerprint, "input_refs": inputs,
            "through_sequence": 8, "unresolved_discovery": False, "discovery_limitations": [],
            "objectives": [{"objective_id": "comparison-" + digest([MANIFEST_VERSION, fingerprint, target]),
                            "target_memory_version": target} for target in targets]}


def coverage(*, assessed=True, published=False):
    return {"version": "bounded-history-manifest-v1", "objectives": [
        {"objective_id": manifest()["objectives"][0]["objective_id"], "assessed": assessed,
         "status": "published_relation" if published else "assessed_no_publication" if assessed else "not_evaluated",
         "verified_link_ids": ["private-relation"] if published else [], "reason": None},
    ], "assessed_count": int(assessed), "published_count": int(published)}


def encoding(identifier="encoding", *, start=1, end=8, attempted=8, child="history", **updates):
    return {**outcome_row(identifier), "window_start": start, "window_end": end, "processed": attempted,
            "attempted": attempted, "phase_payload": allocation_metadata(identifier),
            "history_child": child, "has_history_child": child is not None, "proposal_mode": "local-observation-v1",
            **updates}


def history(identifier="history", *, state="deferred", parent="encoding", root="encoding", **updates):
    evaluated = state == "completed"
    result = outcome_row(identifier, status="completed" if evaluated else "not_evaluated")
    result.update(window_start=1, window_end=8, processed=None, has_processed=False, state=state,
                  attempts=2 if evaluated else 0, model_calls=2 if evaluated else 0, input_bytes=200 if evaluated else 0,
                  phase_payload={
                      **allocation_metadata(identifier, phase=HISTORY_PHASE, root_job_id=root),
                      "encoding_parent_job_id": parent, "encoding_input_fingerprint": manifest()["input_fingerprint"],
                      "history_cutoff_sequence": 8,
                      "history_obligation_manifest": manifest(),
                  }, history_coverage=coverage() if evaluated else None,
                  history_reason=DEFERRED_REASON if state == "deferred" else None,
                  history_category="package_incomplete" if state == "deferred" else None)
    result.update(updates)
    return result


def test_completed_encoding_does_not_erase_deferred_history_or_invent_a_publication():
    result = summarize(encoding(), history())
    phases = result["learning_phases"]
    assert result["counts_complete"] and phases["counts_complete"]
    assert result["window_count"] == 2 and result["open_window_count"] == result["blocked_count"] == 1
    assert result["blocked_reason_counts"] == {DEFERRED_REASON: 1}
    assert phases["encoding_assessed_count"] == 1 and phases["encoding_open_count"] == 0
    assert phases["history_integration_open_count"] == phases["history_integration_deferred_count"] == 1
    assert phases["history_integration_assessed_count"] == 0
    outcomes = result["semantic_outcomes"]
    assert outcomes["evaluated_job_count"] == outcomes["unevaluated_job_count"] == 1
    assert outcomes["by_phase"]["history_integration"]["processing_status_counts"] == {"not_evaluated": 1}
    assert sum(outcomes["published_counts"].values()) == 0
    assert "private-" not in json.dumps(result)


def test_encoding_tail_and_historical_integration_remain_independent_open_obligations():
    parent = encoding(end=40, continuation_id="tail")
    tail = window("tail", 9, 40, continuation_parent="encoding", phase_payload=allocation_metadata("tail"))
    result = summarize(parent, tail, history())
    assert result["counts_complete"] and result["open_window_count"] == 2
    assert result["learning_phases"]["encoding_open_count"] == 1
    assert result["learning_phases"]["history_integration_open_count"] == 1


def test_history_continuation_retains_assessed_prefix_but_counts_one_open_root():
    first = history(state="completed", integration_id="next", has_integration=True)
    next_job = history("next", integration_parent="history", created_at=AT + timedelta(days=10))
    next_job["phase_payload"]["integration_continuation_parent_job_id"] = "history"
    result = summarize(encoding(), first, next_job)
    phases = result["learning_phases"]
    assert result["counts_complete"] and result["open_window_count"] == 1
    assert phases["history_integration_open_count"] == phases["history_integration_assessed_count"] == 1
    assert phases["history_integration_deferred_count"] == 1
    assert result["oldest_pending_window"]["created_at"] == AT.isoformat()


def test_partial_history_keeps_selected_debt_after_an_independent_tail_completes():
    first = history(state="completed", integration_id="next", has_integration=True,
                    history_coverage=coverage(assessed=False, published=True))
    partial = outcome_row("history", status="partial", accepted=1, published=1, reasons={"invalid_proposal": 1})
    first.update({key: partial[key] for key in ("processing_status", "learning_outcome", "accepted_counts",
                                              "published_counts", "rejected_count", "rejection_reason_counts")})
    tail = history("next", state="completed", integration_parent="history")
    tail["phase_payload"]["integration_continuation_parent_job_id"] = "history"
    result = summarize(encoding(), first, tail)
    phases = result["learning_phases"]
    assert phases["counts_complete"] and phases["unknown_count"] == 0
    assert phases["history_integration_open_count"] == phases["history_integration_assessed_count"] == 1
    assert phases["history_integration_unassessed_objective_count"] == 1
    assert result["blocked_reason_counts"] == {"history_integration_unassessed": 1}
    assert result["semantic_outcomes"]["by_phase"]["history_integration"]["counts_complete"]


@pytest.mark.parametrize("status", ["invalid_response", "provider_unavailable", "verification_unavailable", "budget_exhausted",
                                    "partial"])
def test_known_paid_failure_keeps_selected_debt_while_untouched_tail_is_assessed(status):
    first = history(state="cancelled", integration_id="next", has_integration=True,
                    model_calls=1, history_coverage=coverage(assessed=False),
                    processing_status=status, learning_outcome="not_evaluated", last_error="invalid_proposal")
    tail = history("next", state="completed", integration_parent="history")
    tail["phase_payload"]["integration_continuation_parent_job_id"] = "history"
    result = summarize(encoding(), first, tail)
    phases = result["learning_phases"]
    assert phases["counts_complete"] and phases["unknown_count"] == 0
    assert phases["history_integration_open_count"] == phases["history_integration_assessed_count"] == 1
    assert phases["history_integration_unassessed_objective_count"] == 1
    assert result["blocked_reason_counts"] == {"history_integration_unassessed": 1}


def test_individually_published_relation_does_not_claim_full_selected_comparison_assessment():
    summary = coverage_summary(coverage(assessed=False, published=True))
    assert summary["published_count"] == 1 and summary["assessed_count"] == 0
    safe = sanitize_phase_metadata({"learning_contract_version": "encoding-integration-v1",
                                    "learning_phase": "history_integration", "phase_metadata_complete": True,
                                    "history_integration": summary})
    assert safe["phase_metadata_complete"] and safe["history_integration"] == summary


@pytest.mark.parametrize("counter", [0, 2])
def test_invalidated_encoding_preserves_attempted_prefix_and_delegates_only_frozen_untouched_tail(counter):
    first = encoding(state="superseded", processed=0, attempted=None, child=None,
                     continuation_id="tail", has_continuation=True, has_processed=False, model_calls=counter)
    first["phase_payload"]["encoding_window_snapshot"] = {
        "version": "encoding-window-snapshot-v1", "from_sequence": 1,
        "through_sequence": 8, "attempted_through_sequence": 4}
    tail = encoding("tail", start=5, end=8, attempted=8, child=None, proposal_mode="general-v1",
                    continuation_parent="encoding")
    result = summarize(first, tail)
    assert result["learning_phases"]["counts_complete"] is True
    assert result["learning_phases"]["encoding_open_count"] == 1
    assert result["blocked_count"] == 1 and result["unknown_count"] == 0
    assert result["oldest_pending_window"]["from_sequence"] == 1
    assert result["oldest_pending_window"]["through_sequence"] == 4


@pytest.mark.parametrize("damage", ["missing", "bool", "version", "range", "same_root", "provider_failure", "reverse"])
def test_encoding_untouched_tail_requires_valid_frozen_boundary_and_independent_link(damage):
    first = encoding(state="superseded", processed=0, attempted=None, child=None,
                     continuation_id="tail", has_continuation=True, has_processed=False)
    marker = {"version": "encoding-window-snapshot-v1", "from_sequence": 1,
              "through_sequence": 8, "attempted_through_sequence": 4}
    first["phase_payload"]["encoding_window_snapshot"] = marker
    tail = encoding("tail", start=5, end=8, attempted=8, child=None, proposal_mode="general-v1",
                    continuation_parent="encoding")
    if damage == "missing":
        first["phase_payload"].pop("encoding_window_snapshot")
    elif damage == "bool":
        marker["attempted_through_sequence"] = True
    elif damage == "version":
        marker["version"] = "unknown"
    elif damage == "range":
        marker["through_sequence"] = 9
    elif damage == "same_root":
        tail["phase_payload"]["authorization_root_job_id"] = "encoding"
    elif damage == "provider_failure":
        first.update(state="cancelled", last_error="provider_failed")
    else:
        tail["continuation_parent"] = None
    result = summarize(first, tail)
    assert result["learning_phases"]["counts_complete"] is False
    assert result["unknown_count"] >= 1


@pytest.mark.parametrize("charged", [False, True])
def test_only_unpaid_deferred_history_can_delegate_to_later_encoding_allocation(charged):
    first = history(integration_id="funded", has_integration=True, model_calls=int(charged))
    funded = history("funded", state="queued", root="donor", integration_parent="history")
    funded["phase_payload"]["integration_continuation_parent_job_id"] = "history"
    donor = encoding("donor", start=9, end=16, attempted=16, child=None, proposal_mode="general-v1")
    result = summarize(encoding(), first, donor, funded)
    assert result["counts_complete"] is not charged
    if not charged:
        assert result["learning_phases"]["history_integration_open_count"] == 1
        assert result["learning_phases"]["history_integration_deferred_count"] == 0
        assert result["runnable_count"] == 1


def test_history_recovery_preserves_phase_and_finishes_original_obligation_once():
    failed = history(state="cancelled", recovery_id="recovery", last_error="provider_uncertain")
    recovered = history("recovery", state="completed", root="recovery", recovery_parent="history")
    recovered["phase_payload"].update(allocation_metadata("recovery", phase=HISTORY_PHASE, recovery=True))
    result = summarize(encoding(), failed, recovered)
    assert result["counts_complete"] and result["open_window_count"] == 0
    assert result["learning_phases"]["history_integration_assessed_count"] == 1


@pytest.mark.parametrize("damage", [None, "paid", "bytes", "root", "cutoff", "marker"])
def test_unpaid_invalidated_history_replan_keeps_one_obligation_on_same_allocation(damage):
    original = history(state="superseded", integration_id="replanned", has_integration=True)
    original["phase_payload"]["allocation_input_fingerprint"] = "b" * 64
    child = history("replanned", state="queued", integration_parent="history")
    child["phase_payload"].update(integration_continuation_parent_job_id="history",
                                  history_replan_version="unreserved-history-replan-v1")
    if damage == "paid":
        original["model_calls"] = 1
    elif damage == "bytes":
        original["input_bytes"] = 1
    elif damage == "root":
        child["phase_payload"]["authorization_root_job_id"] = "other"
    elif damage == "cutoff":
        child["phase_payload"]["history_cutoff_sequence"] = 9
    elif damage == "marker":
        child["phase_payload"].pop("history_replan_version")
    result = summarize(encoding(), original, child)
    assert result["learning_phases"]["counts_complete"] is (damage is None)
    if damage is None:
        assert result["learning_phases"]["history_integration_open_count"] == 1
        assert result["learning_phases"]["history_integration_assessed_count"] == 0


@pytest.mark.parametrize("damage", [None, "marker", "paid", "bytes", "range", "reverse", "processed", "active"])
def test_only_exact_unpaid_encoding_replans_share_the_retired_root(damage):
    original = window("original", 1, 8, state="superseded", recovery_id="encoding", model_calls=0, input_bytes=0,
                      phase_payload=allocation_metadata("original"))
    child = encoding(child=None, proposal_mode="general-v1", recovery_parent="original")
    child["phase_payload"].update(allocation_metadata("encoding", root_job_id="original"),
                                  encoding_replan_version="unreserved-encoding-replan-v1")
    if damage == "marker":
        child["phase_payload"].pop("encoding_replan_version")
    elif damage == "paid":
        original["model_calls"] = 1
    elif damage == "bytes":
        original["input_bytes"] = 1
    elif damage == "range":
        original["window_end"] = 9
    elif damage == "reverse":
        original["recovery_id"] = "other"
    elif damage == "processed":
        original.update(processed=1, has_processed=True)
    elif damage == "active":
        original["state"] = "queued"
    result = summarize(original, child)
    assert result["learning_phases"]["counts_complete"] is (damage is None)
    if damage is None:
        assert result["learning_phases"]["encoding_assessed_count"] == 1
        assert result["learning_phases"]["encoding_open_count"] == 0


def test_missing_history_link_keeps_successful_encoding_visible_as_separate_historical_result():
    result = summarize(encoding())
    assert not result["counts_complete"]
    assert result["learning_phases"]["encoding_assessed_count"] == 1
    assert result["learning_phases"]["encoding_open_count"] == 0
    assert result["learning_phases"]["history_integration_open_count"] == 1
    assert result["learning_phases"]["unknown_count"] == 1
    assert result["semantic_outcomes"]["by_phase"]["encoding"]["evaluated_job_count"] == 1


@pytest.mark.parametrize("damage", ["scope", "parent", "fingerprint", "range", "category", "contract", "root"])
def test_malformed_history_never_looks_finished_or_disappears(damage):
    child = history()
    if damage == "scope":
        child["conversation"] = "other"
    elif damage == "parent":
        child["phase_payload"]["encoding_parent_job_id"] = "missing"
    elif damage == "fingerprint":
        child["phase_payload"]["encoding_input_fingerprint"] = "private-input"
    elif damage == "range":
        child["window_end"] = 40
    elif damage == "category":
        child["history_category"] = "private-provider-message"
    elif damage == "contract":
        child["phase_payload"]["learning_contract_version"] = "private-version"
    else:
        child["phase_payload"]["authorization_root_job_id"] = "missing"
    result = summarize(encoding(), child)
    assert not result["counts_complete"] and not result["learning_phases"]["counts_complete"]
    assert result["learning_phases"]["unknown_count"] >= 1
    assert result["open_window_count"] >= 1 and "private-" not in json.dumps(result)


def test_cycle_and_cross_phase_recovery_cannot_delegate_work_away():
    first = history(state="cancelled", recovery_id="second", recovery_parent="second")
    second = history("second", state="cancelled", recovery_id="history", recovery_parent="history")
    cyclic = summarize(encoding(), first, second)
    assert cyclic["learning_phases"]["history_integration_open_count"] >= 1
    assert cyclic["learning_phases"]["unknown_count"] >= 2 and not cyclic["counts_complete"]
    parent = encoding(child=None, state="cancelled", processed=0, recovery_id="history", proposal_mode="general-v1")
    child = history(state="queued", recovery_parent="encoding")
    crossed = summarize(parent, child)
    assert not crossed["counts_complete"] and crossed["unknown_count"] >= 1


def test_full_history_phase_summary_retains_old_debt_beyond_two_hundred_new_jobs():
    legacy = [outcome_row(f"legacy-{index}") for index in range(205)]
    result = summarize(*legacy, encoding(), history())
    phases = result["learning_phases"]
    assert result["window_count"] == 207
    assert phases["legacy_unclassified_count"] == 205 and phases["history_integration_deferred_count"] == 1
    assert result["oldest_pending_window"]["job_id"] == "history"


@pytest.mark.parametrize("state", ["queued", "running", "failed", "deferred"])
def test_pending_history_is_open_and_only_live_leases_count_as_running(state):
    child = history(state=state)
    if state == "running":
        child.update(lease_expires_at=AT + timedelta(seconds=30), attempts=1)
    result = summarize(encoding(), child)
    assert result["learning_phases"]["history_integration_open_count"] == 1
    assert result["learning_phases"]["history_integration_running_count"] == int(state == "running")


@pytest.mark.parametrize("bad", [None, [], {"learning_phase": "encoding"}, {"encoding_parent_job_id": "parent"}])
def test_invalid_present_metadata_cannot_be_inferred_as_legacy(bad):
    assert classify(bad) == "unknown"
    result = summarize(window(phase_payload=bad))
    assert result["learning_phases"]["unknown_count"] == 1 and not result["counts_complete"]


@pytest.mark.parametrize("damage", ["bool", "duplicate", "status", "counts", "reason", "reason_object", "extra"])
def test_coverage_is_exact_and_public_output_contains_counts_only(damage):
    original = coverage(published=True)
    safe = coverage_summary(original)
    assert safe == {"version": "bounded-history-manifest-v1", "objective_count": 1,
                    "assessed_count": 1, "published_count": 1}
    assert "private-" not in json.dumps(safe)
    bad = copy.deepcopy(original)
    if damage == "bool":
        bad["assessed_count"] = True
    elif damage == "duplicate":
        bad["objectives"] *= 2
    elif damage == "status":
        bad["objectives"][0]["status"] = "private-status"
    elif damage == "counts":
        bad["published_count"] = 0
    elif damage == "reason":
        bad["objectives"][0]["reason"] = "insufficient_evidence"
    elif damage == "reason_object":
        bad["objectives"][0]["reason"] = {"text": "private-source"}
    else:
        bad["private-body"] = "private-body"
    assert coverage_summary(bad) is None


def test_phase_summary_sanitization_cannot_turn_malformed_or_missing_counts_into_complete_zeroes():
    for value in ({}, {"version": "unknown", "encoding_open_count": "private-text", "counts_complete": True}):
        safe = sanitize_phase_counts(value)
        assert not safe["counts_complete"] and "private-" not in json.dumps(safe)
    value = summarize(encoding(), history())["learning_phases"]
    value["history_integration_running_count"] = 2
    assert not sanitize_phase_counts(value)["counts_complete"]


def exact_rows():
    target = {"conversation_ref": "conversation", "branch_ref": "main"}
    parent = job_row(semantic_result())
    parent.update(id="encoding", tenant_id="tenant", owner_id="owner", payload={
        **allocation_metadata("encoding"), "target": target, "from_sequence": 1, "through_sequence": 8,
    })
    parent["result"].update(history_integration_job_id="history", history_integration_state="deferred",
                             learning_window={"from_sequence": 1, "attempted_through_sequence": 8})
    child = {"id": "history", "kind": "consolidate", "state": "deferred", "model_calls": 0, "input_bytes": 0,
             "last_error": None, "cycle": False, "depth": 0,
             "payload": {**allocation_metadata("history", phase=HISTORY_PHASE, root_job_id="encoding"),
                         "target": target, "from_sequence": 1, "through_sequence": 8,
                         "encoding_parent_job_id": "encoding", "encoding_input_fingerprint": manifest()["input_fingerprint"],
                         "history_obligation_manifest": manifest(),
                         "history_cutoff_sequence": 8},
             "result": {**semantic_result(status="not_evaluated"), "history_integration_reason": DEFERRED_REASON,
                        "history_integration_category": "package_incomplete"}}
    return parent, child


class Rows(list):
    def fetchone(self):
        return self[0] if self else None


class Connection:
    def __init__(self, parent, children, roots=(), ancestors=()):
        self.parent, self.children, self.roots, self.requests = parent, children, roots, []
        self.ancestors = ancestors

    def execute(self, sql, params):
        self.requests.append((sql, params))
        assert "tenant_id=%s AND owner_id=%s AND brain_id=%s" in sql
        assert params[:3] == ("tenant", "owner", "brain")
        if sql.startswith("WITH RECURSIVE ancestry"):
            assert "a.depth<256" in sql and "LIMIT 258" in sql
            return Rows(self.ancestors)
        if sql.startswith("WITH RECURSIVE"):
            assert "l.depth<256" in sql and "LIMIT 258" in sql
            return Rows(self.children)
        if "id=ANY" in sql:
            return Rows(self.roots)
        return Rows([self.parent])


def test_exact_encoding_job_resolves_current_deferred_status_without_sources_or_manifest():
    parent, child = exact_rows()
    connection = Connection(parent, [child])
    public = JobStore(None)._public(parent, connection=connection)
    summary = public["learning_summary"]
    assert summary["history_integration_resolved"] and summary["history_integration_state"] == "deferred"
    assert summary["history_integration_job_id"] == summary["history_integration_current_job_id"] == "history"
    assert summary["history_integration_category"] == "package_incomplete"
    assert len(connection.requests) == 1 and "private-" not in json.dumps(public)
    assert sanitize_learning_summary(summary) == summary


def test_exact_status_follows_valid_recovery_without_overwriting_parent_historical_result():
    parent, child = exact_rows()
    saved = copy.deepcopy(parent)
    child.update(state="cancelled")
    child["payload"]["recovery_job_id"] = "recovered"
    recovered = copy.deepcopy(child)
    recovered.update(id="recovered", state="completed", depth=1)
    recovered["payload"].pop("recovery_job_id")
    recovered["payload"].update(recovery_parent_job_id="history", **allocation_metadata("recovered", phase=HISTORY_PHASE, recovery=True))
    recovered["result"] = {**semantic_result(), "history_integration": coverage()}
    connection = Connection(parent, [child, recovered], [recovered])
    safe = resolve_history_status(connection, parent)
    assert safe["history_integration_state"] == "completed" and safe["history_integration_current_job_id"] == "recovered"
    assert parent == saved


def test_exact_completed_leaf_cannot_hide_unassessed_selected_work_on_a_partial_parent():
    parent, child = exact_rows()
    child.update(state="completed", model_calls=2)
    child["payload"]["integration_continuation_job_id"] = "tail"
    child["result"] = {**semantic_result(status="partial", accepted=1, published=1,
                                         reasons={"invalid_proposal": 1}),
                       "history_integration": coverage(assessed=False, published=True)}
    tail = copy.deepcopy(child)
    tail.update(id="tail", depth=1)
    tail["payload"].pop("integration_continuation_job_id")
    tail["payload"]["integration_continuation_parent_job_id"] = "history"
    tail["result"] = {**semantic_result(), "history_integration": coverage()}
    result = resolve_history_status(Connection(parent, [child, tail]), parent)
    assert result["history_integration_resolved"] and result["history_integration_state"] == "completed"
    assert result["history_integration_open"]
    assert result["history_integration_unassessed_objective_count"] == 1


def test_exact_completed_leaf_cannot_hide_selected_work_after_known_provider_failure():
    parent, child = exact_rows()
    child.update(state="cancelled", model_calls=1, last_error="provider_failed")
    child["payload"]["integration_continuation_job_id"] = "tail"
    child["result"] = {**semantic_result(status="provider_unavailable"), "history_integration": coverage(assessed=False)}
    tail = copy.deepcopy(child)
    tail.update(id="tail", state="completed", depth=1, model_calls=2)
    tail["payload"].pop("integration_continuation_job_id")
    tail["payload"]["integration_continuation_parent_job_id"] = "history"
    tail["result"] = {**semantic_result(), "history_integration": coverage()}
    result = resolve_history_status(Connection(parent, [child, tail]), parent)
    assert result["history_integration_resolved"] and result["history_integration_state"] == "completed"
    assert result["history_integration_open"] and result["history_integration_unassessed_objective_count"] == 1


@pytest.mark.parametrize("charged", [False, True])
def test_exact_history_from_replanned_encoding_validates_its_unpaid_ancestors(charged):
    parent, child = exact_rows()
    parent.update(cycle=False, depth=0)
    parent["payload"].update(allocation_metadata("encoding", root_job_id="original"),
                             encoding_replan_version="unreserved-encoding-replan-v1",
                             recovery_parent_job_id="original")
    original = {**copy.deepcopy(parent), "id": "original", "state": "superseded", "model_calls": int(charged),
                "input_bytes": 0, "result": {}, "depth": 1,
                "payload": {**allocation_metadata("original"), "target": parent["payload"]["target"],
                            "from_sequence": 1, "through_sequence": 8, "recovery_job_id": "encoding"}}
    child["payload"].update(allocation_metadata("history", phase=HISTORY_PHASE, root_job_id="original"))
    connection = Connection(parent, [child], [original], [parent, original])
    result = resolve_history_status(connection, parent)
    assert result["history_integration_resolved"] is not charged
    assert result["history_integration_state"] == ("unknown" if charged else "deferred")


@pytest.mark.parametrize("charged", [False, True])
def test_exact_status_follows_only_unpaid_deferred_donor_lineage(charged):
    parent, child = exact_rows()
    child["model_calls"] = int(charged)
    child["payload"]["integration_continuation_job_id"] = "funded"
    funded = copy.deepcopy(child)
    funded.update(id="funded", state="queued", depth=1, model_calls=0)
    funded["payload"].pop("integration_continuation_job_id")
    funded["payload"].update(integration_continuation_parent_job_id="history",
                             **allocation_metadata("funded", phase=HISTORY_PHASE, root_job_id="donor"))
    donor = copy.deepcopy(parent)
    donor.update(id="donor", payload={**parent["payload"], **allocation_metadata("donor"),
                                     "from_sequence": 9, "through_sequence": 16})
    result = resolve_history_status(Connection(parent, [child, funded], [donor]), parent)
    assert result["history_integration_resolved"] is not charged
    assert result["history_integration_state"] == ("unknown" if charged else "queued")
    if not charged:
        assert result["history_integration_current_job_id"] == "funded"


def test_frozen_deferred_snapshot_cannot_transfer_to_a_new_donor():
    parent, child = exact_rows()
    child["payload"]["allocation_input_fingerprint"] = "b" * 64
    child["payload"]["integration_continuation_job_id"] = "funded"
    funded = copy.deepcopy(child)
    funded.update(id="funded", state="queued", depth=1)
    funded["payload"].pop("integration_continuation_job_id")
    funded["payload"].pop("allocation_input_fingerprint")
    funded["payload"]["integration_continuation_parent_job_id"] = "history"
    result = resolve_history_status(Connection(parent, [child, funded]), parent)
    assert not result["history_integration_resolved"] and result["history_integration_state"] == "unknown"


@pytest.mark.parametrize("damage", ["missing", "scope", "range", "phase", "parent", "cycle", "fingerprint",
                                    "unfinished", "category", "cutoff", "parent_bounds", "paid_deferred"])
def test_exact_status_fails_closed_without_echoing_private_metadata(damage):
    parent, child = exact_rows()
    children = [child]
    if damage == "missing":
        children = []
    elif damage == "scope":
        child["payload"]["target"] = {"conversation_ref": "private-other", "branch_ref": "main"}
    elif damage == "range":
        child["payload"]["through_sequence"] = 40
    elif damage == "phase":
        child["payload"]["learning_phase"] = "encoding"
    elif damage == "parent":
        child["payload"]["encoding_parent_job_id"] = "private-other"
    elif damage == "cycle":
        child["cycle"] = True
    elif damage == "fingerprint":
        child["payload"]["encoding_input_fingerprint"] = "private-fingerprint"
    elif damage == "unfinished":
        child["state"] = "completed"
    elif damage == "cutoff":
        child["payload"]["history_cutoff_sequence"] = 7
    elif damage == "parent_bounds":
        parent["payload"]["through_sequence"] = 7
    elif damage == "paid_deferred":
        child["model_calls"] = 1
    else:
        child["result"]["history_integration_category"] = "private-provider-error"
    safe = resolve_history_status(Connection(parent, children), parent)
    assert safe["history_integration_state"] == "unknown" and not safe["history_integration_resolved"]
    assert "private-" not in json.dumps(safe)


def test_raw_job_list_does_not_claim_a_link_is_current_without_lookup():
    parent, _ = exact_rows()
    public = JobStore(None)._public(parent)
    assert public["learning_summary"]["history_integration_state"] == "unknown"
    assert not public["learning_summary"]["history_integration_resolved"]
    malicious = {**public["learning_summary"], "history_integration_state": "private-state", "history_integration_resolved": True}
    assert sanitize_phase_metadata(malicious)["history_integration_state"] == "unknown"


def test_empty_unused_history_result_does_not_invalidate_encoding_metadata():
    parent, _ = exact_rows()
    parent["result"]["history_integration"] = {}
    summary = JobStore(None)._public(parent)["learning_summary"]
    assert summary["phase_metadata_complete"] and summary["learning_phase"] == "encoding"
    assert "history_integration" not in summary


def test_api_refilters_phase_counts_and_exact_metadata_without_private_manifests():
    principal = Principal("tenant", "owner")
    parent, child = exact_rows()
    public = JobStore(None)._public(parent, connection=Connection(parent, [child]))
    public["learning_summary"]["integration_manifest"] = {"text": "private-source"}
    public["learning_summary"]["history_integration"] = coverage()
    progress = summarize(encoding(), history())
    progress["learning_phases"]["source_text"] = "private-source"
    jobs = SimpleNamespace(get=lambda *_: public, learning_progress=lambda *_, **__: progress)
    settings = Settings(database_url="unused", api_keys={hashlib.sha256(b"key").hexdigest(): principal})
    with TestClient(create_app(settings, service=SimpleNamespace(), jobs=jobs)) as client:
        for path in ("/jobs/encoding", "/learning-progress"):
            response = client.get("/v1/brains/brain/runtime" + path, headers={"Authorization": "Bearer key"})
            assert response.status_code == 200 and "private-" not in response.text
            result = response.json()
            if path.endswith("encoding"):
                assert result["learning_summary"]["history_integration_state"] == "deferred"
                assert not result["learning_summary"]["phase_metadata_complete"]
            else:
                assert result["learning_phases"]["history_integration_deferred_count"] == 1
                assert result["semantic_outcomes"]["by_phase"]["encoding"]["evaluated_job_count"] == 1


def store_pair(stored, *, child_state="deferred", child_payload=None, child_result=None):
    parent, child = exact_rows()
    parent_id, child_id = "encoding", "history"
    parent["payload"]["target"] = child["payload"]["target"] = {
        "conversation_ref": "private-conversation", "branch_ref": "main"}
    stored.add(parent_id, start=1, end=8, processed=8, state="completed", payload=parent["payload"], result=parent["result"])
    stored.add(child_id, start=1, end=8, state=child_state, payload={**child["payload"], **(child_payload or {})},
               result=child["result"] if child_result is None else child_result)
    return parent_id, child_id


@pytest.mark.integration
def test_database_phase_projection_and_exact_lookup_preserve_deferred_history(stored):
    parent_id, child_id = store_pair(stored)
    for index in range(205):
        stored.add(f"legacy-{index}", start=index + 20, end=index + 20, processed=index + 20,
                   state="completed", result=semantic_result())
    progress = stored.progress()
    assert progress["window_count"] == 207 and progress["counts_complete"]
    assert progress["learning_phases"]["history_integration_deferred_count"] == 1
    assert progress["learning_phases"]["legacy_unclassified_count"] == 205
    assert progress["semantic_outcomes"]["by_phase"]["history_integration"]["unevaluated_job_count"] == 1
    jobs = JobStore(stored.database)
    for identifier in (parent_id, child_id):
        result = jobs.get(stored.principal, stored.brain, identifier)
        assert result["learning_summary"]["history_integration_state"] == "deferred"
        assert result["learning_summary"]["history_integration_resolved"]
        assert "private-" not in json.dumps(result)


@pytest.mark.integration
def test_database_exact_recovery_chain_and_oldest_age_are_preserved(stored):
    parent_id, child_id = store_pair(stored, child_state="cancelled", child_payload={"recovery_job_id": "recovered"})
    _, child = exact_rows()
    payload = {**child["payload"], **allocation_metadata("recovered", phase=HISTORY_PHASE, recovery=True),
               "recovery_parent_job_id": child_id,
               "target": {"conversation_ref": "private-conversation", "branch_ref": "main"}}
    stored.add("recovered", start=1, end=8, state="completed", payload=payload,
               created_at=AT + timedelta(days=3), result={**semantic_result(), "history_integration": coverage()})
    progress = stored.progress()
    assert progress["counts_complete"] and progress["open_window_count"] == 0
    assert progress["learning_phases"]["history_integration_assessed_count"] == 1
    result = JobStore(stored.database).get(stored.principal, stored.brain, parent_id)
    assert result["learning_summary"]["history_integration_current_job_id"] == "recovered"
    assert result["learning_summary"]["history_integration_state"] == "completed"


@pytest.mark.integration
def test_database_phase_without_window_is_counted_unknown_instead_of_omitted(stored):
    from psycopg.types.json import Jsonb

    stored.add("encoding", payload=allocation_metadata("encoding"))
    with stored.database.transaction(stored.principal) as connection:
        connection.execute(
            "UPDATE memory_jobs SET payload=payload-'from_sequence' WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (stored.principal.tenant_id, stored.principal.principal_id, stored.brain, "encoding"),
        )
        connection.execute(
            "UPDATE memory_jobs SET payload=payload || %s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb({"private": "private-source-body"}), stored.principal.tenant_id,
             stored.principal.principal_id, stored.brain, "encoding"),
        )
    result = stored.progress()
    assert result["window_count"] == result["unknown_count"] == 1
    assert not result["counts_complete"] and result["learning_phases"]["unknown_count"] == 1
    assert "private-" not in json.dumps(result)


@pytest.mark.integration
def test_database_exact_scope_mismatch_does_not_expose_child_or_hide_obligation(stored):
    parent_id, _ = store_pair(stored, child_payload={"target": {"conversation_ref": "private-other", "branch_ref": "main"}})
    progress = stored.progress()
    assert not progress["counts_complete"] and progress["learning_phases"]["unknown_count"] >= 1
    result = JobStore(stored.database).get(stored.principal, stored.brain, parent_id)
    assert result["learning_summary"]["history_integration_state"] == "unknown"
    assert not result["learning_summary"]["history_integration_resolved"]
    assert "private-" not in json.dumps(result)
