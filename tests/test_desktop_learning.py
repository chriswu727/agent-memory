import json
from contextlib import contextmanager

import httpx
import pytest
from test_desktop_chat import arguments

from membrain import desktop_cli, desktop_learning
from membrain.client import MemoryClient


def install_client(monkeypatch, handler):
    opened = []

    @contextmanager
    def open_client(directory, *, start_service):
        opened.append((directory, start_service))
        with MemoryClient("http://memory.test", "private-key", "brain",
                          transport=httpx.MockTransport(handler)) as memory:
            yield memory

    monkeypatch.setattr(desktop_learning, "open_client", open_client)
    return opened


@pytest.mark.parametrize("extra,suffix", [([], "learning-progress"), (["--job", "blocked"], "jobs/blocked")])
def test_learning_status_reads_without_starting_worker(monkeypatch, capsys, extra, suffix):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"blocked_count": 1})

    opened = install_client(monkeypatch, handle)
    desktop_cli.dispatch(arguments("learning-status", *extra))
    assert json.loads(capsys.readouterr().out) == {"blocked_count": 1}
    assert opened == [(None, False)]
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/v1/brains/brain/runtime/" + suffix


def test_cli_retry_schedules_once_with_required_identity(monkeypatch, capsys):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "recovery", "state": "queued"})

    opened = install_client(monkeypatch, handle)
    desktop_cli.dispatch(arguments("retry-learning", "blocked", "--request-id", "recover-1", "--expected-fence", "2"))
    assert json.loads(capsys.readouterr().out) == {"id": "recovery", "state": "queued"}
    assert opened == [(None, False)]
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {"request_id": "recover-1", "expected_fence": 2}


@pytest.mark.parametrize("failure", [401, 404, 409, 422, 503, 500, "timeout"])
def test_cli_errors_are_safe_and_never_automatically_retry(monkeypatch, capsys, failure):
    calls = []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-network-details", request=request)
        return httpx.Response(failure, json={"error": "private-server-content"})

    install_client(monkeypatch, handle)
    with pytest.raises(SystemExit) as caught:
        desktop_cli.dispatch(arguments("retry-learning", "blocked", "--request-id", "recover-1", "--expected-fence", "2"))
    assert caught.value.code == 1
    result = capsys.readouterr()
    assert not result.out
    assert "private-" not in result.err
    assert len(calls) == 1
    if failure == "timeout":
        assert "reuse the same request ID and fence" in result.err


@pytest.mark.parametrize("options", [[], ["--request-id", "r"], ["--expected-fence", "0"]])
def test_cli_retry_requires_explicit_identity_and_fence(options):
    with pytest.raises(SystemExit):
        arguments("retry-learning", "blocked", *options)


@pytest.mark.parametrize("request_id,fence", [(" ", "0"), ("x" * 257, "0"), ("r", "-1"), ("r", str(2**63))])
def test_cli_rejects_invalid_retry_before_network(monkeypatch, capsys, request_id, fence):
    opened = install_client(monkeypatch, lambda _: pytest.fail("No request expected"))
    with pytest.raises(SystemExit):
        desktop_cli.dispatch(arguments("retry-learning", "blocked", "--request-id", request_id, "--expected-fence", fence))
    assert opened == []
    assert "configuration_error" in capsys.readouterr().err
