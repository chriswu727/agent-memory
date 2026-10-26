import gzip
import hashlib
import importlib
import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from test_semantic_provider import REQUEST

from agent_memory.runtime import ModelRequest
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


@pytest.mark.parametrize("encoding", ["identity", "gzip", "preconsumed"])
def test_explicit_larger_envelope_records_usage_without_reasoning_content(tmp_path, encoding):
    payload = completion()
    payload["choices"][0]["message"]["reasoning_content"] = "private envelope reasoning " * 4096
    raw = json.dumps(payload).encode()
    assert 65536 < len(raw) < 262144
    body = gzip.compress(raw) if encoding == "gzip" else raw
    if encoding == "preconsumed":
        response = httpx.Response(200, content=body)
    else:
        response = httpx.Response(200, stream=Chunks([body[:4096], body[4096:]]),
                                  headers={"Content-Encoding": encoding})
    transport = meter(tmp_path, lambda _: response, max_response_bytes=262144, capture_content=False)
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          max_response_bytes=262144, transport=transport) as provider:
        assert provider(REQUEST) == '{"claims":[]}'
        assert provider.last_usage == payload["usage"]
    record = transport.records[-1]
    assert record["outcome"] == "body_complete" and record["usage_confirmed"] is True
    assert record["raw_response_bytes_observed"] == len(body)
    assert record["response_bytes_observed"] == len(raw)
    assert transport.summary()["usage_unknown_calls"] == 0
    assert transport.summary()["usage_totals"]["total_tokens"] == 28
    assert "private" not in transport.ledger_path.read_text()
    assert "assistant_content" not in record and "reasoning_content" not in record


@pytest.mark.parametrize("encoding", ["identity", "gzip_decoded", "gzip_raw"])
def test_larger_envelope_still_bounds_wire_and_decompressed_bytes(tmp_path, encoding):
    limit = 262144
    if encoding == "identity":
        body = b"x" * (limit + 1)
    elif encoding == "gzip_decoded":
        body = gzip.compress(b"x" * (limit + 1))
    else:
        compressed = gzip.compress(json.dumps(completion()).encode())
        body = compressed[:3] + b"\x08" + compressed[4:10] + b"x" * limit + b"\x00" + compressed[10:]
    stream = Chunks([body, b"must not be read"])
    response = httpx.Response(200, stream=stream, headers={
        "Content-Encoding": "identity" if encoding == "identity" else "gzip",
    })
    transport = meter(tmp_path, lambda _: response, max_response_bytes=limit, capture_content=False)
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          max_response_bytes=limit, transport=transport) as provider:
        with pytest.raises(SemanticProviderError, match="response_too_large"):
            provider(REQUEST)
        assert provider.last_usage is None
    record = transport.records[-1]
    assert record["outcome"] == "response_too_large" and record["usage_confirmed"] is False
    assert record["raw_response_bytes_observed"] == len(body)
    assert record["response_bytes_observed"] <= limit + 1
    assert stream.reads == 1 and stream.closed
    assert transport.summary()["usage_unknown_calls"] == 1
    assert transport.summary()["usage_totals"] == {}


@pytest.mark.parametrize("limit", [None, True, 0, -1, 1.5, "262144", 1048577])
def test_invalid_meter_envelope_limit_is_rejected_before_creating_ledger(tmp_path, limit):
    with pytest.raises(ValueError, match="max_response_bytes"):
        meter(tmp_path, lambda _: pytest.fail("No request expected"), max_response_bytes=limit)
    assert not (tmp_path / "run").exists()


def test_increasing_envelope_limit_cannot_settle_or_retry_prior_unknown_call(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    evaluation = importlib.import_module("examples.evaluate_longitudinal")
    calls = []
    stream = Chunks([b"x" * 65537, b"must not be read"])

    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=stream)

    with evaluation.CampaignJournal(tmp_path / "campaign", evaluation.plan()) as journal:
        path = journal.path / "calls.jsonl"
        transport = MeteredTransport(path, transport=httpx.MockTransport(handle), capture_content=False)
        with ChatCompletionsSemanticProvider("https://example.test", "fake-key", "model",
                                              transport=transport) as provider:
            with pytest.raises(SemanticProviderError, match="response_too_large"):
                evaluation.RecordedProvider(journal, transport, provider, "cognition")(REQUEST)
        original = path.read_bytes()
        restored = MeteredTransport(path, max_response_bytes=262144,
                                    transport=httpx.MockTransport(lambda _: pytest.fail("No retry permitted")))
        with restored:
            with pytest.raises(evaluation.CampaignStopped, match="unknown_provider_usage"):
                evaluation.RecordedProvider(
                    journal, restored, lambda _: pytest.fail("Unknown call cannot be retried"), "cognition",
                )(REQUEST)
            assert restored.summary()["reserved_calls"] == restored.summary()["usage_unknown_calls"] == 1
            assert restored.summary()["usage_confirmed_calls"] == 0
            assert restored.summary()["usage_totals"] == {}
        assert path.read_bytes() == original
    assert len(calls) == stream.reads == 1


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


@pytest.mark.parametrize("usage", [
    {"completion_tokens": 8},
    {"prompt_tokens": 20, "completion_tokens": 8},
    {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 9},
    {"prompt_tokens": True, "completion_tokens": 8, "total_tokens": 9},
    {"prompt_tokens": 2**63, "completion_tokens": 8, "total_tokens": 2**63 + 8},
    {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
     "prompt_cache_hit_tokens": 21},
    {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
     "prompt_cache_hit_tokens": 10, "prompt_cache_miss_tokens": 9},
    {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
     "completion_tokens_details": {"reasoning_tokens": 9}},
    {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
     "prompt_tokens_details": []},
])
def test_partial_or_inconsistent_usage_remains_observed_but_not_confirmed(tmp_path, usage):
    transport = meter(tmp_path, lambda _: httpx.Response(200, json=completion(usage=usage)))
    with ChatCompletionsSemanticProvider("https://example.test", "secret-key", "model",
                                          transport=transport) as provider:
        assert provider(REQUEST) == '{"claims":[]}'
        assert provider.last_usage is None
    record = transport.records[-1]
    assert record["usage"]["completion_tokens"] == 8
    assert record["usage_confirmed"] is False
    assert transport.summary()["usage_unknown_calls"] == 1
    assert transport.summary()["usage_totals"] == {}


def test_known_totals_exclude_invalid_observations_without_refunding_reservation(tmp_path):
    responses = iter([completion(), completion(usage={"completion_tokens": 90})])
    transport = meter(tmp_path, lambda _: httpx.Response(200, json=next(responses)))
    with httpx.Client(transport=transport) as client:
        client.send(request())
        client.send(request())
    summary = transport.summary()
    assert summary["reserved_calls"] == summary["completed_calls"] == 2
    assert summary["reserved_output_tokens"] == 64
    assert summary["usage_confirmed_calls"] == summary["usage_unknown_calls"] == 1
    assert summary["usage_totals"] == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
    assert transport.records[-1]["usage"] == {"completion_tokens": 90}


@pytest.mark.parametrize("confirmed,usage", [
    (True, {"completion_tokens": 8}),
    (True, {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 9}),
    ("true", {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}),
])
def test_reopening_revalidates_claimed_confirmation_without_rewriting_evidence(tmp_path, confirmed, usage):
    path = tmp_path / "usage.jsonl"
    records = [
        {"event": "reserved", "request_id": "old", "reserved_output_tokens": 32},
        {"event": "completed", "request_id": "old", "usage_confirmed": confirmed, "usage": usage},
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in records))
    original = path.read_bytes()
    with MeteredTransport(path, transport=httpx.MockTransport(lambda _: pytest.fail("No network"))) as transport:
        assert transport.summary()["usage_unknown_calls"] == 1
        assert transport.summary()["usage_totals"] == {}
        assert transport.records == records
    assert path.read_bytes() == original


@pytest.mark.parametrize("body", [
    b'{"usage":{"completion_tokens":1},"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}',
    b'{"usage":{"prompt_tokens":2,"completion_tokens":9,"completion_tokens":1,"total_tokens":3}}',
    b'{"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3},"other":NaN}',
])
def test_ambiguous_usage_json_never_confirms_accounting(tmp_path, body):
    transport = meter(tmp_path, lambda _: httpx.Response(200, content=body))
    with httpx.Client(transport=transport) as client:
        assert client.send(request()).content == body
    assert transport.summary()["usage_unknown_calls"] == 1
    assert transport.summary()["usage_totals"] == {}


@pytest.mark.parametrize("role", ["host", "user", "cognition"])
@pytest.mark.parametrize("usage", [
    {"completion_tokens": 8},
    {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 9},
    {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
     "prompt_cache_hit_tokens": 21},
])
def test_invalid_usage_stops_every_subsequent_role(tmp_path, monkeypatch, role, usage):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    evaluation = importlib.import_module("examples.evaluate_longitudinal")
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completion(usage=usage))

    with evaluation.CampaignJournal(tmp_path / "run", evaluation.plan()) as journal:
        transport = MeteredTransport(journal.path / "calls.jsonl", transport=httpx.MockTransport(provider))
        provider_type = ChatCompletionsSemanticProvider if role == "cognition" else evaluation.ChatCompletionsHost
        actual_request = (replace(REQUEST, response_schema={"type": "object"}) if role == "cognition"
                          else ModelRequest("turn", "request", ({"role": "user", "content": "Hello"},), 5, ""))
        with provider_type("https://example.test", "fake-key", "model", transport=transport) as client:
            with pytest.raises(evaluation.CampaignStopped, match="unknown_provider_usage"):
                evaluation.RecordedProvider(journal, transport, client, role)(actual_request)
            for next_role in ("host", "user", "cognition"):
                with pytest.raises(evaluation.CampaignStopped, match="unknown_provider_usage"):
                    evaluation.RecordedProvider(
                        journal, transport, lambda _: pytest.fail("Unknown usage must stop all roles"), next_role,
                    )(actual_request)
        assert len(calls) == 1
        assert transport.summary()["reserved_calls"] == transport.summary()["usage_unknown_calls"] == 1
        assert transport.summary()["usage_totals"] == {}
        assert [event["kind"] for event in journal.events] == ["provider_started", "provider_completed"]


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
