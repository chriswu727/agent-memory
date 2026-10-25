from __future__ import annotations

import math
from typing import Literal

import httpx

from .chat_provider import ChatCompletionsGenerator, ChatProviderError
from .recall_control import RecallReasoningRequest

_MAX_RESPONSE_BYTES = 65536


class RecallProviderError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(f"Recall provider request failed: {code}")


class ChatCompletionsRecallReasoner(ChatCompletionsGenerator):
    def __init__(
        self, base_url: str, api_key: str, model: str, *,
        thinking: Literal["enabled", "disabled"] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        super().__init__(base_url, api_key, model, thinking=thinking,
                         max_response_bytes=_MAX_RESPONSE_BYTES, transport=transport)

    def __call__(self, request: RecallReasoningRequest) -> str:
        self._last_usage, self._last_request_id = None, None
        timeout = request.timeout_seconds
        try:
            if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
                raise ValueError
        except (ValueError, OverflowError):
            raise ValueError("timeout_seconds must be finite and positive") from None
        if type(request.max_output_tokens) is not int or request.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        try:
            return self._complete(request.messages, max_output_tokens=request.max_output_tokens,
                                  timeout_seconds=timeout, json_mode=True)
        except ChatProviderError as error:
            raise RecallProviderError(error.code, status_code=error.status_code) from None
