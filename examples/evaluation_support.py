from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

import httpx

_MAX_RESPONSE_BYTES = 65536
_USAGE_FIELDS = {
    "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
}
_USAGE_DETAILS = {
    "prompt_tokens_details": {"cached_tokens", "audio_tokens"},
    "completion_tokens_details": {
        "reasoning_tokens", "audio_tokens", "accepted_prediction_tokens",
        "rejected_prediction_tokens",
    },
}
_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call"}


class MeteringError(httpx.TransportError):
    pass


class MeteredTransport(httpx.BaseTransport):
    def __init__(
        self, ledger_path: str | Path, *, max_calls: int = 16,
        max_output_tokens: int = 262144, transport: httpx.BaseTransport | None = None,
        capture_content: bool = True,
    ):
        if (type(max_calls) is not int or max_calls < 1
                or type(max_output_tokens) is not int or max_output_tokens < 1):
            raise ValueError("Metering limits must be positive integers")
        self.ledger_path = Path(ledger_path)
        self.max_calls = max_calls
        self.max_output_tokens = max_output_tokens
        self.capture_content = capture_content
        self._transport = transport if transport is not None else httpx.HTTPTransport(retries=0)
        self._lock = threading.RLock()
        self._streams: set[_MeteredStream] = set()
        self._closed = False
        self.ledger_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._ledger()

    def _ledger(self, record: dict | None = None, *, reserve: bool = False) -> list[dict]:
        try:
            descriptor = os.open(self.ledger_path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                os.fchmod(handle.fileno(), 0o600)
                lines = handle.read()
                if lines and not lines.endswith("\n"):
                    raise MeteringError("metering_ledger_incomplete")
                records = [json.loads(line) for line in lines.splitlines()]
                reservations: dict[str, int] = {}
                completed: set[str] = set()
                for item in records:
                    if not isinstance(item, dict) or not isinstance(item.get("request_id"), str):
                        raise MeteringError("metering_ledger_invalid")
                    request_id = item["request_id"]
                    if item.get("event") == "reserved":
                        amount = item.get("reserved_output_tokens")
                        if type(amount) is not int or amount < 1 or request_id in reservations:
                            raise MeteringError("metering_ledger_invalid")
                        reservations[request_id] = amount
                    elif item.get("event") == "completed":
                        if request_id not in reservations or request_id in completed:
                            raise MeteringError("metering_ledger_invalid")
                        completed.add(request_id)
                    else:
                        raise MeteringError("metering_ledger_invalid")
                if reserve and record is not None:
                    if len(reservations) >= self.max_calls:
                        raise MeteringError("metering_call_limit")
                    if (sum(reservations.values()) + record["reserved_output_tokens"]
                            > self.max_output_tokens):
                        raise MeteringError("metering_output_limit")
                if record is not None:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    records.append(record)
                return records
        except MeteringError:
            raise
        except (OSError, ValueError, TypeError, RecursionError):
            raise MeteringError("metering_ledger_unavailable") from None

    @property
    def records(self) -> list[dict]:
        return self._ledger()

    def summary(self) -> dict:
        records = self.records
        reserved = {item["request_id"]: item for item in records if item["event"] == "reserved"}
        completed = {item["request_id"]: item for item in records if item["event"] == "completed"}
        confirmed = [item for item in completed.values() if item.get("usage_confirmed")]
        usage_totals: dict[str, int] = {}
        for item in completed.values():
            for key, value in item.get("usage", {}).items():
                if type(value) is int:
                    usage_totals[key] = usage_totals.get(key, 0) + value
        return {
            "reserved_calls": len(reserved), "completed_calls": len(completed),
            "reserved_output_tokens": sum(item["reserved_output_tokens"]
                                          for item in reserved.values()),
            "usage_confirmed_calls": len(confirmed),
            "usage_unknown_calls": len(reserved) - len(confirmed),
            "usage_totals": usage_totals,
        }

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            body = request.content
            value = json.loads(body)
            limits = [value[key] for key in ("max_tokens", "max_completion_tokens") if key in value]
            if not limits or any(type(limit) is not int or limit < 1 for limit in limits):
                raise ValueError
        except (ValueError, TypeError, KeyError, httpx.RequestNotRead, RecursionError):
            raise MeteringError("metering_explicit_output_limit_required") from None
        request_id = uuid.uuid4().hex
        with self._lock:
            if self._closed:
                raise MeteringError("metering_transport_closed")
            self._ledger({
                "event": "reserved", "request_id": request_id,
                "reserved_output_tokens": max(limits),
                "request_sha256": hashlib.sha256(body).hexdigest(), "request_bytes": len(body),
            }, reserve=True)
        started = time.monotonic()
        try:
            response = self._transport.handle_request(request)
        except BaseException:
            self._ledger({
                "event": "completed", "request_id": request_id, "outcome": "transport_error",
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "usage_confirmed": False,
            })
            raise
        stream = _MeteredStream(
            self, response.stream, request_id, response.status_code, started,
            content_encoding=("identity" if response.is_stream_consumed
                              else response.headers.get("content-encoding", "identity")),
        )
        with self._lock:
            self._streams.add(stream)
        if response.is_stream_consumed:
            stream.observe(response.content)
            stream.finish("body_complete")
        else:
            response.stream = stream
        return response

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            streams = list(self._streams)
        try:
            for stream in streams:
                stream.close()
        finally:
            self._transport.close()


class _MeteredStream(httpx.SyncByteStream):
    def __init__(
        self, owner: MeteredTransport, stream: httpx.SyncByteStream,
        request_id: str, status_code: int, started: float, *, content_encoding: str,
    ):
        self.owner, self.stream = owner, stream
        self.request_id, self.status_code, self.started = request_id, status_code, started
        self.buffer = bytearray()
        self.response_bytes = 0
        self.raw_response_bytes = 0
        self.exceeded = False
        self.finished = False
        self.closed = False
        encoding = content_encoding.lower().strip()
        self.decoder = zlib.decompressobj(zlib.MAX_WBITS | 16) if encoding == "gzip" else None
        self.decoding_failed = encoding not in {"", "identity", "gzip"}

    def observe(self, chunk: bytes) -> None:
        self.raw_response_bytes += len(chunk)
        if not 200 <= self.status_code < 300 or self.exceeded or self.decoding_failed:
            return
        try:
            decoded = (self.decoder.decompress(chunk, _MAX_RESPONSE_BYTES - len(self.buffer) + 1)
                       if self.decoder is not None else chunk)
        except zlib.error:
            self.buffer.clear()
            self.decoding_failed = True
            return
        self.response_bytes += len(decoded)
        if len(self.buffer) + len(decoded) > _MAX_RESPONSE_BYTES:
            self.buffer.clear()
            self.exceeded = True
        else:
            self.buffer.extend(decoded)

    def __iter__(self):
        try:
            for chunk in self.stream:
                self.observe(chunk)
                yield chunk
        except BaseException:
            self.finish("stream_error")
            raise
        self.finish("body_complete")

    def finish(self, outcome: str) -> None:
        with self.owner._lock:
            if self.finished:
                return
            record: dict[str, Any] = {
                "event": "completed", "request_id": self.request_id,
                "status_code": self.status_code,
                "outcome": "response_too_large" if self.exceeded else outcome,
                "elapsed_seconds": round(time.monotonic() - self.started, 6),
                "response_bytes_observed": self.response_bytes, "usage_confirmed": False,
                "raw_response_bytes_observed": self.raw_response_bytes,
            }
            if (outcome == "body_complete" and not self.exceeded
                    and not self.decoding_failed and 200 <= self.status_code < 300
                    and (self.decoder is None or self.decoder.eof)):
                record.update(_completion_metadata(self.buffer, self.owner.capture_content))
            self.owner._ledger(record)
            self.finished = True
            self.buffer.clear()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.stream.close()
        except BaseException:
            self.finish("stream_close_error")
            raise
        finally:
            try:
                self.finish("closed_before_complete")
            finally:
                with self.owner._lock:
                    self.owner._streams.discard(self)


def _completion_metadata(body: bytearray, capture_content: bool) -> dict:
    try:
        value = json.loads(body)
    except (ValueError, TypeError, RecursionError):
        return {}
    if not isinstance(value, dict) or "error" in value:
        return {}
    result: dict[str, Any] = {}
    model = value.get("model")
    if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", model):
        result["model"] = model
    usage = value.get("usage")
    if isinstance(usage, dict):
        safe_usage = {key: number for key, number in usage.items()
                      if key in _USAGE_FIELDS and type(number) is int and number >= 0}
        for key, fields in _USAGE_DETAILS.items():
            if isinstance(usage.get(key), dict):
                detail = {field: number for field, number in usage[key].items()
                          if field in fields and type(number) is int and number >= 0}
                if detail:
                    safe_usage[key] = detail
        result["usage"] = safe_usage
        result["usage_confirmed"] = "completion_tokens" in safe_usage
    choices = value.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        reason = choice.get("finish_reason")
        if isinstance(reason, str):
            result["finish_reason"] = reason if reason in _FINISH_REASONS else "unknown"
        message = choice.get("message")
        if capture_content and isinstance(message, dict) and isinstance(message.get("content"), str):
            result["assistant_content"] = message["content"]
    return result
