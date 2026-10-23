import json
import traceback
from dataclasses import replace

import httpx
import pytest

from agent_memory.embeddings import (
    ChatCompletionsEmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingRequest,
    EmbeddingSpace,
)

REQUEST = EmbeddingRequest(("考试前的那段经历", "We agreed to check in later."), timeout_seconds=2.5)


def response(**changes):
    return {"model": "configured-model", "data": [
        {"index": 1, "embedding": [0.5, 0.75, 0.25]},
        {"index": 0, "embedding": [0.25, 0.5, 0.75]},
    ], "usage": {"prompt_tokens": 12, "total_tokens": 12}, **changes}


def provider(handler, **changes):
    return ChatCompletionsEmbeddingProvider(
        "https://provider.example/v1", "private-secret", "configured-model", model_version="release-2026-10",
        dimensions=3, transport=httpx.MockTransport(handler), **changes,
    )


def test_explicit_space_request_accounting_and_batch_index_reordering():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url == "https://provider.example/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer private-secret"
        assert request.headers["Content-Type"] == "application/json"
        assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 2.5)
        assert json.loads(request.content) == {
            "model": "configured-model", "input": list(REQUEST.texts), "encoding_format": "float",
        }
        return httpx.Response(200, json=response())

    with provider(handle) as embed:
        metadata = embed.request_metadata(REQUEST)
        assert not seen
        result = embed(REQUEST)
        assert result.space == EmbeddingSpace("configured-model", "release-2026-10", 3)
        assert result.space.descriptor() == {"model": "configured-model", "model_version": "release-2026-10", "dimensions": 3}
        assert result.vectors == ((0.25, 0.5, 0.75), (0.5, 0.75, 0.25))
        assert result.usage == {"input_tokens": 12, "total_tokens": 12}
        assert result.input_bytes == metadata["input_bytes"] == len(seen[0].content)
        assert result.request_fingerprint == metadata["request_fingerprint"]
        assert "private-secret" not in repr(embed)
    assert len(seen) == 1
    assert embed._http.is_closed


def test_query_uses_same_space_and_usage_absence_is_unknown():
    with provider(lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2, 3]}]})) as embed:
        result = embed.embed(["Query"])
        assert result.space == embed.space
        assert result.usage is None
        assert result.vectors == ((1.0, 2.0, 3.0),)
        assert embed.space.fingerprint != EmbeddingSpace("configured-model", "other-version", 3).fingerprint
        with pytest.raises(AttributeError):
            embed.space = EmbeddingSpace("configured-model", "other-version", 3)


@pytest.mark.parametrize("change", [
    {"data": []}, {"data": {}}, {"data": None}, {"data": [None, None]},
    {"data": [{"index": 0, "embedding": [1, 2, 3]}, {"index": 0, "embedding": [1, 2, 3]}]},
    {"data": [{"index": True, "embedding": [1, 2, 3]}, {"index": 1, "embedding": [1, 2, 3]}]},
    {"data": [{"index": -1, "embedding": [1, 2, 3]}, {"index": 1, "embedding": [1, 2, 3]}]},
    {"data": [{"index": 0, "embedding": [1, 2, 3]}, {"index": 2, "embedding": [1, 2, 3]}]},
    {"data": [{"embedding": [1, 2, 3]}, {"index": 1, "embedding": [1, 2, 3]}]},
    {"model": "another-model"}, {"usage": "private provider response"},
    {"usage": {"prompt_tokens": -1}}, {"usage": {"prompt_tokens": True}},
    {"usage": {"total_tokens": "12"}}, {"usage": {"total_tokens": 2**63}},
])
def test_malformed_response_count_indexes_model_or_usage_are_rejected(change):
    with provider(lambda _: httpx.Response(200, json=response(**change))) as embed:
        with pytest.raises(EmbeddingProviderError, match="invalid_response"):
            embed(REQUEST)


@pytest.mark.parametrize("vector", [[1, 2], [1, 2, 3, 4], "base64-not-floats", None])
def test_dimension_mismatch_is_not_silently_padded_or_truncated(vector):
    changed = response(data=[{"index": 0, "embedding": vector}, {"index": 1, "embedding": [1, 2, 3]}])
    with provider(lambda _: httpx.Response(200, json=changed)) as embed:
        with pytest.raises(EmbeddingProviderError, match="dimension_mismatch"):
            embed(REQUEST)


@pytest.mark.parametrize("vector", [[True, 1, 2], [None, 1, 2], ["0.3", 1, 2], [0, 0, 0],
                                    [float("nan"), 1, 2], [float("inf"), 1, 2], [-float("inf"), 1, 2], [10**500, 1, 2]])
def test_vectors_require_finite_numbers_and_nonzero_norm(vector):
    content = json.dumps(response(data=[{"index": 0, "embedding": vector}, {"index": 1, "embedding": [1, 2, 3]}]))
    with provider(lambda _: httpx.Response(200, content=content)) as embed:
        with pytest.raises(EmbeddingProviderError, match="invalid_response"):
            embed(REQUEST)


@pytest.mark.parametrize("usage,expected", [(None, None), ({}, {"input_tokens": None, "total_tokens": None}),
                                          ({"prompt_tokens": 0}, {"input_tokens": 0, "total_tokens": None})])
def test_unknown_usage_is_not_reported_as_zero(usage, expected):
    with provider(lambda _: httpx.Response(200, json=response(usage=usage))) as embed:
        assert embed(REQUEST).usage == expected


@pytest.mark.parametrize("texts", [[], (), "query", None, ("",), ("  ",), (1,), ("\ud800",)])
def test_invalid_input_does_not_send_or_reserve_a_request(texts):
    with provider(lambda _: pytest.fail("No request expected")) as embed:
        with pytest.raises(ValueError):
            embed.request_metadata(replace(REQUEST, texts=texts))
        with pytest.raises(ValueError):
            embed.embed(texts)


@pytest.mark.parametrize("options,texts,reason", [
    ({"max_batch_size": 1}, ("one", "two"), "nonempty sequence"),
    ({"max_text_bytes": 5}, ("考试",), "text exceeds byte budget"),
    ({"max_request_bytes": 10}, ("query",), "request exceeds byte budget"),
])
def test_utf8_wire_and_batch_budgets_are_checked_before_network(options, texts, reason):
    with provider(lambda _: pytest.fail("No request expected"), **options) as embed:
        with pytest.raises(ValueError, match=reason):
            embed.embed(texts)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "2", 301])
def test_invalid_timeout_never_calls_provider(timeout):
    with provider(lambda _: pytest.fail("No request expected")) as embed:
        with pytest.raises(ValueError, match="timeout_seconds"):
            embed(replace(REQUEST, timeout_seconds=timeout))


class TrackedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


def test_response_budget_stops_stream_and_closes_connection():
    stream = TrackedStream([b"x" * 64] * 30)
    with provider(lambda _: httpx.Response(200, stream=stream), max_response_bytes=128) as embed:
        with pytest.raises(EmbeddingProviderError, match="response_too_large"):
            embed(REQUEST)
    assert stream.reads == 3
    assert stream.closed


def test_deadline_is_checked_between_chunks(monkeypatch):
    ticks = iter([0.0, 1.0, 3.0])
    monkeypatch.setattr("agent_memory.embeddings.time.monotonic", lambda: next(ticks))
    stream = TrackedStream([b" "] * 4)
    with provider(lambda _: httpx.Response(200, stream=stream)) as embed:
        with pytest.raises(EmbeddingProviderError, match="timeout"):
            embed(REQUEST)
    assert stream.reads == 2
    assert stream.closed


@pytest.mark.parametrize("status", [301, 401, 429, 500, 503])
def test_http_failures_are_not_retried_or_redirected_and_body_is_not_exposed(status):
    stream = TrackedStream([b"private-secret provider response"])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, stream=stream, headers={"Location": "https://other.example"})

    with provider(handler) as embed:
        with pytest.raises(EmbeddingProviderError) as error:
            embed(REQUEST)
    assert error.value.status_code == status
    assert len(calls) == 1
    assert stream.reads == 0
    assert stream.closed
    assert "private-secret" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"),
                                          (httpx.ConnectError, "transport_error"), (httpx.RemoteProtocolError, "transport_error")])
def test_transport_failure_is_redacted_without_automatic_retry(exception, code):
    calls = []

    def handler(request):
        calls.append(request)
        raise exception("private-secret endpoint payload", request=request)

    with provider(handler) as embed:
        with pytest.raises(EmbeddingProviderError) as error:
            embed(REQUEST)
    assert error.value.code == code
    assert len(calls) == 1
    assert "private-secret" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("body", [b"private-secret not JSON", b"\xff", b"", b"[]", b"null"])
def test_non_json_and_wrong_json_types_do_not_expose_body(body):
    with provider(lambda _: httpx.Response(200, content=body)) as embed:
        with pytest.raises(EmbeddingProviderError, match="invalid_response") as error:
            embed(REQUEST)
    assert "private-secret" not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("changes", [
    {"base_url": "private-secret"}, {"base_url": "ftp://provider.example"},
    {"base_url": "https://private-secret@provider.example"},
    {"base_url": "https://provider.example?key=private-secret"},
    {"base_url": "https://provider.example#private-secret"},
    {"api_key": ""}, {"api_key": "private-secret\n"}, {"model": ""}, {"model_version": ""},
    {"dimensions": 0}, {"dimensions": True}, {"dimensions": 65537},
    {"max_batch_size": 0}, {"max_text_bytes": -1}, {"max_request_bytes": True},
])
def test_invalid_configuration_is_rejected_with_redacted_error(changes):
    values = {"base_url": "https://provider.example/v1", "api_key": "private-secret", "model": "model",
              "model_version": "version", "dimensions": 3, **changes}
    with pytest.raises(ValueError) as error:
        ChatCompletionsEmbeddingProvider(**values)
    assert "private-secret" not in "".join(traceback.format_exception(error.value))
