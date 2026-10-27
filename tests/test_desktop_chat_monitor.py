import json

import httpx
import pytest
from test_desktop_chat import arguments
from test_desktop_chat import fake_session as fake_session

from membrain import desktop_chat, desktop_chat_monitor
from membrain.client import MemoryClient
from membrain.local_profile import configure_cognition
from membrain.runtime import TurnResult


def connect(monkeypatch, handle):
    opened, requests = [], []

    def factory(directory, *, start_service, timeout):
        opened.append((directory, start_service, timeout))

        def transport(request):
            requests.append(request)
            assert request.method == "GET"
            assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.0)
            return handle(request)

        return MemoryClient("http://memory.test", "private-key", "brain", timeout=timeout,
                            transport=httpx.MockTransport(transport))

    monkeypatch.setattr(desktop_chat_monitor, "open_client", factory)
    return opened, requests


@pytest.mark.parametrize("status", ["provider_unavailable", "budget_exhausted", "verification_unavailable",
                                    "invalid_response", "not_evaluated"])
def test_degraded_notice_reads_only_exact_turn_job_and_preserves_success(monkeypatch, capsys, status):
    def handle(request):
        if request.url.path.endswith("learning-progress"):
            return httpx.Response(200, json={"blocked_count": 2, "counts_complete": True, "private": "private-text"})
        if request.url.path.endswith("turns/turn"):
            return httpx.Response(200, json={"orientation_job_id": "orient:job"})
        return httpx.Response(200, json={"id": "orient:job", "kind": "orient", "state": "completed",
                                        "learning_summary": {"processing_status": status}, "private": "private-text"})

    opened, requests = connect(monkeypatch, handle)
    with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
        assert desktop_chat._perform(lambda: TurnResult("turn", "completed", "answer"), monitor)
    output = capsys.readouterr()
    assert not output.out and "2 blocked window" in output.err
    assert f"incomplete ({status})" in output.err and "reply was delivered" in output.err
    assert "private-" not in output.err
    assert opened == [(None, False, 2.0)]
    assert [r.url.path for r in requests] == ["/v1/brains/brain/runtime/learning-progress",
                                           "/v1/brains/brain/runtime/turns/turn",
                                           "/v1/brains/brain/runtime/jobs/orient:job"]


@pytest.mark.parametrize("status", ["completed", "partial", "no_sources", None])
def test_normal_and_skipped_orientation_emit_no_learning_success_claim(monkeypatch, capsys, status):
    def handle(request):
        if request.url.path.endswith("learning-progress"):
            return httpx.Response(200, json={"blocked_count": 0, "counts_complete": True})
        if request.url.path.endswith("turns/turn"):
            return httpx.Response(200, json={"orientation_job_id": "job" if status else None})
        return httpx.Response(200, json={"id": "job", "kind": "orient", "learning_summary": {"processing_status": status}})

    _, requests = connect(monkeypatch, handle)
    with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
        assert desktop_chat._perform(lambda: TurnResult("turn", "completed", "answer"), monitor)
    assert not capsys.readouterr().err
    assert len(requests) == (3 if status else 2)


@pytest.mark.parametrize("fault", ["timeout", "malformed", "unknown_status", "wrong_job"])
def test_diagnostic_failure_is_redacted_and_never_fails_delivered_answer(monkeypatch, capsys, fault):
    def handle(request):
        if fault == "timeout":
            raise httpx.ReadTimeout("private-timeout")
        if request.url.path.endswith("learning-progress"):
            return httpx.Response(200, json={"blocked_count": 0, "counts_complete": False})
        if request.url.path.endswith("turns/turn"):
            return httpx.Response(200, json={"orientation_job_id": "job"})
        return httpx.Response(200, json={"id": "private-other" if fault == "wrong_job" else "job", "kind": "orient",
                                        "learning_summary": {"processing_status": [] if fault == "malformed" else "private-status"}})

    _, requests = connect(monkeypatch, handle)
    with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
        assert desktop_chat._perform(lambda: TurnResult("turn", "completed", "answer"), monitor)
    output = capsys.readouterr()
    assert not output.out and "diagnostics unavailable" in output.err
    assert "private-" not in output.err
    assert len(requests) <= 3


def test_disabled_monitor_makes_no_connections(monkeypatch, capsys):
    opened, _ = connect(monkeypatch, lambda _: pytest.fail("No diagnostics expected"))
    with desktop_chat_monitor.learning_monitor(None, enabled=False) as monitor:
        assert monitor is None
    assert not opened and not capsys.readouterr().err


def test_multiple_turns_do_not_repeat_history_scan(monkeypatch):
    def handle(request):
        return httpx.Response(200, json={"blocked_count": 0, "counts_complete": True} if
                              request.url.path.endswith("learning-progress") else {"orientation_job_id": None})

    _, requests = connect(monkeypatch, handle)
    with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
        for turn in ("one", "two", "three"):
            assert desktop_chat._perform(lambda: TurnResult(turn, "completed", "answer"), monitor)
    assert sum(r.url.path.endswith("learning-progress") for r in requests) == 1
    assert all(not r.content or json.loads(r.content) is None for r in requests)


def test_actual_one_shot_chat_wiring_has_no_aggregate_scan(fake_session, monkeypatch, capsys):
    configure_cognition(fake_session.profile, "https://provider.test/v1", "model", "private-key")

    def handle(request):
        if request.url.path.endswith("turns/turn-test"):
            return httpx.Response(200, json={"orientation_job_id": "job"})
        assert request.url.path.endswith("jobs/job")
        return httpx.Response(200, json={"id": "job", "kind": "orient",
                                        "learning_summary": {"processing_status": "provider_unavailable"}})

    _, requests = connect(monkeypatch, handle)
    desktop_chat.run(arguments("chat", "--directory", str(fake_session.profile.directory),
                               "--message", "Hello", "--message-id", "message-1"))
    fake_session.respond.assert_called_once_with("Hello", message_id="message-1")
    output = capsys.readouterr()
    assert not output.out and "provider_unavailable" in output.err
    assert len(requests) == 2


def test_diagnostic_connection_or_close_failure_does_not_escape(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("private-connection")

    monkeypatch.setattr(desktop_chat_monitor, "open_client", fail)
    with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
        assert monitor is None
    assert "private-" not in capsys.readouterr().err

    class BrokenClose:
        close = fail

    monkeypatch.setattr(desktop_chat_monitor, "open_client", lambda *args, **kwargs: BrokenClose())
    with desktop_chat_monitor.learning_monitor(None, enabled=True, startup=False):
        pass
    output = capsys.readouterr()
    assert "diagnostics unavailable" in output.err and "private-" not in output.err


@pytest.mark.parametrize("stream_error", [BrokenPipeError, UnicodeError, ValueError])
def test_closed_diagnostic_stream_never_invalidates_confirmed_delivery(monkeypatch, stream_error):
    class ClosedStream:
        def write(self, value):
            raise stream_error("private-stream-error")

    def handle(request):
        if request.url.path.endswith("learning-progress"):
            return httpx.Response(200, json={"blocked_count": 1, "counts_complete": False})
        raise httpx.ReadTimeout("private-timeout")

    connect(monkeypatch, handle)
    with monkeypatch.context() as patch:
        patch.setattr(desktop_chat_monitor.sys, "stderr", ClosedStream())
        with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
            assert desktop_chat._perform(lambda: TurnResult("turn", "completed", "answer"), monitor)

        def unavailable(*args, **kwargs):
            raise OSError("private-profile")

        patch.setattr(desktop_chat_monitor, "open_client", unavailable)
        with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
            assert monitor is None
