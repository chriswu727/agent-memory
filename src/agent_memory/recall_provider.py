from __future__ import annotations

import json
import math
import time
from typing import Any, Literal

import httpx

from .recall_control import RecallReasoningRequest

_MAX_RESPONSE_BYTES = 65536


class RecallProviderError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(f"Recall provider request failed: {code}")


class ChatCompletionsRecallReasoner:
    def __init__(
        self, base_url: str, api_key: str, model: str, *,
        thinking: Literal["enabled", "disabled"] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        try:
            url = httpx.URL(base_url)
        except (TypeError, ValueError, httpx.InvalidURL):
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL") from None
        if (url.scheme not in {"http", "https"} or not url.host or url.userinfo
                or url.query or url.fragment):
            raise ValueError("base_url must have a host and no credentials, query, or fragment")
        if not isinstance(api_key, str) or not api_key.strip() or any(
            ord(character) < 33 or ord(character) > 126 for character in api_key
        ):
            raise ValueError("api_key must be a nonempty ASCII token")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be explicitly configured")
        if thinking is not None and (
            not isinstance(thinking, str) or thinking not in ("enabled", "disabled")
        ):
            raise ValueError("thinking must be None, 'enabled', or 'disabled'")
        self._model = model
        self._thinking = thinking
        self._http = httpx.Client(
            base_url=str(url).rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport, follow_redirects=False, trust_env=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ChatCompletionsRecallReasoner:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __call__(self, request: RecallReasoningRequest) -> str:
        timeout = request.timeout_seconds
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout_seconds must be finite and positive")
        if type(request.max_output_tokens) is not int or request.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        body = {
            "model": self._model, "messages": list(request.messages),
            "response_format": {"type": "json_object"},
            "max_tokens": request.max_output_tokens, "stream": False,
        }
        if self._thinking is not None:
            body["thinking"] = {"type": self._thinking}
        deadline = time.monotonic() + timeout
        try:
            with self._http.stream(
                "POST", "chat/completions", json=body, timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    raise RecallProviderError("http_error", status_code=response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise RecallProviderError("timeout")
                    if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise RecallProviderError("response_too_large")
                    data.extend(chunk)
                if time.monotonic() >= deadline:
                    raise RecallProviderError("timeout")
        except httpx.TimeoutException:
            raise RecallProviderError("timeout") from None
        except httpx.HTTPError:
            raise RecallProviderError("transport_error") from None
        try:
            value = json.loads(data)
            choices = value["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError
            choice = choices[0]
            if choice["finish_reason"] != "stop":
                raise RecallProviderError("incomplete_response")
            message = choice["message"]
            if message.get("tool_calls") or message.get("function_call"):
                raise RecallProviderError("unexpected_tool_call")
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError
            return content
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            raise RecallProviderError("invalid_response") from None
