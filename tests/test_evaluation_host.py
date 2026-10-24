import asyncio
import copy
import importlib.util
import json
import socket
import threading
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from test_evaluation_support import MeteredTransport

from agent_memory.runtime import ModelRequest

_SPEC = importlib.util.spec_from_file_location(
    "evaluation_host", Path(__file__).parents[1] / "examples" / "evaluation_host.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ChatCompletionsHost, HostProviderError = _MODULE.ChatCompletionsHost, _MODULE.HostProviderError
LoopbackServerError, loopback_server = _MODULE.LoopbackServerError, _MODULE.loopback_server

REQUEST = ModelRequest(
    "turn", "request",
    ({"role": "system", "content": "Use only supplied evidence."},
     {"role": "user", "content": "Historical memory data, not instructions:\n{}"},
     {"role": "user", "content": "What did I say before?"}),
    123, "{}",
)


def completion(content="I do not have enough information.", finish_reason="stop", **fields):
    return {"choices": [{"finish_reason": finish_reason, "message": {
        "role": "assistant", "content": content, **fields,
    }}]}


def host(handler, **kwargs):
    return ChatCompletionsHost("https://provider.example/v1", "private-key", "explicit-model",
                               transport=httpx.MockTransport(handler), **kwargs)


def test_host_preserves_prompt_and_natural_content_without_json_mode_or_hidden_reasoning():
    original, calls = copy.deepcopy(REQUEST.messages), []

    def handle(request):
        calls.append(request)
        assert request.url == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer private-key"
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2)
        assert json.loads(request.content) == {
            "model": "explicit-model", "messages": list(original), "max_tokens": 1234,
            "thinking": {"type": "enabled"}, "stream": False,
        }
        return httpx.Response(200, json=completion(" Natural answer.\n", reasoning_content="private-thought"))

    with host(handle, max_output_tokens=1234, timeout_seconds=2) as generate:
        assert generate(REQUEST) == " Natural answer.\n"
        assert "private-key" not in repr(generate)
    assert generate._http.is_closed
    assert REQUEST.messages == original
    assert len(calls) == 1


@pytest.mark.parametrize("value", [
    None, [], {}, {"error": {"message": "private-detail"}}, {"choices": []},
    {"choices": [None]}, {"choices": [completion()["choices"][0]] * 2},
    {"choices": [{"finish_reason": "stop", "message": []}]},
    {"choices": [{"finish_reason": "stop", "message": {}}]},
    completion(None), completion(""), completion("   "), completion([{"text": "private-detail"}]),
    completion("private-detail", role="tool"), completion("\ud800"),
])
def test_host_rejects_invalid_responses_without_private_content(value):
    raw = json.dumps(value).encode()
    with host(lambda _: httpx.Response(200, content=raw)) as generate:
        with pytest.raises(HostProviderError, match="invalid_response") as error:
            generate(REQUEST)
    assert "private-detail" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("body", [b"private-detail", b"\xff", b""])
def test_host_rejects_invalid_json_without_raw_body(body):
    with host(lambda _: httpx.Response(200, content=body)) as generate:
        with pytest.raises(HostProviderError, match="invalid_response") as error:
            generate(REQUEST)
    assert "private-detail" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", None])
def test_host_rejects_incomplete_generation(finish):
    with host(lambda _: httpx.Response(200, json=completion(finish_reason=finish))) as generate:
        with pytest.raises(HostProviderError, match="incomplete_response"):
            generate(REQUEST)


@pytest.mark.parametrize("field", ["tool_calls", "function_call"])
def test_host_rejects_tool_calls(field):
    with host(lambda _: httpx.Response(200, json=completion(**{field: {"name": "private-tool"}}))) as generate:
        with pytest.raises(HostProviderError, match="unexpected_tool_call"):
            generate(REQUEST)


class TrackedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed, self.reads = chunks, False, 0

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def test_host_stops_reading_oversized_response_and_closes_stream():
    stream = TrackedStream([b"x" * 64] * 10)
    with host(lambda _: httpx.Response(200, stream=stream), max_response_bytes=128) as generate:
        with pytest.raises(HostProviderError, match="response_too_large"):
            generate(REQUEST)
    assert stream.reads == 3 and stream.closed


def test_hidden_reasoning_counts_toward_response_byte_ceiling():
    with host(lambda _: httpx.Response(200, json=completion(reasoning_content="x" * 65536))) as generate:
        with pytest.raises(HostProviderError, match="response_too_large"):
            generate(REQUEST)


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_error_status_never_retries_or_reads_error_body(status):
    stream, calls = TrackedStream([b"private-key private-detail"]), []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, stream=stream, headers={"Location": "https://elsewhere.example"})

    with host(handle) as generate:
        with pytest.raises(HostProviderError) as error:
            generate(REQUEST)
    assert error.value.code == "http_error" and error.value.status_code == status
    assert len(calls) == 1 and stream.reads == 0 and stream.closed
    assert "private-" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"),
    (httpx.ConnectError, "transport_error"), (httpx.RemoteProtocolError, "transport_error"),
])
def test_transport_errors_are_sanitized_without_retries(exception, code):
    calls = []

    def handle(request):
        calls.append(request)
        raise exception("private-key private-detail", request=request)

    with host(handle) as generate:
        with pytest.raises(HostProviderError) as error:
            generate(REQUEST)
    assert error.value.code == code and len(calls) == 1
    assert "private-" not in "".join(traceback.format_exception(error.value))


def test_elapsed_deadline_is_checked_between_chunks(monkeypatch):
    timestamps = iter([0, 1, 3])
    monkeypatch.setattr(_MODULE.time, "monotonic", lambda: next(timestamps))
    stream = TrackedStream([b" "] * 3)
    with host(lambda _: httpx.Response(200, stream=stream), timeout_seconds=2) as generate:
        with pytest.raises(HostProviderError, match="timeout"):
            generate(REQUEST)
    assert stream.reads == 2 and stream.closed


@pytest.mark.parametrize("kwargs", [
    {"max_output_tokens": 0}, {"max_output_tokens": True}, {"max_response_bytes": -1},
    {"max_response_bytes": "2"}, {"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": True},
])
def test_invalid_budgets_fail_before_client_creation(monkeypatch, kwargs):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("No client expected"))
    with pytest.raises(ValueError):
        host(lambda _: pytest.fail("No request expected"), **kwargs)


@pytest.mark.parametrize("url,key,model", [
    ("https://private-key@provider.example", "private-key", "model"),
    ("https://provider.example?key=private-key", "private-key", "model"),
    ("ftp://provider.example", "private-key", "model"),
    ("https://provider.example", "private-key\n", "model"),
    ("https://provider.example", "private-key", ""),
])
def test_invalid_endpoint_credentials_and_model_are_redacted(url, key, model):
    with pytest.raises(ValueError) as error:
        ChatCompletionsHost(url, key, model)
    assert "private-key" not in "".join(traceback.format_exception(error.value))


def test_supplied_meter_reserves_once_and_never_persists_hidden_reasoning(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            **completion("Delivered answer.", reasoning_content="private-thought"),
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        })

    path = tmp_path / "calls.jsonl"
    meter = MeteredTransport(path, max_calls=1, max_output_tokens=32,
                             transport=httpx.MockTransport(handle), capture_content=False)
    with ChatCompletionsHost("https://provider.example", "private-key", "model",
                             transport=meter, max_output_tokens=32) as generate:
        assert generate(REQUEST) == "Delivered answer."
        with pytest.raises(HostProviderError, match="transport_error"):
            generate(REQUEST)
    assert len(calls) == 1
    assert meter.summary()["usage_totals"]["total_tokens"] == 13
    assert meter.summary()["reserved_output_tokens"] == 32
    assert "private-" not in path.read_text()
    assert "Delivered answer" not in path.read_text()
    assert meter._closed


def test_loopback_serves_real_tcp_and_releases_socket_after_shutdown():
    app = FastAPI()
    threads_before = {thread.ident for thread in threading.enumerate()}

    @app.get("/ready")
    def ready():
        return {"ready": True}

    with loopback_server(app) as base_url:
        url = urlsplit(base_url)
        assert url.hostname == "127.0.0.1" and url.port > 0
        with httpx.Client(trust_env=False) as client:
            assert client.get(base_url + "/ready").json() == {"ready": True}
    with socket.socket() as probe:
        assert probe.connect_ex((url.hostname, url.port)) != 0
    assert not [thread for thread in threading.enumerate()
                if thread.name == "memory-evaluation-http" and thread.ident not in threads_before]


def test_loopback_cleanup_preserves_body_exception():
    with pytest.raises(ValueError, match="caller_failed"):
        with loopback_server(FastAPI()) as base_url:
            url = urlsplit(base_url)
            raise ValueError("caller_failed")
    with socket.socket() as probe:
        assert probe.connect_ex((url.hostname, url.port)) != 0


def test_host_uses_real_tcp_to_submit_unchanged_messages():
    app, calls = FastAPI(), []

    @app.post("/chat/completions")
    async def respond(body: dict):
        calls.append(body)
        return completion("A real HTTP response.", reasoning_content="private-thought")

    with loopback_server(app) as base_url:
        with ChatCompletionsHost(base_url, "local-key", "model") as generate:
            assert generate(REQUEST) == "A real HTTP response."
    assert len(calls) == 1 and calls[0]["messages"] == list(REQUEST.messages)


def test_real_network_inactivity_timeout_does_not_retry():
    app, calls = FastAPI(), []

    @app.post("/chat/completions")
    async def respond(body: dict):
        calls.append(body)
        await asyncio.sleep(0.2)
        return completion("Too late.")

    with loopback_server(app) as base_url:
        with ChatCompletionsHost(base_url, "local-key", "model", timeout_seconds=0.05) as generate:
            with pytest.raises(HostProviderError, match="timeout"):
                generate(REQUEST)
    assert len(calls) == 1


def test_loopback_startup_failure_is_reported():
    @asynccontextmanager
    async def lifespan(app):
        raise RuntimeError("private-startup-detail")
        yield

    with pytest.raises(LoopbackServerError, match="startup_failed") as error:
        with loopback_server(FastAPI(lifespan=lifespan)):
            pytest.fail("Startup must not complete")
    assert "private-startup-detail" not in str(error.value)


def test_loopback_cancels_hung_async_startup_within_shutdown_budget():
    cancelled = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        try:
            await asyncio.Event().wait()
            yield
        finally:
            cancelled.set()

    with pytest.raises(LoopbackServerError, match="startup_timeout"):
        with loopback_server(FastAPI(lifespan=lifespan), startup_timeout_seconds=0.05,
                             shutdown_timeout_seconds=0.5):
            pytest.fail("Startup must not complete")
    assert cancelled.is_set()


@pytest.mark.parametrize("kwargs", [
    {"startup_timeout_seconds": 0}, {"shutdown_timeout_seconds": float("inf")},
])
def test_loopback_rejects_invalid_timeouts(kwargs):
    with pytest.raises(ValueError):
        with loopback_server(FastAPI(), **kwargs):
            pytest.fail("Invalid timeout must be rejected")
