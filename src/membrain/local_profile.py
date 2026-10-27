from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from .config import Settings
from .helpers import Principal

PROFILE_VERSION = 1
PROFILE_FILE = "profile.json"
_MAX_PROFILE_BYTES = 65536
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()


def _reset_locks_after_fork():
    global _LOCKS, _LOCKS_GUARD, _HELD
    _LOCKS, _LOCKS_GUARD, _HELD = {}, threading.Lock(), threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_locks_after_fork)


class LocalProfileError(ValueError):
    pass


class LocalProfileBusy(LocalProfileError):
    pass


def _platform_data_directory(title: str, slug: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / title
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA")
        return (Path(root) if root and Path(root).is_absolute() else Path.home() / "AppData" / "Local") / title
    root = os.environ.get("XDG_DATA_HOME")
    return (Path(root) if root and Path(root).is_absolute() else Path.home() / ".local" / "share") / slug


def default_profile_directory() -> Path:
    current = _platform_data_directory("memBrain", "membrain")
    # Installations created before the memBrain rename keep their existing brain.
    legacy = _platform_data_directory("Agent Memory", "agent-memory")
    if not current.exists() and (legacy / PROFILE_FILE).exists():
        return legacy
    return current


def _directory(directory: Path | None) -> Path:
    path = Path(os.path.abspath((directory or default_profile_directory()).expanduser()))
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise LocalProfileError("Profile paths must not contain symbolic links.")
    return path


def _restrict_windows_access(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID),
                        ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    apply = security.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    apply.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    descriptor = wintypes.LPVOID()
    if not convert("D:P(A;OICI;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise LocalProfileError("Could not restrict profile access to its Windows owner.")
    try:
        if not apply(str(path), 0x80000004, descriptor):
            raise LocalProfileError("Could not restrict profile access to its Windows owner.")
    finally:
        kernel.LocalFree(descriptor)


def _private(path: Path, *, directory: bool = False) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not (
        stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    ):
        raise LocalProfileError("Profile paths must be regular files and directories.")
    if os.name == "nt":
        _restrict_windows_access(path)
    else:
        if metadata.st_uid != os.getuid() or (not directory and metadata.st_nlink != 1):
            raise LocalProfileError("Profile files must belong exclusively to the current user.")
        path.chmod(0o700 if directory else 0o600)


def _open_private(path: Path, flags: int) -> int:
    if path.is_symlink():
        raise LocalProfileError("Profile files must not be symbolic links.")
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise LocalProfileError("Profile files must be private regular files.")
        _private(path)
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise LocalProfileError("Profile file changed while opening it.")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def profile_lock(directory: Path | None = None, *, timeout: float = 30.0):
    """Serialize profile operations across processes; nesting in one thread is supported."""
    path = _directory(directory)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _private(path, directory=True)
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise LocalProfileBusy("Another process is updating this profile; try again shortly.")
    descriptor = None
    held = getattr(_HELD, "profiles", None)
    if held is None:
        held = _HELD.profiles = set()
    try:
        if key in held:
            yield path
            return
        descriptor = _open_private(path / ".profile.lock", os.O_CREAT | os.O_RDWR)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise LocalProfileBusy("Another process is updating this profile; try again shortly.") from None
                time.sleep(0.05)
        held.add(key)
        try:
            yield path
        finally:
            held.remove(key)
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock.release()


@dataclass(frozen=True)
class DesktopProfile:
    directory: Path
    installation_id: str
    brain_id: str
    name: str
    api_port: int
    database_port: int
    api_key: str = field(repr=False)
    admin_password: str = field(repr=False)
    app_password: str = field(repr=False)
    tenant_id: str
    principal_id: str
    cognition: dict | None = field(default=None, repr=False)
    database_engine: str = "docker"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def project_name(self) -> str:
        return "agent-memory-" + self.installation_id.replace("-", "")

    @property
    def database_url(self) -> str:
        return f"postgresql://memory_app:{quote(self.app_password, safe='')}@127.0.0.1:{self.database_port}/agent_memory?connect_timeout=5"

    @property
    def migration_database_url(self) -> str:
        return f"postgresql://postgres:{quote(self.admin_password, safe='')}@127.0.0.1:{self.database_port}/agent_memory?connect_timeout=5"

    def settings(self) -> Settings:
        from .cognition_config import load_cognition_policy
        from .recovery_profile import profile_settings

        cognition = self.cognition or {}
        return Settings(
            database_url=self.database_url,
            migration_database_url=self.migration_database_url,
            api_keys={hashlib.sha256(self.api_key.encode()).hexdigest(): Principal(self.tenant_id, self.principal_id)},
            allowed_hosts=("localhost", "127.0.0.1"),
            cognition_base_url=cognition.get("base_url"),
            cognition_api_key=cognition.get("api_key"),
            cognition_model=cognition.get("model"),
            cognition_thinking=cognition.get("thinking"),
            embedding_local_directory=_local_embeddings(self.directory),
            **load_cognition_policy(self.directory).settings(),
            **profile_settings(self),
        )


def _local_embeddings(directory: Path) -> str | None:
    from .local_embeddings import installed, runtime_available

    model = installed(directory)
    return str(model) if model is not None and runtime_available() else None


def _cognition(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"base_url", "model", "api_key", "thinking"}:
        raise LocalProfileError("Cognition configuration must include its URL, model, key and thinking setting.")
    if any(not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 8192
           or any(ord(char) < 32 for char in value[key]) for key in ("base_url", "model", "api_key")):
        raise LocalProfileError("Cognition URL, model and key must be nonempty text without control characters.")
    try:
        url = urlsplit(value["base_url"])
        valid = (url.scheme in {"http", "https"} and url.hostname and not url.username
                 and not url.password and not url.query and not url.fragment and url.port != 0)
        if valid:
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                hostname = url.hostname.encode("idna").decode().rstrip(".")
                valid = len(hostname) <= 253 and all(re.fullmatch(
                    r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)", label,
                ) for label in hostname.split("."))
            valid = valid and not any(character.isspace() for character in value["base_url"])
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise LocalProfileError("Cognition URL must be an HTTP(S) base URL without credentials, query or fragment.")
    if value["thinking"] not in (None, "enabled", "disabled"):
        raise LocalProfileError("Cognition thinking must be enabled, disabled, or unset.")
    if any(not 33 <= ord(character) <= 126 for character in value["api_key"]):
        raise LocalProfileError("Cognition key must contain only visible ASCII characters.")
    return dict(value)


def _validate(document, directory: Path) -> DesktopProfile:
    fields = {field.name for field in DesktopProfile.__dataclass_fields__.values()} - {"directory"}
    optional = {"database_engine"}
    if not isinstance(document, dict) or not (fields - optional) | {"version"} <= set(document) <= fields | {"version"}:
        raise LocalProfileError("Profile is incomplete or contains unknown fields; restore its original configuration.")
    if document.get("database_engine", "docker") not in ("docker", "embedded"):
        raise LocalProfileError("Profile database engine must be docker or embedded.")
    if type(document["version"]) is not int or document["version"] != PROFILE_VERSION:
        raise LocalProfileError("Profile version is unsupported.")
    for name in ("installation_id", "brain_id", "tenant_id", "principal_id"):
        try:
            if not isinstance(document[name], str) or str(UUID(document[name])) != document[name]:
                raise ValueError
        except ValueError:
            raise LocalProfileError("Profile contains an invalid persistent identity.") from None
    name = document["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or any(ord(char) < 32 for char in name):
        raise LocalProfileError("Profile brain name must contain 1 to 120 printable characters.")
    for name in ("api_port", "database_port"):
        if type(document[name]) is not int or not 1024 <= document[name] <= 65535:
            raise LocalProfileError("Profile ports must be integers from 1024 to 65535.")
    if document["api_port"] == document["database_port"]:
        raise LocalProfileError("API and database ports must differ.")
    for name in ("api_key", "admin_password", "app_password"):
        value = document[name]
        if not isinstance(value, str) or not 32 <= len(value) <= 1024 or any(
            char.isspace() or ord(char) < 32 for char in value
        ):
            raise LocalProfileError("Profile credentials are missing or invalid; restore its original configuration.")
        if name == "api_key" and any(not 33 <= ord(character) <= 126 for character in value):
            raise LocalProfileError("Profile API credential must contain only visible ASCII characters.")
    values = {key: document[key] for key in fields if key in document}
    values["cognition"] = _cognition(values["cognition"])
    return DesktopProfile(directory=directory, **values)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LocalProfileError("Profile contains duplicate fields.")
        result[key] = value
    return result


def _load(directory: Path) -> DesktopProfile:
    try:
        descriptor = _open_private(directory / PROFILE_FILE, os.O_RDONLY)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            content = stream.read(_MAX_PROFILE_BYTES + 1)
        if len(content.encode()) > _MAX_PROFILE_BYTES:
            raise LocalProfileError("Profile exceeds the supported size.")
        document = json.loads(content, object_pairs_hook=_pairs)
    except FileNotFoundError:
        raise LocalProfileError("No desktop profile exists; run desktop setup first.") from None
    except (json.JSONDecodeError, UnicodeError):
        raise LocalProfileError("Profile is damaged; restore its original configuration.") from None
    return _validate(document, directory)


def load_profile(directory: Path | None = None) -> DesktopProfile:
    path = _directory(directory)
    if not path.is_dir():
        raise LocalProfileError("No desktop profile exists; run desktop setup first.")
    with profile_lock(path):
        return _load(path)


def _save(profile: DesktopProfile) -> None:
    document = {name: getattr(profile, name) for name in DesktopProfile.__dataclass_fields__ if name != "directory"}
    if profile.database_engine == "docker":
        del document["database_engine"]  # Keep profiles written before engine selection byte-compatible.
    document["version"] = PROFILE_VERSION
    _validate(document, profile.directory)
    destination = profile.directory / PROFILE_FILE
    if destination.is_symlink():
        raise LocalProfileError("Profile files must not be symbolic links.")
    temporary = profile.directory / (".profile-" + secrets.token_hex(16) + ".tmp")
    try:
        descriptor = _open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if os.name != "nt":
            descriptor = os.open(profile.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _available_port(requested: int | None, preferred: int, excluded: set[int]) -> int:
    if requested is not None and (type(requested) is not int or not 1024 <= requested <= 65535):
        raise LocalProfileError("Requested ports must be integers from 1024 to 65535.")
    candidate = preferred if requested is None else requested
    if candidate in excluded and requested is not None:
        raise LocalProfileError("API and database ports must differ.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            if candidate in excluded:
                raise OSError
            listener.bind(("127.0.0.1", candidate))
        except OSError:
            if requested is not None:
                raise LocalProfileError("The requested local port is already in use.") from None
            listener.bind(("127.0.0.1", 0))
        selected = listener.getsockname()[1]
    if selected in excluded:
        return _available_port(requested, preferred, excluded)
    return selected


def ensure_profile(
    directory: Path | None = None, *, api_port: int | None = None, database_port: int | None = None,
    database_engine: str = "docker",
) -> DesktopProfile:
    if database_engine not in ("docker", "embedded"):
        raise LocalProfileError("Database engine must be docker or embedded.")
    for port in (api_port, database_port):
        if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
            raise LocalProfileError("Requested ports must be integers from 1024 to 65535.")
    with profile_lock(directory) as path:
        if (path / PROFILE_FILE).exists() or (path / PROFILE_FILE).is_symlink():
            profile = _load(path)
            if any(value is not None and value != getattr(profile, name) for name, value in (
                ("api_port", api_port), ("database_port", database_port),
            )):
                raise LocalProfileError("Existing profile ports cannot be changed during setup.")
            return profile
        if any(entry.name != ".profile.lock" and not re.fullmatch(
            r"\.profile-[0-9a-f]{32}\.tmp", entry.name,
        ) for entry in path.iterdir()):
            raise LocalProfileError("Profile configuration is missing from an existing installation; restore it before setup.")
        api = _available_port(api_port, 8788, set())
        database = _available_port(database_port, 55433, {api})
        profile = DesktopProfile(
            directory=path, installation_id=str(uuid4()), brain_id=str(uuid4()), name="My Agent",
            api_port=api, database_port=database, api_key="am_" + secrets.token_urlsafe(36),
            admin_password=secrets.token_hex(24), app_password=secrets.token_hex(24),
            tenant_id=str(uuid4()), principal_id=str(uuid4()), database_engine=database_engine,
        )
        _save(profile)
        return profile


def configure_cognition(
    profile: DesktopProfile, base_url: str, model: str, api_key: str, thinking: str | None = None,
) -> DesktopProfile:
    cognition = _cognition({"base_url": base_url, "model": model, "api_key": api_key, "thinking": thinking})
    with profile_lock(profile.directory):
        current = _load(profile.directory)
        if any(getattr(current, name) != getattr(profile, name) for name in (
            "installation_id", "brain_id", "tenant_id", "principal_id", "api_key", "admin_password", "app_password",
        )):
            raise LocalProfileError("Profile identity changed; reload it before configuring cognition.")
        from .cognition_config import load_cognition_policy

        load_cognition_policy(profile.directory).validate_thinking(cognition["thinking"])
        result = replace(current, cognition=cognition)
        _save(result)
        return result
