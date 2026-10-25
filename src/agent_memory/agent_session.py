from __future__ import annotations

import json
import os
import re
import secrets
import threading
import unicodedata
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path

import httpx

from . import desktop_connection
from .delivery_receipts import confirmed_delivery_receipt
from .helpers import digest
from .local_profile import (
    LocalProfileBusy,
    LocalProfileError,
    _open_private,
    _private,
    load_profile,
    profile_lock,
)
from .runtime import (
    MEMORY_CONSUMPTION_VERSION,
    BrainRuntime,
    RuntimeBlocked,
    RuntimePolicy,
    TurnResult,
    _scope_conflict,
)
from .turns import FAILURE_CODES, STAGES

_MAX_PENDING_BYTES = 8192
_SESSION_TOKEN = object()
_ACTIVE_SCOPES: set[str] = set()
_ACTIVE_GUARD = threading.Lock()
_ERROR_CODES = {
    "acknowledgement_required", "delivery_already_recorded", "invalid_turn_id", "message_identity_conflict",
    "commit_recovery_required",
    "configuration_changed", "invalid_configuration", "invalid_message", "invalid_runtime_result",
    "invalid_scope", "no_pending_turn", "pending_turn", "profile_identity_changed",
    "profile_unavailable", "session_busy", "session_closed", "session_state_invalid",
    "session_state_unavailable",
}


def _reset_after_fork():
    global _ACTIVE_SCOPES, _ACTIVE_GUARD
    _ACTIVE_SCOPES, _ACTIVE_GUARD = set(), threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


class SessionError(RuntimeError):
    def __init__(self, code: str, turn_id: str | None = None):
        if (not isinstance(code, str) or code not in _ERROR_CODES or (turn_id is not None and (
                not isinstance(turn_id, str) or not re.fullmatch(r"turn-[0-9a-f]{40}", turn_id)))):
            raise ValueError("Invalid session error metadata")
        self.code, self.turn_id = code, turn_id
        super().__init__(code + (f": turn {turn_id}" if turn_id else ""))


def _identifier(value):
    return (isinstance(value, str) and 1 <= len(value) <= 256 and not any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ))


def _identity(profile, conversation_id, branch_id):
    return {name: getattr(profile, name) for name in (
        "installation_id", "brain_id", "tenant_id", "principal_id",
    )} | {"conversation_id": conversation_id, "branch_id": branch_id}


def _turn_id(identity, message_id):
    return "turn-" + digest([
        identity["brain_id"], identity["conversation_id"], identity["branch_id"], message_id,
    ])[:40]


def _unique_pairs(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError
        document[key] = value
    return document


def _read_pending(path, identity):
    try:
        descriptor = _open_private(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(_MAX_PENDING_BYTES + 1)
        if len(raw) > _MAX_PENDING_BYTES:
            raise ValueError
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        if not isinstance(value, dict) or set(value) != {
            "version", "identity", "message_id", "input_fingerprint", "turn_id",
            "generator_fingerprint", "runtime_fingerprint",
        }:
            raise ValueError
        if type(value["version"]) is not int or value["version"] != 1:
            raise ValueError
        if value["identity"] != identity or not _identifier(value["message_id"]):
            raise ValueError
        if any(not isinstance(value[name], str) or not re.fullmatch(r"[0-9a-f]{64}", value[name])
               for name in ("input_fingerprint", "generator_fingerprint", "runtime_fingerprint")):
            raise ValueError
        if value["turn_id"] != _turn_id(identity, value["message_id"]):
            raise ValueError
        return value
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError, RecursionError):
        raise SessionError("session_state_invalid") from None
    except (OSError, LocalProfileError):
        raise SessionError("session_state_unavailable") from None


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _save_pending(path, document):
    temporary = path.parent / (".pending-" + secrets.token_hex(16) + ".tmp")
    try:
        data = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(data) > _MAX_PENDING_BYTES:
            raise SessionError("session_state_invalid")
        if path.exists() or path.is_symlink():
            _private(path)
        descriptor = _open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except (OSError, LocalProfileError):
        raise SessionError("session_state_unavailable") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class AgentSession:
    """A locked synchronous conversation created by ``open_agent``."""

    def __init__(self, *, profile, identity, runtime, path, generator_fingerprint,
                 runtime_fingerprint, _token=None):
        if _token is not _SESSION_TOKEN:
            raise SessionError("invalid_configuration")
        self._profile, self._identity, self._runtime, self._path = profile, identity, runtime, path
        self._generator_fingerprint = generator_fingerprint
        self._runtime_fingerprint = runtime_fingerprint
        self._closed = False
        self._operation_lock = threading.Lock()

    @contextmanager
    def _operation(self):
        if not self._operation_lock.acquire(blocking=False):
            raise SessionError("session_busy")
        try:
            if self._closed:
                raise SessionError("session_closed")
            try:
                current = load_profile(self._profile.directory)
            except (OSError, LocalProfileError):
                raise SessionError("profile_unavailable") from None
            if _identity(current, self._identity["conversation_id"], self._identity["branch_id"]) != self._identity:
                raise SessionError("profile_identity_changed")
            yield
        finally:
            self._operation_lock.release()

    @property
    def pending(self) -> dict | None:
        with self._operation():
            value = _read_pending(self._path, self._identity)
            return {key: value[key] for key in ("turn_id", "message_id")} if value else None

    def _configuration(self, pending):
        if (pending["generator_fingerprint"] != self._generator_fingerprint
                or pending["runtime_fingerprint"] != self._runtime_fingerprint):
            raise SessionError("configuration_changed", pending["turn_id"])

    def _recovery_configuration(self, pending, row):
        if row is not None and row.get("stage") == "committed":
            raise SessionError("commit_recovery_required", pending["turn_id"])
        if row is None or (row.get("stage") not in ("delivered", "completed")
                           and not confirmed_delivery_receipt(row)):
            self._configuration(pending)

    def _binding(self, row, pending, *, code="session_state_invalid"):
        expected = {name: pending["identity"][name] for name in (
            "brain_id", "conversation_id", "branch_id",
        )} | {name: pending[name] for name in ("turn_id", "input_fingerprint")}
        if not isinstance(row, dict) or any(row.get(key) != value for key, value in expected.items()):
            raise SessionError(code, pending["turn_id"])

    def _existing_turn(self, pending, *, allow_missing=False, code="session_state_invalid"):
        try:
            row = self._runtime.journal.get(pending["turn_id"])
        except httpx.HTTPStatusError as error:
            if allow_missing and error.response.status_code == 404:
                return None
            raise
        self._binding(row, pending, code=code)
        return row

    def _clear(self, pending):
        try:
            self._path.unlink()
            _sync_directory(self._path.parent)
        except OSError:
            raise SessionError("session_state_unavailable", pending["turn_id"]) from None

    def _clear_confirmed_failure(self, pending, error):
        if error.turn_id != pending["turn_id"]:
            return
        try:
            row = self._existing_turn(pending)
        except httpx.HTTPError:
            return
        if confirmed_delivery_receipt(row):
            self._clear(pending)

    def _result(self, result, pending):
        if not isinstance(result, TurnResult) or result.turn_id != pending["turn_id"]:
            raise SessionError("invalid_runtime_result", pending["turn_id"])
        if result.stage == "committed":
            raise SessionError("commit_recovery_required", pending["turn_id"])
        if result.stage == "completed":
            self._clear(pending)
        return result

    def respond(self, text: str, *, message_id: str) -> TurnResult:
        if not isinstance(text, str) or not text.strip() or not _identifier(message_id):
            raise SessionError("invalid_message")
        try:
            text.encode("utf-8")
        except UnicodeError:
            raise SessionError("invalid_message") from None
        with self._operation():
            pending = _read_pending(self._path, self._identity)
            new_pending = pending is None
            fingerprint = digest({"message_id": message_id, "text": text})
            if pending:
                if pending["message_id"] != message_id or pending["input_fingerprint"] != fingerprint:
                    raise SessionError("pending_turn", pending["turn_id"])
                self._recovery_configuration(pending, self._existing_turn(pending, allow_missing=True))
            else:
                pending = {
                    "version": 1, "identity": self._identity, "message_id": message_id,
                    "input_fingerprint": fingerprint, "turn_id": _turn_id(self._identity, message_id),
                    "generator_fingerprint": self._generator_fingerprint,
                    "runtime_fingerprint": self._runtime_fingerprint,
                }
                self._existing_turn(pending, allow_missing=True, code="message_identity_conflict")
                _save_pending(self._path, pending)
            try:
                result = self._runtime.respond(
                    text, message_id=message_id, conversation_id=self._identity["conversation_id"],
                    branch_id=self._identity["branch_id"],
                )
            except RuntimeBlocked as error:
                if (new_pending and error.code == "scope_conflict" and error.turn_id == pending["turn_id"]
                        and self._existing_turn(pending, allow_missing=True) is None):
                    self._clear(pending)
                elif error.code != "scope_conflict":
                    self._clear_confirmed_failure(pending, error)
                raise
            return self._result(result, pending)

    def resume(self) -> TurnResult:
        with self._operation():
            pending = _read_pending(self._path, self._identity)
            if pending is None:
                raise SessionError("no_pending_turn")
            try:
                row = self._existing_turn(pending)
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    self._configuration(pending)
                raise
            self._recovery_configuration(pending, row)
            try:
                result = self._runtime.resume(pending["turn_id"])
            except RuntimeBlocked as error:
                self._clear_confirmed_failure(pending, error)
                raise
            return self._result(result, pending)

    def cancel(self, *, turn_id: str, acknowledge_uncertain: bool = False) -> dict:
        """Close a pending turn without undoing captured memory or external effects."""
        if not isinstance(turn_id, str) or not re.fullmatch(r"turn-[0-9a-f]{40}", turn_id):
            raise SessionError("invalid_turn_id")
        if type(acknowledge_uncertain) is not bool:
            raise SessionError("invalid_configuration")
        with self._operation():
            pending = _read_pending(self._path, self._identity)
            if pending is None:
                raise SessionError("no_pending_turn")
            if pending["turn_id"] != turn_id:
                raise SessionError("pending_turn", pending["turn_id"])
            row = self._existing_turn(pending, allow_missing=True)
            if row is None:
                try:
                    row = self._runtime.journal.begin(
                        turn_id=turn_id, conversation_id=self._identity["conversation_id"],
                        branch_id=self._identity["branch_id"], input_fingerprint=pending["input_fingerprint"],
                    )
                except httpx.HTTPStatusError as error:
                    if _scope_conflict(error) and self._existing_turn(pending, allow_missing=True) is None:
                        self._clear(pending)
                        return {"turn_id": turn_id, "stage": "not_started", "failure_code": "scope_conflict"}
                    raise
                self._binding(row, pending)
            if row.get("stage") in ("delivered", "committed"):
                raise SessionError("delivery_already_recorded", turn_id)
            if row.get("stage") not in (*STAGES, "failed"):
                raise SessionError("invalid_runtime_result", turn_id)
            if row["stage"] not in ("completed", "failed"):
                failure_code = {"generating": "generation_uncertain", "delivering": "delivery_uncertain"}.get(
                    row["stage"], "cancelled",
                )
                if failure_code != "cancelled" and not acknowledge_uncertain:
                    raise SessionError("acknowledgement_required", turn_id)
                row = self._runtime.journal.claim(
                    turn_id, worker_id=self._runtime.worker_id,
                    lease_seconds=RuntimePolicy().lease_seconds, expected_revision=row["revision"],
                )
                self._binding(row, pending)
                row = self._runtime.journal.fail(
                    turn_id, lease_token=row["lease_token"], expected_revision=row["revision"],
                    failure_code=failure_code,
                )
                self._binding(row, pending)
            if (row.get("stage") not in ("completed", "failed") or (
                    row["stage"] == "failed" and (not isinstance(row.get("failure_code"), str)
                                                  or row["failure_code"] not in FAILURE_CODES))):
                raise SessionError("invalid_runtime_result", turn_id)
            result = {"turn_id": turn_id, "stage": row["stage"],
                      "failure_code": row["failure_code"] if row["stage"] == "failed" else None}
            self._clear(pending)
            return result


@contextmanager
def open_agent(
    directory: Path | None = None, *, conversation_id: str, branch_id: str = "main",
    deliver=None, instructions: str = "", policy: RuntimePolicy | None = None,
    start_service: bool = True, generate=None, generator_fingerprint: str | None = None,
):
    """Hold a local conversation lock and own its memory connection and default generator."""
    if not _identifier(conversation_id) or not _identifier(branch_id):
        raise SessionError("invalid_scope")
    if (not isinstance(instructions, str) or (policy is not None and not isinstance(policy, RuntimePolicy))
            or (deliver is not None and not callable(deliver)) or type(start_service) is not bool
            or (generate is not None and (not callable(generate) or not _identifier(generator_fingerprint)))
            or (generate is None and generator_fingerprint is not None)):
        raise SessionError("invalid_configuration")
    try:
        instructions.encode("utf-8")
    except UnicodeError:
        raise SessionError("invalid_configuration") from None
    policy = policy or RuntimePolicy()
    runtime_fingerprint = digest({
        "version": "agent-session-v1", "memory_protocol": MEMORY_CONSUMPTION_VERSION,
        "instructions": instructions, "policy": asdict(policy),
    })
    try:
        profile = load_profile(directory)
        identity = _identity(profile, conversation_id, branch_id)
        root = profile.directory / "sessions"
        root.mkdir(mode=0o700, exist_ok=True)
        _private(root, directory=True)
    except (OSError, LocalProfileError):
        raise SessionError("profile_unavailable") from None
    session_directory = root / digest(identity)
    key = str(session_directory)
    with _ACTIVE_GUARD:
        if key in _ACTIVE_SCOPES:
            raise SessionError("session_busy")
        _ACTIVE_SCOPES.add(key)
    try:
        with ExitStack() as stack:
            try:
                stack.enter_context(profile_lock(session_directory, timeout=0))
            except LocalProfileBusy:
                raise SessionError("session_busy") from None
            except (OSError, LocalProfileError):
                raise SessionError("session_state_unavailable") from None
            pending_path = session_directory / "pending.json"
            _read_pending(pending_path, identity)
            if generate is None:
                from .chat_config import load_chat_configuration

                configuration = load_chat_configuration(profile.directory)
                generator_fingerprint = configuration.fingerprint
                generate = configuration.generator()
                stack.callback(generate.close)
            generator_fingerprint = digest(generator_fingerprint)
            client = stack.enter_context(desktop_connection.open_client(
                profile.directory, start_service=start_service,
            ))
            try:
                current = load_profile(profile.directory)
            except (OSError, LocalProfileError):
                raise SessionError("profile_unavailable") from None
            if _identity(current, conversation_id, branch_id) != identity or client.brain_id != profile.brain_id:
                raise SessionError("profile_identity_changed")
            session = AgentSession(
                profile=profile, identity=identity,
                runtime=BrainRuntime(client, generate, deliver=deliver, instructions=instructions, policy=policy),
                path=pending_path, generator_fingerprint=generator_fingerprint,
                runtime_fingerprint=runtime_fingerprint, _token=_SESSION_TOKEN,
            )
            try:
                yield session
            finally:
                with session._operation_lock:
                    session._closed = True
    finally:
        with _ACTIVE_GUARD:
            _ACTIVE_SCOPES.discard(key)
