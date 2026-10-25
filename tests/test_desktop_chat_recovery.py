import io
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from test_desktop_chat import arguments
from test_desktop_chat import fake_session as fake_session
from test_desktop_turns import TURN, install

from agent_memory import desktop_chat
from agent_memory.chat_provider import ChatProviderError
from agent_memory.runtime import RuntimeBlocked

PENDING = "turn-" + "a" * 40
CANDIDATE = "turn-" + "b" * 40


def run_failure(fake_session, error):
    fake_session.respond.side_effect = error
    with pytest.raises(SystemExit) as caught:
        desktop_chat.run(arguments("chat", "--directory", str(fake_session.profile.directory),
                                   "--conversation", "room", "--branch", "branch", "--message", "Hello",
                                   "--message-id", "input-1"))
    assert caught.value.code == 1
    fake_session.respond.assert_called_once_with("Hello", message_id="input-1")
    fake_session.resume.assert_not_called()
    fake_session.cancel.assert_not_called()


@pytest.mark.parametrize("status,advice", [(401, "API key"), (403, "permissions"), (404, "endpoint"),
                                         (429, "rate or quota"), (500, "availability")])
def test_provider_http_failure_preserves_safe_status_and_persisted_pending_id(fake_session, capsys, status, advice):
    fake_session.pending = {"turn_id": PENDING, "message_id": "private-message-id", "text": "private-source"}
    run_failure(fake_session, ChatProviderError("http_error", status_code=status))
    output = capsys.readouterr()
    assert not output.out and "http_error" in output.err
    assert f"Model provider HTTP {status}" in output.err and advice in output.err
    assert "Local pending turn: " + PENDING in output.err
    assert "private-" not in output.err


@pytest.mark.parametrize("code", ["timeout", "transport_error"])
def test_unknown_provider_outcome_stays_unknown_and_never_retries(fake_session, capsys, code):
    fake_session.pending = {"turn_id": PENDING}
    run_failure(fake_session, ChatProviderError(code))
    output = capsys.readouterr()
    assert code in output.err and "outcome may be unknown" in output.err and PENDING in output.err
    assert "Model provider HTTP" not in output.err


def test_missing_pending_does_not_invent_turn_identity(fake_session, capsys):
    fake_session.pending = None
    run_failure(fake_session, ChatProviderError("http_error", status_code=401))
    output = capsys.readouterr()
    assert "401" in output.err and "Local pending turn" not in output.err


def test_scope_conflict_reports_actual_remote_turn_not_rejected_candidate(fake_session, monkeypatch, capsys):
    opened, requests = install(monkeypatch, {"turn": TURN})
    run_failure(fake_session, RuntimeBlocked("scope_conflict", CANDIDATE))
    output = capsys.readouterr()
    assert "scope_conflict" in output.err and "turn-old (generated)" in output.err
    assert CANDIDATE not in output.err and "Local pending turn" not in output.err
    assert opened == [(fake_session.profile.directory, False, 2.0)]
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("response,failure", [({"turn": None}, None), (None, "timeout"),
                                            ({"turn": {**TURN, "brain_id": "wrong"}}, None)])
def test_missing_or_unavailable_blocker_keeps_original_scope_error(fake_session, monkeypatch, capsys, response, failure):
    _, requests = install(monkeypatch, response, failure)
    run_failure(fake_session, RuntimeBlocked("scope_conflict", CANDIDATE))
    output = capsys.readouterr()
    assert "scope_conflict" in output.err and CANDIDATE not in output.err and "turn-old" not in output.err
    assert "private-" not in output.err and len(requests) == 1
    assert "No unfinished turn" in output.err if response == {"turn": None} else "Scope inspection unavailable" in output.err


def test_pending_read_failure_preserves_original_provider_error(monkeypatch, capsys):
    class Unreadable:
        @property
        def pending(self):
            raise OSError("private-profile-path")

    operation = Mock(side_effect=ChatProviderError("http_error", status_code=401))
    args = SimpleNamespace(directory=None, conversation="room", branch="branch")
    assert desktop_chat._call(operation, lambda error: desktop_chat._recovery_notice(error, Unreadable(), args)) is None
    output = capsys.readouterr()
    assert "http_error" in output.err and "401" in output.err and "could not be inspected" in output.err
    assert "private-" not in output.err
    operation.assert_called_once()


def test_transport_failure_reports_pending_without_error_body(fake_session, capsys):
    fake_session.pending = {"turn_id": PENDING}
    run_failure(fake_session, httpx.ConnectError("https://private-key@private-host"))
    output = capsys.readouterr()
    assert "memory_service_unavailable" in output.err and PENDING in output.err and "private-" not in output.err


def test_error_diagnostics_survive_closed_stderr_without_retry(monkeypatch):
    stream = io.StringIO()
    stream.close()
    operation = Mock(side_effect=ChatProviderError("timeout"))
    with monkeypatch.context() as patch:
        patch.setattr(desktop_chat.sys, "stderr", stream)
        assert desktop_chat._call(operation, Mock(side_effect=ValueError("private-diagnostic"))) is None
    operation.assert_called_once()
