import copy
import fcntl
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path


def _json_bytes(value):
    def validate(item):
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Journal JSON object keys must be strings")
            for child in item.values():
                validate(child)
        elif type(item) is list:
            for child in item:
                validate(child)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("Journal values must be JSON data")

    validate(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _json_load(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate journal JSON key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("Non-finite journal JSON number")

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid journal JSON") from error


def _digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _write_all(descriptor, raw):
    remaining = memoryview(raw)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Journal write made no progress")
        remaining = remaining[written:]
    os.fsync(descriptor)


class CampaignJournal:
    def __init__(self, path, plan):
        if type(plan) is not dict:
            raise ValueError("Campaign plan must be a JSON object")
        self.path = Path(os.path.abspath(path))
        self._plan_bytes = _json_bytes(plan)
        self.plan_sha256 = hashlib.sha256(self._plan_bytes).hexdigest()
        self._events = []
        self._directory = None
        self._lock = None
        self._failed = False
        self._owner_pid = None
        self._file_states = {}

    @property
    def plan(self):
        return _json_load(self._plan_bytes)

    @property
    def events(self):
        return copy.deepcopy(self._events)

    def _open_directory(self):
        descriptor = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.path.parts[1:]:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            os.fchmod(descriptor, 0o700)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _open_file(self, name, flags):
        descriptor = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                             dir_fd=self._directory)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("Campaign files must be regular files without hard links")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _read(self, name):
        descriptor = self._open_file(name, os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()

    def _create(self, name, raw):
        descriptor = self._open_file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            _write_all(descriptor, raw)
        finally:
            os.close(descriptor)

    def _head(self, events):
        return {"version": 1, "plan_sha256": self.plan_sha256, "event_count": len(events),
                "last_sha256": events[-1]["sha256"] if events else self.plan_sha256}

    def _file_state(self, name):
        metadata = os.stat(name, dir_fd=self._directory, follow_symlinks=False)
        return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
                metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)

    def _remember_file_states(self):
        self._file_states = {name: self._file_state(name)
                             for name in ("plan.json", "journal.jsonl", "head.json")}

    def _assert_unchanged(self):
        for name, expected in self._file_states.items():
            if self._file_state(name) != expected:
                raise ValueError("Campaign journal files changed while locked")
        if _json_bytes(_json_load(self._read("head.json"))) != _json_bytes(self._head(self._events)):
            raise ValueError("Campaign journal does not match its committed head")

    def _load(self):
        if _json_bytes(_json_load(self._read("plan.json"))) != self._plan_bytes:
            raise ValueError("Campaign plan does not match the frozen plan")
        raw = self._read("journal.jsonl")
        if raw and not raw.endswith(b"\n"):
            raise ValueError("Campaign journal has a truncated record")
        events = []
        previous = self.plan_sha256
        for line in raw.splitlines():
            event = _json_load(line)
            if (type(event) is not dict or set(event) != {
                    "sequence", "kind", "payload", "plan_sha256", "previous_sha256", "sha256"}
                    or type(event["sequence"]) is not int or event["sequence"] != len(events) + 1
                    or type(event["kind"]) is not str or not event["kind"]
                    or type(event["payload"]) is not dict
                    or event["plan_sha256"] != self.plan_sha256
                    or event["previous_sha256"] != previous):
                raise ValueError("Invalid campaign journal chain")
            body = {key: value for key, value in event.items() if key != "sha256"}
            if event["sha256"] != _digest(body):
                raise ValueError("Campaign journal hash mismatch")
            if events and events[-1]["kind"] in {"stopped", "completed"}:
                raise ValueError("Campaign journal continues after a terminal event")
            events.append(event)
            previous = event["sha256"]
        if _json_bytes(_json_load(self._read("head.json"))) != _json_bytes(self._head(events)):
            raise ValueError("Campaign journal does not match its committed head")
        return events

    def __enter__(self):
        if self._directory is not None:
            raise RuntimeError("Campaign journal context is already open")
        try:
            self._directory = self._open_directory()
            self._lock = self._open_file(".lock", os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Campaign journal is locked by another process") from error
            names = {"plan.json", "journal.jsonl", "head.json"}
            present = names.intersection(os.listdir(self._directory))
            if not present:
                self._create("plan.json", self._plan_bytes + b"\n")
                self._create("journal.jsonl", b"")
                self._create("head.json", _json_bytes(self._head([])) + b"\n")
                os.fsync(self._directory)
            elif present != names:
                raise ValueError("Campaign journal initialization is incomplete")
            self._events = self._load()
            self._remember_file_states()
            self._failed = False
            self._owner_pid = os.getpid()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        if self._directory is not None:
            os.close(self._directory)
            self._directory = None
        self._owner_pid = None

    def _assert_open(self):
        if self._directory is None or self._failed:
            raise RuntimeError("Campaign journal requires a healthy open context")
        if self._owner_pid != os.getpid():
            raise RuntimeError("Campaign journal context belongs to another process")
        locked = os.fstat(self._lock)
        current = os.stat(".lock", dir_fd=self._directory, follow_symlinks=False)
        if (locked.st_dev, locked.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError("Campaign journal lock file was replaced")

    def assert_resumable(self):
        self._assert_open()
        self._assert_unchanged()
        if self._load() != self._events:
            raise ValueError("Campaign journal changed while locked")
        if self._events and self._events[-1]["kind"] not in {"session_completed", "paused"}:
            raise RuntimeError("Campaign journal has no safe resumable session boundary")

    def append(self, kind, payload):
        self._assert_open()
        if type(kind) is not str or not kind or type(payload) is not dict:
            raise ValueError("Journal events require a nonempty kind and JSON object payload")
        payload = _json_load(_json_bytes(payload))
        if self._events and self._events[-1]["kind"] in {"stopped", "completed"}:
            raise RuntimeError("Campaign journal is terminal")
        self._assert_unchanged()
        body = {"sequence": len(self._events) + 1, "kind": kind, "payload": payload,
                "plan_sha256": self.plan_sha256,
                "previous_sha256": self._events[-1]["sha256"] if self._events else self.plan_sha256}
        event = {**body, "sha256": _digest(body)}
        temporary = f".head-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = self._open_file("journal.jsonl", os.O_WRONLY | os.O_APPEND)
            try:
                _write_all(descriptor, _json_bytes(event) + b"\n")
            finally:
                os.close(descriptor)
            events = [*self._events, event]
            self._create(temporary, _json_bytes(self._head(events)) + b"\n")
            os.replace(temporary, "head.json", src_dir_fd=self._directory,
                       dst_dir_fd=self._directory)
            os.fsync(self._directory)
            self._events = events
            self._remember_file_states()
            return copy.deepcopy(event)
        except BaseException:
            self._failed = True
            raise
        finally:
            try:
                os.unlink(temporary, dir_fd=self._directory)
            except FileNotFoundError:
                pass
