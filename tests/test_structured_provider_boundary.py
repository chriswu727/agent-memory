import gzip
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from test_answer_review import assessment, prompt, reviewer
from test_recall_provider import REQUEST as RECALL_REQUEST
from test_semantic_provider import REQUEST as SEMANTIC_REQUEST

from membrain.answer_review import AnswerReviewError
from membrain.answer_review_provider import ChatCompletionsAnswerReviewProvider
from membrain.recall_provider import ChatCompletionsRecallReasoner, RecallProviderError
from membrain.semantic_provider import ChatCompletionsSemanticProvider, SemanticProviderError

USAGE = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}


@pytest.fixture(params=["recall", "semantic", "review"])
def adapter(request):
    return {
        "recall": (ChatCompletionsRecallReasoner, RecallProviderError, RECALL_REQUEST),
        "semantic": (ChatCompletionsSemanticProvider, SemanticProviderError, SEMANTIC_REQUEST),
        "review": (ChatCompletionsAnswerReviewProvider, AnswerReviewError,
                   reviewer(assessment()).prepare(prompt(), "A proposed answer.")),
    }[request.param]


def response(**message):
    return {"choices": [{"index": 0, "finish_reason": "stop", "message": {
        "role": "assistant", "content": "{}", **message,
    }}], "usage": USAGE}


@pytest.mark.parametrize("change,code", [
    ({"role": "user"}, "invalid_response"),
    ({"refusal": "private provider refusal"}, "refused_response"),
    ({"function_call": {}}, "unexpected_tool_call"),
    ({"audio": {}}, "invalid_response"),
])
def test_all_structured_providers_reject_non_answers_and_preserve_reported_usage(adapter, change, code):
    provider_type, error_type, request = adapter
    with provider_type("https://provider.example/v1", "private-key", "model", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=response(**change)),
    )) as provider:
        with pytest.raises(error_type) as error:
            provider(request)
        assert error.value.code == code
        assert "private" not in str(error.value)
        assert provider.last_usage == USAGE


@pytest.mark.parametrize("body", [
    b'{"choices":[],"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{}"}}]}',
    b'{"usage":{"prompt_tokens":NaN},"choices":[]}',
    json.dumps({**response(), "choices": response()["choices"] * 2}).encode(),
])
def test_ambiguous_or_nonstandard_structured_response_is_rejected(adapter, body):
    provider_type, error_type, request = adapter
    with provider_type("https://provider.example/v1", "private-key", "model", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=body),
    )) as provider:
        with pytest.raises(error_type, match="invalid_response"):
            provider(request)


def test_structured_gzip_expansion_is_bounded_and_closes_the_stream(adapter):
    provider_type, error_type, request = adapter

    class Stream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield gzip.compress(json.dumps(response(content="x" * 200000)).encode())

        def close(self):
            self.closed = True

    stream = Stream()
    with provider_type("https://provider.example/v1", "private-key", "model", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=stream, headers={"Content-Encoding": "gzip"}),
    )) as provider:
        with pytest.raises(error_type, match="response_too_large"):
            provider(request)
        assert provider.last_usage is None
    assert stream.closed


def test_structured_usage_is_per_call_and_unknown_is_not_zero(adapter):
    provider_type, error_type, request = adapter
    responses = iter([response(), {"choices": response()["choices"]}])
    with provider_type("https://provider.example/v1", "private-key", "model", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=next(responses)),
    )) as provider:
        assert provider(request) == "{}"
        assert provider.last_usage == USAGE
        assert provider(request) == "{}"
        assert provider.last_usage is None
    with pytest.raises(error_type, match="provider_closed"):
        provider(request)


@pytest.mark.parametrize("provider_type,model_request", [
    (ChatCompletionsSemanticProvider, SEMANTIC_REQUEST),
    (ChatCompletionsAnswerReviewProvider, reviewer(assessment()).prepare(prompt(), "A proposed answer.")),
])
def test_invalid_phase_does_not_reuse_previous_provider_usage(provider_type, model_request):
    with provider_type("https://provider.example/v1", "private-key", "model", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=response()),
    )) as provider:
        provider(model_request)
        assert provider.last_usage == USAGE
        with pytest.raises(ValueError, match="phase"):
            provider(replace(model_request, phase="unsupported"))
        assert provider.last_usage is None and provider.last_request_id is None


def test_provider_usage_belongs_to_the_calling_thread(adapter):
    provider_type, _, request = adapter
    local = threading.local()
    completed = threading.Barrier(2)

    def respond(_):
        return httpx.Response(200, json={**response(), "usage": {
            "prompt_tokens": local.tokens, "completion_tokens": 2, "total_tokens": local.tokens + 2,
        }}, headers={"x-request-id": f"call-{local.tokens}"})

    with provider_type("https://provider.example/v1", "private-key", "model",
                       transport=httpx.MockTransport(respond)) as provider:
        def call(tokens):
            local.tokens = tokens
            provider(request)
            completed.wait(timeout=5)
            return provider.last_usage, provider.last_request_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(call, [10, 20]))
        assert [item[0]["prompt_tokens"] for item in values] == [10, 20]
        assert [item[1] for item in values] == ["call-10", "call-20"]
        assert provider.last_usage is None and provider.last_request_id is None
