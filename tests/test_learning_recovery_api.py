import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.client import MemoryClient
from agent_memory.config import Settings
from agent_memory.helpers import DomainError, Principal
from agent_memory.runtime_client import RuntimeClient

BASE = "/v1/brains/brain/runtime"
RETRY = BASE + "/jobs/blocked/retry-learning"
HEADERS = {"Authorization": "Bearer learning-test-key"}
PRINCIPAL = Principal("learning-tenant", "learning-owner")
BODY = {"request_id": "recover-1", "expected_fence": 1}


class Jobs:
    def __init__(self):
        self.calls = []
        self.failure = None

    def get(self, principal, brain_id, job_id):
        self.calls.append(("get", principal, brain_id, job_id))
        if self.failure:
            raise self.failure
        return {"id": job_id, "kind": "consolidate", "state": "cancelled", "fence": 1}

    def retry_learning(self, principal, brain_id, job_id, **body):
        self.calls.append(("retry", principal, brain_id, job_id, body))
        if self.failure:
            raise self.failure
        return {"id": "recovery", "kind": "consolidate", "state": "queued", "fence": 0,
                "max_model_calls": 2, "max_input_bytes": 196608,
                "payload": {"source": "private-input"}, "lease_token": "private-token"}

    def learning_progress(self, principal, brain_id, *, configured):
        self.calls.append(("progress", principal, brain_id, configured))
        return {"provider_state": "configured" if configured else "not_configured",
                "blocked_count": 1, "window_count": 1, "counts_complete": True,
                "blocked_reason_counts": {"provider_failed": 1, "private-error": 1},
                "content": "private-input", "blocked_examples": [{
                    "job_id": "blocked", "fence": 1, "from_sequence": 1, "through_sequence": 1,
                    "processed_through_sequence": 0, "last_error": "provider_failed",
                    "reason": "provider_failed", "content": "private-source",
                }]}


@pytest.fixture
def recovery_api():
    jobs = Jobs()
    service = SimpleNamespace(semantic_processor=object())
    settings = Settings(database_url="unused", max_request_bytes=2000, api_keys={
        hashlib.sha256(b"learning-test-key").hexdigest(): PRINCIPAL,
    })
    with TestClient(create_app(settings, service=service, jobs=jobs)) as client:
        yield client, jobs, service


def test_retry_is_authenticated_scoped_and_returns_safe_job(recovery_api):
    client, jobs, _ = recovery_api
    for headers in ({}, {"Authorization": "Bearer wrong-key"}):
        assert client.post(RETRY, headers=headers, json=BODY).status_code == 401
        assert client.get(BASE + "/learning-progress", headers=headers).status_code == 401
    assert jobs.calls == []
    response = client.post(RETRY, headers=HEADERS, json=BODY)
    assert response.status_code == 200
    assert response.json()["id"] == "recovery"
    assert "private-" not in response.text
    assert jobs.calls == [("retry", PRINCIPAL, "brain", "blocked", BODY)]


@pytest.mark.parametrize("body", [
    {}, {**BODY, "expected_fence": True}, {**BODY, "expected_fence": "1"},
    {**BODY, "expected_fence": -1}, {**BODY, "expected_fence": 2**63},
    {**BODY, "request_id": ""}, {**BODY, "request_id": 1},
    {**BODY, "request_id": "x" * 257}, {**BODY, "max_model_calls": 1000},
    {**BODY, "owner_id": "another-owner"},
])
def test_retry_rejects_invalid_or_budget_override_fields(recovery_api, body):
    client, jobs, _ = recovery_api
    assert client.post(RETRY, headers=HEADERS, json=body).status_code == 422
    assert jobs.calls == []


def test_retry_requires_configured_learning_after_scope_check(recovery_api):
    client, jobs, service = recovery_api
    service.semantic_processor = None
    assert client.post(RETRY, headers=HEADERS, json=BODY).status_code == 503
    assert jobs.calls == [("get", PRINCIPAL, "brain", "blocked")]
    jobs.failure = DomainError("not_found_or_forbidden", "Job unavailable.")
    assert client.post(RETRY, headers=HEADERS, json=BODY).status_code == 404


def test_progress_is_safe_and_reports_configuration_without_scheduling(recovery_api):
    client, jobs, service = recovery_api
    for configured in (True, False):
        service.semantic_processor = object() if configured else None
        response = client.get(BASE + "/learning-progress", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["provider_state"] == ("configured" if configured else "not_configured")
        assert response.json()["blocked_count"] == 1
        assert "private-" not in response.text
        assert jobs.calls[-1] == ("progress", PRINCIPAL, "brain", configured)
    assert all(call[0] == "progress" for call in jobs.calls)


def test_runtime_status_refilters_learning_progress(recovery_api):
    client, jobs, _ = recovery_api
    client.app.state.turns = SimpleNamespace(status=lambda *_: {
        "brain_id": "brain", "learning_progress": jobs.learning_progress(PRINCIPAL, "brain", configured=True),
    })
    response = client.get(BASE + "/status", headers=HEADERS)
    assert response.json()["learning_progress"]["blocked_count"] == 1
    assert "private-" not in response.text


def test_python_runtime_client_keeps_stable_retry_identity_and_encoded_scope():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "recovery"})

    with MemoryClient("http://memory.test", "key", "brain/a", transport=httpx.MockTransport(handle)) as memory:
        runtime = RuntimeClient(memory)
        runtime.learning_progress()
        runtime.job("job/a")
        for _ in range(2):
            assert runtime.retry_learning("job/a", **BODY)["id"] == "recovery"
    assert [request.method for request in requests] == ["GET", "GET", "POST", "POST"]
    prefix = b"/v1/brains/brain%2Fa/runtime/"
    assert requests[0].url.raw_path == prefix + b"learning-progress"
    assert requests[1].url.raw_path == prefix + b"jobs/job%2Fa"
    assert requests[2].url.raw_path == prefix + b"jobs/job%2Fa/retry-learning"
    assert json.loads(requests[2].content) == json.loads(requests[3].content) == BODY


def test_python_retry_does_not_repeat_a_lost_response():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown response", request=request)

    with MemoryClient("http://memory.test", "key", "brain", transport=httpx.MockTransport(handle)) as memory:
        with pytest.raises(httpx.ReadTimeout):
            RuntimeClient(memory).retry_learning("blocked", **BODY)
    assert len(calls) == 1
