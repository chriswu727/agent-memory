import json
from contextlib import contextmanager

import httpx
import pytest
from test_desktop_chat import arguments

from agent_memory import desktop_budget, desktop_cli
from agent_memory.client import MemoryClient

CHANGE = ("configure-budget", "--request-id", "allowance-1", "--expected-revision", "2",
          "--window-seconds", "3600", "--max-model-calls", "200", "--max-input-bytes", "4000000")


def install_client(monkeypatch, handler):
    opened = []

    @contextmanager
    def open_client(directory, *, start_service):
        opened.append((directory, start_service))
        with MemoryClient("http://memory.test", "private-key", "brain",
                          transport=httpx.MockTransport(handler)) as memory:
            yield memory

    monkeypatch.setattr(desktop_budget, "open_client", open_client)
    return opened


def test_budget_reads_without_starting_a_worker(monkeypatch, capsys):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"revision": 2, "used": {"model_calls": 1}})

    opened = install_client(monkeypatch, handle)
    desktop_cli.dispatch(arguments("budget"))
    assert json.loads(capsys.readouterr().out)["used"]["model_calls"] == 1
    assert opened == [(None, False)]
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].url.path == "/v1/brains/brain/runtime/cognitive-budget"


def test_configuration_preserves_explicit_change_identity_and_policy(monkeypatch, capsys):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"request_id": "allowance-1", "configuration": {"revision": 3}})

    opened = install_client(monkeypatch, handle)
    desktop_cli.dispatch(arguments(*CHANGE))
    assert json.loads(capsys.readouterr().out)["configuration"]["revision"] == 3
    assert opened == [(None, False)]
    assert len(calls) == 1 and calls[0].method == "PUT"
    assert json.loads(calls[0].content) == {
        "request_id": "allowance-1", "expected_revision": 2,
        "policy": {"window_seconds": 3600, "max_model_calls": 200, "max_input_bytes": 4_000_000},
    }


@pytest.mark.parametrize("failure", [401, 404, 409, 422, 503, 500, "timeout"])
def test_errors_are_redacted_and_uncertain_changes_are_not_retried(monkeypatch, capsys, failure):
    calls = []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-network-details", request=request)
        return httpx.Response(failure, json={"error": "private-server-content"})

    install_client(monkeypatch, handle)
    with pytest.raises(SystemExit) as caught:
        desktop_cli.dispatch(arguments(*CHANGE))
    assert caught.value.code == 1
    result = capsys.readouterr()
    assert not result.out and "private-" not in result.err
    assert len(calls) == 1
    if failure == "timeout":
        assert "reuse the same request ID, revision, and policy" in result.err


@pytest.mark.parametrize("flag,value", [("--request-id", " "), ("--expected-revision", "-1"),
                                        ("--window-seconds", "2592001"), ("--max-model-calls", "1000001"),
                                        ("--max-input-bytes", "1000000000001")])
def test_invalid_changes_are_rejected_before_connecting(monkeypatch, capsys, flag, value):
    options = list(CHANGE)
    options[options.index(flag) + 1] = value
    opened = install_client(monkeypatch, lambda _: pytest.fail("No request expected"))
    with pytest.raises(SystemExit):
        desktop_cli.dispatch(arguments(*options))
    assert opened == []
    assert "configuration_error" in capsys.readouterr().err
