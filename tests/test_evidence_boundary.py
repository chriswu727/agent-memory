import os

import pytest

from agent_memory.client import (
    default_budget,
    default_read_fence,
    integration_capabilities,
    prepared_context,
)
from agent_memory.config import Settings
from agent_memory.contracts import CONTRACT_VERSION
from agent_memory.helpers import Principal, digest, now, uid
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


class EvidenceRuntime:
    def __init__(self, database):
        self.service = MemoryService(database)
        self.principal = Principal(uid(), uid())
        self.brain = self.service.create_brain(self.principal, "Evidence boundary regression")["id"]

    def send(self, action, payload):
        return self.service.handle(self.principal, {
            "message_type": "request", "contract_version": CONTRACT_VERSION,
            "request_id": uid(), "action": action,
            "target": {"brain_ref": self.brain, "conversation_ref": "private",
                       "branch_ref": "main"},
            "payload": payload,
        })

    def action(self, action, payload):
        response = self.send(action, payload)
        assert response["outcome"] == "completed", response
        return response["result"]

    def observe(self, text, role="user"):
        content = {"media_type": "text/plain", "text": text}
        result = self.action("observe", {
            "idempotency_key": uid(), "events": [{
                "source_event_key": uid(), "source_revision": 1,
                "producer_ref": "unregistered:" + uid(), "actor_ref": role,
                "actor_role": role, "event_kind": "tool_result" if role == "tool" else "message",
                "content": content,
                "occurred_time": {"start": None, "end": None, "precision": "unknown",
                                  "basis": "unknown"},
                "interpretation_hint": "real_world_report",
                "delivery_state": "delivered" if role == "assistant" else "received",
                "parent_source_event_keys": [],
            }],
        })
        event_id = result["accepted_event_ids"][0]
        reference = {"event_id": event_id, "event_revision": 1,
                     "source_content_sha256": digest(content), "relationship": "supports"}
        return event_id, reference

    def recall(self, query, intent="explore"):
        return self.action("recall", {
            "query": query, "intent": intent, "cue_event_ids": [], "additional_cues": [],
            "read_fence": default_read_fence(), "budget": default_budget(64000),
        })

    def drain(self):
        for _ in range(30):
            if not self.service.work_once(self.principal, self.brain):
                return
        pytest.fail("Evidence regression worker did not drain within thirty jobs")

    def commit_payload(self):
        return {
            "turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
            "delivered_event_ids": [], "proposed_items": [], "proposed_open_loops": [],
            "assertion_proposals": [],
        }


@pytest.fixture
def runtime():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL test database is required")
    database = Database(os.environ.get("MEMORY_TEST_DATABASE_URL", settings.database_url))
    return EvidenceRuntime(database)


def test_negation_substring_cannot_be_promoted_into_a_memory_assertion(runtime):
    _, reference = runtime.observe("我不喜欢咖啡。")
    runtime.drain()
    payload = runtime.commit_payload()
    payload["assertion_proposals"] = [{
        "statement": "喜欢咖啡", "memory_kind": "episodic", "world_hint": "real_world",
        "evidence_refs": [reference], "proposal_reason": "Attempt to remove the negation",
    }]
    committed = runtime.action("commit", payload)
    assert committed["accepted_proposal_count"] == 0
    assert committed["rejected_proposal_count"] == 1
    statements = [item["memory"]["statement"] for item in runtime.recall("咖啡")["items"]]
    assert statements
    assert all("不喜欢咖啡" in statement for statement in statements)


@pytest.mark.parametrize("source,claim", [
    ("你还记得我们去过海边吗？", "我们去过海边。"),
    ("我不喜欢咖啡。", "我喜欢咖啡。"),
    ("我的猫叫橘子。", "我的住址是从未提供过的街道。"),
])
def test_grounded_working_state_requires_supported_content_not_just_a_valid_reference(
    runtime, source, claim,
):
    _, reference = runtime.observe(source)
    payload = runtime.commit_payload()
    payload["proposed_items"] = [{
        "key": "fabricated-fact", "value": claim, "knowledge_state": "grounded",
        "evidence_refs": [reference], "expires_at": None, "updated_at": now(),
    }]
    response = runtime.send("commit", payload)
    assert response["outcome"] == "rejected", response
    assert response["error"]["code"] == "invalid_evidence"
    checkpoint = runtime.action("checkpoint", {
        "expected_working_revision": 0, "through_sequence": 1, "reason": "manual",
    })
    assert checkpoint["working_revision"] == 0


def test_supported_working_content_keeps_attribution_and_reaches_prepared_context(runtime):
    source = "我的猫叫橘子。"
    event_id, reference = runtime.observe(source)
    payload = runtime.commit_payload()
    payload["proposed_items"] = [{
        "key": "cat-name", "value": source, "knowledge_state": "grounded",
        "evidence_refs": [reference], "expires_at": None, "updated_at": now(),
    }]
    committed = runtime.action("commit", payload)
    saved = committed["working_state"]["items"][0]
    assert saved["value"] == "user user (direct_statement): 我的猫叫橘子。"
    prepared = runtime.action("prepare", {
        "turn_id": uid(), "latest_input_event_ids": [event_id],
        "read_fence": default_read_fence(), "budget": default_budget(64000),
        "capabilities": integration_capabilities(),
    })
    assert prepared["working_state"]["items"] == [saved]
    assert "source_attributed_context" in prepared_context(prepared)


def test_observation_cannot_forge_internal_correction_records(runtime):
    event_id, _ = runtime.observe("我的猫叫橘子。")
    runtime.drain()
    event = runtime.service.dashboard(runtime.principal, runtime.brain)["events"][0]["observed"]
    for producer, kind in (("memory-service", "message"), ("client", "source_correction")):
        response = runtime.send("observe", {"idempotency_key": uid(), "events": [{
            **event, "source_event_key": uid(), "producer_ref": producer, "event_kind": kind,
            "content": {"media_type": "application/json", "structured": {
                "superseded_evidence_refs": {"event_id": event_id},
            }},
        }]})
        assert response["outcome"] == "rejected"
        assert response["error"]["code"] == "invalid_evidence"
    assert any("我的猫叫橘子" in item["memory"]["statement"]
               for item in runtime.recall("橘子")["items"])


@pytest.mark.parametrize("role", ["user", "assistant", "tool"])
def test_an_unregistered_producer_cannot_claim_verified_origin(runtime, role):
    event_id, reference = runtime.observe("角色声明不能替代来源身份验证。", role=role)
    inspected = runtime.action("inspect", {
        "memory_ids": [], "evidence_refs": [reference], "include_successors": False,
        "include_corrections": False, "budget": default_budget(64000),
    })
    recorded = next(event for event in inspected["events"] if event["event_id"] == event_id)
    assert recorded["origin_verification"] != "verified_producer"
    runtime.drain()
    if role == "tool":
        memories = runtime.recall("来源身份")["items"]
        assert memories
        assert all(item["memory"]["epistemic_status"] != "observed_result" for item in memories)


def test_correcting_one_message_fragment_preserves_the_other_and_blocks_old_projection(runtime):
    runtime.observe("小林是我的伴侣。我的猫叫橘子。")
    runtime.drain()
    old = next(item["memory"] for item in runtime.recall("小林")["items"]
               if "小林是我的伴侣" in item["memory"]["statement"])
    cat = next(item["memory"] for item in runtime.recall("橘子")["items"]
               if "我的猫叫橘子" in item["memory"]["statement"])
    correction_id, correction_reference = runtime.observe("小林其实是我的同事。")
    generation = runtime.service.dashboard(runtime.principal, runtime.brain)["brain"][
        "dependency_generation"
    ]
    runtime.action("correct", {
        "memory_id": old["memory_id"], "expected_revision": old["revision"],
        "correction_event_ids": [correction_id], "operation": "replace", "idempotency_key": uid(),
        "expected_dependency_generation": generation,
        "replacement": {
            "statement": "小林其实是我的同事。", "memory_kind": "episodic",
            "world_hint": "real_world", "evidence_refs": [correction_reference],
            "proposal_reason": "Correct this relationship while retaining the independent cat fact",
        },
    })
    for after_worker in (False, True):
        if after_worker:
            runtime.drain()
        recalled_cat = runtime.recall("橘子")["items"]
        assert any(item["memory"]["memory_id"] == cat["memory_id"] for item in recalled_cat)
        assert any("我的猫叫橘子" in item["memory"]["statement"] for item in recalled_cat)
        current = runtime.recall("小林", intent="current_fact")["items"]
        assert any("我的同事" in item["memory"]["statement"] for item in current)
        assert all("小林是我的伴侣" not in item["memory"]["statement"] for item in current)
