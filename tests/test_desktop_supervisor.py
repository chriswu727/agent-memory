import json
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_memory.desktop import LocalSupervisor, _identity_signature, _logger, _private_write
from agent_memory.local_profile import LocalProfileError, ensure_profile


@pytest.fixture
def supervisor(tmp_path):
    profile = ensure_profile(tmp_path / "profile")
    return LocalSupervisor(profile, logging.getLogger("desktop-test"))


def test_control_endpoints_require_credentials_and_current_instance(supervisor, monkeypatch):
    monkeypatch.setattr(supervisor, "snapshot", lambda: {"ready": True})
    supervisor.server = SimpleNamespace(should_exit=False)
    with TestClient(supervisor.app(), base_url="http://127.0.0.1") as client:
        assert client.get("/v1/local/status").status_code == 401
        assert client.post("/v1/local/stop").status_code == 401
        headers = {"Authorization": "Bearer " + supervisor.profile.api_key}
        assert client.get("/v1/local/status", headers=headers).json() == {"ready": True}
        assert client.post("/v1/local/stop", headers=headers).status_code == 409
        assert not supervisor.stopped.is_set()
        assert not supervisor.server.should_exit
        headers["X-Memory-Instance"] = supervisor.instance_id
        assert client.post("/v1/local/stop", headers=headers).status_code == 200
        assert supervisor.stopped.is_set()
        assert supervisor.server.should_exit


def test_host_header_cannot_control_local_service(supervisor):
    with TestClient(supervisor.app(), base_url="http://foreign.example") as client:
        response = client.post("/v1/local/stop", headers={
            "Authorization": "Bearer " + supervisor.profile.api_key,
            "X-Memory-Instance": supervisor.instance_id,
        })
        assert response.status_code == 400
        assert not supervisor.stopped.is_set()


def test_local_identity_proves_key_possession_without_receiving_key(supervisor):
    challenge = "a" * 64
    with TestClient(supervisor.app(), base_url="http://127.0.0.1") as client:
        assert client.get("/v1/local/identity").status_code == 422
        response = client.get("/v1/local/identity", headers={"X-Memory-Challenge": challenge})
        assert response.status_code == 200
        document = response.json()
        assert document["signature"] == _identity_signature(supervisor.profile, supervisor.instance_id, challenge)
        assert document["signature"] != _identity_signature(supervisor.profile, supervisor.instance_id, "b" * 64)
        assert supervisor.profile.api_key not in response.text
        assert supervisor.profile.admin_password not in response.text


def test_database_failure_and_dead_worker_are_not_ready(supervisor, monkeypatch):
    import agent_memory.storage

    def unavailable(*args, **kwargs):
        raise RuntimeError("secret connection information")

    monkeypatch.setattr(agent_memory.storage.Database, "transaction", unavailable)
    result = supervisor.snapshot()
    assert result["database"] == "unavailable"
    assert result["worker"] == "stopped"
    assert result["ready"] is False
    assert "secret" not in json.dumps(result)
    assert supervisor.profile.api_key not in json.dumps(result)


def test_worker_failure_records_fixed_code_and_stays_not_ready(supervisor, monkeypatch, caplog):
    import agent_memory.desktop

    def unavailable(*args):
        raise RuntimeError("secret conversation content")

    monkeypatch.setattr(agent_memory.desktop, "configured_service", unavailable)
    with caplog.at_level(logging.INFO):
        supervisor.work()
    assert supervisor.worker_state == "failed"
    assert "worker_failed" in caplog.text
    assert "secret conversation" not in caplog.text


def test_logs_are_bounded_structured_and_do_not_contain_profile_secrets(supervisor):
    logger, handler = _logger(supervisor.profile)
    try:
        supervisor.logger = logger
        supervisor.log("service_started")
        assert handler.maxBytes == 1_000_000
        assert handler.backupCount == 3
        document = json.loads((supervisor.profile.directory / "service.log").read_text())
        assert set(document) == {"time", "event", "instance_id"}
        assert document["event"] == "service_started"
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_managed_file_refuses_hard_link_before_truncating(tmp_path):
    original = tmp_path / "original"
    original.write_text("keep me")
    linked = tmp_path / "linked"
    linked.hardlink_to(original)
    with pytest.raises(LocalProfileError):
        _private_write(linked, "replace")
    assert original.read_text() == "keep me"


def test_log_refuses_hard_link(supervisor, tmp_path):
    original = tmp_path / "original"
    original.write_text("keep me")
    (supervisor.profile.directory / "service.log").hardlink_to(original)
    with pytest.raises(LocalProfileError):
        _logger(supervisor.profile)
    assert original.read_text() == "keep me"
