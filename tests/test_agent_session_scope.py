from unittest.mock import Mock

import httpx
import pytest
from test_agent_session import live_local as live_local
from test_agent_session import mocked as mocked
from test_agent_session import session, turn_id
from test_runtime_delivery_recovery import receipt_row, response_error

from membrain.agent_session import SessionError, open_agent
from membrain.helpers import digest
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked


def test_new_pending_clears_only_after_explicit_scope_rejection_and_confirmed_absence(mocked):
    mocked.runtime.respond.side_effect = RuntimeBlocked("scope_conflict", turn_id(mocked.profile))
    with session(mocked) as agent:
        with pytest.raises(RuntimeBlocked, match="scope_conflict"):
            agent.respond("Message", message_id="one")
        assert agent.pending is None
    assert mocked.runtime.journal.get.call_count == 2
    mocked.runtime.journal.claim.assert_not_called()
    mocked.runtime.journal.fail.assert_not_called()


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("Unknown begin outcome"), response_error("scope_conflict"),
    RuntimeBlocked("lease_lost", "turn-" + "a" * 40),
])
def test_ambiguous_or_unbound_errors_do_not_clear_pending(mocked, failure):
    mocked.runtime.respond.side_effect = failure
    with session(mocked) as agent:
        with pytest.raises(type(failure)):
            agent.respond("Message", message_id="one")
        assert agent.pending == {"message_id": "one", "turn_id": turn_id(mocked.profile)}


def test_scope_rejection_does_not_clear_if_absence_recheck_fails(mocked):
    original = mocked.runtime.journal.get.side_effect
    calls = 0

    def get(identifier):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise httpx.ConnectError("Absence could not be confirmed")
        return original(identifier)

    mocked.runtime.journal.get.side_effect = get
    mocked.runtime.respond.side_effect = RuntimeBlocked("scope_conflict", turn_id(mocked.profile))
    with session(mocked) as agent:
        with pytest.raises(httpx.ConnectError):
            agent.respond("Message", message_id="one")
        assert agent.pending["message_id"] == "one"


def test_callback_cannot_clear_a_started_turn_by_raising_scope_conflict(mocked):
    original = mocked.runtime.respond.side_effect

    def respond(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeBlocked("scope_conflict", turn_id(mocked.profile))

    mocked.runtime.respond.side_effect = respond
    with session(mocked) as agent:
        with pytest.raises(RuntimeBlocked, match="scope_conflict"):
            agent.respond("Message", message_id="one")
        assert agent.pending["message_id"] == "one"


def make_uncaptured_pending(mocked, agent):
    mocked.runtime.respond.side_effect = httpx.ConnectError("Begin outcome unknown")
    with pytest.raises(httpx.ConnectError):
        agent.respond("Original text", message_id="one")
    return agent.pending


def test_existing_uncaptured_pending_requires_explicit_cancel_after_scope_rejection(mocked):
    with session(mocked) as agent:
        pending = make_uncaptured_pending(mocked, agent)
        mocked.runtime.respond.side_effect = RuntimeBlocked("scope_conflict", pending["turn_id"])
        with pytest.raises(RuntimeBlocked, match="scope_conflict"):
            agent.respond("Original text", message_id="one")
        assert agent.pending == pending
        mocked.runtime.journal.begin.side_effect = response_error()
        assert agent.cancel(turn_id=pending["turn_id"]) == {
            "turn_id": pending["turn_id"], "stage": "not_started", "failure_code": "scope_conflict",
        }
        assert agent.pending is None
    mocked.runtime.journal.claim.assert_not_called()
    mocked.runtime.journal.fail.assert_not_called()


@pytest.mark.parametrize("outcome", ["offline", "different_error", "appeared"])
def test_cancel_does_not_discard_uncertain_or_concurrently_created_turn(mocked, outcome):
    with session(mocked) as agent:
        pending = make_uncaptured_pending(mocked, agent)

        def begin(**fields):
            if outcome == "offline":
                raise httpx.ConnectError("No confirmed rejection")
            if outcome == "appeared":
                mocked.rows[fields["turn_id"]] = fields | {"brain_id": mocked.profile.brain_id}
            raise response_error("stage_conflict" if outcome == "different_error" else "scope_conflict")

        mocked.runtime.journal.begin.side_effect = begin
        with pytest.raises(httpx.HTTPError):
            agent.cancel(turn_id=pending["turn_id"])
        assert agent.pending == pending
    mocked.runtime.journal.claim.assert_not_called()
    mocked.runtime.journal.fail.assert_not_called()


def install_failed_receipt(mocked, identifier, *, damage=None):
    original = mocked.rows[identifier]
    row = receipt_row() | {key: original[key] for key in (
        "turn_id", "brain_id", "conversation_id", "branch_id", "input_fingerprint",
    )}
    if damage == "missing":
        row.pop("delivery_receipt")
    elif damage == "hash":
        row["delivery_receipt"]["content_sha256"] = "invalid"
    elif damage == "binding":
        row["delivery_receipt"]["authorization"]["context_fingerprint"] = "0" * 64
    elif damage == "scope_reserved":
        row["scope_reserved"] = True
    elif damage == "failure":
        row["failure_code"] = "delivery_uncertain"
    mocked.rows[identifier] = row
    mocked.stage = "failed"
    return row


def test_exact_confirmed_failure_clears_pending_without_claiming_success(mocked):
    original = mocked.runtime.respond.side_effect

    def respond(*args, **kwargs):
        result = original(*args, **kwargs)
        install_failed_receipt(mocked, result.turn_id)
        raise RuntimeBlocked("source_unavailable", result.turn_id)

    mocked.runtime.respond.side_effect = respond
    with session(mocked) as agent:
        with pytest.raises(RuntimeBlocked, match="source_unavailable"):
            agent.respond("Message", message_id="one")
        assert agent.pending is None


@pytest.mark.parametrize("damage", ["missing", "hash", "binding", "scope_reserved", "failure"])
def test_untrusted_or_unknown_failed_outcome_never_clears_pending(mocked, damage):
    with session(mocked) as agent:
        first = agent.respond("Message", message_id="one")
        install_failed_receipt(mocked, first.turn_id, damage=damage)
        mocked.runtime.resume.side_effect = RuntimeBlocked("source_unavailable", first.turn_id)
        with pytest.raises(RuntimeBlocked, match="source_unavailable"):
            agent.resume()
        assert agent.pending["turn_id"] == first.turn_id


@pytest.mark.parametrize("operation", ["resume", "respond"])
def test_known_failed_receipt_cleanup_ignores_changed_generation_configuration(mocked, operation):
    with session(mocked) as agent:
        first = agent.respond("Message", message_id="one")
    install_failed_receipt(mocked, first.turn_id)
    mocked.runtime.resume.side_effect = RuntimeBlocked("source_unavailable", first.turn_id)
    mocked.runtime.respond.side_effect = RuntimeBlocked("source_unavailable", first.turn_id)
    with session(mocked, generator_fingerprint="changed-model", instructions="Changed instructions") as agent:
        with pytest.raises(RuntimeBlocked, match="source_unavailable"):
            agent.resume() if operation == "resume" else agent.respond("Message", message_id="one")
        assert agent.pending is None


def test_failed_receipt_cleanup_requires_fresh_source_identity(mocked):
    with session(mocked) as agent:
        first = agent.respond("Message", message_id="one")

        def resume(_):
            install_failed_receipt(mocked, first.turn_id)
            mocked.rows[first.turn_id]["input_fingerprint"] = digest("Different input")
            raise RuntimeBlocked("source_unavailable", first.turn_id)

        mocked.runtime.resume.side_effect = resume
        with pytest.raises(SessionError, match="session_state_invalid"):
            agent.resume()
        assert agent.pending["turn_id"] == first.turn_id


@pytest.mark.integration
def test_real_scope_conflict_leaves_local_session_ready_after_other_host_finishes(live_local):
    generate, deliver = Mock(return_value="Local answer"), Mock(return_value=DeliveryReceipt("delivered"))
    with open_agent(live_local.directory, conversation_id="shared", generate=generate,
                    generator_fingerprint="test-model", deliver=deliver, start_service=False) as agent:
        other = BrainRuntime(agent._runtime.memory, lambda _: "Remote draft")
        draft = other.respond("Remote message", message_id="remote", conversation_id="shared")
        before = other.journal.status()
        with pytest.raises(RuntimeBlocked, match="scope_conflict"):
            agent.respond("Local message", message_id="local")
        assert agent.pending is None
        assert other.journal.status() == before
        generate.assert_not_called()
        deliver.assert_not_called()
        other.deliver = lambda _: DeliveryReceipt("delivered")
        assert other.resume(draft.turn_id).stage == "completed"
        assert agent.respond("Local message", message_id="local").stage == "completed"
        assert agent.pending is None
    assert generate.call_count == deliver.call_count == 1


@pytest.mark.integration
def test_real_confirmed_stale_delivery_clears_pending_and_allows_next_turn(live_local):
    sends = []
    generate = Mock(return_value="Answer")
    with open_agent(live_local.directory, conversation_id="stale-receipt", generate=generate,
                    generator_fingerprint="test-model", start_service=False) as agent:
        def send(request):
            sends.append(request)
            agent._runtime.memory.commit("concurrent", conversation_id="stale-receipt", branch_id="main",
                                         expected_working_revision=0)
            return DeliveryReceipt("delivered")

        agent._runtime.deliver = send
        with pytest.raises(RuntimeBlocked, match="commit_failed"):
            agent.respond("First message", message_id="one")
        assert agent.pending is None
        agent._runtime.deliver = lambda request: sends.append(request) or DeliveryReceipt("delivered")
        assert agent.respond("Next message", message_id="two").stage == "completed"
    assert generate.call_count == len(sends) == 2
