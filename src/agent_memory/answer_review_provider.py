from __future__ import annotations

from .answer_review import AnswerReviewError, AnswerReviewRequest
from .recall_provider import ChatCompletionsRecallReasoner, RecallProviderError


class ChatCompletionsAnswerReviewProvider(ChatCompletionsRecallReasoner):
    def __call__(self, request: AnswerReviewRequest) -> str:
        self._last_usage, self._last_request_id = None, None
        if request.phase != "answer_review":
            raise ValueError("Unknown answer review request phase")
        try:
            return super().__call__(request)
        except RecallProviderError as error:
            raise AnswerReviewError(error.code) from None
