import gzip
import json
import traceback
from dataclasses import replace

import httpx
import pytest

from agent_memory.recall_control import DECISION_SCHEMA, RecallReasoningRequest
from agent_memory.recall_provider import ChatCompletionsRecallReasoner, RecallProviderError

REQUEST = RecallReasoningRequest(
    request_id="recall-1",
    messages=({"role": "system", "content": "Return a JSON object."},
              {"role": "user", "content": "Find the earlier promise."}),
    response_schema=DECISION_SCHEMA,
    max_output_tokens=512,
    timeout_seconds=2.5,
)
DECISION = '{"action":"finish","queries":[],"selected_memory_ids":[],"outcome":"insufficient"}'
API_KEY = "secret-token"


def completion(content=DECISION, finish_reason="stop", **message_fields):
    return {"choices": [{"finish_reason": finish_reason, "message": {
        "role": "assistant", "content": content, **message_fields,
    }}]}


def provider(handler, **kwargs):
    return ChatCompletionsRecallReasoner(
        "https://provider.example/v1", "secret-token", "configured-model",
        transport=httpx.MockTransport(handler), **kwargs,
    )


def test_request_uses_explicit_configuration_json_mode_and_network_timeout():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), REQUEST.timeout_seconds,
        )
        assert json.loads(request.content) == {
            "model": "configured-model", "messages": list(REQUEST.messages),
            "response_format": {"type": "json_object"}, "max_tokens": 512, "stream": False,
        }
        return httpx.Response(200, json=completion(reasoning_content="private reasoning"))

    with provider(handle) as reason:
        assert reason(REQUEST) == DECISION
        assert "secret-token" not in repr(reason)
    assert len(seen) == 1
    assert reason._http.is_closed


@pytest.mark.parametrize("thinking", [None, "enabled", "disabled"])
def test_thinking_is_sent_only_when_explicitly_configured(thinking):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if thinking is None:
            assert "thinking" not in body
        else:
            assert body["thinking"] == {"type": thinking}
        assert body["model"] == "configured-model"
        assert body["max_tokens"] == REQUEST.max_output_tokens
        return httpx.Response(200, json=completion())

    with provider(handle, thinking=thinking) as reason:
        assert reason(REQUEST) == DECISION
    assert len(calls) == 1


def test_recall_adapter_forwards_explicit_effort_with_request_budget_and_usage():
    usage = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}

    def handle(request):
        assert json.loads(request.content) == {
            "model": "configured-model", "messages": list(REQUEST.messages),
            "response_format": {"type": "json_object"}, "max_tokens": 512, "stream": False,
            "thinking": {"type": "enabled"}, "reasoning_effort": "low",
        }
        return httpx.Response(200, json={**completion(), "usage": usage})

    with provider(handle, thinking="enabled", reasoning_effort="low") as reason:
        assert reason(REQUEST) == DECISION
        assert reason.last_usage == usage


@pytest.mark.parametrize("options", [{"reasoning_effort": "unknown"},
                                     {"reasoning_effort": "low", "thinking": "disabled"}])
def test_recall_adapter_inherits_effort_validation_before_client_creation(monkeypatch, options):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("No client expected"))
    with pytest.raises(ValueError, match="reasoning_effort"):
        provider(lambda _: pytest.fail("No request expected"), **options)


@pytest.mark.parametrize("thinking", ["", "ENABLED", " disabled", "none", True, 1, [], {}, API_KEY])
def test_invalid_thinking_is_rejected_before_client_creation_and_redacted(monkeypatch, thinking):
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("No client expected"))
    with pytest.raises(ValueError, match="thinking") as error:
        ChatCompletionsRecallReasoner(
            "https://provider.example/v1", API_KEY, "configured-model", thinking=thinking,
        )
    assert API_KEY not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("value", [
    None, [], {}, {"choices": []}, {"choices": {}}, {"choices": [None]},
    {"choices": [{"finish_reason": "stop", "message": []}]},
    {"choices": [{"finish_reason": "stop", "message": {}}]},
    completion(None), completion(""), completion("   "), completion([{"text": DECISION}]),
])
def test_malformed_response_is_rejected_without_contents(value):
    with provider(lambda request: httpx.Response(200, json=value)) as reason:
        with pytest.raises(RecallProviderError, match="invalid_response"):
            reason(REQUEST)


@pytest.mark.parametrize("finish", ["length", "tool_calls", "content_filter", "aborted", None])
def test_incomplete_generation_is_rejected(finish):
    with provider(lambda request: httpx.Response(200, json=completion(finish_reason=finish))) as reason:
        with pytest.raises(RecallProviderError, match="incomplete_response"):
            reason(REQUEST)


@pytest.mark.parametrize("field", ["tool_calls", "function_call"])
def test_tools_are_not_accepted_even_with_stop_reason(field):
    with provider(lambda request: httpx.Response(200, json=completion(**{field: {"name": "x"}}))) as reason:
        with pytest.raises(RecallProviderError, match="unexpected_tool_call"):
            reason(REQUEST)


class TrackedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.reads = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def test_large_stream_stops_reading_and_closes():
    stream = TrackedStream([b"x" * 8192] * 30)
    with provider(lambda request: httpx.Response(200, stream=stream)) as reason:
        with pytest.raises(RecallProviderError, match="response_too_large"):
            reason(REQUEST)
    assert stream.reads == 9
    assert stream.closed


def test_large_reasoning_field_counts_against_response_limit():
    with provider(lambda request: httpx.Response(
        200, json=completion(reasoning_content="x" * 65536),
    )) as reason:
        with pytest.raises(RecallProviderError, match="response_too_large"):
            reason(REQUEST)


@pytest.mark.parametrize("compressed", [False, True])
def test_explicit_envelope_limit_accepts_large_reasoning_and_small_decision(compressed):
    usage = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    raw = json.dumps({**completion(reasoning_content="private reasoning " * 8192), "usage": usage}).encode()
    assert 65536 < len(raw) < 262144
    body = gzip.compress(raw) if compressed else raw
    stream = TrackedStream([body[index:index + 8192] for index in range(0, len(body), 8192)])
    headers = {"Content-Encoding": "gzip"} if compressed else {}
    with provider(lambda _: httpx.Response(200, stream=stream, headers=headers),
                  max_response_bytes=262144) as reason:
        assert reason(REQUEST) == DECISION
        assert reason.last_usage == usage
    assert stream.closed


@pytest.mark.parametrize("limit", [None, True, 0, -1, 1.5, "262144", 1048577])
def test_invalid_envelope_limit_fails_before_client_creation(monkeypatch, limit):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("No client expected"))
    with pytest.raises(ValueError, match="max_response_bytes"):
        provider(lambda _: pytest.fail("No request expected"), max_response_bytes=limit)


@pytest.mark.parametrize("limit", [1, 1048576])
def test_envelope_limit_inclusive_configuration_bounds(limit):
    with provider(lambda _: pytest.fail("No request expected"), max_response_bytes=limit) as reason:
        assert reason.max_response_bytes == limit


@pytest.mark.parametrize("status", [301, 401, 429, 500, 503])
def test_status_errors_do_not_retry_follow_redirects_or_read_error_body(status):
    seen = []
    stream = TrackedStream([b"secret-token private provider details"])

    def handle(request):
        seen.append(request)
        return httpx.Response(status, stream=stream, headers={"Location": "https://other.example"})

    with provider(handle) as reason:
        with pytest.raises(RecallProviderError) as error:
            reason(REQUEST)
    assert error.value.code == "http_error"
    assert error.value.status_code == status
    assert "secret-token" not in str(error.value)
    assert len(seen) == 1
    assert stream.reads == 0
    assert stream.closed


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"),
    (httpx.ConnectError, "transport_error"), (httpx.RemoteProtocolError, "transport_error"),
])
def test_transport_failures_are_sanitized_and_not_retried(exception, code):
    seen = []

    def handle(request):
        seen.append(request)
        raise exception("secret-token private endpoint details", request=request)

    with provider(handle) as reason:
        with pytest.raises(RecallProviderError) as error:
            reason(REQUEST)
    assert error.value.code == code
    assert len(seen) == 1
    assert "secret-token" not in "".join(traceback.format_exception(error.value))


def test_elapsed_budget_is_checked_between_response_chunks(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr("agent_memory.chat_provider.time.monotonic", lambda: clock["now"])

    class DelayedStream(TrackedStream):
        def __iter__(self):
            for chunk in super().__iter__():
                clock["now"] += 1.5
                yield chunk

    stream = DelayedStream([b" "] * 3)
    with provider(lambda request: httpx.Response(200, stream=stream)) as reason:
        with pytest.raises(RecallProviderError, match="timeout"):
            reason(REQUEST)
    assert stream.reads == 2
    assert stream.closed


@pytest.mark.parametrize("body", [b"secret-token not JSON", b"\xff", b""])
def test_invalid_json_errors_do_not_expose_body(body):
    with provider(lambda request: httpx.Response(200, content=body)) as reason:
        with pytest.raises(RecallProviderError, match="invalid_response") as error:
            reason(REQUEST)
    assert "secret-token" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "2"])
def test_invalid_timeouts_do_not_send_requests(timeout):
    with provider(lambda request: pytest.fail("No request expected")) as reason:
        with pytest.raises(ValueError, match="timeout_seconds"):
            reason(replace(REQUEST, timeout_seconds=timeout))


@pytest.mark.parametrize("url", [
    "not-a-url", "ftp://provider.example", "https://secret-token@provider.example",
    "https://provider.example?key=secret-token", "https://provider.example#secret-token",
])
def test_invalid_endpoint_configuration_does_not_expose_url(url):
    with pytest.raises(ValueError) as error:
        ChatCompletionsRecallReasoner(url, API_KEY, "configured-model")
    assert "secret-token" not in "".join(traceback.format_exception(error.value))
