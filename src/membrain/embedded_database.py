"""Embedded PostgreSQL for desktop and plugin installations without Docker.

Only the PostgreSQL and pgvector binaries from the optional ``pgserver`` wheel are used. The cluster
listens on 127.0.0.1 with password authentication, so connection strings, migrations, roles and
row-level security match the Docker engine exactly.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

ENGINES = ("docker", "embedded")
SERVICE_ACCOUNT = "membrain-db"
SHARED_ROOT = Path("/var/lib/membrain")
# Root clusters created before the memBrain rename stay where they are, under their original account.
LEGACY_SERVICE_ACCOUNT = "agent-memory-db"
LEGACY_SHARED_ROOT = Path("/var/lib/agent-memory")
_EXECUTABLE = ".exe" if os.name == "nt" else ""


class EmbeddedDatabaseError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _installation() -> Path | None:
    try:
        spec = importlib.util.find_spec("pgserver")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    root = Path(next(iter(spec.submodule_search_locations))) / "pginstall"
    required = ("initdb", "pg_ctl", "postgres", "pg_dump", "pg_restore")
    if not all((root / "bin" / (name + _EXECUTABLE)).is_file() for name in required):
        return None
    return root


def available() -> bool:
    return _installation() is not None


def default_engine() -> str:
    return "embedded" if available() else "docker"


def _running_as_root() -> bool:
    return os.name != "nt" and os.geteuid() == 0


@dataclass(frozen=True)
class _Layout:
    binaries: Path
    data: Path
    log: Path
    account: tuple[int, int] | None


def _nologin() -> str:
    return next((path for path in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false") if Path(path).exists()),
                "/bin/false")


def _service_account(name: str = SERVICE_ACCOUNT) -> tuple[int, int]:
    import pwd

    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        commands = (
            ["useradd", "--system", "--no-create-home", "--home-dir", "/nonexistent",
             "--shell", _nologin(), name],
            ["adduser", "-S", "-D", "-H", "-h", "/nonexistent", "-s", _nologin(), name],
        )
        for command in commands:
            if shutil.which(command[0]):
                subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30, check=False)
                break
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            raise EmbeddedDatabaseError(
                "embedded_database_account_unavailable",
                f"PostgreSQL cannot run as root and the '{name}' system account could not be "
                "created. Run as a regular user or choose the Docker database engine.",
            ) from None
    return entry.pw_uid, entry.pw_gid


def _shared_binaries(source: Path, shared_root: Path = SHARED_ROOT) -> Path:
    """Copy binaries where an unprivileged account can execute them; root home directories are private."""
    marker = str(source.resolve())
    target = shared_root / ("postgres-" + hashlib.sha256(marker.encode()).hexdigest()[:16])
    if (target / ".complete").is_file():
        return target
    shared_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    staging = shared_root / (".staging-" + secrets.token_hex(8))
    try:
        # Keep the wheel's relative layout: binaries locate vendored libraries through
        # an RPATH such as $ORIGIN/../../../pgserver.libs.
        shutil.copytree(source, staging / "pgserver" / "pginstall", symlinks=True)
        vendored = source.parent.parent / "pgserver.libs"
        if vendored.is_dir():
            shutil.copytree(vendored, staging / "pgserver.libs", symlinks=True)
        for directory, _, names in os.walk(staging):
            os.chmod(directory, 0o755)
            for name in names:
                path = Path(directory) / name
                if not path.is_symlink():
                    os.chmod(path, 0o755 if os.access(path, os.X_OK) else 0o644)
        (staging / ".complete").write_text(marker + "\n", encoding="utf-8")
        try:
            os.replace(staging, target)
        except OSError:
            if not (target / ".complete").is_file():
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return target


def _traversable(directory: Path) -> bool:
    """Whether every directory down to ``directory`` lets other accounts pass through it."""
    try:
        path = Path(directory).resolve()
        return all(os.stat(part).st_mode & 0o001 for part in (path, *path.parents))
    except OSError:
        return False


def _layout(profile) -> _Layout:
    source = _installation()
    if source is None:
        raise EmbeddedDatabaseError(
            "embedded_database_unavailable",
            "The embedded database is not installed for this platform. Install the 'embedded' extra "
            "or choose the Docker database engine.",
        )
    if not _running_as_root():
        return _Layout(source / "bin", profile.directory / "postgres", profile.directory / "postgres.log", None)
    legacy = (LEGACY_SHARED_ROOT / "clusters" / profile.installation_id / "data" / "PG_VERSION").is_file()
    shared_root = LEGACY_SHARED_ROOT if legacy else SHARED_ROOT
    account = _service_account(LEGACY_SERVICE_ACCOUNT if legacy else SERVICE_ACCOUNT)
    cluster = shared_root / "clusters" / profile.installation_id
    shared_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    (shared_root / "clusters").mkdir(mode=0o755, exist_ok=True)
    if not legacy and not (cluster / "data" / "PG_VERSION").is_file() and _traversable(profile.directory):
        # Keep the data with the installation (a container's data volume, for example) when the service account
        # can reach it; only the binaries need the shared root.
        cluster = profile.directory / "postgres-cluster"
    cluster.mkdir(mode=0o700, exist_ok=True)
    os.chown(cluster, *account)
    binaries = _shared_binaries(source, shared_root) / "pgserver" / "pginstall" / "bin"
    return _Layout(binaries, cluster / "data", cluster / "postgres.log", account)


def _run(layout: _Layout, program: str, *arguments: str, timeout: float = 120,
         capture: bool = False) -> subprocess.CompletedProcess:
    command = [str(layout.binaries / (program + _EXECUTABLE)), *arguments]
    options = {}
    if layout.account is not None:
        options.update(user=layout.account[0], group=layout.account[1], extra_groups=[],
                       cwd=str(layout.data.parent))
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PG", "MEMORY_")) and key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment["LC_ALL"] = "C"
    # pg_ctl leaves the postmaster attached to inherited pipes, so only short-lived tools are captured.
    output = subprocess.PIPE if capture else subprocess.DEVNULL
    return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT if capture
                          else subprocess.DEVNULL, env=environment, timeout=timeout, check=False, **options)


def _log_tail(layout: _Layout, limit: int = 1200) -> str:
    try:
        with open(layout.log, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - limit))
            return stream.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _failure(code: str, summary: str, layout: _Layout) -> EmbeddedDatabaseError:
    tail = _log_tail(layout)
    detail = f" Last database log lines:\n{tail}" if tail else f" See {layout.log}."
    return EmbeddedDatabaseError(code, summary + detail)


def _initialize(profile, layout: _Layout) -> None:
    if (layout.data / "PG_VERSION").is_file():
        return
    staging = layout.data.parent / (".initdb-" + secrets.token_hex(8))
    password_file = layout.data.parent / (".password-" + secrets.token_hex(8))
    try:
        descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(profile.admin_password + "\n")
        if layout.account is not None:
            os.chown(password_file, *layout.account)
        result = _run(layout, "initdb", "-D", str(staging), "-U", "postgres", "-A", "scram-sha-256",
                      "--pwfile", str(password_file), "-E", "UTF8", "--locale=C", "--no-instructions",
                      timeout=300, capture=True)
        if result.returncode:
            detail = result.stdout.decode("utf-8", "replace").strip()[-800:]
            raise EmbeddedDatabaseError("database_initialization_failed",
                                        "The embedded database could not be initialized." + (
                                            f" initdb reported:\n{detail}" if detail else ""))
        with open(staging / "postgresql.conf", "a", encoding="utf-8") as stream:
            stream.write("\n# memBrain embedded engine\n"
                         "listen_addresses = '127.0.0.1'\n"
                         f"port = {profile.database_port}\n"
                         "unix_socket_directories = ''\n"
                         "max_connections = 50\n"
                         "shared_buffers = 32MB\n")
        os.replace(staging, layout.data)
    finally:
        password_file.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def _admin_connection(profile, database: str):
    return psycopg.connect(host="127.0.0.1", port=profile.database_port, user="postgres",
                           password=profile.admin_password, dbname=database, connect_timeout=5,
                           autocommit=True)


def running(profile) -> bool:
    layout = _layout(profile)
    return (layout.data / "PG_VERSION").is_file() and _run(layout, "pg_ctl", "status", "-D", str(layout.data),
                                                         timeout=30).returncode == 0


def start(profile) -> None:
    layout = _layout(profile)
    _initialize(profile, layout)
    if _run(layout, "pg_ctl", "status", "-D", str(layout.data), timeout=30).returncode != 0:
        result = _run(layout, "pg_ctl", "start", "-D", str(layout.data), "-l", str(layout.log),
                      "-w", "-t", "90", timeout=120)
        if result.returncode:
            raise _failure("database_start_failed", "The embedded database did not start.", layout)
    deadline = time.monotonic() + 30
    while True:
        try:
            with _admin_connection(profile, "postgres") as connection:
                exists = connection.execute(
                    "SELECT 1 FROM pg_database WHERE datname='agent_memory'").fetchone()
                if not exists:
                    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier("agent_memory")))
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise _failure("database_start_failed", "The embedded database is not accepting connections.",
                               layout) from None
            time.sleep(0.3)


def stop(profile) -> None:
    layout = _layout(profile)
    if not (layout.data / "PG_VERSION").is_file():
        return
    if _run(layout, "pg_ctl", "status", "-D", str(layout.data), timeout=30).returncode != 0:
        return
    if _run(layout, "pg_ctl", "stop", "-D", str(layout.data), "-m", "fast", "-w", "-t", "60",
            timeout=90).returncode:
        raise _failure("database_stop_failed", "The embedded database did not stop.", layout)


def client_command(profile, program: str, *arguments: str) -> tuple[list[str], dict[str, str]]:
    """Command and environment for pg_dump or pg_restore against the embedded cluster."""
    layout = _layout(profile)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("PG", "MEMORY_"))}
    environment.update(PGPASSWORD=profile.admin_password, PGCONNECT_TIMEOUT="5")
    command = [str(layout.binaries / (program + _EXECUTABLE)), "--host=127.0.0.1",
               f"--port={profile.database_port}", *arguments]
    return command, environment
