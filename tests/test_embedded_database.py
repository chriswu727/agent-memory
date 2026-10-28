import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from membrain import backup_transport, desktop, desktop_cli, embedded_database
from membrain.local_profile import LocalProfileError, ensure_profile, load_profile


def test_docker_profiles_keep_their_original_file_format(tmp_path):
    profile = ensure_profile(tmp_path / "docker")
    document = json.loads((tmp_path / "docker" / "profile.json").read_text())
    assert profile.database_engine == "docker"
    assert "database_engine" not in document
    assert load_profile(tmp_path / "docker") == profile


def test_embedded_engine_is_persisted_and_not_changed_by_later_setup(tmp_path):
    profile = ensure_profile(tmp_path / "embedded", database_engine="embedded")
    document = json.loads((tmp_path / "embedded" / "profile.json").read_text())
    assert document["database_engine"] == "embedded"
    assert load_profile(tmp_path / "embedded").database_engine == "embedded"
    assert ensure_profile(tmp_path / "embedded", database_engine="docker") == profile


def test_unknown_engines_are_rejected(tmp_path):
    with pytest.raises(LocalProfileError):
        ensure_profile(tmp_path / "bad", database_engine="sqlite")
    profile = ensure_profile(tmp_path / "edited")
    path = tmp_path / "edited" / "profile.json"
    document = json.loads(path.read_text())
    document["database_engine"] = "sqlite"
    path.write_text(json.dumps(document))
    with pytest.raises(LocalProfileError):
        load_profile(profile.directory)


@pytest.mark.parametrize("available,expected", [(True, "embedded"), (False, "docker")])
def test_setup_auto_selects_an_installed_engine(tmp_path, monkeypatch, capsys, available, expected):
    monkeypatch.setattr(embedded_database, "available", lambda: available)
    monkeypatch.setattr(desktop, "_port_open", lambda port: False)
    directory = tmp_path / "auto"
    desktop_cli.dispatch(SimpleNamespace(
        desktop_command="setup", directory=directory, api_port=None, database_port=None, database="auto",
        base_url=None, model=None, api_key_env=None, thinking=None, no_start=True, non_interactive=True,
    ))
    capsys.readouterr()
    assert load_profile(directory).database_engine == expected


def test_backup_streams_use_bundled_client_tools(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "embedded", database_engine="embedded")
    calls = []
    monkeypatch.setattr(embedded_database, "_layout", lambda profile: embedded_database._Layout(
        tmp_path / "bin", tmp_path / "data", tmp_path / "log", None))
    monkeypatch.setattr(backup_transport, "stream_process",
                        lambda command, environment, path, **options: calls.append((command, environment)))
    backup_transport.database_stream(profile, tmp_path / "dump", snapshot="0A-1")
    command, environment = calls[0]
    assert command[:3] == [str(tmp_path / "bin" / "pg_dump"), "--host=127.0.0.1",
                           f"--port={profile.database_port}"]
    assert "--dbname=agent_memory" in command and "--snapshot=0A-1" in command
    assert environment["PGPASSWORD"] == profile.admin_password
    assert not any(key.startswith("MEMORY_") for key in environment)


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_embedded_installation_starts_without_docker_and_keeps_memory_across_restarts(tmp_path, monkeypatch):
    from membrain.desktop_connection import open_client
    from membrain.mcp_server import recalled_context

    def no_docker(*args, **kwargs):
        raise AssertionError("Docker must not be used by the embedded engine")

    monkeypatch.setattr(desktop, "_compose", no_docker)
    profile = ensure_profile(tmp_path / "brain", database_engine="embedded")
    try:
        assert desktop.start(profile.directory)["ready"]
        with open_client(profile.directory, start_service=False) as client:
            assert client.observe("My cat is called Biscuit and she hates thunder.", conversation_id="first",
                                  branch_id="main", source_event_key="biscuit")["outcome"] == "completed"
        assert desktop.stop(profile.directory)["state"] == "stopped"
        assert not embedded_database.running(profile)
        assert desktop.start(profile.directory)["ready"]
        with open_client(profile.directory, start_service=False) as client:
            context = json.loads(recalled_context(client.recall("what is my cat called")))
        assert any("Biscuit" in memory["statement"] for memory in context["memories"])
    finally:
        try:
            desktop.stop(profile.directory)
        except desktop.DesktopError:
            embedded_database.stop(profile)
    assert not embedded_database.running(profile)


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_initialization_failures_report_the_database_tool_output(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "brain", database_engine="embedded")
    original = embedded_database._run

    def failing_initdb(layout, program, *arguments, **options):
        if program == "initdb":
            return subprocess.CompletedProcess([program], 1, stdout=b"initdb: could not create directory")
        return original(layout, program, *arguments, **options)

    monkeypatch.setattr(embedded_database, "_run", failing_initdb)
    with pytest.raises(desktop.DesktopError) as error:
        desktop._database_up(profile)
    assert error.value.code == "database_initialization_failed"
    assert "could not create directory" in str(error.value)


def test_root_installations_keep_new_clusters_with_their_data_when_reachable(tmp_path, monkeypatch):
    monkeypatch.setattr(embedded_database, "_installation", lambda: tmp_path / "venv")
    monkeypatch.setattr(embedded_database, "_running_as_root", lambda: True)
    monkeypatch.setattr(embedded_database, "_service_account", lambda name="x": (os.getuid(), os.getgid()))
    monkeypatch.setattr(embedded_database, "_shared_binaries", lambda source, root: root / "binaries")
    monkeypatch.setattr(embedded_database, "SHARED_ROOT", tmp_path / "shared")
    monkeypatch.setattr(embedded_database, "LEGACY_SHARED_ROOT", tmp_path / "legacy")
    reachable, hidden = tmp_path / "volume" / "membrain", tmp_path / "home" / "membrain"
    reachable.mkdir(parents=True)
    hidden.mkdir(parents=True)
    hidden.parent.chmod(0o700)
    assert not embedded_database._traversable(hidden)
    monkeypatch.setattr(embedded_database, "_traversable", lambda path: Path(path) == reachable)
    layout = embedded_database._layout(SimpleNamespace(directory=reachable, installation_id="a"))
    assert layout.data == reachable / "postgres-cluster" / "data"
    layout = embedded_database._layout(SimpleNamespace(directory=hidden, installation_id="b"))
    assert layout.data == tmp_path / "shared" / "clusters" / "b" / "data"
    (tmp_path / "shared" / "clusters" / "a" / "data").mkdir(parents=True)
    (tmp_path / "shared" / "clusters" / "a" / "data" / "PG_VERSION").write_text("17")
    layout = embedded_database._layout(SimpleNamespace(directory=reachable, installation_id="a"))
    assert layout.data == tmp_path / "shared" / "clusters" / "a" / "data"  # Existing clusters stay where they are.
