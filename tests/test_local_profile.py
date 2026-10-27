import json
import os
import socket
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from membrain import local_profile
from membrain.local_profile import (
    LocalProfileBusy,
    LocalProfileError,
    configure_cognition,
    default_profile_directory,
    ensure_profile,
    load_profile,
    profile_lock,
)


def test_profile_is_atomic_persistent_and_independent_of_working_directory(tmp_path, monkeypatch):
    directory = tmp_path / "电脑 profiles" / "my brain"
    profile = ensure_profile(directory)
    original = (directory / "profile.json").read_bytes()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_profile(directory) == profile
    assert ensure_profile(directory) == profile
    assert (directory / "profile.json").read_bytes() == original
    assert not list(directory.glob("*.tmp"))
    assert profile.api_port != profile.database_port
    assert profile.project_name == "agent-memory-" + profile.installation_id.replace("-", "")
    assert profile.base_url == f"http://127.0.0.1:{profile.api_port}"
    assert not (elsewhere / "profile.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_private_files_have_owner_only_permissions(tmp_path):
    ensure_profile(tmp_path)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    for name in ("profile.json", ".profile.lock"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_secrets_are_absent_from_repr_and_environment_is_ignored(tmp_path, monkeypatch):
    profile = configure_cognition(ensure_profile(tmp_path), "https://provider.example/v1", "model", "model-secret")
    monkeypatch.setenv("MEMORY_DATABASE_URL", "postgresql://wrong")
    monkeypatch.setenv("MEMORY_COGNITION_API_KEY", "wrong")
    monkeypatch.setattr("membrain.config.load_dotenv", lambda: pytest.fail("No dotenv lookup allowed"))
    settings = profile.settings()
    assert settings.database_url == profile.database_url
    assert settings.cognition_api_key == "model-secret"
    assert settings.api_keys_file is None
    assert len(settings.principals()) == 1
    principal = next(iter(settings.principals().values()))
    assert (principal.tenant_id, principal.principal_id) == (profile.tenant_id, profile.principal_id)
    for secret in (profile.api_key, profile.app_password, profile.admin_password, "model-secret"):
        assert secret not in repr(profile)
        assert secret not in repr(settings)
    for dsn in (profile.database_url, profile.migration_database_url):
        assert parse_qs(urlsplit(dsn).query) == {"connect_timeout": ["5"]}


@pytest.mark.parametrize("platform,variable,suffix", [
    ("darwin", None, ("Library", "Application Support", "memBrain")),
    ("linux", "XDG_DATA_HOME", ("membrain",)),
    ("win32", "LOCALAPPDATA", ("memBrain",)),
])
def test_default_profile_directory_uses_platform_storage(tmp_path, monkeypatch, platform, variable, suffix):
    monkeypatch.setattr(local_profile.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if variable:
        monkeypatch.setenv(variable, str(tmp_path))
    assert default_profile_directory() == tmp_path.joinpath(*suffix)


@pytest.mark.parametrize("platform,legacy,current", [
    ("darwin", ("Library", "Application Support", "Agent Memory"), ("Library", "Application Support", "memBrain")),
    ("linux", (".local", "share", "agent-memory"), (".local", "share", "membrain")),
])
def test_installations_from_before_the_rename_keep_their_directory(tmp_path, monkeypatch, platform, legacy, current):
    monkeypatch.setattr(local_profile.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    tmp_path.joinpath(*legacy).mkdir(parents=True)
    assert default_profile_directory() == tmp_path.joinpath(*current)
    profile = ensure_profile(tmp_path.joinpath(*legacy))
    assert default_profile_directory() == profile.directory
    tmp_path.joinpath(*current).mkdir(parents=True)
    assert default_profile_directory() == tmp_path.joinpath(*current)


def test_pre_rename_imports_resolve_to_the_same_modules():
    import agent_memory.local_profile

    import membrain
    import membrain.local_profile

    assert agent_memory.local_profile is membrain.local_profile
    assert agent_memory.__version__ == membrain.__version__


def test_relative_xdg_directory_is_not_resolved_from_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(local_profile.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert default_profile_directory() == tmp_path / ".local" / "share" / "membrain"


def test_default_ports_fall_back_when_occupied_and_remain_fixed(tmp_path, monkeypatch):
    calls = []
    real_socket = local_profile.socket.socket

    class Socket:
        def __init__(self, *args):
            self.socket = real_socket(*args)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.socket.close()

        def bind(self, address):
            calls.append(address)
            if address[1] in (8788, 55433):
                raise OSError("already occupied")
            self.socket.bind(address)

        def getsockname(self):
            return self.socket.getsockname()

    monkeypatch.setattr(local_profile.socket, "socket", Socket)
    profile = ensure_profile(tmp_path)
    assert profile.api_port not in (8788, 55433)
    assert profile.database_port not in (8788, 55433, profile.api_port)
    assert ("127.0.0.1", 8788) in calls
    assert ("127.0.0.1", 55433) in calls
    monkeypatch.setattr(local_profile, "_available_port", lambda *args: pytest.fail("Existing ports are fixed"))
    assert ensure_profile(tmp_path) == profile


def test_explicit_occupied_port_does_not_create_profile(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        with pytest.raises(LocalProfileError, match="already in use"):
            ensure_profile(tmp_path, api_port=listener.getsockname()[1])
    assert not (tmp_path / "profile.json").exists()


@pytest.mark.parametrize("port", [True, False, 80, 65536, "8788", 8788.0])
def test_invalid_explicit_port_is_rejected_even_for_existing_profile(tmp_path, port):
    profile = ensure_profile(tmp_path)
    with pytest.raises(LocalProfileError, match="ports"):
        ensure_profile(tmp_path, api_port=port)
    assert load_profile(tmp_path) == profile


def test_existing_profile_cannot_silently_change_ports(tmp_path):
    profile = ensure_profile(tmp_path)
    new_port = 1024 if profile.api_port != 1024 else 1025
    with pytest.raises(LocalProfileError, match="cannot be changed"):
        ensure_profile(tmp_path, api_port=new_port)
    assert load_profile(tmp_path) == profile


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"brain_id": "invalid"}, {"tenant_id": 3},
    {"api_port": True}, {"database_port": -1}, {"api_key": "short"}, {"name": ""},
    {"cognition": {}}, {"unexpected": "value"},
])
def test_damaged_profile_is_never_reinitialized(tmp_path, change):
    ensure_profile(tmp_path)
    path = tmp_path / "profile.json"
    document = json.loads(path.read_text())
    document.update(change)
    original = json.dumps(document).encode()
    path.write_bytes(original)
    with pytest.raises(LocalProfileError):
        ensure_profile(tmp_path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("content", [b"{", b"\xff", b'{"version":1,"version":1}', b" " * 65537])
def test_unreadable_profile_is_not_overwritten(tmp_path, content):
    (tmp_path / "profile.json").write_bytes(content)
    with pytest.raises(LocalProfileError):
        ensure_profile(tmp_path)
    assert (tmp_path / "profile.json").read_bytes() == content


def test_missing_profile_does_not_create_directory(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(LocalProfileError, match="setup"):
        load_profile(missing)
    assert not missing.exists()


def test_lost_profile_cannot_silently_replace_an_existing_installation(tmp_path):
    ensure_profile(tmp_path)
    (tmp_path / "compose.yaml").write_text("existing database installation")
    (tmp_path / "profile.json").unlink()
    with pytest.raises(LocalProfileError, match="existing installation"):
        ensure_profile(tmp_path)
    assert not (tmp_path / "profile.json").exists()


def test_interrupted_initial_atomic_write_can_be_retried(tmp_path):
    (tmp_path / (".profile-" + "a" * 32 + ".tmp")).write_text("incomplete write")
    profile = ensure_profile(tmp_path)
    assert load_profile(tmp_path) == profile


def test_symlinked_directory_and_profile_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(LocalProfileError, match="symbolic"):
        ensure_profile(link)
    original = tmp_path / "original"
    original.write_text("untouched")
    (target / "profile.json").symlink_to(original)
    with pytest.raises(LocalProfileError, match="symbolic"):
        ensure_profile(target)
    assert original.read_text() == "untouched"


def test_symlinked_lock_is_rejected_without_modifying_target(tmp_path):
    target = tmp_path / "external"
    target.write_text("untouched")
    (tmp_path / ".profile.lock").symlink_to(target)
    with pytest.raises(LocalProfileError, match="symbolic"):
        ensure_profile(tmp_path)
    assert target.read_text() == "untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard-link restriction")
def test_hardlinked_profile_is_not_read_or_replaced(tmp_path):
    profile = ensure_profile(tmp_path)
    os.link(tmp_path / "profile.json", tmp_path / "copy")
    with pytest.raises(LocalProfileError, match="regular files"):
        load_profile(profile.directory)


def test_nested_locks_and_helpers_are_reentrant(tmp_path):
    with profile_lock(tmp_path):
        profile = ensure_profile(tmp_path)
        with profile_lock(tmp_path):
            assert load_profile(tmp_path) == profile
            updated = configure_cognition(profile, "https://provider.example/v1", "model", "secret")
    assert load_profile(tmp_path) == updated


def test_cross_process_lock_times_out_and_can_be_reused(tmp_path):
    script = """
from pathlib import Path
from membrain.local_profile import profile_lock, LocalProfileBusy
import sys
try:
    with profile_lock(Path(sys.argv[1]), timeout=0.15):
        print('acquired')
except LocalProfileBusy:
    print('locked')
"""
    with profile_lock(tmp_path):
        result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], text=True,
                                capture_output=True, timeout=15, check=True)
    assert result.stdout.strip() == "locked"
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], text=True,
                            capture_output=True, timeout=15, check=True)
    assert result.stdout.strip() == "acquired"


def test_unsafe_lock_path_is_not_reported_as_busy(tmp_path):
    target = tmp_path / "target"
    target.touch()
    (tmp_path / ".profile.lock").symlink_to(target)
    with pytest.raises(LocalProfileError) as error:
        with profile_lock(tmp_path, timeout=0):
            pytest.fail("Unsafe lock must be rejected")
    assert not isinstance(error.value, LocalProfileBusy)


def test_concurrent_profile_creation_preserves_one_identity(tmp_path):
    script = """
from pathlib import Path
from membrain.local_profile import ensure_profile
import sys
profile = ensure_profile(Path(sys.argv[1]))
print(profile.installation_id, profile.brain_id)
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    outputs = []
    try:
        for process in processes:
            output, error = process.communicate(timeout=15)
            assert process.returncode == 0, error
            outputs.append(output.strip())
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    profile = load_profile(tmp_path)
    assert set(outputs) == {f"{profile.installation_id} {profile.brain_id}"}


def test_cognition_configuration_replaces_only_cognition(tmp_path):
    profile = ensure_profile(tmp_path)
    updated = configure_cognition(profile, "https://provider.example/v1", "model", "first-secret", "enabled")
    assert replace(updated, cognition=None) == profile
    updated = configure_cognition(profile, "http://127.0.0.1:11434/v1", "local-model", "second-secret")
    assert load_profile(tmp_path) == updated
    assert updated.cognition["thinking"] is None
    assert updated.settings().cognition_base_url == "http://127.0.0.1:11434/v1"


@pytest.mark.parametrize("base_url,model,api_key,thinking", [
    ("ftp://provider.example", "model", "secret", None),
    ("https://", "model", "secret", None),
    ("https://user:password@provider.example", "model", "secret", None),
    ("https://provider.example?key=secret", "model", "secret", None),
    ("https://provider.example#secret", "model", "secret", None),
    ("https://provider.example:0", "model", "secret", None),
    ("https://provider.example:bad", "model", "secret", None),
    ("https://provider .example", "model", "secret", None),
    ("https://provider..example", "model", "secret", None),
    ("https://provider.example", "", "secret", None),
    ("https://provider.example", "model", "", None),
    ("https://provider.example", "model", "secret key", None),
    ("https://provider.example", "model", "中文-secret", None),
    ("https://provider.example", "model", "secret\x7f", None),
    ("https://provider.example", "model", "secret\nkey", None),
    ("https://provider.example", "model", "secret", "invalid"),
])
def test_invalid_cognition_configuration_preserves_previous_profile(tmp_path, base_url, model, api_key, thinking):
    profile = ensure_profile(tmp_path)
    before = (tmp_path / "profile.json").read_bytes()
    with pytest.raises(LocalProfileError) as error:
        configure_cognition(profile, base_url, model, api_key, thinking)
    assert "secret" not in str(error.value)
    assert (tmp_path / "profile.json").read_bytes() == before


def test_stale_identity_cannot_update_another_profile(tmp_path):
    profile = ensure_profile(tmp_path)
    forged = replace(profile, brain_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(LocalProfileError, match="identity changed"):
        configure_cognition(forged, "https://provider.example", "model", "secret")
    assert load_profile(tmp_path) == profile


def test_atomic_replace_failure_keeps_the_complete_original_profile(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path)
    original = (tmp_path / "profile.json").read_bytes()

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(local_profile.os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        configure_cognition(profile, "https://provider.example", "model", "secret")
    assert (tmp_path / "profile.json").read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))
    assert load_profile(tmp_path) == profile
