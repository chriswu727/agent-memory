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


def provider(handler):
    return ChatCompletionsRecallReasoner(
        "https://provider.example/v1", "secret-token", "configured-model",
        transport=httpx.MockTransport(handler),
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
    timestamps = iter([0.0, 1.0, 3.0])
    monkeypatch.setattr("agent_memory.recall_provider.time.monotonic", lambda: next(timestamps))
    stream = TrackedStream([b" "] * 3)
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
