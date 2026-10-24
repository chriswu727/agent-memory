import copy
import json

import httpx
import pytest
from test_runtime_api import HEADERS, PRINCIPAL, TURN
from test_runtime_api import runtime_api as runtime_api

from agent_memory.api import runtime_metadata
from agent_memory.client import MemoryClient
from agent_memory.contracts import validate
from agent_memory.helpers import DomainError
from agent_memory.runtime_client import RuntimeClient

RESERVE = {
    "lease_token": "lease", "expected_revision": 1, "policy_fingerprint": "a" * 64,
    "request_fingerprint": "b" * 64, "draft_sha256": "c" * 64,
}
COMPLETE = {
    "lease_token": "lease", "expected_revision": 2, "request_fingerprint": "b" * 64,
    "status": "approved", "issue_codes": [],
}
AUDIT = {
    "status": "pending", "policy_fingerprint": "a" * 64, "request_fingerprint": "b" * 64,
    "draft_sha256": "c" * 64, "context_fingerprint": "d" * 64,
    "context_generation": 0, "working_revision": 0, "issue_codes": [], "not_a_truth_certificate": True,
}


def connect_recorders(turns, monkeypatch):
    for operation in ("reserve_response_review", "complete_response_review"):
        def record(principal, brain_id, turn_id, _operation=operation, **payload):
            return {**turns.record(_operation, principal, brain_id, turn_id, **payload),
                    "response_review_policy": "a" * 64, "response_review": AUDIT,
                    "response_review_calls": 1}
        monkeypatch.setattr(turns, operation, record, raising=False)


@pytest.mark.parametrize(("route", "body", "operation"), [
    ("reserve", RESERVE, "reserve_response_review"),
    ("complete", COMPLETE, "complete_response_review"),
])
def test_review_routes_authenticate_and_dispatch_exact_metadata(runtime_api, monkeypatch, route, body, operation):
    client, turns = runtime_api
    connect_recorders(turns, monkeypatch)
    path = TURN + "/response-review/" + route
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        assert client.post(path, json=body, headers=headers).status_code == 401
    assert not turns.calls
    response = client.post(path, json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["response_review"] == AUDIT
    assert response.json()["response_review_calls"] == 1
    assert "private-" not in response.text
    assert turns.calls == [(operation, PRINCIPAL, "brain", "turn", body)]


@pytest.mark.parametrize(("route", "body"), [
    ("reserve", {**RESERVE, "expected_revision": True}),
    ("reserve", {**RESERVE, "policy_fingerprint": "A" * 64}),
    ("reserve", {**RESERVE, "draft_sha256": "private draft"}),
    ("reserve", {**RESERVE, "request_fingerprint": 12}),
    ("reserve", {**RESERVE, "context_fingerprint": "d" * 64}),
    ("complete", {**COMPLETE, "expected_revision": "2"}),
    ("complete", {**COMPLETE, "status": "pending"}),
    ("complete", {**COMPLETE, "issue_codes": "applicability_override"}),
    ("complete", {**COMPLETE, "issue_codes": ["private source text"]}),
    ("complete", {**COMPLETE, "issue_codes": ["review_unavailable"] * 17}),
    ("complete", {**COMPLETE, "model_reasoning": "private reasoning"}),
])
def test_review_bodies_reject_coercion_and_arbitrary_text_before_dispatch(runtime_api, route, body):
    client, turns = runtime_api
    response = client.post(TURN + "/response-review/" + route, json=body, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert not turns.calls


@pytest.mark.parametrize("change", [
    {"status": "approved", "issue_codes": ["applicability_override"]},
    {"status": "rejected", "issue_codes": []},
    {"status": "inconclusive", "issue_codes": []},
    {"status": "rejected", "issue_codes": ["review_unavailable"] * 2},
    {"issue_codes": ["private source text"]},
    {"not_a_truth_certificate": False},
    {"draft_text": "private draft"},
    {"working_revision": True},
    {"context_generation": -1},
    {"policy_fingerprint": "invalid"},
])
def test_monitoring_contract_rejects_malformed_or_content_bearing_review(change):
    with pytest.raises(DomainError) as error:
        runtime_metadata({"response_review": {**AUDIT, **change}})
    assert error.value.code == "validation_failed"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "inconclusive"])
def test_review_contract_preserves_valid_nontruth_audit(status):
    audit = {**AUDIT, "status": status,
             "issue_codes": ["review_unavailable"] if status in {"rejected", "inconclusive"} else []}
    state = {"response_review_policy": "a" * 64, "response_review": audit, "response_review_calls": 1}
    validate("RuntimeResponseReviewState", state)
    assert runtime_metadata({**state, "hidden_reasoning": "secret"}) == state
    validate("RuntimeResponseReviewState", {"response_review_policy": None, "response_review": None,
                                             "response_review_calls": 0})


def test_client_wires_prepared_policy_and_review_requests_with_no_implicit_retries(runtime_api, monkeypatch):
    api, turns = runtime_api
    connect_recorders(turns, monkeypatch)

    def handle(request):
        assert request.headers["authorization"] == HEADERS["Authorization"]
        response = api.request(request.method, request.url.raw_path.decode(),
                               content=request.content, headers=HEADERS)
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    with MemoryClient("http://memory.test", "runtime-test-key", "brain",
                      transport=httpx.MockTransport(handle)) as memory:
        runtime = RuntimeClient(memory)
        runtime.advance("turn", stage="prepared", lease_token="lease", expected_revision=1,
                        response_review_policy="a" * 64)
        runtime.reserve_response_review("turn", **RESERVE)
        runtime.complete_response_review("turn", **COMPLETE)
    assert turns.calls[0][-1]["response_review_policy"] == "a" * 64
    assert turns.calls[1][-1] == RESERVE
    assert turns.calls[2][-1] == COMPLETE

    calls = []

    def lost_response(request):
        calls.append(request)
        raise httpx.ReadTimeout("Review response was lost", request=request)

    with MemoryClient("http://memory.test", "key", "brain",
                      transport=httpx.MockTransport(lost_response)) as memory:
        runtime = RuntimeClient(memory)
        for method, body in ((runtime.reserve_response_review, RESERVE),
                             (runtime.complete_response_review, COMPLETE)):
            with pytest.raises(httpx.ReadTimeout):
                method("turn", **copy.deepcopy(body))
    assert len(calls) == 2
    assert [json.loads(request.content) for request in calls] == [RESERVE, COMPLETE]
