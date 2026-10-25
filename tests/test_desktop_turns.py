import copy
import json

import httpx
import pytest
from test_desktop_chat import arguments

from agent_memory import desktop_cli, desktop_turns
from agent_memory.client import MemoryClient
from agent_memory.local_profile import LocalProfileError

TURN = {"turn_id": "turn-old", "brain_id": "brain", "conversation_id": "room", "branch_id": "branch",
        "stage": "generated", "scope_reserved": True, "lease_active": False, "recovery_required": None}


def install(monkeypatch, response=None, failure=None):
    opened, requests = [], []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-network-location", request=request)
        return httpx.Response(failure or 200, json=response if failure is None else {"error": "private-error-body"})

    def connect(directory, *, start_service, timeout):
        opened.append((directory, start_service, timeout))
        return MemoryClient("http://memory.test", "private-key", "brain", timeout=timeout,
                            transport=httpx.MockTransport(handle))

    monkeypatch.setattr(desktop_turns, "open_client", connect)
    return opened, requests


@pytest.mark.parametrize("present", [False, True])
def test_turn_status_uses_one_read_only_scope_lookup_and_minimal_output(monkeypatch, capsys, present):
    response = {"turn": {**TURN, "private": "private-content", "lease_token": "private-lease"} if present else None,
                "private": "private-top-level"}
    opened, requests = install(monkeypatch, response)
    desktop_cli.dispatch(arguments("turn-status", "--conversation", "room", "--branch", "branch"))
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result == {"conversation_id": "room", "branch_id": "branch", "turn": {
        key: TURN[key] for key in ("turn_id", "stage", "scope_reserved", "lease_active", "recovery_required")
    } if present else None}
    assert "private-" not in output.out + output.err
    assert opened == [(None, False, 2.0)]
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].url.path == "/v1/brains/brain/runtime/scope"
    assert dict(requests[0].url.params) == {"conversation_id": "room", "branch_id": "branch"}
    assert requests[0].extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.0)


def test_turn_status_defaults_match_chat_scope(monkeypatch, capsys):
    _, requests = install(monkeypatch, {"turn": None})
    desktop_cli.dispatch(arguments("turn-status"))
    assert dict(requests[0].url.params) == {"conversation_id": "desktop-chat", "branch_id": "main"}
    assert json.loads(capsys.readouterr().out)["turn"] is None


@pytest.mark.parametrize("changes", [
    {"brain_id": "different"}, {"conversation_id": "different"}, {"branch_id": "different"},
    {"turn_id": "private\ntext"}, {"turn_id": []}, {"stage": "private-stage"}, {"stage": []},
    {"scope_reserved": 1}, {"lease_active": None}, {"recovery_required": "private-error"},
])
def test_invalid_scope_binding_or_metadata_is_rejected_without_private_output(monkeypatch, capsys, changes):
    row = copy.deepcopy(TURN)
    row.update(changes)
    _, requests = install(monkeypatch, {"turn": row})
    with pytest.raises(SystemExit) as caught:
        desktop_cli.dispatch(arguments("turn-status", "--conversation", "room", "--branch", "branch"))
    assert caught.value.code == 1 and len(requests) == 1
    output = capsys.readouterr()
    assert not output.out and "turn_status_unavailable" in output.err
    assert "private-" not in output.err


@pytest.mark.parametrize("failure", [401, 404, 409, 422, 500, "timeout"])
def test_turn_status_failure_never_retries_or_leaks_response(monkeypatch, capsys, failure):
    _, requests = install(monkeypatch, failure=failure)
    with pytest.raises(SystemExit) as caught:
        desktop_cli.dispatch(arguments("turn-status"))
    output = capsys.readouterr()
    assert caught.value.code == 1 and len(requests) == 1
    assert not output.out and "turn_status_unavailable" in output.err and "private-" not in output.err


@pytest.mark.parametrize("scope", ["", "white space", "control\nvalue", "x" * 257])
def test_invalid_scope_fails_before_connection(monkeypatch, capsys, scope):
    opened, _ = install(monkeypatch, {"turn": None})
    with pytest.raises(SystemExit):
        desktop_cli.dispatch(arguments("turn-status", "--conversation", scope))
    assert not opened and not capsys.readouterr().out


def test_unavailable_profile_does_not_claim_a_server_response(monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise LocalProfileError("private-local-path")

    monkeypatch.setattr(desktop_turns, "open_client", unavailable)
    with pytest.raises(SystemExit) as caught:
        desktop_cli.dispatch(arguments("turn-status"))
    output = capsys.readouterr()
    assert caught.value.code == 1 and not output.out
    assert "turn_status_unavailable" in output.err and "local connection configuration" in output.err
    assert "private-" not in output.err and "service returned" not in output.err
