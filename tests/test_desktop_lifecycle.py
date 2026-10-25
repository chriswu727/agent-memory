import itertools
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from agent_memory import desktop, desktop_cli
from agent_memory.local_profile import (
    LocalProfileBusy,
    LocalProfileError,
    configure_cognition,
    ensure_profile,
    load_profile,
)


@pytest.fixture
def profile(tmp_path):
    return ensure_profile(tmp_path)


def ready(profile, **changes):
    return {**desktop._base_status(profile), "ready": True, "state": "ready",
            "database": "ready", "worker": "ready", "instance_id": "test-instance",
            "configuration": desktop._fingerprint(profile), **changes}


def identity(profile, request, **changes):
    return {"installation_id": profile.installation_id, "brain_id": profile.brain_id,
            "instance_id": "test-instance", "signature": desktop._identity_signature(
                profile, "test-instance", request.headers["X-Memory-Challenge"],
            ), **changes}


def forbid(*args, **kwargs):
    pytest.fail("This operation must not run")


def fake_http(monkeypatch, handler):
    client = httpx.Client
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(desktop.httpx, "Client", factory)
    return options


def test_start_is_idempotent_for_healthy_matching_installation(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: ready(candidate))
    monkeypatch.setattr(desktop, "_prepare_database", forbid)
    monkeypatch.setattr(desktop, "_launch", forbid)
    result = desktop.start(profile.directory)
    assert result["ready"] is True
    assert result["configuration_current"] is True
    assert result["brain_id"] == profile.brain_id
    assert load_profile(profile.directory) == profile


def test_start_foreign_port_never_prepares_database_or_launches(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: None)
    monkeypatch.setattr(desktop, "_port_open", lambda port: True)
    monkeypatch.setattr(desktop, "_prepare_database", forbid)
    monkeypatch.setattr(desktop, "_launch", forbid)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.start(profile.directory)
    assert error.value.code == "port_conflict"


def test_start_degraded_owned_service_does_not_spawn_a_second_process(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: ready(candidate, ready=False, state="degraded"))
    monkeypatch.setattr(desktop, "_prepare_database", forbid)
    monkeypatch.setattr(desktop, "_launch", forbid)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.start(profile.directory)
    assert error.value.code == "service_degraded"


def test_changed_configuration_requires_restart_without_implicit_new_process(profile, monkeypatch):
    previous = ready(profile)
    updated = configure_cognition(profile, "https://provider.example/v1", "model", "private-model-key")
    monkeypatch.setattr(desktop, "_probe", lambda candidate: dict(previous))
    monkeypatch.setattr(desktop, "_prepare_database", forbid)
    monkeypatch.setattr(desktop, "_launch", forbid)
    result = desktop.status(updated.directory)
    assert result["state"] == "restart_required"
    assert result["ready"] is False
    assert result["configuration_current"] is False
    with pytest.raises(desktop.DesktopError) as error:
        desktop.start(updated.directory)
    assert error.value.code == "restart_required"


def test_status_does_not_confuse_configured_provider_with_running_service(profile, monkeypatch):
    configured = configure_cognition(profile, "https://provider.example/v1", "model", "private-model-key")
    monkeypatch.setattr(desktop, "_probe", lambda candidate: None)
    monkeypatch.setattr(desktop, "_port_open", lambda port: False)
    result = desktop.status(configured.directory)
    assert result["cognition"] == "configured"
    assert result["ready"] is False
    assert result["state"] == "stopped"
    assert result["host_adapter"] == "not_verified"
    assert result["mcp"] == "tools_only"
    encoded = json.dumps(result)
    for secret in (profile.api_key, profile.app_password, profile.admin_password, "private-model-key"):
        assert secret not in encoded


def test_start_waits_for_ready_and_keeps_progress_off_stdout(profile, monkeypatch, capsys):
    responses = iter([None, None, ready(profile)])
    monkeypatch.setattr(desktop, "_probe", lambda candidate: next(responses))
    monkeypatch.setattr(desktop, "_port_open", lambda port: False)
    called = []
    monkeypatch.setattr(desktop, "_prepare_database", lambda candidate: called.append("database"))
    monkeypatch.setattr(desktop, "_launch", lambda candidate: called.append("launch") or SimpleNamespace(poll=lambda: None))
    result = desktop.start(profile.directory)
    assert result["ready"]
    assert called == ["database", "launch"]
    output = capsys.readouterr()
    assert output.out == ""
    assert profile.api_key not in output.err
    assert profile.admin_password not in output.err


def test_database_preparation_failure_never_launches_or_destroys_data(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: None)
    monkeypatch.setattr(desktop, "_port_open", lambda port: False)
    monkeypatch.setattr(desktop, "_launch", forbid)

    def failed(candidate):
        raise desktop.DesktopError("database_initialization_failed", "failed")

    monkeypatch.setattr(desktop, "_prepare_database", failed)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.start(profile.directory)
    assert error.value.code == "database_initialization_failed"
    assert load_profile(profile.directory) == profile


def test_process_exit_during_start_preserves_profile_and_database(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: None)
    monkeypatch.setattr(desktop, "_port_open", lambda port: False)
    monkeypatch.setattr(desktop, "_prepare_database", lambda candidate: None)
    monkeypatch.setattr(desktop, "_launch", lambda candidate: SimpleNamespace(poll=lambda: 1))
    monkeypatch.setattr(desktop, "_compose", forbid)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.start(profile.directory)
    assert error.value.code == "service_start_failed"
    assert load_profile(profile.directory) == profile


def test_stop_foreign_port_does_not_send_shutdown_or_touch_database(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: None)
    monkeypatch.setattr(desktop, "_port_open", lambda port: True)
    monkeypatch.setattr(desktop, "_compose", forbid)
    monkeypatch.setattr(desktop.httpx, "Client", forbid)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.stop(profile.directory)
    assert error.value.code == "port_conflict"


def test_stop_waits_for_owned_instance_then_stops_database_without_removing_volume(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: ready(candidate))
    steps = []

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/local/stop"
        assert request.headers["Authorization"] == "Bearer " + profile.api_key
        assert request.headers["X-Memory-Instance"] == "test-instance"
        steps.append("request")
        return httpx.Response(200, json={"state": "stopping", "instance_id": "test-instance"})

    options = fake_http(monkeypatch, handler)
    monkeypatch.setattr(desktop, "_wait_stopped", lambda candidate: steps.append("wait"))
    monkeypatch.setattr(desktop, "_compose", lambda candidate, *args: steps.append(args))
    result = desktop.stop(profile.directory)
    assert steps == ["request", "wait", ("stop", "--timeout", "15")]
    assert result["data_preserved"] is True
    assert result["ready"] is False
    assert options == [{"trust_env": False, "timeout": 7, "follow_redirects": False}]


def test_stop_timeout_preserves_running_database(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: ready(candidate))
    fake_http(monkeypatch, lambda request: httpx.Response(200, json={"state": "stopping"}))

    def timeout(candidate):
        raise desktop.DesktopError("stop_timeout", "still finishing")

    monkeypatch.setattr(desktop, "_wait_stopped", timeout)
    monkeypatch.setattr(desktop, "_compose", forbid)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.stop(profile.directory)
    assert error.value.code == "stop_timeout"
    assert load_profile(profile.directory) == profile


@pytest.mark.parametrize("status_code", [401, 409, 503])
def test_unconfirmed_shutdown_does_not_stop_database(profile, monkeypatch, status_code):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: ready(candidate))
    fake_http(monkeypatch, lambda request: httpx.Response(status_code, json={"error": "refused"}))
    monkeypatch.setattr(desktop, "_compose", forbid)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.stop(profile.directory)
    assert error.value.code == "stop_unconfirmed"


def test_wait_stopped_times_out_only_for_actual_lock_contention(profile, monkeypatch):
    @contextmanager
    def locked(*args, **kwargs):
        raise LocalProfileBusy("busy")
        yield

    monkeypatch.setattr(desktop, "profile_lock", locked)
    ticks = itertools.count(0, 30)
    monkeypatch.setattr(desktop.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(desktop.time, "sleep", lambda delay: None)
    with pytest.raises(desktop.DesktopError) as error:
        desktop._wait_stopped(profile, timeout=55)
    assert error.value.code == "stop_timeout"


def test_wait_stopped_propagates_unsafe_paths_instead_of_treating_them_as_busy(profile, monkeypatch):
    @contextmanager
    def unsafe(*args, **kwargs):
        raise LocalProfileError("unsafe path")
        yield

    monkeypatch.setattr(desktop, "profile_lock", unsafe)
    monkeypatch.setattr(desktop.time, "sleep", forbid)
    with pytest.raises(LocalProfileError, match="unsafe path"):
        desktop._wait_stopped(profile)


def test_probe_rejects_foreign_identity_and_never_follows_redirects(profile, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1/local/identity"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=identity(profile, request, installation_id="another-installation"))

    options = fake_http(monkeypatch, handler)
    assert desktop._probe(profile) is None
    assert len(requests) == 1
    assert options == [{"trust_env": False, "timeout": 7, "follow_redirects": False}]


def test_probe_rejects_redirects_without_forwarding_credentials(profile, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://external.example/"})

    fake_http(monkeypatch, handler)
    assert desktop._probe(profile) is None
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"
    assert "Authorization" not in requests[0].headers


def test_probe_authenticates_only_after_verified_identity_on_the_same_client(profile, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/local/identity":
            assert "Authorization" not in request.headers
            challenge = request.headers["X-Memory-Challenge"]
            assert len(challenge) == 64
            assert len(bytes.fromhex(challenge)) == 32
            return httpx.Response(200, json=identity(profile, request))
        assert len(requests) == 2
        assert request.url.path == "/v1/local/status"
        assert request.headers["Authorization"] == "Bearer " + profile.api_key
        return httpx.Response(200, json=ready(profile))

    options = fake_http(monkeypatch, handler)
    assert desktop._probe(profile) == ready(profile)
    assert len(requests) == 2
    assert len(options) == 1


@pytest.mark.parametrize("attack", ["invalid_signature", "replayed_challenge", "changed_brain"])
def test_probe_does_not_send_bearer_after_failed_identity_verification(profile, monkeypatch, attack):
    requests = []
    monkeypatch.setattr(desktop.secrets, "token_hex", lambda size: "c" * 64)

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1/local/identity"
        assert "Authorization" not in request.headers
        document = identity(profile, request)
        if attack == "invalid_signature":
            document["signature"] = "0" * 64
        elif attack == "replayed_challenge":
            document["signature"] = desktop._identity_signature(profile, "test-instance", "b" * 64)
        else:
            document["brain_id"] = "other-brain"
        return httpx.Response(200, json=document)

    fake_http(monkeypatch, handler)
    assert desktop._probe(profile) is None
    assert len(requests) == 1


def test_probe_rejects_status_from_a_different_service_instance(profile, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/local/identity":
            return httpx.Response(200, json=identity(profile, request))
        assert request.url.path == "/v1/local/status"
        return httpx.Response(200, json=ready(profile, instance_id="restarted-instance"))

    fake_http(monkeypatch, handler)
    assert desktop._probe(profile) is None
    assert len(requests) == 2


def test_environment_excludes_ambient_memory_compose_and_python_configuration(monkeypatch):
    for name in ("MEMORY_DATABASE_URL", "MEMORY_API_KEY", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME",
                 "PYTHONPATH", "PYTHONHOME"):
        monkeypatch.setenv(name, "private-environment-value")
    environment = desktop._environment()
    assert not any(key.startswith(("MEMORY_", "COMPOSE_")) for key in environment)
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert "PATH" in environment


def test_cli_no_start_reports_configuration_without_claiming_ready(profile, monkeypatch, capsys):
    from agent_memory import desktop_connection

    monkeypatch.setattr(desktop, "_probe", lambda candidate: None)
    monkeypatch.setattr(desktop, "_port_open", lambda port: False)
    monkeypatch.setattr(desktop, "start", forbid)
    monkeypatch.setattr(desktop_connection, "mcp_config", lambda directory: {"mcpServers": {"memory": {}}})
    args = SimpleNamespace(desktop_command="setup", directory=profile.directory, api_port=None,
                           database_port=None, base_url=None, model=None, api_key_env=None,
                           thinking=None, no_start=True, non_interactive=True)
    desktop_cli.dispatch(args)
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["state"] == "stopped"
    assert result["ready"] is False
    assert "storage is ready" not in output.err
    assert profile.api_key not in output.out + output.err
    assert profile.app_password not in output.out + output.err
    assert profile.admin_password not in output.out + output.err


@pytest.mark.parametrize("changes", [
    {"state": {}}, {"state": []}, {"worker": []}, {"database": {}},
    {"ready": "true"}, {"configuration": None}, {"instance_id": None},
    {"database": "unavailable"}, {"worker": "stopped"}, {"brain_id": "other"},
])
def test_probe_rejects_malformed_or_inconsistent_health_responses(profile, monkeypatch, changes):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/local/identity":
            assert "Authorization" not in request.headers
            return httpx.Response(200, json=identity(profile, request))
        assert request.headers["Authorization"] == "Bearer " + profile.api_key
        return httpx.Response(200, json=ready(profile, **changes))

    fake_http(monkeypatch, handler)
    assert desktop._probe(profile) is None
    assert len(requests) == 2


def test_start_does_not_launch_while_an_owned_supervisor_is_transitioning(profile, monkeypatch):
    monkeypatch.setattr(desktop, "_probe", lambda candidate: None)
    monkeypatch.setattr(desktop, "_supervisor_active", lambda candidate: True)
    monkeypatch.setattr(desktop, "_port_open", lambda port: False)
    monkeypatch.setattr(desktop, "_prepare_database", forbid)
    monkeypatch.setattr(desktop, "_launch", forbid)
    with pytest.raises(desktop.DesktopError) as error:
        desktop.start(profile.directory)
    assert error.value.code == "service_transitioning"


def test_private_managed_file_writer_rejects_hardlinks_before_truncation(profile, tmp_path):
    import os

    original = tmp_path / "original"
    original.write_text("keep this original content")
    link = tmp_path / "managed"
    os.link(original, link)
    with pytest.raises(LocalProfileError):
        desktop._private_write(link, "replacement")
    assert original.read_text() == "keep this original content"


@pytest.mark.parametrize("no_start", [False, True])
def test_setup_applies_changed_provider_with_automatic_restart_unless_disabled(profile, monkeypatch, no_start):
    from agent_memory import desktop_connection

    profile = configure_cognition(profile, "https://old-provider.example/v1", "old-model", "old-private-key")
    monkeypatch.setenv("TEST_DESKTOP_PROVIDER_KEY", "new-private-key")
    monkeypatch.setattr(desktop_connection, "mcp_config", lambda directory: {"mcpServers": {"memory": {}}})
    monkeypatch.setattr(desktop, "start", forbid)
    operations = []

    def status(directory):
        current = load_profile(directory)
        assert current.cognition == {"base_url": "https://new-provider.example/v1", "model": "new-model",
                                     "api_key": "new-private-key", "thinking": "enabled"}
        operations.append("status")
        return ready(profile, state="restart_required", ready=False, configuration_current=False)

    def restart(directory):
        assert not no_start
        operations.append("restart")
        return ready(load_profile(directory), configuration_current=True)

    monkeypatch.setattr(desktop, "status", status)
    monkeypatch.setattr(desktop, "restart", restart)
    args = SimpleNamespace(directory=profile.directory, api_port=None, database_port=None,
                           base_url="https://new-provider.example/v1", model="new-model",
                           api_key_env="TEST_DESKTOP_PROVIDER_KEY", thinking="enabled",
                           no_start=no_start, non_interactive=True)
    result = desktop_cli._setup(args)
    assert operations == (["status"] if no_start else ["status", "restart"])
    assert result["state"] == ("restart_required" if no_start else "ready")
    assert result["ready"] is not no_start
    assert "new-private-key" not in json.dumps(result)
    assert load_profile(profile.directory).brain_id == profile.brain_id
