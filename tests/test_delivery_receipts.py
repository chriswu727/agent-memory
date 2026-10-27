import copy
import hashlib
import json

import pytest
from test_learning import learning as learning
from test_turns import turns as turns

from membrain.delivery_receipts import (
    confirmed_delivery_receipt,
    failed_receipt,
    receipt_for,
    recorded_delivery_receipt,
    sanitize_delivery_receipt,
)
from membrain.helpers import DomainError, digest, now, uid
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked


def authorized_row():
    return {
        "output_event_refs": [{"event_id": "draft", "revision": 1}], "context_fingerprint": digest("context"),
        "context_generation": 3, "working_revision": 2, "fence": 1, "revision": 7,
    }


def recorded_row(stage="delivered"):
    original = authorized_row()
    receipt = receipt_for(original, "Actual reply", "delivered", content_recorded=True)
    return {**original, "output_event_refs": [{"event_id": "draft", "revision": 2}], "revision": 8,
            "delivery_receipt": receipt, "stage": stage, "failure_code": None, "scope_reserved": stage == "delivered",
            "working_revision": original["working_revision"] + int(stage == "completed")}


def test_receipt_contains_only_bounded_metadata_and_sanitizer_strips_extra_fields():
    receipt = receipt_for(authorized_row(), "Actual 中文 reply", "partially_delivered", content_recorded=True)
    assert receipt["content_sha256"] == hashlib.sha256("Actual 中文 reply".encode()).hexdigest()
    assert receipt["content_bytes"] == len("Actual 中文 reply".encode())
    hostile = copy.deepcopy(receipt)
    hostile["body"] = "Private reply"
    hostile["authorization"]["lease_token"] = "Private token"
    hostile["authorization"]["draft_event_ref"]["text"] = "Private draft"
    assert sanitize_delivery_receipt(hostile) == receipt
    assert "Private" not in json.dumps(sanitize_delivery_receipt(hostile))


@pytest.mark.parametrize(("field", "value"), [
    ("version", "other"), ("state", []), ("content_sha256", "x" * 64), ("content_bytes", True),
    ("content_bytes", 64001), ("content_bytes", 0), ("disposition", "arbitrary"),
    ("stale_reasons", ["raw secret"]), ("stale_reasons", ["source_unavailable", "source_unavailable"]),
    ("failure_code", "source_unavailable"), ("authorization", []),
])
def test_receipt_sanitizer_rejects_malformed_or_inconsistent_metadata(field, value):
    receipt = receipt_for(authorized_row(), "Actual reply", "delivered", content_recorded=True)
    receipt[field] = value
    assert sanitize_delivery_receipt(receipt) is None


def test_recorded_receipt_requires_exact_binding_and_terminal_classification():
    assert recorded_delivery_receipt(recorded_row())
    assert recorded_delivery_receipt(recorded_row("completed"))
    for changes in ({"stage": []}, {"scope_reserved": 1}, {"working_revision": True},
                    {"context_fingerprint": digest("different")}, {"context_generation": 4},
                    {"output_event_refs": [{"event_id": "other", "revision": 2}]},
                    {"revision": 7}, {"failure_code": "cancelled"}):
        assert not recorded_delivery_receipt(recorded_row() | changes)
    receipt = failed_receipt(recorded_row()["delivery_receipt"], ["working_revision_changed"])
    failed = recorded_row() | {"stage": "failed", "scope_reserved": False, "failure_code": "commit_failed",
                               "delivery_receipt": receipt}
    assert confirmed_delivery_receipt(failed)
    assert not confirmed_delivery_receipt(recorded_row())
    receipt = failed_receipt(receipt, ["source_unavailable"])
    assert receipt["failure_code"] == "source_unavailable"
    assert sanitize_delivery_receipt(receipt) == receipt


def ready(turns, context=None):
    turns.prepare(context_refs=[context] if context else [])
    turns.advance("generating")
    turns.record_output("Original draft")
    return turns.advance("delivering")


def forget_input(turns):
    return turns.action("forget", {
        "idempotency_key": uid(), "selector": {"kind": "events", "ids": [turns.row["input_event_refs"][0]["event_id"]]},
        "mode": "erase_content", "expected_scope_generation": turns.store.status(turns.principal, turns.brain)["scope_generation"],
    })


def change_working(turns):
    item = {"key": "new-host-state", "value": "Keep the new state", "knowledge_state": "unknown",
            "evidence_refs": [], "expires_at": None, "updated_at": now()}
    turns.action("commit", {"turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
                            "delivered_event_ids": [], "proposed_items": [item], "proposed_open_loops": [],
                            "assertion_proposals": []})
    return item


def working(turns):
    with turns.database.transaction(turns.principal) as conn:
        result = conn.execute("SELECT document FROM memory_working_states WHERE tenant_id=%s AND brain_id=%s "
                              "AND conversation_id=%s AND branch_id=%s",
                              (turns.principal.tenant_id, turns.brain, turns.conversation, turns.branch)).fetchone()
        return result["document"] if result else None


@pytest.mark.integration
@pytest.mark.parametrize("change", ["erased", "corrected"])
def test_receipt_after_source_invalidation_never_resurrects_a_draft(turns, change):
    context = turns.observe("Earlier source", source_key="context", conversation="past")
    ready(turns, context)
    original = copy.deepcopy(turns.row)
    if change == "erased":
        forget_input(turns)
    else:
        turns.observe("Corrected source", source_key="context", revision=2, conversation="past")
    before = turns.store.status(turns.principal, turns.brain)["durable_sequence"]
    row = turns.record_output("Already delivered 中文", "partially_delivered")
    assert row["stage"] == "failed" and row["failure_code"] == "source_unavailable"
    assert row["scope_reserved"] is False and row["lease_active"] is False
    assert row["output_event_refs"] == original["output_event_refs"]
    assert row["delivery_receipt"]["disposition"] == "content_withheld"
    assert row["delivery_receipt"]["authorization"]["turn_revision"] == original["revision"]
    assert confirmed_delivery_receipt(row)
    assert turns.store.status(turns.principal, turns.brain)["durable_sequence"] == before
    with turns.database.transaction(turns.principal) as conn:
        outputs = conn.execute("SELECT revision,document,retired,deleted FROM memory_events WHERE tenant_id=%s "
                               "AND brain_id=%s AND id=%s", (turns.principal.tenant_id, turns.brain,
                                                             original["output_event_refs"][0]["event_id"])).fetchall()
    assert len(outputs) == 1 and outputs[0]["revision"] == 1
    assert outputs[0]["retired"] or outputs[0]["deleted"]
    if change == "erased":
        assert outputs[0]["document"] is None
    assert "Already delivered" not in json.dumps(row)


@pytest.mark.integration
@pytest.mark.parametrize("change", ["working", "generation"])
def test_stale_but_available_delivery_is_captured_without_committing_old_state(turns, change):
    turns.observe("Unrelated source", source_key="other", conversation="other")
    ready(turns)
    item = change_working(turns) if change == "working" else None
    if change == "generation":
        turns.observe("Changed unrelated source", source_key="other", revision=2, conversation="other")
    row = turns.record_output("What the host actually sent", "delivered")
    assert confirmed_delivery_receipt(row)
    assert row["failure_code"] == ("commit_failed" if change == "working" else "context_stale")
    assert row["delivery_receipt"]["disposition"] == "content_recorded"
    assert turns.call("sources")["output_events"][0]["observed"]["content"]["text"] == "What the host actually sent"
    if item:
        assert working(turns)["revision"] == 1 and working(turns)["items"] == [item]
    else:
        assert working(turns) is None


@pytest.mark.integration
def test_delivery_replay_survives_erasure_but_rejects_different_bytes_or_state(turns):
    ready(turns)
    params = {"lease_token": turns.token, "expected_revision": turns.row["revision"],
              "text": "Sent output", "delivery_state": "delivered"}
    turns.row = turns.call("record_output", **params)
    forget_input(turns)
    row = turns.call("finish", lease_token=turns.token, expected_revision=turns.row["revision"])
    assert confirmed_delivery_receipt(row)
    assert turns.call("record_output", **params) == row
    for changed in ({"text": "Different output"}, {"delivery_state": "partially_delivered"}):
        with pytest.raises(DomainError) as error:
            turns.call("record_output", **(params | changed))
        assert error.value.code == "idempotency_conflict"
    with pytest.raises(DomainError) as error:
        turns.call("record_output", **(params | {"text": "Original draft", "delivery_state": "draft"}))
    assert error.value.code == "source_unavailable"


@pytest.mark.integration
@pytest.mark.parametrize("change", ["expired", "token", "revision", "scope"])
def test_new_receipt_requires_current_authority_even_after_host_delivery(turns, change):
    ready(turns)
    params = {"lease_token": turns.token, "expected_revision": turns.row["revision"],
              "text": "Sent output", "delivery_state": "delivered"}
    if change == "expired":
        turns.expire()
    elif change == "token":
        params["lease_token"] = "wrong-token"
    elif change == "revision":
        params["expected_revision"] -= 1
    else:
        with turns.database.transaction(turns.principal) as conn:
            conn.execute("UPDATE memory_runtime_turns SET scope_reserved=false WHERE tenant_id=%s AND brain_id=%s "
                         "AND turn_id=%s", (turns.principal.tenant_id, turns.brain, turns.turn_id))
    with pytest.raises(DomainError):
        turns.call("record_output", **params)
    row = turns.call("get")
    assert row["stage"] == "delivering" and row["delivery_receipt"] is None
    assert row["output_event_refs"][0]["revision"] == 1


@pytest.mark.integration
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("change", ["working", "source"])
def test_finish_closes_stale_delivery_and_preserves_evidence_without_rebasing(turns, legacy, change):
    if legacy:
        turns.deliver()
    else:
        ready(turns)
        turns.record_output("Sent output", "delivered")
    original_refs = copy.deepcopy(turns.row["output_event_refs"])
    item = change_working(turns) if change == "working" else None
    if change == "source":
        forget_input(turns)
    row = turns.call("finish", lease_token=turns.token, expected_revision=turns.row["revision"])
    assert row["stage"] == "failed" and row["scope_reserved"] is False
    assert row["failure_code"] == ("commit_failed" if change == "working" else "source_unavailable")
    assert row["output_event_refs"] == original_refs
    assert (row["delivery_receipt"] is None) is legacy
    if item:
        assert working(turns)["revision"] == 1 and working(turns)["items"] == [item]
    else:
        assert working(turns) is None


@pytest.mark.integration
@pytest.mark.parametrize("legacy", [False, True])
def test_direct_failure_cannot_relabel_delivered_content_as_cancelled(turns, legacy):
    if legacy:
        turns.deliver()
    else:
        ready(turns)
        turns.record_output("Sent output", "delivered")
    with pytest.raises(DomainError) as error:
        turns.call("fail", lease_token=turns.token, expected_revision=turns.row["revision"], failure_code="cancelled")
    assert error.value.code == "stage_conflict"
    assert turns.call("get")["stage"] == "delivered"


@pytest.mark.integration
@pytest.mark.parametrize("after_capture", [False, True])
def test_invalidated_orientation_cannot_erase_a_receipt_or_rebase_old_working_state(turns, monkeypatch, after_capture):
    ready(turns)
    if after_capture:
        turns.record_output("Sent output", "delivered")

    def changed(*args, **kwargs):
        raise DomainError("context_stale", "The original skip proof no longer applies.")

    monkeypatch.setattr(turns.store, "_check_orientation_route", changed)
    row = (turns.call("finish", lease_token=turns.token, expected_revision=turns.row["revision"])
           if after_capture else turns.record_output("Sent output", "delivered"))
    assert confirmed_delivery_receipt(row) and row["failure_code"] == "context_stale"
    assert row["delivery_receipt"]["stale_reasons"] == ["orientation_changed"]
    assert turns.call("sources")["output_events"][0]["observed"]["content"]["text"] == "Sent output"
    assert working(turns) is None


@pytest.mark.integration
@pytest.mark.parametrize("change", ["generation", "orientation"])
def test_actual_orientation_snapshot_is_not_rebound_after_postcallback_invalidation(learning, change):
    learning.observe("Unrelated earlier record", conversation="other", source_key="other")
    original = []

    def delivered(request):
        row = runtime.journal.get(request.turn_id)
        original.append(learning.job(row["orientation_job_id"])["result"])
        if change == "generation":
            learning.observe("Changed unrelated record", conversation="other", source_key="other", revision=2)
        else:
            with learning.database.transaction(learning.principal) as conn:
                conn.execute("UPDATE memory_jobs SET state='cancelled' WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                             (learning.principal.tenant_id, learning.brain, row["orientation_job_id"]))
        return DeliveryReceipt("delivered")

    runtime = BrainRuntime(learning.memory, lambda _: "The host sent this", deliver=delivered)
    with pytest.raises(RuntimeBlocked, match="context_stale"):
        runtime.respond("I need a study plan now.", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert confirmed_delivery_receipt(row)
    assert row["delivery_receipt"]["disposition"] == "content_recorded"
    assert learning.job(row["orientation_job_id"])["result"] == original[0]
    assert learning.working() is None
