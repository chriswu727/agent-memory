import hashlib

import pytest
from fastapi.testclient import TestClient

from membrain.api import create_app
from membrain.config import Settings
from membrain.helpers import Principal


class RecordingService:
    def __init__(self):
        self.calls = []

    def list_brains(self, principal):
        self.calls.append(principal)
        return []

    def create_brain(self, principal, name):
        self.calls.append((principal, name))
        return {"id": "brain", "name": name}


@pytest.fixture
def api_client():
    service = RecordingService()
    settings = Settings(
        database_url="unused",
        api_keys={hashlib.sha256(b"test-only-key").hexdigest(): Principal("tenant-a", "owner-a")},
        max_request_bytes=1000,
    )
    with TestClient(create_app(settings, service)) as client:
        yield client, service


def test_missing_or_invalid_key_never_reaches_service(api_client):
    client, service = api_client
    for headers in ({}, {"Authorization": "Bearer incorrect"}):
        assert client.get("/v1/brains", headers=headers).status_code == 401
    assert not service.calls


def test_principal_is_derived_from_key_and_not_request(api_client):
    client, service = api_client
    headers = {"Authorization": "Bearer test-only-key"}
    response = client.post("/v1/brains", headers=headers, json={"name": "我的大脑", "tenant_id": "victim"})
    assert response.status_code == 201
    assert service.calls == [(Principal("tenant-a", "owner-a"), "我的大脑")]


def test_action_identity_injection_rejected_before_dispatch(api_client):
    client, service = api_client
    response = client.post("/v1/actions", headers={"Authorization": "Bearer test-only-key"}, json={
        "message_type": "request", "contract_version": "1.0.0-draft", "request_id": "attempt",
        "action": "observe", "target": {"brain_ref": "b", "tenant_id": "victim"},
        "payload": {},
    })
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert not service.calls


def test_request_size_and_nonfinite_numbers_are_rejected(api_client):
    client, _ = api_client
    headers = {"Authorization": "Bearer test-only-key"}
    assert client.post("/v1/actions", headers=headers, content="x" * 1001).status_code == 413
    assert client.post("/v1/actions", headers=headers, content='{"value": NaN}').status_code == 422


def test_console_does_not_require_or_embed_credentials(api_client):
    client, _ = api_client
    page = client.get("/")
    assert page.status_code == 200
    assert "test-only-key" not in page.text
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/v1/brains", headers={"host": "attacker.invalid"}).status_code == 400
