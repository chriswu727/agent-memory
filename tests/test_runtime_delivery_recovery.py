import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from test_agent_session import live_local as live_local
from test_runtime import memory as memory

from membrain.agent_session import open_agent
from membrain.config import Settings
from membrain.delivery_receipts import receipt_for
from membrain.helpers import Principal, digest, now
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked, _Lease
from membrain.storage import Database


def response_error(code="scope_conflict", *, status=409, body=None):
    response = httpx.Response(status, json=body if body is not None else {"error": {"code": code}},
                              request=httpx.Request("POST", "http://memory/runtime/turns"))
    return httpx.HTTPStatusError("Request rejected", request=response.request, response=response)


def receipt_row(*, stage="failed", failure="source_unavailable", recorded=False):
    row = {
        "turn_id": "turn-" + "a" * 40, "brain_id": "brain", "conversation_id": "chat", "branch_id": "main",
        "input_fingerprint": digest("input"), "revision": 7, "fence": 1, "lease_token": "token",
        "context_fingerprint": digest("context"), "context_generation": 0, "working_revision": 0,
        "output_event_refs": [{"event_id": "draft", "revision": 1}], "sources_available": True,
        "stage": "delivering", "response_review_policy": None,
    }
    reasons = {"source_unavailable": ["source_unavailable"], "commit_failed": ["working_revision_changed"],
               "context_stale": ["context_generation_changed"], None: []}[failure]
    receipt = receipt_for(row, "Sent reply", "delivered", reasons=reasons, content_recorded=recorded)
    return row | {
        "stage": stage, "revision": 8, "failure_code": failure, "delivery_receipt": receipt,
        "scope_reserved": stage not in ("failed", "completed"),
        "output_event_refs": [{"event_id": "draft", "revision": 2 if recorded else 1}],
        "sources_available": failure != "source_unavailable",
    }


def test_scope_conflict_is_recognized_only_at_begin_before_any_runtime_effects():
    runtime = BrainRuntime(SimpleNamespace(brain_id="brain"), Mock(), deliver=Mock())
    runtime.journal = Mock()
    runtime.journal.begin.side_effect = response_error()
    runtime._run = Mock()
    with pytest.raises(RuntimeBlocked, match="scope_conflict") as error:
        runtime.respond("New message", message_id="two", conversation_id="chat")
    assert error.value.turn_id == "turn-" + digest(["brain", "chat", "main", "two"])[:40]
    runtime._run.assert_not_called()
    runtime.generate.assert_not_called()
    runtime.deliver.assert_not_called()


@pytest.mark.parametrize(("status", "body"), [
    (503, {"error": {"code": "scope_conflict"}}), (409, {"error": {"code": "lease_conflict"}}),
    (409, {"error": []}), (409, []), (409, {"error": {"code": ["scope_conflict"]}}),
])
def test_other_begin_failures_remain_ambiguous(status, body):
    runtime = BrainRuntime(SimpleNamespace(brain_id="brain"), Mock())
    runtime.journal = Mock()
    failure = response_error(status=status, body=body)
    runtime.journal.begin.side_effect = failure
    with pytest.raises(httpx.HTTPStatusError) as error:
        runtime.respond("Message", message_id="one", conversation_id="chat")
    assert error.value is failure


@pytest.mark.parametrize("heartbeat_error", [
    RuntimeBlocked("source_unavailable", "turn"), httpx.ConnectError("Renewal response lost"),
    RuntimeBlocked("lease_lost", "turn"),
])
def test_receipt_submission_rechecks_server_authority_despite_sticky_heartbeat_error(heartbeat_error):
    journal = Mock()
    row = receipt_row() | {"stage": "generated", "revision": 6, "delivery_receipt": None,
                          "sources_available": True}
    lease = _Lease(journal, row, 30)
    journal.renew.return_value = row
    journal.advance.return_value = row | {"stage": "delivering", "revision": 7}
    journal.record_output.return_value = receipt_row()

    def send(_):
        lease.error = heartbeat_error
        return DeliveryReceipt("delivered")

    runtime = BrainRuntime(SimpleNamespace(brain_id="brain"), Mock(), deliver=send)
    runtime._output = Mock(return_value="Sent reply")
    runtime._deliver(lease, {})
    journal.renew.assert_called_once()
    journal.record_output.assert_called_once_with(
        row["turn_id"], lease_token="token", expected_revision=7, text="Sent reply", delivery_state="delivered",
    )
    assert lease.row["stage"] == "failed" and lease.error is None and lease.stopped.is_set()


def test_receipt_never_bypasses_server_lease_rejection():
    journal = Mock()
    lease = _Lease(journal, receipt_row(), 30)
    lease.error = RuntimeBlocked("source_unavailable", lease.row["turn_id"])
    failure = response_error("lease_conflict")
    journal.record_output.side_effect = failure
    with pytest.raises(httpx.HTTPStatusError) as error:
        lease.record_output("Sent reply", "delivered")
    assert error.value is failure and lease.error is not None


def test_sticky_error_still_blocks_new_draft_storage():
    journal = Mock()
    lease = _Lease(journal, receipt_row(), 30)
    lease.error = RuntimeBlocked("source_unavailable", lease.row["turn_id"])
    with pytest.raises(RuntimeBlocked, match="lease_lost"):
        lease.record_output("Unsent reply", "draft")
    journal.record_output.assert_not_called()


def test_confirmed_failed_receipt_resumes_without_reading_erased_sources_or_callbacks():
    runtime = BrainRuntime(SimpleNamespace(brain_id="brain"), Mock(), deliver=Mock())
    runtime.journal = Mock()
    row = receipt_row()
    runtime.journal.get.return_value = row
    with pytest.raises(RuntimeBlocked, match="source_unavailable"):
        runtime.resume(row["turn_id"])
    runtime.journal.sources.assert_not_called()
    runtime.journal.claim.assert_not_called()
    runtime.generate.assert_not_called()
    runtime.deliver.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_persisted_delivery_can_reconcile_erased_sources_through_authoritative_finish(legacy):
    runtime = BrainRuntime(SimpleNamespace(brain_id="brain"), Mock(), deliver=Mock())
    runtime.journal = Mock()
    row = receipt_row(stage="delivered", failure=None, recorded=True) | {"sources_available": False}
    if legacy:
        row["delivery_receipt"] = None
    runtime.journal.get.return_value = row
    runtime.journal.claim.return_value = row | {"revision": 9, "fence": 2}
    runtime.journal.finish.return_value = receipt_row(recorded=True) | {"revision": 10, "fence": 2}
    with pytest.raises(RuntimeBlocked, match="source_unavailable"):
        runtime.resume(row["turn_id"])
    runtime.journal.finish.assert_called_once_with(row["turn_id"], lease_token="token", expected_revision=9)
    runtime.journal.sources.assert_not_called()
    runtime.journal.renew.assert_not_called()
    runtime.generate.assert_not_called()
    runtime.deliver.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_delivered_finish_submits_despite_sticky_source_error(legacy):
    journal = Mock()
    row = receipt_row(stage="delivered", failure=None, recorded=True)
    if legacy:
        row["delivery_receipt"] = None
    lease = _Lease(journal, row, 30)
    lease.error = RuntimeBlocked("source_unavailable", row["turn_id"])
    journal.finish.return_value = receipt_row(recorded=True)
    assert lease.finish()["stage"] == "failed"
    journal.finish.assert_called_once_with(row["turn_id"], lease_token="token", expected_revision=8)


@pytest.mark.integration
@pytest.mark.parametrize(("change", "code"), [
    ("working", "commit_failed"), ("unrelated", "context_stale"), ("source", "source_unavailable"),
])
def test_callback_changes_cannot_erase_the_known_delivery_outcome(memory, change, code):
    sends = []
    generate = Mock(return_value="Delivered private answer iris-486.")
    if change == "unrelated":
        memory.observe("Unrelated original", source_event_key="unrelated", producer_ref="receipt-test",
                       conversation_id="other", branch_id="main")

    def send(request):
        sends.append(request)
        row = runtime.journal.get(request.turn_id)
        if change == "working":
            memory.commit("concurrent-working", conversation_id="chat", branch_id="main",
                          expected_working_revision=row["working_revision"], proposed_items=[{
                              "key": "retained", "value": "Concurrent working note", "knowledge_state": "unknown",
                              "evidence_refs": [], "expires_at": None, "updated_at": now(),
                          }])
        elif change == "unrelated":
            memory.observe("Unrelated correction", source_event_key="unrelated", source_revision=2,
                           producer_ref="receipt-test", conversation_id="other", branch_id="main")
        else:
            memory.forget([row["input_event_refs"][0]["event_id"]], kind="events", mode="erase_content",
                          expected_scope_generation=runtime.journal.status()["scope_generation"])
        return DeliveryReceipt("delivered")

    runtime = BrainRuntime(memory, generate, deliver=send)
    with pytest.raises(RuntimeBlocked, match=code):
        runtime.respond("Private source iris-486", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "failed" and row["failure_code"] == code
    assert row["delivery_receipt"]["state"] == "delivered"
    assert row["delivery_receipt"]["disposition"] == (
        "content_withheld" if change == "source" else "content_recorded"
    )
    restarted = BrainRuntime(memory, Mock(), deliver=Mock())
    with pytest.raises(RuntimeBlocked, match=code):
        restarted.resume(row["turn_id"])
    restarted.generate.assert_not_called()
    restarted.deliver.assert_not_called()
    assert generate.call_count == len(sends) == 1
    if change == "source":
        assert "iris-486" not in json.dumps(memory.dashboard())
    else:
        actual = runtime.journal.sources(row["turn_id"])["output_events"][0]
        assert actual["observed"]["content"]["text"] == "Delivered private answer iris-486."
    if change == "working":
        prepared = memory.prepare("inspect", conversation_id="chat", branch_id="main")["result"]
        assert prepared["working_state"]["items"][0]["value"] == "Concurrent working note"


@pytest.mark.integration
def test_lost_stale_receipt_response_recovers_without_resending(memory, monkeypatch):
    sends = Mock(return_value=DeliveryReceipt("partially_delivered", "Actually sent."))

    def send(request):
        memory.commit("concurrent-working", conversation_id="chat", branch_id="main", expected_working_revision=0)
        return sends(request)

    runtime = BrainRuntime(memory, Mock(return_value="Actually sent. Unsent secret."), deliver=send)
    original = runtime.journal.record_output

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["delivery_state"] == "partially_delivered":
            raise httpx.ReadError("Receipt response lost")
        return result

    monkeypatch.setattr(runtime.journal, "record_output", lose_response)
    with pytest.raises(httpx.ReadError):
        runtime.respond("Hello", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "failed" and row["delivery_receipt"]["state"] == "partially_delivered"
    restarted = BrainRuntime(memory, Mock(), deliver=Mock(), delivery_is_idempotent=True)
    with pytest.raises(RuntimeBlocked, match="commit_failed"):
        restarted.resume(row["turn_id"])
    restarted.generate.assert_not_called()
    restarted.deliver.assert_not_called()
    assert sends.call_count == 1
    assert runtime.journal.sources(row["turn_id"])["output_events"][0]["observed"]["content"]["text"] == "Actually sent."


@pytest.mark.integration
@pytest.mark.parametrize(("change", "code"), [("source", "source_unavailable"), ("working", "commit_failed")])
def test_legacy_delivered_session_can_close_stale_turn_and_cancel_without_reading_sources(
    live_local, monkeypatch, change, code,
):
    generate, deliver = Mock(return_value="Delivered reply"), Mock(return_value=DeliveryReceipt("delivered"))
    with open_agent(live_local.directory, conversation_id="legacy-delivered", generate=generate,
                    generator_fingerprint="test-model", deliver=deliver, start_service=False) as agent:
        journal, memory = agent._runtime.journal, agent._runtime.memory
        with monkeypatch.context() as patch:
            patch.setattr(journal, "finish", Mock(side_effect=httpx.ConnectError("Stopped before finish")))
            with pytest.raises(httpx.ConnectError):
                agent.respond("First source", message_id="one")
        pending = agent.pending
        row = journal.get(pending["turn_id"])
        assert row["stage"] == "delivered"
        principal = Principal(live_local.tenant_id, live_local.principal_id)
        with Database(Settings.from_env().database_url).transaction(principal) as connection:
            connection.execute("UPDATE memory_runtime_turns SET delivery_receipt=NULL "
                               "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND turn_id=%s",
                               (principal.tenant_id, principal.principal_id, live_local.brain_id, pending["turn_id"]))
        if change == "source":
            memory.forget([row["input_event_refs"][0]["event_id"]], kind="events", mode="erase_content",
                          expected_scope_generation=journal.status()["scope_generation"])
        else:
            memory.commit("later-working", conversation_id="legacy-delivered", branch_id="main",
                          expected_working_revision=0)
        with monkeypatch.context() as patch:
            source_lookup = Mock(side_effect=AssertionError("Legacy cleanup must not need source text"))
            patch.setattr(journal, "sources", source_lookup)
            with pytest.raises(RuntimeBlocked, match=code):
                agent.resume()
            final = journal.get(pending["turn_id"])
            assert final["stage"] == "failed" and final["failure_code"] == code
            assert final["delivery_receipt"] is None and final["scope_reserved"] is False
            assert agent.pending == pending
            assert agent.cancel(turn_id=pending["turn_id"])["failure_code"] == code
            assert agent.pending is None
            source_lookup.assert_not_called()
        assert generate.call_count == deliver.call_count == 1
        assert agent.respond("Next source", message_id="two").stage == "completed"
    assert generate.call_count == deliver.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize("change", ["source", "working"])
def test_restarted_persisted_receipt_can_finish_after_later_context_invalidation(memory, monkeypatch, change):
    sends = Mock(return_value=DeliveryReceipt("delivered"))
    runtime = BrainRuntime(memory, Mock(return_value="Sent answer"), deliver=sends)
    original = runtime.journal.record_output

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["delivery_state"] == "delivered":
            raise httpx.ReadError("Receipt saved but response lost")
        return result

    monkeypatch.setattr(runtime.journal, "record_output", lose_response)
    with pytest.raises(httpx.ReadError):
        runtime.respond("Source", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "delivered" and row["delivery_receipt"]["failure_code"] is None
    if change == "source":
        memory.forget([row["input_event_refs"][0]["event_id"]], kind="events", mode="erase_content",
                      expected_scope_generation=runtime.journal.status()["scope_generation"])
    else:
        memory.commit("later-working", conversation_id="chat", branch_id="main", expected_working_revision=0)
    restarted = BrainRuntime(memory, Mock(), deliver=Mock())
    with pytest.raises(RuntimeBlocked, match="source_unavailable" if change == "source" else "commit_failed"):
        restarted.resume(row["turn_id"])
    saved = restarted.journal.get(row["turn_id"])
    assert saved["stage"] == "failed" and saved["delivery_receipt"]["state"] == "delivered"
    assert saved["scope_reserved"] is False
    restarted.generate.assert_not_called()
    restarted.deliver.assert_not_called()
    assert sends.call_count == 1
