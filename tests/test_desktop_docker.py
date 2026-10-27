import json
import subprocess
from importlib.resources import files
from types import SimpleNamespace

import pytest

from agent_memory import desktop
from agent_memory.local_profile import ensure_profile


@pytest.mark.parametrize("endpoint", [
    "unix:///var/run/docker.sock", "npipe:////./pipe/docker_engine",
    "tcp://127.0.0.1:2375", "http://localhost:2375", "https://[::1]:2376",
])
def test_explicit_local_docker_endpoint_is_accepted_without_context_lookup(monkeypatch, endpoint):
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: pytest.fail("No context lookup expected"))
    assert desktop._docker_endpoint("docker", {"DOCKER_HOST": endpoint}) == endpoint


@pytest.mark.parametrize("endpoint", [
    "ssh://remote.example", "tcp://remote.example:2375", "https://192.168.1.5:2376",
    "tcp://127.0.0.1.attacker.example:2375", "http://localhost.attacker.example",
    "https://[2001:db8::1]:2376", "unknown:///daemon", "invalid", "https://[broken",
    "npipe://remote.example/pipe/docker_engine", "npipe:////remote.example/pipe/docker_engine",
    "unix://remote.example/var/run/docker.sock", "unix:relative/docker.sock",
])
def test_remote_or_invalid_docker_endpoint_is_rejected(monkeypatch, endpoint):
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: pytest.fail("No context lookup expected"))
    with pytest.raises(desktop.DesktopError) as error:
        desktop._docker_endpoint("docker", {"DOCKER_HOST": endpoint})
    assert error.value.code == "remote_docker_unsupported"
    assert endpoint not in str(error.value)


def test_context_lookup_uses_bounded_json_command_and_preserves_context_precedence(monkeypatch):
    environment = {"DOCKER_CONTEXT": "desktop-local", "DOCKER_HOST": "ssh://ignored.example"}
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps("unix:///local/docker.sock"))

    monkeypatch.setattr(desktop.subprocess, "run", run)
    assert desktop._docker_endpoint("/opt/docker", environment) == "unix:///local/docker.sock"
    assert calls == [([
        "/opt/docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}",
    ], {"env": environment, "capture_output": True, "text": True, "timeout": 10, "check": False})]


def test_default_context_is_checked_before_use(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps("tcp://remote.example:2375"))

    monkeypatch.setattr(desktop.subprocess, "run", run)
    with pytest.raises(desktop.DesktopError) as error:
        desktop._docker_endpoint("docker", {})
    assert error.value.code == "remote_docker_unsupported"
    assert len(calls) == 1


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=1, stdout="private daemon diagnostics"),
    SimpleNamespace(returncode=0, stdout="invalid private response"),
    SimpleNamespace(returncode=0, stdout="null"),
    SimpleNamespace(returncode=0, stdout="{}"),
])
def test_unavailable_or_malformed_context_has_safe_error(monkeypatch, result):
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(desktop.DesktopError) as error:
        desktop._docker_endpoint("docker", {})
    assert error.value.code == "docker_unavailable"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("failure", [OSError("private detail"), subprocess.TimeoutExpired("docker", 10)])
def test_context_process_failure_is_bounded_and_sanitized(monkeypatch, failure):
    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(desktop.subprocess, "run", run)
    with pytest.raises(desktop.DesktopError) as error:
        desktop._docker_endpoint("docker", {})
    assert error.value.code == "docker_unavailable"
    assert "private" not in str(error.value)


def test_compose_uses_packaged_template_and_private_stable_profile(tmp_path, monkeypatch, capsys):
    profile = ensure_profile(tmp_path / "电脑 profile")
    for key in ("MEMORY_DATABASE_URL", "MEMORY_COGNITION_API_KEY", "MEMORY_API_KEY", "COMPOSE_FILE"):
        monkeypatch.setenv(key, "private-ambient-value")
    monkeypatch.setenv("DOCKER_CONTEXT", "initial-local-context")
    monkeypatch.setenv("DOCKER_HOST", "ssh://ignored-context-host.example")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/opt/tools/docker")
    calls = []

    def endpoint(docker, environment):
        calls.append(("endpoint", docker))
        assert environment["MEMORY_DESKTOP_ADMIN_PASSWORD"] == profile.admin_password
        return "unix:///var/run/docker.sock"

    def run(command, **kwargs):
        calls.append(("compose", command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(desktop, "_docker_endpoint", endpoint)
    monkeypatch.setattr(desktop.subprocess, "run", run)
    desktop._compose(profile, "up", "--detach", "--wait")
    assert calls[0] == ("endpoint", "/opt/tools/docker")
    _, arguments, options = calls[1]
    assert arguments[:2] == ["/opt/tools/docker", "compose"]
    assert arguments[-3:] == ["up", "--detach", "--wait"]
    assert arguments[arguments.index("--project-name") + 1] == profile.project_name
    assert arguments[arguments.index("--project-directory") + 1] == str(profile.directory)
    assert options["cwd"] == profile.directory
    assert profile.directory.is_absolute()
    assert options["stdout"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.PIPE
    assert options["timeout"] == 300
    memory_keys = {key for key in options["env"] if key.startswith("MEMORY_")}
    assert memory_keys == {"MEMORY_DESKTOP_DATABASE_PORT", "MEMORY_DESKTOP_ADMIN_PASSWORD"}
    assert not any(key.startswith("COMPOSE_") for key in options["env"])
    assert options["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert "DOCKER_CONTEXT" not in options["env"]
    assert profile.api_key not in json.dumps(arguments)
    assert profile.admin_password not in json.dumps(arguments)
    assert profile.app_password not in json.dumps(arguments)
    assert (profile.directory / "compose.env").read_text() == ""
    template = files("agent_memory").joinpath("resources/desktop-compose.yaml").read_text()
    assert (profile.directory / "compose.yaml").read_text() == template
    assert "memory_database:/var/lib/postgresql/data" in template
    assert "127.0.0.1:${MEMORY_DESKTOP_DATABASE_PORT" in template
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_compose_never_mutates_remote_docker(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/opt/docker")
    monkeypatch.setenv("DOCKER_HOST", "ssh://private-remote.example")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: pytest.fail("Remote Docker must not run"))
    with pytest.raises(desktop.DesktopError) as error:
        desktop._compose(profile, "up", "--detach")
    assert error.value.code == "remote_docker_unsupported"


def test_database_stop_command_preserves_volume(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/opt/docker")
    monkeypatch.setattr(desktop, "_docker_endpoint", lambda *args: "unix:///var/run/docker.sock")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(desktop.subprocess, "run", run)
    desktop._compose(profile, "stop", "--timeout", "15")
    assert commands[0][-3:] == ["stop", "--timeout", "15"]
    assert not {"down", "rm", "--volumes", "-v"}.intersection(commands[0])


@pytest.mark.parametrize("returncode", [1, 125])
def test_compose_command_failure_never_echoes_credentials(tmp_path, monkeypatch, capsys, returncode):
    profile = ensure_profile(tmp_path)
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/opt/docker")
    monkeypatch.setattr(desktop, "_docker_endpoint", lambda *args: "unix:///var/run/docker.sock")
    leaked = f"bind failed for postgres:{profile.admin_password} key={profile.api_key} app={profile.app_password}"
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=returncode, stderr=leaked.encode()))
    with pytest.raises(desktop.DesktopError) as error:
        desktop._compose(profile, "up", "--detach")
    assert error.value.code == "database_start_failed"
    assert "bind failed for postgres:[redacted]" in str(error.value)
    output = capsys.readouterr()
    for secret in (profile.api_key, profile.app_password, profile.admin_password):
        assert secret not in str(error.value) + output.out + output.err
