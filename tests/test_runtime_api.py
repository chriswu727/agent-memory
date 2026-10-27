import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from membrain.api import create_app
from membrain.client import MemoryClient
from membrain.config import Settings
from membrain.helpers import DomainError, Principal
from membrain.runtime_client import RuntimeClient

BASE = "/v1/brains/brain/runtime"
TURN = BASE + "/turns/turn"
HEADERS = {"Authorization": "Bearer runtime-test-key"}
PRINCIPAL = Principal("runtime-tenant", "runtime-owner")
BEGIN = {
    "turn_id": "turn", "conversation_id": "conversation", "branch_id": "branch",
    "input_fingerprint": "a" * 64,
}
RESERVATION = {
    "lease_token": "lease", "expected_revision": 1, "input_bytes": 100,
    "max_model_calls": 2, "max_input_bytes": 200, "context_generation": 0,
    "source_refs": [{"event_id": "event", "revision": 1}], "request_fingerprint": "c" * 64,
}


class RecordingTurns:
    def __init__(self):
        self.calls = []
        self.failure = None

    def record(self, operation, principal, brain_id, turn_id=None, **payload):
        self.calls.append((operation, principal, brain_id, turn_id, payload))
        if self.failure:
            raise self.failure
        return {
            "turn_id": turn_id or "turn", "brain_id": brain_id, "stage": "started",
            "revision": 1, "lease_active": True, "lease_token": "private-lease-token",
            "lease_token_digest": "private-lease-digest", "raw_content": "private-input",
            "content": "private-input", "hidden_reasoning": "private-reasoning",
            "input_event_refs": [{"event_id": "event", "revision": 1, "content": "private-input"}],
            "sources_available": True,
        }

    def begin(self, principal, brain_id, **payload):
        return self.record("begin", principal, brain_id, **payload)

    def get(self, principal, brain_id, turn_id):
        return self.record("get", principal, brain_id, turn_id)

    def list(self, principal, brain_id, *, limit=100):
        return [self.record("list", principal, brain_id, limit=limit)]

    def history(self, principal, brain_id, turn_id, *, limit=100):
        result = self.record("history", principal, brain_id, turn_id, limit=limit)
        return [{**result, "operation": "begin", "fence": 0, "occurred_at": "2026-10-22T00:00:00Z"}]

    def claim(self, principal, brain_id, turn_id, **payload):
        return self.record("claim", principal, brain_id, turn_id, **payload)

    def renew(self, principal, brain_id, turn_id, **payload):
        return self.record("renew", principal, brain_id, turn_id, **payload)

    def advance(self, principal, brain_id, turn_id, **payload):
        return self.record("advance", principal, brain_id, turn_id, **payload)

    def reserve_recall(self, principal, brain_id, turn_id, **payload):
        return self.record("reserve_recall", principal, brain_id, turn_id, **payload)

    def release(self, principal, brain_id, turn_id, **payload):
        return self.record("release", principal, brain_id, turn_id, **payload)

    def record_output(self, principal, brain_id, turn_id, **payload):
        return {**self.record("record_output", principal, brain_id, turn_id, **payload),
                "text": payload["text"]}

    def fail(self, principal, brain_id, turn_id, **payload):
        return self.record("fail", principal, brain_id, turn_id, **payload)

    def finish(self, principal, brain_id, turn_id, **payload):
        return self.record("finish", principal, brain_id, turn_id, **payload)

    def status(self, principal, brain_id):
        self.record("status", principal, brain_id)
        return {"brain_id": brain_id, "turn_count": 1, "stage_counts": {"started": 1},
                "lease_token": "private-lease-token", "content": "private-input"}

    def sources(self, principal, brain_id, turn_id):
        self.record("sources", principal, brain_id, turn_id)
        return {"input_events": [{"event_id": "event", "content": "private-input"}], "output_events": []}


@pytest.fixture
def runtime_api():
    turns = RecordingTurns()
    settings = Settings(
        database_url="unused", max_request_bytes=2000,
        api_keys={hashlib.sha256(b"runtime-test-key").hexdigest(): PRINCIPAL},
    )
    with TestClient(create_app(settings, service=object(), turns=turns)) as client:
        yield client, turns


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", BASE + "/turns", BEGIN),
    ("GET", BASE + "/turns", None),
    ("GET", BASE + "/status", None),
    ("GET", TURN, None),
    ("GET", TURN + "/history", None),
    ("GET", TURN + "/sources", None),
    ("POST", TURN + "/claim", {"worker_id": "worker"}),
    ("POST", TURN + "/renew", {"lease_token": "lease"}),
    ("POST", TURN + "/advance", {"lease_token": "lease", "expected_revision": 1, "stage": "captured"}),
    ("POST", TURN + "/recall-reservations", RESERVATION),
    ("POST", TURN + "/release", {"lease_token": "lease", "expected_revision": 1}),
    ("POST", TURN + "/finish", {"lease_token": "lease", "expected_revision": 1}),
    ("POST", TURN + "/output", {"lease_token": "lease", "expected_revision": 1,
                                "text": "private-output", "delivery_state": "draft"}),
    ("POST", TURN + "/fail", {"lease_token": "lease", "expected_revision": 1, "failure_code": "cancelled"}),
])
def test_every_runtime_endpoint_requires_authenticated_principal(runtime_api, method, path, body):
    client, turns = runtime_api
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        assert client.request(method, path, json=body, headers=headers).status_code == 401
    assert not turns.calls
    response = client.request(method, path, json=body, headers=HEADERS)
    assert response.status_code in {200, 201}
    assert turns.calls[-1][1:3] == (PRINCIPAL, "brain")


@pytest.mark.parametrize(("path", "body"), [
    (BASE + "/turns", {**BEGIN, "principal_id": "victim"}),
    (BASE + "/turns", {**BEGIN, "turn_id": 123}),
    (BASE + "/turns", {**BEGIN, "input_fingerprint": "not-a-digest"}),
    (TURN + "/claim", {"worker_id": "worker", "lease_seconds": "30"}),
    (TURN + "/claim", {"worker_id": "worker", "lease_seconds": True}),
    (TURN + "/claim", {"worker_id": "worker", "lease_seconds": 301}),
    (TURN + "/claim", {"worker_id": "worker", "expected_revision": False}),
    (TURN + "/renew", {"lease_token": 22}),
    (TURN + "/release", {"lease_token": "lease", "expected_revision": 1, "brain_id": "victim"}),
    (TURN + "/finish", {"lease_token": "lease", "expected_revision": 0}),
    (TURN + "/output", {"lease_token": "lease", "expected_revision": 1,
                        "text": 42, "delivery_state": "draft"}),
    (TURN + "/output", {"lease_token": "lease", "expected_revision": 1,
                        "text": "draft", "delivery_state": "unknown"}),
    (TURN + "/output", {"lease_token": "lease", "expected_revision": 1,
                        "text": "draft", "delivery_state": "draft", "actor_role": "user"}),
    (TURN + "/fail", {"lease_token": "lease", "expected_revision": 1, "failure_code": "private-error-content"}),
    (TURN + "/advance", {"lease_token": "lease", "expected_revision": 1, "stage": "completed"}),
    (TURN + "/advance", {"lease_token": "lease", "expected_revision": 1, "stage": "captured",
                          "input_event_refs": [{"event_id": "event", "revision": 1, "content": "injected"}]}),
    (TURN + "/advance", {"lease_token": "lease", "expected_revision": 1, "stage": "prepared",
                          "context_generation": "2"}),
    (TURN + "/advance", {"lease_token": "lease", "expected_revision": 1, "stage": "prepared",
                          "context_event_refs": [{"event_id": "event", "revision": True}]}),
    (TURN + "/recall-reservations", {**RESERVATION, "input_bytes": True}),
    (TURN + "/recall-reservations", {**RESERVATION, "input_bytes": 0}),
    (TURN + "/recall-reservations", {**RESERVATION, "input_bytes": 262145}),
    (TURN + "/recall-reservations", {**RESERVATION, "max_model_calls": 9}),
    (TURN + "/recall-reservations", {**RESERVATION, "max_input_bytes": 262145}),
    (TURN + "/recall-reservations", {**RESERVATION, "request_fingerprint": "private query"}),
    (TURN + "/recall-reservations", {**RESERVATION, "context_generation": "0"}),
    (TURN + "/recall-reservations", {**RESERVATION, "source_refs": [{"event_id": "event", "revision": 1,
                                                                 "text": "Private source"}]}),
    (TURN + "/recall-reservations", {**RESERVATION, "query": "Private query"}),
])
def test_runtime_bodies_reject_extra_fields_and_coercion_before_dispatch(runtime_api, path, body):
    client, turns = runtime_api
    response = client.post(path, headers=HEADERS, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert not turns.calls


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=true", "limit=1.5", "limit=1&limit=2", "tenant_id=victim"])
def test_runtime_collection_limits_are_bounded(runtime_api, query):
    client, turns = runtime_api
    for path in (BASE + "/turns", TURN + "/history"):
        assert client.get(path + "?" + query, headers=HEADERS).status_code == 422
    assert not turns.calls


def test_monitoring_contains_only_metadata_and_claim_alone_reveals_lease(runtime_api):
    client, _ = runtime_api
    for path in (BASE + "/turns", BASE + "/status", TURN, TURN + "/history"):
        response = client.get(path, headers=HEADERS)
        assert response.status_code == 200
        assert "private-" not in response.text
        assert "lease_token" not in response.text
    claim = client.post(TURN + "/claim", headers=HEADERS, json={"worker_id": "worker"}).json()
    assert claim["lease_token"] == "private-lease-token"
    assert "lease_token_digest" not in claim
    assert "content" not in claim
    assert claim["input_event_refs"] == [{"event_id": "event", "revision": 1}]
    sources = client.get(TURN + "/sources", headers=HEADERS)
    assert sources.json()["input_events"][0]["content"] == "private-input"


def test_runtime_dispatch_is_explicit_and_errors_preserve_conflict_revision(runtime_api):
    client, turns = runtime_api
    for operation in ("_transaction", "record", "delete", "__class__"):
        assert client.post(TURN + "/" + operation, headers=HEADERS, json={}).status_code == 404
    assert not turns.calls
    turns.failure = DomainError("lease_conflict", "Lease expired", current_revision=4)
    response = client.post(TURN + "/claim", headers=HEADERS, json={"worker_id": "worker"})
    assert response.status_code == 409
    assert response.json()["error"]["current_revision"] == 4


def test_runtime_body_size_and_nonfinite_numbers_rejected(runtime_api):
    client, turns = runtime_api
    assert client.post(BASE + "/turns", headers=HEADERS, content="x" * 2001).status_code == 413
    assert client.post(TURN + "/claim", headers=HEADERS, content='{"lease_seconds":NaN}').status_code == 422
    assert not turns.calls


def test_runtime_client_uses_existing_transport_for_all_operations(runtime_api):
    api, turns = runtime_api

    def handle(request):
        assert request.headers["authorization"] == HEADERS["Authorization"]
        response = api.request(
            request.method, request.url.raw_path.decode(), content=request.content, headers=HEADERS,
        )
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    with MemoryClient("http://memory.test", "runtime-test-key", "brain",
                      transport=httpx.MockTransport(handle)) as memory:
        runtime = RuntimeClient(memory)
        runtime.begin(**BEGIN)
        assert runtime.get("turn")["turn_id"] == "turn"
        assert len(runtime.list(limit=5)) == 1
        assert len(runtime.history("turn", limit=7)) == 1
        assert runtime.status()["turn_count"] == 1
        assert runtime.sources("turn")["output_events"] == []
        claimed = runtime.claim("turn", worker_id="worker", lease_seconds=60, expected_revision=1)
        token = claimed["lease_token"]
        runtime.renew("turn", lease_token=token, lease_seconds=45, expected_revision=2)
        runtime.advance(
            "turn", lease_token=token, expected_revision=2, stage="captured",
            input_event_refs=[{"event_id": "event", "revision": 1}], output_event_refs=[],
            context_event_refs=[], context_fingerprint="b" * 64, context_generation=0,
            working_revision=0, durable_sequence=1, context_tokens=0,
        )
        runtime.release("turn", lease_token=token, expected_revision=3)
        runtime.fail("turn", lease_token=token, expected_revision=4, failure_code="cancelled")
        runtime.finish("turn", lease_token=token, expected_revision=5)
        recorded = runtime.record_output(
            "turn", lease_token=token, expected_revision=6, text="一段真实交付。", delivery_state="delivered",
        )
        assert "text" not in recorded
        assert "lease_token" not in recorded
        reserved = runtime.reserve_recall("turn", **RESERVATION)
        assert "lease_token" not in reserved
    assert [call[0] for call in turns.calls] == [
        "begin", "get", "list", "history", "status", "sources", "claim", "renew", "advance",
        "release", "fail", "finish", "record_output", "reserve_recall",
    ]
    assert turns.calls[2][-1] == {"limit": 5}
    assert turns.calls[3][-1] == {"limit": 7}
    assert turns.calls[8][-1]["context_tokens"] == 0
    assert turns.calls[8][-1]["output_event_refs"] == []
    assert turns.calls[8][-1]["context_event_refs"] == []
    assert turns.calls[12][-1] == {
        "lease_token": token, "expected_revision": 6,
        "text": "一段真实交付。", "delivery_state": "delivered",
    }
    assert turns.calls[13][-1] == RESERVATION


@pytest.mark.parametrize("delivery_state", ["draft", "delivered", "partially_delivered", "cancelled"])
def test_output_preserves_delivery_state_and_returns_only_metadata(runtime_api, delivery_state):
    client, turns = runtime_api
    response = client.post(TURN + "/output", headers=HEADERS, json={
        "lease_token": "lease", "expected_revision": 1, "text": "private-output",
        "delivery_state": delivery_state,
    })
    assert response.status_code == 200
    assert "private-" not in response.text
    assert turns.calls[-1][-1]["delivery_state"] == delivery_state
    assert turns.calls[-1][-1]["text"] == "private-output"


def test_output_text_character_bound_is_enforced_before_storage():
    turns = RecordingTurns()
    settings = Settings(
        database_url="unused", max_request_bytes=100000,
        api_keys={hashlib.sha256(b"runtime-test-key").hexdigest(): PRINCIPAL},
    )
    with TestClient(create_app(settings, service=object(), turns=turns)) as client:
        body = {"lease_token": "lease", "expected_revision": 1,
                "delivery_state": "draft", "text": "x" * 64000}
        assert client.post(TURN + "/output", headers=HEADERS, json=body).status_code == 200
        body["text"] += "x"
        assert client.post(TURN + "/output", headers=HEADERS, json=body).status_code == 422
    assert len(turns.calls) == 1


def test_runtime_client_requires_brain_and_never_retries_ambiguous_writes():
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    with MemoryClient("http://memory.test", "key", transport=httpx.MockTransport(fail)) as memory:
        with pytest.raises(ValueError, match="brain_id"):
            RuntimeClient(memory).get("turn")
        assert not calls
        memory.brain_id = "brain"
        with pytest.raises(httpx.ReadTimeout):
            RuntimeClient(memory).claim("turn", worker_id="worker")
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {"worker_id": "worker", "lease_seconds": 30}
