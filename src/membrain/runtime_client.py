from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from .client import JsonObject, MemoryClient


class RuntimeClient:
    def __init__(self, memory: MemoryClient):
        self.memory = memory

    def _path(self, turn_id: str | None = None, operation: str | None = None) -> str:
        brain_id = self.memory._scope()["brain_ref"]
        path = f"v1/brains/{quote(brain_id, safe='')}/runtime/turns"
        if turn_id is not None:
            path += f"/{quote(turn_id, safe='')}"
        if operation is not None:
            path += f"/{operation}"
        return path

    def _post(self, turn_id: str, operation: str, **payload: Any) -> JsonObject:
        return self.memory._request(
            "POST", self._path(turn_id, operation),
            json={key: value for key, value in payload.items() if value is not None},
        )

    def begin(
        self, *, turn_id: str, conversation_id: str, branch_id: str, input_fingerprint: str,
    ) -> JsonObject:
        return self.memory._request("POST", self._path(), json={
            "turn_id": turn_id, "conversation_id": conversation_id,
            "branch_id": branch_id, "input_fingerprint": input_fingerprint,
        })

    def get(self, turn_id: str) -> JsonObject:
        return self.memory._request("GET", self._path(turn_id))

    def sources(self, turn_id: str) -> JsonObject:
        return self.memory._request("GET", self._path(turn_id, "sources"))

    def list(self, *, limit: int = 100) -> list[JsonObject]:
        result = self.memory._request("GET", self._path(), params={"limit": limit})
        return self._records(result, "turns")

    def history(self, turn_id: str, *, limit: int = 100) -> list[JsonObject]:
        result = self.memory._request(
            "GET", self._path(turn_id, "history"), params={"limit": limit},
        )
        return self._records(result, "history")

    @staticmethod
    def _records(result: JsonObject, key: str) -> list[JsonObject]:
        records = result.get(key)
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError(f"Runtime API returned an invalid {key} collection")
        return records

    def status(self) -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request("GET", f"v1/brains/{quote(brain_id, safe='')}/runtime/status")

    def cognitive_usage(self) -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request("GET", f"v1/brains/{quote(brain_id, safe='')}/runtime/cognitive-usage")

    def cognitive_budget(self) -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request("GET", f"v1/brains/{quote(brain_id, safe='')}/runtime/cognitive-budget")

    def configure_cognitive_budget(
        self, *, request_id: str, expected_revision: int, window_seconds: int,
        max_model_calls: int, max_input_bytes: int,
    ) -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request("PUT", f"v1/brains/{quote(brain_id, safe='')}/runtime/cognitive-budget", json={
            "request_id": request_id, "expected_revision": expected_revision,
            "policy": {"window_seconds": window_seconds, "max_model_calls": max_model_calls,
                       "max_input_bytes": max_input_bytes},
        })

    def learning_progress(self) -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request("GET", f"v1/brains/{quote(brain_id, safe='')}/runtime/learning-progress")

    def active_scope(self, conversation_id: str, branch_id: str = "main") -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request(
            "GET", f"v1/brains/{quote(brain_id, safe='')}/runtime/scope",
            params={"conversation_id": conversation_id, "branch_id": branch_id},
        )

    def job(self, job_id: str) -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request(
            "GET", f"v1/brains/{quote(brain_id, safe='')}/runtime/jobs/{quote(job_id, safe='')}",
        )

    def retry_learning(self, job_id: str, *, request_id: str, expected_fence: int) -> JsonObject:
        brain_id = self.memory._scope()["brain_ref"]
        return self.memory._request(
            "POST", f"v1/brains/{quote(brain_id, safe='')}/runtime/jobs/{quote(job_id, safe='')}/retry-learning",
            json={"request_id": request_id, "expected_fence": expected_fence},
        )

    def claim(
        self, turn_id: str, *, worker_id: str, lease_seconds: int = 30,
        expected_revision: int | None = None,
    ) -> JsonObject:
        return self._post(
            turn_id, "claim", worker_id=worker_id, lease_seconds=lease_seconds,
            expected_revision=expected_revision,
        )

    def renew(
        self, turn_id: str, *, lease_token: str, lease_seconds: int = 30,
        expected_revision: int | None = None,
    ) -> JsonObject:
        return self._post(
            turn_id, "renew", lease_token=lease_token, lease_seconds=lease_seconds,
            expected_revision=expected_revision,
        )

    def advance(
        self, turn_id: str, *, lease_token: str, expected_revision: int, stage: str,
        input_event_refs: Sequence[Mapping[str, Any]] | None = None,
        output_event_refs: Sequence[Mapping[str, Any]] | None = None,
        context_event_refs: Sequence[Mapping[str, Any]] | None = None,
        context_fingerprint: str | None = None, context_generation: int | None = None,
        working_revision: int | None = None, durable_sequence: int | None = None,
        context_tokens: int | None = None,
        recall_plan: Mapping[str, Any] | None = None,
        response_review_policy: str | None = None,
        orientation_route: Mapping[str, Any] | None = None,
        prepared_packet_id: str | None = None,
        memory_context_sha256: str | None = None,
        selection_omitted_reason: str | None = None,
    ) -> JsonObject:
        return self._post(
            turn_id, "advance", lease_token=lease_token, expected_revision=expected_revision,
            stage=stage,
            input_event_refs=[dict(ref) for ref in input_event_refs] if input_event_refs is not None else None,
            output_event_refs=[dict(ref) for ref in output_event_refs] if output_event_refs is not None else None,
            context_event_refs=[dict(ref) for ref in context_event_refs] if context_event_refs is not None else None,
            context_fingerprint=context_fingerprint, context_generation=context_generation,
            working_revision=working_revision, durable_sequence=durable_sequence,
            context_tokens=context_tokens, recall_plan=dict(recall_plan) if recall_plan is not None else None,
            response_review_policy=response_review_policy,
            orientation_route=dict(orientation_route) if orientation_route is not None else None,
            prepared_packet_id=prepared_packet_id, memory_context_sha256=memory_context_sha256,
            selection_omitted_reason=selection_omitted_reason,
        )

    def reserve_response_review(
        self, turn_id: str, *, lease_token: str, expected_revision: int,
        policy_fingerprint: str, request_fingerprint: str, draft_sha256: str,
    ) -> JsonObject:
        return self._post(
            turn_id, "response-review/reserve", lease_token=lease_token, expected_revision=expected_revision,
            policy_fingerprint=policy_fingerprint, request_fingerprint=request_fingerprint, draft_sha256=draft_sha256,
        )

    def complete_response_review(
        self, turn_id: str, *, lease_token: str, expected_revision: int,
        request_fingerprint: str, status: str, issue_codes: Sequence[str] = (),
    ) -> JsonObject:
        return self._post(
            turn_id, "response-review/complete", lease_token=lease_token, expected_revision=expected_revision,
            request_fingerprint=request_fingerprint, status=status, issue_codes=list(issue_codes),
        )

    def reserve_recall(
        self, turn_id: str, *, lease_token: str, expected_revision: int, input_bytes: int,
        max_model_calls: int, max_input_bytes: int, context_generation: int,
        source_refs: Sequence[Mapping[str, Any]], request_fingerprint: str,
    ) -> JsonObject:
        return self._post(
            turn_id, "recall-reservations", lease_token=lease_token,
            expected_revision=expected_revision, input_bytes=input_bytes,
            max_model_calls=max_model_calls, max_input_bytes=max_input_bytes,
            context_generation=context_generation, source_refs=[dict(ref) for ref in source_refs],
            request_fingerprint=request_fingerprint,
        )

    def orient(self, turn_id: str, *, lease_token: str, expected_revision: int) -> JsonObject:
        return self._post(
            turn_id, "orient", lease_token=lease_token, expected_revision=expected_revision,
        )

    def record_output(
        self, turn_id: str, *, lease_token: str, expected_revision: int,
        text: str, delivery_state: str,
    ) -> JsonObject:
        return self._post(
            turn_id, "output", lease_token=lease_token, expected_revision=expected_revision,
            text=text, delivery_state=delivery_state,
        )

    def release(self, turn_id: str, *, lease_token: str, expected_revision: int) -> JsonObject:
        return self._post(
            turn_id, "release", lease_token=lease_token, expected_revision=expected_revision,
        )

    def fail(
        self, turn_id: str, *, lease_token: str, expected_revision: int, failure_code: str,
    ) -> JsonObject:
        return self._post(
            turn_id, "fail", lease_token=lease_token, expected_revision=expected_revision,
            failure_code=failure_code,
        )

    def finish(self, turn_id: str, *, lease_token: str, expected_revision: int) -> JsonObject:
        return self._post(
            turn_id, "finish", lease_token=lease_token, expected_revision=expected_revision,
        )
