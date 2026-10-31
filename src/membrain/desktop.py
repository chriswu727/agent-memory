from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from importlib.resources import files
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from . import __version__
from .build_identity import BUILD_ID, SCHEMA_ID
from .config import configured_service
from .helpers import Principal
from .local_profile import (
    LocalProfileBusy,
    _open_private,
    _private,
    ensure_profile,
    load_profile,
    profile_lock,
)


class DesktopError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _port_open(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


WORK_SLICE_SECONDS = 2.0


def _environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("MEMORY_", "COMPOSE_"))
            and key not in {"PYTHONPATH", "PYTHONHOME"}}


def _private_write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise DesktopError("unsafe_path", "A managed file is a symbolic link.")
    fd = _open_private(path, os.O_WRONLY | os.O_CREAT)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.truncate(0)
        stream.write(content)


def _docker_endpoint(docker: str, environment: dict) -> str:
    endpoint = environment.get("DOCKER_HOST") if not environment.get("DOCKER_CONTEXT") else None
    if not endpoint:
        try:
            result = subprocess.run(
                [docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                env=environment, capture_output=True, text=True, timeout=10, check=False,
            )
            endpoint = json.loads(result.stdout) if not result.returncode else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            endpoint = None
    if not isinstance(endpoint, str):
        raise DesktopError("docker_unavailable", "The Docker context is unavailable; open local Docker and retry.")
    try:
        parsed = urlsplit(endpoint)
        local = (parsed.scheme == "unix" and not parsed.netloc and parsed.path.startswith("/")) or (
            parsed.scheme == "npipe" and (
                not parsed.netloc and parsed.path.startswith("//./pipe/")
                or parsed.netloc == "." and parsed.path.startswith("/pipe/"))) or (
            parsed.scheme in {"tcp", "http", "https"}
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    except ValueError:
        local = False
    if not local:
        raise DesktopError("remote_docker_unsupported", "Desktop memory requires a local Docker context. Select local Docker and retry; no remote database was created.")
    return endpoint


def _compose_invocation(profile, *arguments: str):
    docker = shutil.which("docker")
    if docker is None and sys.platform == "darwin":
        docker = next((str(path) for path in (
            Path("/usr/local/bin/docker"), Path("/opt/homebrew/bin/docker"),
            Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
        ) if path.is_file()), None)
    if docker is None:
        raise DesktopError("docker_missing", "Install and open Docker Desktop, then run setup again.")
    compose = profile.directory / "compose.yaml"
    _private_write(compose, files("membrain").joinpath(
        "resources/desktop-compose.yaml").read_text(encoding="utf-8"))
    environment_file = profile.directory / "compose.env"
    _private_write(environment_file, "")
    environment = _environment()
    environment.update(MEMORY_DESKTOP_DATABASE_PORT=str(profile.database_port),
                       MEMORY_DESKTOP_ADMIN_PASSWORD=profile.admin_password)
    environment["DOCKER_HOST"] = _docker_endpoint(docker, environment)
    environment.pop("DOCKER_CONTEXT", None)
    command = [docker, "compose", "--project-name", profile.project_name,
               "--project-directory", str(profile.directory), "--env-file", str(environment_file),
               "--file", str(compose), *arguments]
    return command, environment


def _compose(profile, *arguments: str) -> None:
    command, environment = _compose_invocation(profile, *arguments)
    try:
        result = subprocess.run(command, cwd=profile.directory, env=environment,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise DesktopError("docker_unavailable", "Docker did not respond; open Docker and retry.") from None
    if result.returncode:
        detail = (getattr(result, "stderr", None) or b"").decode("utf-8", "replace")
        for secret in (profile.api_key, profile.admin_password, profile.app_password):
            detail = detail.replace(secret, "[redacted]")
        detail = detail.strip()[-600:]
        raise DesktopError("database_start_failed", "Docker could not manage the database. Check Docker, available ports and disk space, then retry." + (
            f" Docker reported:\n{detail}" if detail else ""))


def _database_up(profile) -> None:
    if profile.database_engine != "embedded":
        _compose(profile, "up", "--detach", "--wait", "--wait-timeout", "90")
        return
    from . import embedded_database

    try:
        embedded_database.start(profile)
    except embedded_database.EmbeddedDatabaseError as error:
        raise DesktopError(error.code, str(error)) from None
    except (OSError, subprocess.SubprocessError):
        raise DesktopError("database_start_failed", "The embedded database could not be started; data was preserved.") from None


def _database_stop(profile) -> None:
    if profile.database_engine != "embedded":
        _compose(profile, "stop", "--timeout", "15")
        return
    from . import embedded_database

    try:
        embedded_database.stop(profile)
    except embedded_database.EmbeddedDatabaseError as error:
        raise DesktopError(error.code, str(error)) from None
    except (OSError, subprocess.SubprocessError):
        raise DesktopError("database_stop_failed", "The embedded database did not confirm shutdown; data was preserved.") from None


def _fingerprint(profile, *, settings=None) -> str:
    from .recovery_profile import load_binding

    settings = settings or profile.settings()
    values = {"database_port": profile.database_port, "api_port": profile.api_port,
              "cognition": profile.cognition, "cognition_policy": asdict(settings.semantic_policy()),
              "cognition_reasoning_effort": settings.cognition_reasoning_effort,
              "cognition_max_envelope_bytes": settings.cognition_max_envelope_bytes,
              "recovery_binding": load_binding(profile), "version": __version__, "build_id": BUILD_ID}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _identity_signature(profile, instance_id: str, challenge: str) -> str:
    message = json.dumps([profile.installation_id, profile.brain_id, instance_id, challenge])
    return hmac.new(profile.api_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def _probe(profile) -> dict | None:
    try:
        with httpx.Client(trust_env=False, timeout=7, follow_redirects=False) as client:
            challenge = secrets.token_hex(32)
            identity_response = client.get(profile.base_url + "/v1/local/identity",
                                           headers={"X-Memory-Challenge": challenge})
            if identity_response.status_code != 200 or len(identity_response.content) > 4096:
                return None
            identity = identity_response.json()
            if (not isinstance(identity, dict)
                    or identity.get("installation_id") != profile.installation_id
                    or identity.get("brain_id") != profile.brain_id
                    or not isinstance(identity.get("instance_id"), str)
                    or not 1 <= len(identity["instance_id"]) <= 128
                    or not isinstance(identity.get("signature"), str)
                    or not hmac.compare_digest(identity["signature"].encode(), _identity_signature(
                        profile, identity["instance_id"], challenge).encode())):
                return None
            response = client.get(profile.base_url + "/v1/local/status",
                                  headers={"Authorization": "Bearer " + profile.api_key})
        document = response.json()
        if (response.status_code == 200 and isinstance(document, dict)
                and document.get("installation_id") == profile.installation_id
                and document.get("brain_id") == profile.brain_id
                and type(document.get("ready")) is bool
                and document.get("state") in {"ready", "degraded", "stopping"}
                and document.get("instance_id") == identity["instance_id"]
                and isinstance(document.get("configuration"), str)
                and document.get("database") in {
                    "ready", "brain_missing", "unavailable", "incompatible", "migration_required",
                }
                and document.get("worker") in {"starting", "ready", "busy", "retrying", "failed", "stopped", "stalled"}
                and (not document["ready"] or (
                    document["state"] == "ready" and document["database"] == "ready"
                    and document["worker"] in {"ready", "busy"}))):
            return document
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return None


def _base_status(profile) -> dict:
    return {"profile": str(profile.directory), "brain_id": profile.brain_id,
            "installation_id": profile.installation_id, "base_url": profile.base_url,
            "cognition": "configured" if profile.cognition else "not_configured",
            "host_adapter": "not_verified", "mcp": "tools_only",
            "version": __version__, "build_id": BUILD_ID, "schema_id": SCHEMA_ID}


def _supervisor_active(profile) -> bool:
    try:
        with profile_lock(profile.directory / "supervisor", timeout=0):
            return False
    except LocalProfileBusy:
        return True


def status(directory: Path | None = None) -> dict:
    profile = load_profile(directory)
    from .recovery_profile import MAINTENANCE_FILE, load_binding, read_document

    marker, binding = read_document(profile.directory / MAINTENANCE_FILE), load_binding(profile)
    if marker is not None or binding is not None and binding["state"] == "quarantined":
        return {**_base_status(profile), "ready": False, "state": "recovery_blocked",
                "database": "not_checked", "worker": "not_checked",
                "recovery_epoch": binding["recovery_epoch"] if binding else None}
    result = _probe(profile)
    if result is not None:
        result["configuration_current"] = result.get("configuration") == _fingerprint(profile)
        if not result["configuration_current"]:
            result.update(ready=False, state="restart_required")
        return result
    state = "starting_or_stopping" if _supervisor_active(profile) else (
        "port_conflict" if _port_open(profile.api_port) else "stopped")
    return {**_base_status(profile), "ready": False, "state": state,
            "database": "not_checked", "worker": "not_checked"}


def _check_database(profile, *, require_current=False) -> dict:
    from .storage import Database, SchemaCompatibilityError

    try:
        return Database(profile.database_url, profile.migration_database_url).check_compatibility(
            require_current=require_current,
        )
    except SchemaCompatibilityError:
        raise DesktopError("database_schema_incompatible", "This installation cannot use the database schema. Install a compatible release; the existing service and data were preserved.") from None
    except Exception:
        raise DesktopError("database_unavailable", "Database compatibility could not be checked. Check the database before retrying; the existing service and data were preserved.") from None


def _prepare_database(profile) -> None:
    from .cli import bootstrap
    from .service import MemoryService
    from .storage import Database

    if _port_open(profile.database_port):
        _check_database(profile)
    _database_up(profile)
    _check_database(profile)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            bootstrap(profile.settings(), app_password=profile.app_password)
        MemoryService(Database(profile.database_url)).ensure_brain(
            Principal(profile.tenant_id, profile.principal_id), profile.brain_id, profile.name,
        )
        from .recovery_profile import ensure_authority

        ensure_authority(profile)
    except Exception:
        raise DesktopError("database_initialization_failed", "Database initialization failed. Existing credentials and data were preserved; retry after checking the database.") from None


def _launch(profile):
    arguments = [sys.executable, "-m", "membrain.cli", "desktop", "_run",
                 "--directory", str(profile.directory)]
    options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    }
    return subprocess.Popen(arguments, cwd=profile.directory, env=_environment(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **options)


def start(directory: Path | None = None) -> dict:
    profile = ensure_profile(directory)
    with profile_lock(profile.directory / "lifecycle", timeout=360):
        profile = load_profile(profile.directory)
        from .recovery_profile import check_start_allowed

        check_start_allowed(profile, allow_initializing=True)
        existing = status(profile.directory)
        if existing["state"] == "restart_required":
            raise DesktopError("restart_required", "Configuration changed; run desktop restart to apply it.")
        if existing.get("ready"):
            return existing
        if _probe(profile) is not None:
            raise DesktopError("service_degraded", "The local service is running but not ready. Check desktop status before restarting.")
        if _supervisor_active(profile):
            raise DesktopError("service_transitioning", "The service is starting or finishing work. Check desktop status before retrying.")
        if _port_open(profile.api_port):
            raise DesktopError("port_conflict", "The saved API port belongs to another service. Free that port and retry; no process was stopped.")
        print("Starting the local memory database and service…", file=sys.stderr)
        _prepare_database(profile)
        process = _launch(profile)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            result = status(profile.directory)
            if result.get("ready"):
                return result
            if process.poll() is not None:
                raise DesktopError("service_start_failed", "The local service exited during startup. Check desktop logs; data was preserved.")
            time.sleep(0.2)
        raise DesktopError("service_start_timeout", "The service has not become ready. Check desktop status and logs before retrying.")


def _wait_stopped(profile, timeout=55) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with profile_lock(profile.directory / "supervisor", timeout=0):
                return
        except LocalProfileBusy:
            time.sleep(0.2)
    raise DesktopError("stop_timeout", "The service is still finishing work; the database was left running. Check status before retrying.")


def stop(directory: Path | None = None) -> dict:
    profile = load_profile(directory)
    with profile_lock(profile.directory / "lifecycle", timeout=90):
        running = _probe(profile)
        if running:
            try:
                with httpx.Client(trust_env=False, timeout=7, follow_redirects=False) as client:
                    response = client.post(profile.base_url + "/v1/local/stop", headers={
                        "Authorization": "Bearer " + profile.api_key,
                        "X-Memory-Instance": running["instance_id"],
                    })
                    response.raise_for_status()
            except (httpx.HTTPError, KeyError):
                raise DesktopError("stop_unconfirmed", "The service did not confirm shutdown; no process was killed.") from None
        elif _port_open(profile.api_port):
            raise DesktopError("port_conflict", "The API port is not this installation; no process was stopped.")
        _wait_stopped(profile)
        _database_stop(profile)
        return {**_base_status(profile), "ready": False, "state": "stopped",
                "database": "stopped", "worker": "stopped", "data_preserved": True}


def restart(directory: Path | None = None) -> dict:
    profile = load_profile(directory)
    with profile_lock(profile.directory / "lifecycle", timeout=360):
        if _probe(profile) is not None or _port_open(profile.database_port):
            _check_database(profile)
        stop(profile.directory)
        return start(profile.directory)


def _logger(profile):
    path = profile.directory / "service.log"
    if any(candidate.is_symlink() for candidate in (path, *(path.with_name(f"service.log.{i}") for i in range(1, 4)))):
        raise DesktopError("unsafe_path", "The log path is a symbolic link.")
    if not path.exists():
        _private_write(path, "")
    _private(path)
    logger = logging.getLogger("membrain.desktop." + profile.installation_id)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return logger, handler


class LocalSupervisor:
    def __init__(self, profile, logger):
        self.profile = profile
        self.logger = logger
        self.instance_id = str(uuid4())
        self.stopped = threading.Event()
        self.worker = None
        self.worker_state = "starting"
        self.last_progress = time.monotonic()
        self.server = None
        with profile_lock(profile.directory):
            self.settings = profile.settings()
            self.configuration = _fingerprint(profile, settings=self.settings)

    def log(self, event, error=None):
        record = {"time": round(time.time(), 3), "event": event, "instance_id": self.instance_id}
        if error is not None:
            # The class and error code identify the cause; exception text may contain memory content.
            record["error"] = {"type": type(error).__name__, "code": getattr(error, "code", None)}
        self.logger.info(json.dumps(record))

    def work(self):
        try:
            with configured_service(self.settings) as service:
                principal = Principal(self.profile.tenant_id, self.profile.principal_id)
                while not self.stopped.is_set():
                    try:
                        self.worker_state = "busy"
                        for brain in service.list_brains(principal):
                            if self.stopped.is_set():
                                break
                            # Drain queued work briefly instead of one job per second; backfills can be long.
                            deadline = time.monotonic() + WORK_SLICE_SECONDS
                            while (service.work_once(principal, brain["id"]) and not self.stopped.is_set()
                                   and time.monotonic() < deadline):
                                pass
                        self.last_progress = time.monotonic()
                        self.worker_state = "ready"
                    except Exception as error:
                        self.worker_state = "retrying"
                        self.log("worker_iteration_failed", error)
                    self.stopped.wait(1)
        except Exception as error:
            self.worker_state = "failed"
            self.log("worker_failed", error)
        finally:
            if self.stopped.is_set():
                self.worker_state = "stopped"

    def snapshot(self):
        from psycopg.conninfo import make_conninfo

        from .storage import Database, SchemaCompatibilityError, schema_status

        database = "unavailable"
        try:
            with Database(make_conninfo(self.profile.database_url, connect_timeout="2")).transaction(
                Principal(self.profile.tenant_id, self.profile.principal_id),
            ) as connection:
                connection.execute("SET LOCAL statement_timeout = '2000'")
                schema = schema_status(connection)
                if schema["state"] != "current":
                    database = "migration_required"
                else:
                    from .recovery_profile import authority_for, load_binding

                    if load_binding(self.profile) is not None:
                        authority_for(self.profile).check(connection, Principal(self.profile.tenant_id, self.profile.principal_id))
                    row = connection.execute(
                        "SELECT id FROM memory_brains WHERE id=%s AND tenant_id=%s AND owner_id=%s",
                        (self.profile.brain_id, self.profile.tenant_id, self.profile.principal_id),
                    ).fetchone()
                    database = "ready" if row else "brain_missing"
        except SchemaCompatibilityError:
            database = "incompatible"
        except Exception:
            pass
        worker = self.worker_state
        if self.worker is None or not self.worker.is_alive():
            worker = "stopped"
        elif time.monotonic() - self.last_progress > max(300, self.settings.cognition_max_wall_time_ms / 1000 + 30):
            worker = "stalled"
        ready = (database == "ready" and worker in {"ready", "busy"}
                 and not self.stopped.is_set())
        return {**_base_status(self.profile), "ready": ready,
                "state": "stopping" if self.stopped.is_set() else "ready" if ready else "degraded",
                "database": database, "worker": worker,
                "instance_id": self.instance_id, "configuration": self.configuration}

    def app(self):
        from fastapi import Body, Header, HTTPException

        from .api import create_app

        app = create_app(self.settings)

        def authenticate(authorization):
            expected = "Bearer " + self.profile.api_key
            if not isinstance(authorization, str) or not hmac.compare_digest(authorization.encode(), expected.encode()):
                raise HTTPException(401, "A valid local credential is required")

        @app.get("/v1/local/identity")
        def local_identity(x_memory_challenge: str | None = Header(default=None)):
            if (not isinstance(x_memory_challenge, str) or len(x_memory_challenge) != 64
                    or any(char not in "0123456789abcdef" for char in x_memory_challenge)):
                raise HTTPException(422, "A valid identity challenge is required")
            return {"installation_id": self.profile.installation_id, "brain_id": self.profile.brain_id,
                    "instance_id": self.instance_id, "signature": _identity_signature(
                        self.profile, self.instance_id, x_memory_challenge)}

        @app.post("/v1/local/ui-session")
        def ui_session(body: dict = Body(...)):
            from . import host

            if not host.consume_ui_code(self.profile.directory, body.get("code")):
                raise HTTPException(401, "This link has expired or was already used. Run membrain plugin ui again.")
            return {"api_key": self.profile.api_key, "brain_id": self.profile.brain_id}

        @app.get("/v1/local/recording")
        def recording_state(authorization: str | None = Header(default=None)):
            from . import host

            authenticate(authorization)
            return {"recording": "paused" if host.paused(self.profile.directory) else "on"}

        @app.post("/v1/local/recording")
        def change_recording(body: dict = Body(...), authorization: str | None = Header(default=None)):
            from . import host

            authenticate(authorization)
            if body.get("paused") is True:
                hours = body.get("hours")
                try:
                    return host.pause(self.profile.directory, hours if hours is None else float(hours))
                except (TypeError, ValueError) as error:
                    raise HTTPException(422, str(error)) from None
            if body.get("paused") is False:
                return host.resume(self.profile.directory)
            raise HTTPException(422, "Set paused to true or false")

        @app.get("/v1/local/memory-status")
        def memory_status(authorization: str | None = Header(default=None)):
            from . import host

            authenticate(authorization)
            path = self.profile.directory / "plugin" / "spool.jsonl"
            try:
                with path.open(encoding="utf-8") as stream:
                    pending = sum(bool(line.strip()) for line in stream)
            except FileNotFoundError:
                pending = 0
            semantic = host.semantic_state(self.profile.directory)
            if semantic == "ready" and not (self.settings.embedding_local_directory or self.settings.embedding_base_url):
                semantic = "restart_required"
            return {**self.snapshot(), "semantic_recall": semantic,
                    "pending_records": pending}

        @app.post("/v1/local/semantic-model")
        def prepare_semantic_model(authorization: str | None = Header(default=None)):
            from . import host

            authenticate(authorization)
            state = host.semantic_state(self.profile.directory)
            if state == "unavailable":
                raise HTTPException(409, "Local model support is missing. Reinstall with the semantic extra.")
            if state != "ready":
                host._fetch_in_background(self.profile.directory)
            elif not self.settings.embedding_local_directory:
                host._fetch_in_background(self.profile.directory)
            return {"semantic_recall": host.semantic_state(self.profile.directory)}

        @app.get("/v1/local/status")
        def local_status(authorization: str | None = Header(default=None)):
            authenticate(authorization)
            return self.snapshot()

        @app.post("/v1/local/stop")
        def local_stop(authorization: str | None = Header(default=None),
                       x_memory_instance: str | None = Header(default=None)):
            authenticate(authorization)
            if x_memory_instance != self.instance_id:
                raise HTTPException(409, "The service instance has changed")
            self.log("shutdown_requested")
            self.stopped.set()
            if self.server:
                self.server.should_exit = True
            return {"state": "stopping", "instance_id": self.instance_id}

        return app


def run(directory: Path | None = None) -> None:
    import uvicorn

    profile = load_profile(directory)
    if os.name != "nt":
        os.umask(0o077)
    with profile_lock(profile.directory / "supervisor", timeout=0):
        from .recovery_profile import check_start_allowed

        check_start_allowed(profile)
        _check_database(profile, require_current=True)
        logger, handler = _logger(profile)
        supervisor = LocalSupervisor(profile, logger)
        supervisor.worker = threading.Thread(target=supervisor.work, name="memory-worker", daemon=True)
        try:
            supervisor.server = uvicorn.Server(uvicorn.Config(
                supervisor.app(), host="127.0.0.1", port=profile.api_port,
                access_log=False, log_level="critical", log_config=None,
                timeout_graceful_shutdown=10,
            ))
            supervisor.worker.start()
            supervisor.log("service_started")
            supervisor.server.run()
        except Exception:
            supervisor.log("service_failed")
            raise DesktopError("service_failed", "Local service failed; see the structured log.") from None
        finally:
            supervisor.stopped.set()
            if supervisor.worker.is_alive():
                supervisor.worker.join()
            supervisor.log("service_stopped")
            logger.removeHandler(handler)
            handler.close()
