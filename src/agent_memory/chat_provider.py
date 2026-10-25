from __future__ import annotations

import copy
import json
import math
import re
import time
import zlib
from typing import Any, Literal

import httpx

from .runtime import ModelRequest

_ERROR_CODES = frozenset({
    "http_error", "timeout", "transport_error", "response_too_large", "unsupported_encoding",
    "invalid_response", "incomplete_response", "unexpected_tool_call", "refused_response",
    "invalid_request", "provider_closed",
})
_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
_CACHE_FIELDS = ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
_USAGE_DETAILS = {
    "prompt_tokens_details": ("cached_tokens", "audio_tokens"),
    "completion_tokens_details": (
        "reasoning_tokens", "audio_tokens", "accepted_prediction_tokens",
        "rejected_prediction_tokens",
    ),
}
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")


class ChatProviderError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None):
        if not isinstance(code, str) or code not in _ERROR_CODES:
            raise ValueError("Unknown chat provider error code")
        if status_code is not None and (
            type(status_code) is not int or not 100 <= status_code <= 599
        ):
            raise ValueError("status_code must be a valid HTTP status or None")
        self.code, self.status_code = code, status_code
        super().__init__(f"Chat provider request failed: {code}")


def _validate_json(value: Any) -> None:
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError
            key.encode("utf-8")
            _validate_json(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item)
    elif type(value) is float and not math.isfinite(value):
        raise ValueError
    elif value is not None and type(value) not in (bool, int, float):
        raise ValueError


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError


def _usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    if any(type(value.get(key)) is not int or value[key] < 0 for key in _USAGE_FIELDS):
        return None
    if value["total_tokens"] != value["prompt_tokens"] + value["completion_tokens"]:
        return None
    result = {key: value[key] for key in _USAGE_FIELDS}
    for key in _CACHE_FIELDS:
        if key in value:
            if type(value[key]) is not int or not 0 <= value[key] <= value["prompt_tokens"]:
                return None
            result[key] = value[key]
    if all(key in result for key in _CACHE_FIELDS) and (
        sum(result[key] for key in _CACHE_FIELDS) != value["prompt_tokens"]
    ):
        return None
    for key, fields in _USAGE_DETAILS.items():
        detail = value.get(key)
        if detail is None:
            continue
        if not isinstance(detail, dict):
            return None
        selected = {}
        for field in fields:
            if field in detail:
                if type(detail[field]) is not int or detail[field] < 0:
                    return None
                maximum = value[key.removesuffix("_details")]
                if field != "rejected_prediction_tokens" and detail[field] > maximum:
                    return None
                selected[field] = detail[field]
        if selected:
            result[key] = selected
    return result


def _request_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


class ChatCompletionsGenerator:
    def __init__(
        self, base_url: str, api_key: str, model: str, *,
        thinking: Literal["enabled", "disabled"] | None = None,
        max_output_tokens: int = 4096, timeout_seconds: float = 180,
        max_response_bytes: int = 1048576, transport: httpx.BaseTransport | None = None,
    ):
        try:
            if (not isinstance(base_url, str) or not base_url
                    or any(ord(character) <= 32 or ord(character) == 127 for character in base_url)):
                raise ValueError
            base_url.encode("utf-8")
            url = httpx.URL(base_url)
            if (url.scheme not in {"http", "https"} or not url.host or url.userinfo
                    or "?" in base_url or "#" in base_url
                    or (url.port is not None and not 1 <= url.port <= 65535)):
                raise ValueError
        except (TypeError, ValueError, httpx.InvalidURL):
            raise ValueError("base_url must be an absolute HTTP URL without credentials, query, or fragment") from None
        if not isinstance(api_key, str) or not api_key or any(
            ord(character) < 33 or ord(character) > 126 for character in api_key
        ):
            raise ValueError("api_key must be a nonempty ASCII token")
        try:
            if (not isinstance(model, str) or not model or model != model.strip()
                    or any(ord(character) < 32 or ord(character) == 127 for character in model)):
                raise ValueError
            model.encode("utf-8")
        except (ValueError, TypeError):
            raise ValueError("model must be an explicitly configured valid text identifier") from None
        if thinking is not None and (
            not isinstance(thinking, str) or thinking not in ("enabled", "disabled")
        ):
            raise ValueError("thinking must be None, 'enabled', or 'disabled'")
        for value, name in (
            (max_output_tokens, "max_output_tokens"), (max_response_bytes, "max_response_bytes"),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        try:
            if (type(timeout_seconds) not in (int, float)
                    or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
                raise ValueError
        except (ValueError, OverflowError):
            raise ValueError("timeout_seconds must be finite and positive") from None
        if transport is not None and not isinstance(transport, httpx.BaseTransport):
            raise ValueError("transport must be a synchronous HTTP transport or None")
        self._model, self._thinking = model, thinking
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._last_usage: dict | None = None
        self._last_request_id: str | None = None
        self._http = httpx.Client(
            base_url=str(url).rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}", "Accept-Encoding": "identity"},
            transport=transport if transport is not None else httpx.HTTPTransport(
                retries=0, trust_env=False,
            ),
            follow_redirects=False, trust_env=False,
        )

    @property
    def last_usage(self) -> dict | None:
        return copy.deepcopy(self._last_usage)

    @property
    def last_request_id(self) -> str | None:
        return self._last_request_id

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ChatCompletionsGenerator:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _check_deadline(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise ChatProviderError("timeout")

    def _read_response(self, response: httpx.Response, deadline: float) -> bytes:
        encoding = ("identity" if response.is_stream_consumed else
                    response.headers.get("content-encoding", "identity").strip().lower())
        if encoding not in ("", "identity", "gzip"):
            raise ChatProviderError("unsupported_encoding")
        decoder = zlib.decompressobj(zlib.MAX_WBITS | 16) if encoding == "gzip" else None
        chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
        data, raw_bytes = bytearray(), 0
        for chunk in chunks:
            self._check_deadline(deadline)
            raw_bytes += len(chunk)
            if raw_bytes > self.max_response_bytes:
                raise ChatProviderError("response_too_large")
            try:
                decoded = (decoder.decompress(chunk, self.max_response_bytes - len(data) + 1)
                           if decoder is not None else chunk)
            except zlib.error:
                raise ChatProviderError("invalid_response") from None
            if len(data) + len(decoded) > self.max_response_bytes:
                raise ChatProviderError("response_too_large")
            data.extend(decoded)
            if decoder is not None and decoder.unused_data:
                raise ChatProviderError("invalid_response")
        self._check_deadline(deadline)
        if decoder is not None and not decoder.eof:
            raise ChatProviderError("invalid_response")
        return bytes(data)

    def __call__(self, request: ModelRequest) -> str:
        self._last_usage, self._last_request_id = None, None
        if self._http.is_closed:
            raise ChatProviderError("provider_closed")
        deadline = time.monotonic() + self.timeout_seconds
        try:
            if (not isinstance(request, ModelRequest) or not isinstance(request.messages, tuple)
                    or not request.messages or any(not isinstance(item, dict) for item in request.messages)):
                raise ValueError
            body = {
                "model": self._model, "messages": list(request.messages),
                "max_tokens": self.max_output_tokens, "stream": False,
            }
            if self._thinking is not None:
                body["thinking"] = {"type": self._thinking}
            _validate_json(body)
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise ChatProviderError("invalid_request") from None
        self._check_deadline(deadline)
        try:
            with self._http.stream(
                "POST", "chat/completions", content=payload,
                headers={"Content-Type": "application/json"}, timeout=self.timeout_seconds,
            ) as response:
                self._last_request_id = _request_id(response.headers.get("x-request-id"))
                self._check_deadline(deadline)
                if response.status_code != 200:
                    raise ChatProviderError("http_error", response.status_code)
                raw = self._read_response(response, deadline)
        except httpx.TimeoutException:
            raise ChatProviderError("timeout") from None
        except httpx.HTTPError:
            raise ChatProviderError("transport_error") from None
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            _validate_json(value)
            if not isinstance(value, dict) or "error" in value:
                raise ValueError
            self._last_usage = _usage(value.get("usage"))
            choices = value["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if not isinstance(choice, dict) or (
                "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0)
            ):
                raise ValueError
            if choice["finish_reason"] != "stop":
                raise ChatProviderError("incomplete_response")
            message = choice["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError
            if (message.get("tool_calls") not in (None, [])
                    or message.get("function_call") is not None):
                raise ChatProviderError("unexpected_tool_call")
            if message.get("refusal") is not None:
                raise ChatProviderError("refused_response")
            content = message["content"]
            if (not isinstance(content, str) or not content.strip()
                    or message.get("audio") is not None):
                raise ValueError
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            raise ChatProviderError("invalid_response") from None
        self._check_deadline(deadline)
        return content
