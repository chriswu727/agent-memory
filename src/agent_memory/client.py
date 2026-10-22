from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx

CONTRACT_VERSION = "1.0.0-draft"
ACTIONS = (
    "observe", "prepare", "recall", "inspect", "commit", "checkpoint",
    "correct", "forget", "export", "restore",
)
Action = Literal[
    "observe", "prepare", "recall", "inspect", "commit", "checkpoint",
    "correct", "forget", "export", "restore",
]
JsonObject = dict[str, Any]


def default_budget(max_context_tokens: int = 2000) -> JsonObject:
    return {
        "max_context_tokens": max_context_tokens,
        "max_model_calls": 0,
        "max_embedding_calls": 0,
        "max_expansion_steps": 2,
        "max_wall_time_ms": 5000,
        "max_cost_microunits": 0,
        "currency": "USD",
        "token_counting": "conservative_estimate",
        "tokenizer_id": None,
    }


def default_read_fence(minimum_durable_sequence: int = 0) -> JsonObject:
    return {
        "minimum_durable_sequence": minimum_durable_sequence,
        "consistency": "read_your_writes",
        "snapshot_revision": None,
    }


def integration_capabilities(transport: str = "sdk") -> JsonObject:
    return {
        "adapter_id": f"agent-memory-{transport}",
        "adapter_version": "0.1.0",
        "protocol_version": CONTRACT_VERSION,
        "transport": transport,
        "supported_actions": list(ACTIONS),
        "capture": dict.fromkeys([
            "user_messages", "delivered_assistant_messages", "tool_results",
            "message_edits", "message_deletions", "stable_source_ids", "branch_identity",
        ], False),
        "hooks": dict.fromkeys([
            "before_turn", "after_delivery", "before_compaction", "on_resume",
        ], False),
        "context_control": "tool_output_only" if transport == "mcp_stdio" else "none",
        "active_recall": True,
        "automatic_capture": False,
        "automatic_recall": False,
        "guarantee_level": "tools_only",
        "verification_state": "declared",
        "verified_at": None,
        "limitations": [
            "The host must submit actual events and invoke lifecycle operations.",
            "This adapter does not intercept conversation history or compaction.",
            "The adapter cannot withdraw context already supplied to a model.",
        ],
    }


class MemoryActionError(RuntimeError):
    def __init__(self, response: JsonObject):
        self.response = response
        error = response.get("error", {})
        self.code = error.get("code", "unknown")
        self.retryable = error.get("retryable", False)
        super().__init__(f"{self.code}: {error.get('message', 'Memory action failed')}")


def prepared_context(packet_or_response: Mapping[str, Any]) -> str:
    from .cognition import render_context

    packet = dict(packet_or_response)
    if packet.get("message_type") == "response":
        if packet.get("action") != "prepare":
            raise ValueError("Expected a prepare response")
        if packet.get("outcome") in {"rejected", "conflict", "failed"}:
            raise MemoryActionError(packet)
        packet = packet["result"]
    return render_context(packet)


class MemoryClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        brain_id: str | None = None,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("api_key must not be empty")
        self.brain_id = brain_id
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> MemoryClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> JsonObject:
        response = self._http.request(method, path, **kwargs)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("Memory API returned a non-object response")
        return value

    def _scope(
        self, conversation_id: str | None = None, branch_id: str | None = None,
        brain_id: str | None = None,
    ) -> JsonObject:
        brain = brain_id or self.brain_id
        if not brain:
            raise ValueError("A brain_id is required for memory actions")
        target: JsonObject = {"brain_ref": brain}
        if conversation_id is not None:
            target["conversation_ref"] = conversation_id
        if branch_id is not None:
            target["branch_ref"] = branch_id
        return target

    def _key(self, action: str, identity: Any, scope: Mapping[str, Any]) -> str:
        value = json.dumps([action, dict(scope), identity], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return f"{action}:{hashlib.sha256(value.encode()).hexdigest()}"

    def send(self, request: Mapping[str, Any], *, raise_on_error: bool = True) -> JsonObject:
        """Send an unchanged contract envelope; reuse it when retrying uncertain requests."""
        response = self._request("POST", "v1/actions", json=dict(request))
        if response.get("request_id") != request.get("request_id"):
            raise ValueError("Memory API response request_id did not match")
        if response.get("action") != request.get("action"):
            raise ValueError("Memory API response action did not match")
        if raise_on_error and response.get("outcome") in {"rejected", "conflict", "failed"}:
            raise MemoryActionError(response)
        return response

    def action(
        self, action: Action, payload: Mapping[str, Any], *,
        conversation_id: str | None = None, branch_id: str | None = None,
        brain_id: str | None = None, target: Mapping[str, Any] | None = None,
        request_id: str | None = None, raise_on_error: bool = True,
    ) -> JsonObject:
        if action not in ACTIONS:
            raise ValueError(f"Unsupported memory action: {action}")
        scope = dict(target) if target is not None else self._scope(conversation_id, branch_id, brain_id)
        return self.send({
            "message_type": "request", "contract_version": CONTRACT_VERSION,
            "request_id": request_id or str(uuid4()), "action": action,
            "target": scope, "payload": dict(payload),
        }, raise_on_error=raise_on_error)

    def brains(self) -> JsonObject:
        return self._request("GET", "v1/brains")

    def create_brain(self, name: str) -> JsonObject:
        return self._request("POST", "v1/brains", json={"name": name})

    def capabilities(self) -> JsonObject:
        return self._request("GET", "v1/capabilities")

    def download_archive(self, archive_id: str) -> JsonObject:
        return self._request("GET", f"v1/archives/{quote(archive_id, safe='')}")

    def upload_archive(self, archive: Mapping[str, Any], brain_id: str | None = None) -> JsonObject:
        brain = self._scope(brain_id=brain_id)["brain_ref"]
        return self._request("POST", f"v1/brains/{quote(brain, safe='')}/archives", json=dict(archive))

    def dashboard(self, brain_id: str | None = None) -> JsonObject:
        brain = self._scope(brain_id=brain_id)["brain_ref"]
        return self._request("GET", f"v1/brains/{quote(brain, safe='')}/dashboard")

    def work(self, brain_id: str | None = None) -> JsonObject:
        brain = self._scope(brain_id=brain_id)["brain_ref"]
        return self._request("POST", f"v1/brains/{quote(brain, safe='')}/work")

    def observe(
        self, text: str, *, conversation_id: str, branch_id: str,
        source_event_key: str, role: str = "user", producer_ref: str = "sdk",
        actor_ref: str | None = None, source_revision: int = 1,
        event_kind: str = "message", interpretation_hint: str = "unknown",
        delivery_state: str = "received", occurred_time: Mapping[str, Any] | None = None,
        speech_act_hint: str = "unknown", parent_source_event_keys: Sequence[str] = (),
        idempotency_key: str | None = None, request_id: str | None = None,
    ) -> JsonObject:
        scope = self._scope(conversation_id, branch_id)
        event = {
            "source_event_key": source_event_key, "source_revision": source_revision,
            "producer_ref": producer_ref, "actor_ref": actor_ref or role,
            "actor_role": role, "event_kind": event_kind,
            "content": {"media_type": "text/plain", "text": text},
            "occurred_time": dict(occurred_time) if occurred_time is not None else {
                "start": None, "end": None, "precision": "unknown", "basis": "unknown",
            },
            "interpretation_hint": interpretation_hint, "delivery_state": delivery_state,
            "speech_act_hint": speech_act_hint,
            "parent_source_event_keys": list(parent_source_event_keys),
        }
        return self.action("observe", {
            "idempotency_key": idempotency_key or self._key(
                "observe", [producer_ref, source_event_key, source_revision], scope,
            ),
            "events": [event],
        }, target=scope, request_id=request_id)

    def recall(
        self, query: str, *, conversation_id: str | None = None,
        branch_id: str | None = None, intent: str = "explore",
        cue_event_ids: Sequence[str] = (), additional_cues: Sequence[str] = (),
        read_fence: Mapping[str, Any] | None = None, budget: Mapping[str, Any] | None = None,
        request_id: str | None = None, **retrieval: Any,
    ) -> JsonObject:
        return self.action("recall", {
            "query": query, "intent": intent, "cue_event_ids": list(cue_event_ids),
            "additional_cues": list(additional_cues),
            "read_fence": dict(read_fence) if read_fence is not None else default_read_fence(),
            "budget": dict(budget) if budget is not None else default_budget(16000), **retrieval,
        }, conversation_id=conversation_id, branch_id=branch_id, request_id=request_id)

    def inspect(
        self, memory_ids: Sequence[str] = (), *, evidence_refs: Sequence[Mapping[str, Any]] = (),
        include_successors: bool = True, include_corrections: bool = True,
        budget: Mapping[str, Any] | None = None, **scope: Any,
    ) -> JsonObject:
        return self.action("inspect", {
            "memory_ids": list(memory_ids), "evidence_refs": list(evidence_refs),
            "include_successors": include_successors, "include_corrections": include_corrections,
            "budget": dict(budget) if budget is not None else default_budget(16000),
        }, **scope)

    def prepare(
        self, turn_id: str, *, conversation_id: str, branch_id: str,
        latest_input_event_ids: Sequence[str] = (), read_fence: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None, capabilities: Mapping[str, Any] | None = None,
        evidence_mode: str | None = None, evidence_cutoff: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> JsonObject:
        payload = {
            "turn_id": turn_id, "latest_input_event_ids": list(latest_input_event_ids),
            "read_fence": dict(read_fence) if read_fence is not None else default_read_fence(),
            "budget": dict(budget) if budget is not None else default_budget(),
            "capabilities": dict(capabilities) if capabilities is not None else integration_capabilities(),
        }
        if evidence_mode is not None:
            payload["evidence_mode"] = evidence_mode
        if evidence_cutoff is not None:
            payload["evidence_cutoff"] = dict(evidence_cutoff)
        return self.action("prepare", payload, conversation_id=conversation_id,
                           branch_id=branch_id, request_id=request_id)

    def commit(
        self, turn_id: str, *, conversation_id: str, branch_id: str,
        expected_working_revision: int, delivered_event_ids: Sequence[str] = (),
        proposed_items: Sequence[Mapping[str, Any]] = (),
        proposed_open_loops: Sequence[Mapping[str, Any]] = (),
        assertion_proposals: Sequence[Mapping[str, Any]] = (),
        recall_feedback: Sequence[Mapping[str, Any]] = (),
        idempotency_key: str | None = None, request_id: str | None = None,
    ) -> JsonObject:
        scope = self._scope(conversation_id, branch_id)
        return self.action("commit", {
            "turn_id": turn_id, "idempotency_key": idempotency_key or self._key("commit", turn_id, scope),
            "expected_working_revision": expected_working_revision,
            "delivered_event_ids": list(delivered_event_ids), "proposed_items": list(proposed_items),
            "proposed_open_loops": list(proposed_open_loops),
            "assertion_proposals": list(assertion_proposals), "recall_feedback": list(recall_feedback),
        }, target=scope, request_id=request_id)

    def checkpoint(
        self, *, conversation_id: str, branch_id: str,
        expected_working_revision: int, through_sequence: int, reason: str = "manual",
        request_id: str | None = None,
    ) -> JsonObject:
        return self.action("checkpoint", {
            "expected_working_revision": expected_working_revision,
            "through_sequence": through_sequence, "reason": reason,
        }, conversation_id=conversation_id, branch_id=branch_id, request_id=request_id)

    def correct(
        self, memory_id: str, *, expected_revision: int, expected_dependency_generation: int,
        correction_event_ids: Sequence[str], operation: str,
        replacement: Mapping[str, Any] | None = None, idempotency_key: str | None = None,
        conversation_id: str | None = None, branch_id: str | None = None,
        request_id: str | None = None,
    ) -> JsonObject:
        scope = self._scope(conversation_id, branch_id)
        payload = {
            "memory_id": memory_id, "expected_revision": expected_revision,
            "expected_dependency_generation": expected_dependency_generation,
            "correction_event_ids": list(correction_event_ids), "operation": operation,
            "idempotency_key": idempotency_key or self._key(
                "correct", [memory_id, expected_revision, list(correction_event_ids)], scope,
            ),
        }
        if replacement is not None:
            payload["replacement"] = dict(replacement)
        return self.action("correct", payload, target=scope, request_id=request_id)

    def forget(
        self, ids: Sequence[str], *, kind: str, mode: str, expected_scope_generation: int,
        idempotency_key: str | None = None, conversation_id: str | None = None,
        branch_id: str | None = None, request_id: str | None = None,
    ) -> JsonObject:
        scope = self._scope(conversation_id, branch_id)
        return self.action("forget", {
            "idempotency_key": idempotency_key or self._key(
                "forget", [kind, list(ids), mode, expected_scope_generation], scope,
            ),
            "selector": {"kind": kind, "ids": list(ids)}, "mode": mode,
            "expected_scope_generation": expected_scope_generation,
        }, target=scope, request_id=request_id)

    def export(
        self, *, include_events: bool = True, include_working_states: bool = True,
        include_assertions: bool = True, budget: Mapping[str, Any] | None = None,
        **scope: Any,
    ) -> JsonObject:
        return self.action("export", {
            "include_events": include_events, "include_working_states": include_working_states,
            "include_assertions": include_assertions, "include_provenance": True,
            "include_tombstones": True,
            "budget": dict(budget) if budget is not None else default_budget(),
        }, **scope)

    def restore(
        self, uploaded_archive_ref: str, *, expected_manifest_sha256: str,
        expected_scope_generation: int, mode: str = "merge_authorized_brain",
        budget: Mapping[str, Any] | None = None, **scope: Any,
    ) -> JsonObject:
        return self.action("restore", {
            "uploaded_archive_ref": uploaded_archive_ref,
            "expected_manifest_sha256": expected_manifest_sha256, "mode": mode,
            "apply_current_tombstones": True, "expected_scope_generation": expected_scope_generation,
            "budget": dict(budget) if budget is not None else default_budget(),
        }, **scope)
