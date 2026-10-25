from __future__ import annotations

from .recall_provider import ChatCompletionsRecallReasoner, RecallProviderError
from .semantic import CognitiveRequest


class SemanticProviderError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(f"Semantic provider request failed: {code}")


class ChatCompletionsSemanticProvider(ChatCompletionsRecallReasoner):
    def __call__(self, request: CognitiveRequest) -> str:
        self._last_usage, self._last_request_id = None, None
        if request.phase not in {"proposal", "verification"}:
            raise ValueError("Unknown cognitive request phase")
        try:
            return super().__call__(request)
        except RecallProviderError as error:
            raise SemanticProviderError(error.code, status_code=error.status_code) from None
