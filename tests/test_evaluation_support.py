import gzip
import hashlib
import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from test_semantic_provider import REQUEST

from agent_memory.semantic_provider import ChatCompletionsSemanticProvider, SemanticProviderError

_SPEC = importlib.util.spec_from_file_location(
    "evaluation_support", Path(__file__).parents[1] / "examples" / "evaluation_support.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
MeteredTransport, MeteringError = _MODULE.MeteredTransport, _MODULE.MeteringError


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks, *, error=None):
        self.chunks, self.error = chunks, error
        self.reads = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


def completion(**changes):
    return {
        "model": "deepseek-reasoner", "usage": {
            "prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
            "completion_tokens_details": {"reasoning_tokens": 6},
        },
        "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"claims":[]}', "reasoning_content": "private reasoning",
        }}], **changes,
    }


def request(limit=32):
    return httpx.Request("POST", "https://example.test/v1/chat/completions",
                         headers={"Authorization": "Bearer secret-key"},
                         json={"model": "configured-model", "max_tokens": limit,
                               "messages": [{"role": "user", "content": "private prompt"}]})


def meter(tmp_path, handler, **kwargs):
    return MeteredTransport(tmp_path / "run" / "usage.jsonl",
                            transport=httpx.MockTransport(handler), **kwargs)


def test_forwards_request_and_stream_unchanged_with_durable_reservation(tmp_path):
    body = json.dumps(completion()).encode()
    chunks = Chunks([body[:35], body[35:]])
    original = request()

    def handle(received):
        assert received is original
        records = transport.records
        assert len(records) == 1 and records[0]["event"] == "reserved"
        return httpx.Response(200, stream=chunks)

    transport = meter(tmp_path, handle)
    response = transport.handle_request(original)
    assert list(response.stream) == chunks.chunks
    response.close()
    transport.close()
    records = transport.records
    assert records[0]["request_sha256"] == hashlib.sha256(original.content).hexdigest()
    assert records[0]["request_bytes"] == len(original.content)
    assert records[1]["status_code"] == 200
    assert records[1]["model"] == "deepseek-reasoner"
    assert records[1]["finish_reason"] == "stop"
    assert records[1]["usage"]["completion_tokens_details"] == {"reasoning_tokens": 6}
    assert records[1]["assistant_content"] == '{"claims":[]}'
    assert records[1]["usage_confirmed"] is True
    assert records[1]["elapsed_seconds"] >= 0
    assert transport.summary()["usage_totals"]["completion_tokens"] == 8
    assert chunks.closed
    text = transport.ledger_path.read_text()
    assert all(secret not in text for secret in [
        "secret-key", "Authorization", "private prompt", "private reasoning", "reasoning_content",
    ])
    assert transport.ledger_path.stat().st_mode & 0o777 == 0o600


def test_preconsumed_mock_response_is_recorded_without_changing_content(tmp_path):
    transport = meter(tmp_path, lambda _: httpx.Response(200, json=completion()),
                      capture_content=False)
    with httpx.Client(transport=transport) as client:
        response = client.send(request())
    assert response.json() == completion()
    assert transport.records[-1]["usage_confirmed"]
    assert "assistant_content" not in transport.records[-1]


def test_gzip_stream_is_metered_without_changing_wire_bytes(tmp_path):
    decoded = json.dumps(completion()).encode()
    compressed = gzip.compress(decoded)
    chunks = Chunks([compressed[:13], compressed[13:31], compressed[31:]])
    transport = meter(tmp_path, lambda _: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=chunks,
    ))
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          transport=transport) as provider:
        assert provider(REQUEST) == '{"claims":[]}'
    record = transport.records[-1]
    assert record["usage_confirmed"]
    assert record["usage"]["completion_tokens"] == 8
    assert record["assistant_content"] == '{"claims":[]}'
    assert record["response_bytes_observed"] == len(decoded)
    assert record["raw_response_bytes_observed"] == len(compressed)
    assert "private reasoning" not in transport.ledger_path.read_text()


def test_gzip_expansion_is_bounded_and_does_not_bypass_production_cap(tmp_path):
    compressed = gzip.compress(b"a" * (10 * 1024 * 1024))
    chunks = Chunks([compressed, b"must not be read"])
    transport = meter(tmp_path, lambda _: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=chunks,
    ))
    response = transport.handle_request(request())
    iterator = iter(response.stream)
    assert next(iterator) == compressed
    assert not response.stream.buffer
    assert response.stream.exceeded
    response.close()
    record = transport.records[-1]
    assert record["outcome"] == "response_too_large"
    assert record["response_bytes_observed"] == 65537
    assert record["raw_response_bytes_observed"] == len(compressed)
    assert transport.summary()["usage_unknown_calls"] == 1
    assert chunks.reads == 1

    another = meter(tmp_path / "production", lambda _: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=Chunks([compressed]),
    ))
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          transport=another) as provider:
        with pytest.raises(SemanticProviderError, match="response_too_large"):
            provider(REQUEST)
    assert another.records[-1]["outcome"] == "response_too_large"


@pytest.mark.parametrize("encoding,body", [
    ("gzip", b"invalid gzip private body"),
    ("gzip", gzip.compress(json.dumps(completion()).encode())[:-8]),
    ("unsupported", json.dumps(completion()).encode()),
])
def test_unconfirmed_decoding_never_claims_usage(tmp_path, encoding, body):
    transport = meter(tmp_path, lambda _: httpx.Response(
        200, headers={"Content-Encoding": encoding}, stream=Chunks([body]),
    ))
    response = transport.handle_request(request())
    assert list(response.stream) == [body]
    response.close()
    assert transport.summary()["usage_unknown_calls"] == 1
    assert "assistant_content" not in transport.records[-1]
    assert "private" not in transport.ledger_path.read_text()


@pytest.mark.parametrize("code", [400, 401, 429, 500])
def test_http_error_never_reads_or_records_error_body(tmp_path, code):
    chunks = Chunks([b"private error body"])
    transport = meter(tmp_path, lambda _: httpx.Response(code, stream=chunks))
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          transport=transport) as provider:
        with pytest.raises(SemanticProviderError, match="http_error"):
            provider(REQUEST)
    assert chunks.reads == 0
    assert chunks.closed
    assert transport.records[-1]["status_code"] == code
    assert transport.summary()["usage_unknown_calls"] == 1
    assert "private error body" not in transport.ledger_path.read_text()


def test_production_response_cap_is_preserved_and_usage_remains_unknown(tmp_path):
    chunks = Chunks([b"a" * 65535, b"bb", b"must not be read"])
    transport = meter(tmp_path, lambda _: httpx.Response(200, stream=chunks))
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          transport=transport) as provider:
        with pytest.raises(SemanticProviderError, match="response_too_large"):
            provider(REQUEST)
    assert chunks.reads == 2
    assert chunks.closed
    assert transport.records[-1]["outcome"] == "response_too_large"
    assert transport.records[-1]["response_bytes_observed"] == 65537
    assert transport.summary()["usage_unknown_calls"] == 1
    assert len(transport.ledger_path.read_text()) < 1000


def test_early_close_does_not_drain_response_or_claim_usage(tmp_path):
    chunks = Chunks([json.dumps(completion()).encode()])
    transport = meter(tmp_path, lambda _: httpx.Response(200, stream=chunks))
    response = transport.handle_request(request())
    response.close()
    transport.close()
    assert chunks.reads == 0
    assert transport.records[-1]["outcome"] == "closed_before_complete"
    assert transport.summary()["usage_unknown_calls"] == 1


def test_transport_close_finishes_unread_active_responses(tmp_path):
    chunks = Chunks([b"private body"])
    transport = meter(tmp_path, lambda _: httpx.Response(200, stream=chunks))
    transport.handle_request(request())
    transport.close()
    transport.close()
    assert chunks.closed and chunks.reads == 0
    assert len(transport.records) == 2
    with pytest.raises(MeteringError, match="transport_closed"):
        transport.handle_request(request())


def test_transport_close_releases_stream_after_body_completed(tmp_path):
    chunks = Chunks([json.dumps(completion()).encode()])
    transport = meter(tmp_path, lambda _: httpx.Response(200, stream=chunks))
    response = transport.handle_request(request())
    list(response.stream)
    assert transport.records[-1]["usage_confirmed"]
    transport.close()
    assert chunks.closed
    assert len(transport.records) == 2


@pytest.mark.parametrize("during_stream", [False, True])
def test_network_errors_are_recorded_safely_once_without_retry(tmp_path, during_stream):
    count = 0

    def handle(incoming):
        nonlocal count
        count += 1
        error = httpx.ReadError("private failure detail", request=incoming)
        if during_stream:
            return httpx.Response(200, stream=Chunks([b"partial private text"], error=error))
        raise error

    transport = meter(tmp_path, handle)
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          transport=transport) as provider:
        with pytest.raises(SemanticProviderError, match="transport_error"):
            provider(REQUEST)
    assert count == 1 and len(transport.records) == 2
    assert transport.records[-1]["outcome"] == ("stream_error" if during_stream
                                               else "transport_error")
    assert "private" not in transport.ledger_path.read_text()


@pytest.mark.parametrize("limits,code", [
    ({"max_calls": 1}, "metering_call_limit"),
    ({"max_output_tokens": 48}, "metering_output_limit"),
])
def test_limits_survive_reopening_and_do_not_refund_success(tmp_path, limits, code):
    transport = meter(tmp_path, lambda _: httpx.Response(200, json=completion()), **limits)
    transport.handle_request(request(32))
    transport.close()
    restored = meter(tmp_path, lambda _: pytest.fail("Budget denial must precede network"), **limits)
    with pytest.raises(MeteringError, match=code):
        restored.handle_request(request(32))
    assert restored.summary()["reserved_output_tokens"] == 32
    assert restored.summary()["reserved_calls"] == 1


def test_interrupted_reservation_stays_in_budget_and_unknown(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text(json.dumps({"event": "reserved", "request_id": "interrupted",
                               "reserved_output_tokens": 50}) + "\n")
    transport = MeteredTransport(path, max_output_tokens=60,
                                 transport=httpx.MockTransport(lambda _: pytest.fail("No budget")))
    with pytest.raises(MeteringError, match="output_limit"):
        transport.handle_request(request(11))
    assert transport.summary()["usage_unknown_calls"] == 1


@pytest.mark.parametrize("body", [
    {}, {"max_tokens": 0}, {"max_tokens": True}, {"max_tokens": -1},
    {"max_tokens": 3.5}, {"max_tokens": "20"}, None, ["max_tokens"],
])
def test_explicit_valid_output_budget_required_before_sending(tmp_path, body):
    transport = meter(tmp_path, lambda _: pytest.fail("Invalid request must not be sent"))
    with pytest.raises(MeteringError, match="explicit_output_limit_required"):
        transport.handle_request(httpx.Request("POST", "https://example.test", json=body))
    assert transport.records == []


def test_both_output_fields_reserve_larger_value(tmp_path):
    transport = meter(tmp_path, lambda _: httpx.Response(200, json=completion()),
                      max_output_tokens=50)
    with pytest.raises(MeteringError, match="output_limit"):
        transport.handle_request(httpx.Request("POST", "https://example.test", json={
            "max_tokens": 10, "max_completion_tokens": 60,
        }))


@pytest.mark.parametrize("body", [b"partial json", b'{"error":"private error"}', b"[]"])
def test_invalid_success_payload_cannot_expose_text_or_confirm_usage(tmp_path, body):
    transport = meter(tmp_path, lambda _: httpx.Response(200, stream=Chunks([body])))
    with httpx.Client(transport=transport) as client:
        assert client.send(request()).content == body
    assert transport.summary()["usage_unknown_calls"] == 1
    assert "private error" not in transport.ledger_path.read_text()


def test_usage_only_accepts_allowlisted_nonnegative_integers(tmp_path):
    payload = completion(usage={
        "prompt_tokens": True, "completion_tokens": -1, "total_tokens": 5.5,
        "prompt_cache_hit_tokens": 4, "private": "secret-key",
        "completion_tokens_details": {"reasoning_tokens": 3, "reasoning_content": "private"},
    })
    transport = meter(tmp_path, lambda _: httpx.Response(200, json=payload))
    transport.handle_request(request())
    assert transport.records[-1]["usage"] == {
        "prompt_cache_hit_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 3},
    }
    assert not transport.records[-1]["usage_confirmed"]


def test_broken_ledger_blocks_network_instead_of_resetting_budget(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text('{"event":"reserved"')
    with pytest.raises(MeteringError, match="ledger_incomplete"):
        MeteredTransport(path, transport=httpx.MockTransport(lambda _: pytest.fail("No network")))


def test_distinct_transports_cannot_race_past_shared_budget(tmp_path):
    barrier = threading.Barrier(2)
    first = meter(tmp_path, lambda _: httpx.Response(200, json=completion()), max_calls=1)
    second = meter(tmp_path, lambda _: httpx.Response(200, json=completion()), max_calls=1)

    def send(transport):
        barrier.wait()
        try:
            transport.handle_request(request())
        except MeteringError as error:
            return str(error)
        return "sent"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(send, [first, second]))
    assert sorted(outcomes) == ["metering_call_limit", "sent"]
    assert first.summary()["reserved_calls"] == 1


def test_failed_reservation_flush_blocks_network_and_redacts_failure(tmp_path, monkeypatch):
    transport = meter(tmp_path, lambda _: pytest.fail("Failed reservation must not be sent"))

    def fail(_):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(_MODULE.os, "fsync", fail)
    with pytest.raises(MeteringError, match="metering_ledger_unavailable") as error:
        transport.handle_request(request())
    assert "private" not in str(error.value)
    assert transport.summary()["usage_unknown_calls"] == 1


def test_stream_close_failure_is_recorded_without_private_error_text(tmp_path):
    class BrokenClose(Chunks):
        def close(self):
            raise httpx.ReadError("private close failure")

    chunks = BrokenClose([b"private body"])
    transport = meter(tmp_path, lambda _: httpx.Response(200, stream=chunks))
    response = transport.handle_request(request())
    with pytest.raises(httpx.ReadError):
        response.close()
    assert transport.records[-1]["outcome"] == "stream_close_error"
    assert transport.summary()["usage_unknown_calls"] == 1
    assert "private" not in transport.ledger_path.read_text()
