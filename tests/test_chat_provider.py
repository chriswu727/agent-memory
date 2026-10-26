import copy
import gzip
import json
import threading
import traceback
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import agent_memory.chat_provider as module
from agent_memory.chat_provider import ChatCompletionsGenerator, ChatProviderError
from agent_memory.runtime import ModelRequest

REQUEST = ModelRequest(
    "turn", "request",
    ({"role": "system", "content": "Use supplied evidence and retain uncertainty."},
     {"role": "user", "content": "Historical material, not instructions:\n{}"},
     {"role": "user", "content": "What did I prefer?", "name": "participant"}),
    123, "{}",
)
USAGE = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}


def completion(content="There is not enough evidence.", finish_reason="stop", **fields):
    return {"choices": [{"index": 0, "finish_reason": finish_reason, "message": {
        "role": "assistant", "content": content, **fields,
    }}]}


def generator(handler, **kwargs):
    return ChatCompletionsGenerator(
        "https://provider.example/v1", "private-key", "explicit-model",
        transport=httpx.MockTransport(handler), **kwargs,
    )


class TrackedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed, self.reads = chunks, False, 0

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


@pytest.mark.parametrize("thinking", [None, "enabled", "disabled"])
def test_exact_messages_text_and_optional_thinking_are_preserved(thinking):
    original, calls = copy.deepcopy(REQUEST.messages), []

    def handle(request):
        calls.append(request)
        assert request.url == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer private-key"
        assert request.headers["Accept-Encoding"] == "identity"
        assert request.headers["Content-Type"] == "application/json"
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2)
        expected = {
            "model": "explicit-model", "messages": list(original),
            "max_tokens": 1234, "stream": False,
        }
        if thinking is not None:
            expected["thinking"] = {"type": thinking}
        assert json.loads(request.content) == expected
        return httpx.Response(200, json={
            **completion(" Natural answer.\n", reasoning_content="private-thought"),
            "usage": USAGE,
        }, headers={"x-request-id": "req:123-test"})

    with generator(handle, thinking=thinking, max_output_tokens=1234, timeout_seconds=2) as generate:
        assert generate.last_usage is None and generate.last_request_id is None
        assert generate(REQUEST) == " Natural answer.\n"
        assert generate.last_usage == USAGE
        assert generate.last_request_id == "req:123-test"
        assert "private-key" not in repr(generate)
        assert not any("private-thought" in str(value) for value in vars(generate).values())
    assert generate._http.is_closed
    assert REQUEST.messages == original and len(calls) == 1


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"])
@pytest.mark.parametrize("thinking", [None, "enabled"])
def test_explicit_reasoning_effort_is_forwarded_without_changing_other_limits(effort, thinking):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        expected = {"model": "explicit-model", "messages": list(REQUEST.messages),
                    "max_tokens": 8192, "stream": False, "reasoning_effort": effort}
        if thinking is not None:
            expected["thinking"] = {"type": thinking}
        assert body == expected
        return httpx.Response(200, json=completion())

    with generator(handle, thinking=thinking, reasoning_effort=effort, max_output_tokens=8192) as generate:
        assert generate(REQUEST) == "There is not enough evidence."
    assert len(calls) == 1


@pytest.mark.parametrize("effort", ["", "none", "LOW", " low", "high ", "private-detail", True, 1, [], {}])
def test_invalid_reasoning_effort_is_redacted_and_rejected_before_client_creation(monkeypatch, effort):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("No client expected"))
    with pytest.raises(ValueError, match="reasoning_effort") as error:
        generator(lambda _: pytest.fail("No request expected"), reasoning_effort=effort)
    assert "private-detail" not in str(error.value)


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"])
def test_explicit_effort_cannot_silently_override_disabled_thinking(monkeypatch, effort):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("No client expected"))
    with pytest.raises(ValueError, match="thinking is disabled"):
        generator(lambda _: pytest.fail("No request expected"), thinking="disabled", reasoning_effort=effort)


def test_explicit_effort_preserves_known_truncation_usage_and_resets_later_failure_metadata():
    calls = []
    exhausted = {"prompt_tokens": 20, "completion_tokens": 8192, "total_tokens": 8212,
                 "completion_tokens_details": {"reasoning_tokens": 8192}}

    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json={**completion("Answer."), "usage": USAGE},
                                  headers={"x-request-id": "first"})
        if len(calls) == 2:
            return httpx.Response(200, json={**completion("", finish_reason="length",
                                                         reasoning_content="private-thought"), "usage": exhausted},
                                  headers={"x-request-id": "exhausted"})
        raise httpx.ReadError("private-detail", request=request)

    with generator(handle, thinking="enabled", reasoning_effort="low", max_output_tokens=8192) as generate:
        assert generate(REQUEST) == "Answer."
        first = generate.last_call_id
        with pytest.raises(ChatProviderError, match="incomplete_response"):
            generate(REQUEST)
        second = generate.last_call_id
        assert generate.last_usage == exhausted and generate.last_request_id == "exhausted"
        with pytest.raises(ChatProviderError, match="transport_error"):
            generate(REQUEST)
        assert generate.last_usage is None and generate.last_request_id is None
        assert len({first, second, generate.last_call_id}) == 3
    assert len(calls) == 3
    assert all(body["reasoning_effort"] == "low" and body["max_tokens"] == 8192 for body in calls)


@pytest.mark.parametrize("value", [
    None, [], {}, {"error": {"message": "private-detail"}}, {"choices": []},
    {"choices": [None]}, {"choices": [completion()["choices"][0]] * 2},
    {"choices": [{"finish_reason": "stop", "message": []}]},
    {"choices": [{"finish_reason": "stop", "message": {}}]},
    {"choices": [{"index": True, "finish_reason": "stop", "message": {
        "role": "assistant", "content": "private-detail",
    }}]},
    {"choices": [{"index": 1, "finish_reason": "stop", "message": {
        "role": "assistant", "content": "private-detail",
    }}]},
    completion(None), completion(""), completion("   "), completion([{"text": "private-detail"}]),
    completion("private-detail", role="tool"), completion("\ud800"),
    completion("valid", reasoning_content="\udfff"), completion("valid", audio={"id": "audio"}),
])
def test_invalid_responses_are_rejected_without_exposing_content(value):
    raw = json.dumps(value).encode()
    with generator(lambda _: httpx.Response(200, content=raw)) as generate:
        with pytest.raises(ChatProviderError, match="invalid_response") as error:
            generate(REQUEST)
    assert "private-detail" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("raw", [
    b"private-detail", b"\xff", b"", b"{} trailing-private-detail",
    b'{"choices": [], "choices": []}', b'{"choices": [], "extra": NaN}',
    b'{"choices": [], "extra": Infinity}', b'{"choices": [], "extra": 1e999}',
    json.dumps(completion()).encode("utf-16"),
    br'{"\ud800": "private-detail"}',
    b"[" * 2000 + b"]" * 2000,
])
def test_invalid_json_unicode_and_ambiguous_json_are_redacted(raw):
    with generator(lambda _: httpx.Response(200, content=raw)) as generate:
        with pytest.raises(ChatProviderError, match="invalid_response") as error:
            generate(REQUEST)
        assert generate.last_usage is None
    assert "private-detail" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", None, True, "unknown"])
def test_incomplete_response_is_not_an_answer_but_valid_usage_is_retained(finish):
    with generator(lambda _: httpx.Response(200, json={
        **completion(finish_reason=finish), "usage": USAGE,
    })) as generate:
        with pytest.raises(ChatProviderError, match="incomplete_response"):
            generate(REQUEST)
        assert generate.last_usage == USAGE


@pytest.mark.parametrize("field,value,code", [
    ("tool_calls", [{"name": "private-tool"}], "unexpected_tool_call"),
    ("tool_calls", {}, "unexpected_tool_call"),
    ("tool_calls", False, "unexpected_tool_call"),
    ("function_call", {"name": "private-tool"}, "unexpected_tool_call"),
    ("function_call", {}, "unexpected_tool_call"),
    ("refusal", "private-refusal", "refused_response"),
    ("refusal", "", "refused_response"),
    ("refusal", False, "refused_response"),
])
def test_tool_calls_and_refusals_never_become_answers(field, value, code):
    with generator(lambda _: httpx.Response(200, json=completion(**{field: value}))) as generate:
        with pytest.raises(ChatProviderError) as error:
            generate(REQUEST)
        assert error.value.code == code
    assert "private-" not in "".join(traceback.format_exception(error.value))


def test_null_optional_fields_do_not_invent_a_refusal_or_tool_call():
    with generator(lambda _: httpx.Response(200, json=completion(
        "Accepted.", tool_calls=[], function_call=None, refusal=None,
    ))) as generate:
        assert generate(REQUEST) == "Accepted."


def test_valid_usage_is_filtered_and_returned_as_an_independent_snapshot():
    usage = {
        **USAGE, "prompt_cache_hit_tokens": 6, "prompt_cache_miss_tokens": 4,
        "prompt_tokens_details": {"cached_tokens": 6, "audio_tokens": 0, "secret": "private"},
        "completion_tokens_details": {
            "reasoning_tokens": 2, "rejected_prediction_tokens": 9, "raw_text": "private",
        }, "provider_metadata": "private",
    }
    with generator(lambda _: httpx.Response(200, json={**completion(), "usage": usage})) as generate:
        generate(REQUEST)
        snapshot = generate.last_usage
        assert snapshot == {
            **USAGE, "prompt_cache_hit_tokens": 6, "prompt_cache_miss_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 6, "audio_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 2, "rejected_prediction_tokens": 9},
        }
        snapshot["total_tokens"] = 0
        snapshot["completion_tokens_details"]["reasoning_tokens"] = 0
        assert generate.last_usage["total_tokens"] == 13
        assert generate.last_usage["completion_tokens_details"]["reasoning_tokens"] == 2


@pytest.mark.parametrize("usage", [
    None, {}, [], {"total_tokens": 0}, {"prompt_tokens": 10, "completion_tokens": 3},
    {**USAGE, "prompt_tokens": True}, {**USAGE, "completion_tokens": -1},
    {**USAGE, "total_tokens": 14}, {**USAGE, "total_tokens": 13.0},
    {**USAGE, "total_tokens": "13"},
    {**USAGE, "prompt_cache_hit_tokens": -1},
    {**USAGE, "prompt_cache_hit_tokens": 11},
    {**USAGE, "prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 1},
    {**USAGE, "prompt_tokens_details": []},
    {**USAGE, "prompt_tokens_details": {"cached_tokens": True}},
    {**USAGE, "prompt_tokens_details": {"cached_tokens": 11}},
    {**USAGE, "completion_tokens_details": {"reasoning_tokens": "2"}},
    {**USAGE, "completion_tokens_details": {"reasoning_tokens": 4}},
])
def test_missing_or_invalid_usage_remains_unknown_without_discarding_the_answer(usage):
    with generator(lambda _: httpx.Response(200, json={**completion("Answer."), "usage": usage})) as generate:
        assert generate(REQUEST) == "Answer."
        assert generate.last_usage is None


def test_actual_reported_zero_is_retained_without_fabricating_absent_usage():
    with generator(lambda _: httpx.Response(200, json={
        **completion(), "usage": dict.fromkeys(USAGE, 0),
    })) as generate:
        generate(REQUEST)
        assert generate.last_usage == dict.fromkeys(USAGE, 0)


@pytest.mark.parametrize("second", ["missing_usage", "http_error", "transport_error", "invalid_request"])
def test_diagnostics_are_reset_before_each_attempt(second):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={**completion(), "usage": USAGE},
                                  headers={"x-request-id": "first-request"})
        if second == "transport_error":
            raise httpx.ConnectError("private-detail", request=request)
        if second == "http_error":
            return httpx.Response(503, content=b"private-detail")
        return httpx.Response(200, json=completion())

    with generator(handle) as generate:
        generate(REQUEST)
        assert generate.last_usage == USAGE and generate.last_request_id == "first-request"
        if second == "missing_usage":
            generate(REQUEST)
        else:
            with pytest.raises(ChatProviderError):
                generate(None if second == "invalid_request" else REQUEST)
        assert generate.last_usage is None and generate.last_request_id is None


@pytest.mark.parametrize("request_id", ["", "x" * 257, "private detail", "private\ncontent", "../secret"])
def test_invalid_provider_request_id_is_not_copied_into_diagnostics(request_id):
    with generator(lambda _: httpx.Response(200, json=completion(),
                                           headers={"x-request-id": request_id})) as generate:
        generate(REQUEST)
        assert generate.last_request_id is None


def test_missing_header_does_not_substitute_response_content_for_request_id():
    with generator(lambda _: httpx.Response(200, json={
        **completion(), "id": "private-response-content",
    })) as generate:
        generate(REQUEST)
        assert generate.last_request_id is None


def test_oversized_response_stops_reading_and_closes_stream():
    stream = TrackedStream([b"x" * 64] * 10)
    with generator(lambda _: httpx.Response(200, stream=stream), max_response_bytes=128) as generate:
        with pytest.raises(ChatProviderError, match="response_too_large"):
            generate(REQUEST)
    assert stream.reads == 3 and stream.closed


def test_response_at_exact_byte_limit_is_accepted_without_trimming():
    raw = json.dumps(completion(" Preserved answer.\n")).encode()
    stream = TrackedStream([raw[:9], raw[9:]])
    with generator(lambda _: httpx.Response(200, stream=stream), max_response_bytes=len(raw)) as generate:
        assert generate(REQUEST) == " Preserved answer.\n"
    assert stream.closed


def test_hidden_reasoning_is_included_in_the_response_byte_budget():
    with generator(lambda _: httpx.Response(200, json=completion(
        reasoning_content="x" * 1024,
    )), max_response_bytes=1024) as generate:
        with pytest.raises(ChatProviderError, match="response_too_large"):
            generate(REQUEST)


def test_gzip_is_decoded_with_a_decoded_byte_limit():
    raw = json.dumps({**completion("Compressed answer."), "usage": USAGE}).encode()
    compressed = gzip.compress(raw)
    stream = TrackedStream([compressed[:5], compressed[5:]])
    with generator(lambda _: httpx.Response(
        200, stream=stream, headers={"Content-Encoding": "gzip"},
    ), max_response_bytes=max(len(raw), len(compressed))) as generate:
        assert generate(REQUEST) == "Compressed answer."
        assert generate.last_usage == USAGE
    assert stream.closed


def test_compressed_expansion_stops_before_reading_more_bytes():
    compressed = gzip.compress(json.dumps(completion(reasoning_content="x" * 100000)).encode())
    stream = TrackedStream([compressed, b"not-read"])
    with generator(lambda _: httpx.Response(
        200, stream=stream, headers={"Content-Encoding": "gzip"},
    ), max_response_bytes=1024) as generate:
        with pytest.raises(ChatProviderError, match="response_too_large"):
            generate(REQUEST)
        assert generate.last_usage is None
    assert stream.reads == 1 and stream.closed


@pytest.mark.parametrize("damage", ["truncated", "invalid", "trailing", "concatenated"])
def test_damaged_or_ambiguous_gzip_is_not_a_valid_completion(damage):
    compressed = gzip.compress(json.dumps(completion()).encode())
    if damage == "truncated":
        compressed = compressed[:-4]
    elif damage == "invalid":
        compressed = b"private-content"
    elif damage == "trailing":
        compressed += b"private-content"
    else:
        compressed += gzip.compress(b" ")
    stream = TrackedStream([compressed])
    with generator(lambda _: httpx.Response(
        200, stream=stream, headers={"Content-Encoding": "gzip"},
    )) as generate:
        with pytest.raises(ChatProviderError, match="invalid_response"):
            generate(REQUEST)
    assert stream.closed


@pytest.mark.parametrize("encoding", ["br", "deflate", "gzip, gzip", "private-encoding"])
def test_unsupported_compression_is_rejected_before_reading(encoding):
    stream = TrackedStream([b"private-content"])
    with generator(lambda _: httpx.Response(
        200, stream=stream, headers={"Content-Encoding": encoding},
    )) as generate:
        with pytest.raises(ChatProviderError, match="unsupported_encoding"):
            generate(REQUEST)
    assert stream.reads == 0 and stream.closed


@pytest.mark.parametrize("status", [201, 204, 301, 307, 401, 429, 500])
def test_non_200_response_never_retries_redirects_or_reads_error_content(status):
    stream, calls = TrackedStream([b"private-key private-content"]), []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, stream=stream, headers={
            "Location": "https://elsewhere.example", "x-request-id": "error-request-123",
        })

    with generator(handle) as generate:
        with pytest.raises(ChatProviderError) as error:
            generate(REQUEST)
        assert generate.last_request_id == "error-request-123"
        assert generate.last_usage is None
    assert error.value.code == "http_error" and error.value.status_code == status
    assert len(calls) == 1 and stream.reads == 0 and stream.closed
    assert "private-" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"),
    (httpx.ConnectError, "transport_error"), (httpx.RemoteProtocolError, "transport_error"),
])
def test_transport_errors_are_redacted_and_not_retried(exception, code):
    calls = []

    def handle(request):
        calls.append(request)
        raise exception("private-key private-content", request=request)

    with generator(handle) as generate:
        with pytest.raises(ChatProviderError) as error:
            generate(REQUEST)
        assert generate.last_usage is None
    assert error.value.code == code and len(calls) == 1
    assert "private-" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("phase", ["headers", "body", "after_body", "after_parse"])
def test_deadline_rejects_late_results_at_every_boundary(monkeypatch, phase):
    clock = {"now": 0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])
    raw = json.dumps(completion("Late answer.")).encode()

    class DelayedStream(TrackedStream):
        def __iter__(self):
            if phase == "body":
                clock["now"] = 3
            yield from super().__iter__()
            if phase == "after_body":
                clock["now"] = 3

    stream = DelayedStream([raw])

    def handle(request):
        if phase == "headers":
            clock["now"] = 3
        return httpx.Response(200, stream=stream)

    original = module._usage

    def delayed_usage(value):
        if phase == "after_parse":
            clock["now"] = 3
        return original(value)

    monkeypatch.setattr(module, "_usage", delayed_usage)
    with generator(handle, timeout_seconds=2) as generate:
        with pytest.raises(ChatProviderError, match="timeout"):
            generate(REQUEST)
    assert stream.closed


@pytest.mark.parametrize("kwargs", [
    {"max_output_tokens": 0}, {"max_output_tokens": True}, {"max_output_tokens": 1.5},
    {"max_response_bytes": -1}, {"max_response_bytes": "2"}, {"max_response_bytes": False},
    {"timeout_seconds": 0}, {"timeout_seconds": -1}, {"timeout_seconds": "2"},
    {"timeout_seconds": float("inf")}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": True}, {"timeout_seconds": 10 ** 1000},
    {"thinking": True}, {"thinking": "auto"}, {"thinking": []},
])
def test_invalid_parameters_fail_before_client_creation(monkeypatch, kwargs):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("No client expected"))
    with pytest.raises(ValueError):
        generator(lambda _: pytest.fail("No request expected"), **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"base_url": None}, {"base_url": httpx.URL("https://provider.example")},
    {"base_url": "https://private-key@provider.example"},
    {"base_url": "https://provider.example?key=private-key"},
    {"base_url": "https://provider.example?"}, {"base_url": "https://provider.example#"},
    {"base_url": "ftp://provider.example"}, {"base_url": "https://"},
    {"base_url": " https://provider.example"}, {"base_url": "https://provider.example/\nprivate"},
    {"base_url": "https://provider.example/\ud800"}, {"base_url": "https://provider.example:65536"},
    {"api_key": "private-key\n"}, {"api_key": "private key"}, {"api_key": ""},
    {"api_key": None}, {"api_key": "private-\u00e9"},
    {"model": ""}, {"model": " "}, {"model": " model"}, {"model": None},
    {"model": "private\nmodel"}, {"model": "\ud800"}, {"transport": object()},
])
def test_invalid_endpoint_credentials_model_and_transport_are_redacted(monkeypatch, kwargs):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("No client expected"))
    config = {"base_url": "https://provider.example", "api_key": "private-key", "model": "model"}
    with pytest.raises(ValueError) as error:
        ChatCompletionsGenerator(**(config | kwargs))
    assert "private" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("model_request", [
    None, {}, replace(REQUEST, messages=()), replace(REQUEST, messages=[{"role": "user"}]),
    replace(REQUEST, messages=(None,)), replace(REQUEST, messages=({"content": "\ud800"},)),
    replace(REQUEST, messages=({"content": float("nan")},)),
    replace(REQUEST, messages=({"content": {1: "private-detail"}},)),
    replace(REQUEST, messages=({"content": object()},)),
])
def test_invalid_requests_fail_without_network_or_private_diagnostics(model_request):
    with generator(lambda _: pytest.fail("No request expected")) as generate:
        with pytest.raises(ChatProviderError, match="invalid_request") as error:
            generate(model_request)
        assert generate.last_usage is None
    assert "private-detail" not in "".join(traceback.format_exception(error.value))


def test_recursive_request_is_rejected_before_network_access():
    message = {"role": "user"}
    message["content"] = message
    with generator(lambda _: pytest.fail("No request expected")) as generate:
        with pytest.raises(ChatProviderError, match="invalid_request"):
            generate(replace(REQUEST, messages=(message,)))


def test_default_transport_disables_environment_and_retries(monkeypatch):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return httpx.MockTransport(lambda _: httpx.Response(200, json=completion()))

    monkeypatch.setattr(httpx, "HTTPTransport", transport)
    with ChatCompletionsGenerator("https://provider.example", "key", "model") as generate:
        assert generate._http.trust_env is False
        assert generate._http.follow_redirects is False
        generate(REQUEST)
    assert calls == [{"retries": 0, "trust_env": False}]


def test_real_local_http_ignores_environment_proxy_and_uses_the_expected_path(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, json.loads(body)))
            content = json.dumps({**completion("Local response."), "usage": USAGE}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Request-ID", "local-request")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format, *args):
            pass

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with ChatCompletionsGenerator(
            f"http://127.0.0.1:{server.server_port}/v1", "local-key", "model", timeout_seconds=2,
        ) as generate:
            assert generate(REQUEST) == "Local response."
            assert generate.last_usage == USAGE and generate.last_request_id == "local-request"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(received) == 1
    assert received[0][0] == "/v1/chat/completions"
    assert received[0][1]["messages"] == list(REQUEST.messages)


def test_closed_generator_clears_diagnostics_and_refuses_new_requests():
    with generator(lambda _: httpx.Response(200, json={**completion(), "usage": USAGE})) as generate:
        generate(REQUEST)
    generate.close()
    with pytest.raises(ChatProviderError, match="provider_closed"):
        generate(REQUEST)
    assert generate.last_usage is None and generate.last_request_id is None


def test_error_codes_cannot_echo_arbitrary_provider_messages():
    with pytest.raises(ValueError) as error:
        ChatProviderError("private-content")
    assert "private-content" not in str(error.value)
    with pytest.raises(ValueError):
        ChatProviderError("http_error", status_code=True)
