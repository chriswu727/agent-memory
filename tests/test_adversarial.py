import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from agent_memory.client import (
    default_budget,
    default_read_fence,
    integration_capabilities,
)
from agent_memory.config import Settings
from agent_memory.contracts import CONTRACT_VERSION
from agent_memory.helpers import Principal, now, uid
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


def serialized(value):
    return json.dumps(value, ensure_ascii=False)


class Runtime:
    def __init__(self, service, principal):
        self.service = service
        self.principal = principal
        self.brain = service.create_brain(principal, "Adversarial isolated brain")["id"]

    def request(self, action, payload):
        return {
            "message_type": "request", "contract_version": CONTRACT_VERSION,
            "request_id": uid(), "action": action,
            "target": {"brain_ref": self.brain, "conversation_ref": "private",
                       "branch_ref": "main"},
            "payload": payload,
        }

    def send(self, request):
        return self.service.handle(self.principal, request)

    def action(self, action, payload):
        response = self.send(self.request(action, payload))
        assert response["outcome"] == "completed", response
        return response["result"]

    def observe(self, text, key, revision=1):
        return self.action("observe", {
            "idempotency_key": uid(), "events": [{
                "source_event_key": key, "source_revision": revision,
                "producer_ref": "adversarial-test", "actor_ref": "owner",
                "actor_role": "user", "event_kind": "message",
                "content": {"media_type": "text/plain", "text": text},
                "occurred_time": {"start": None, "end": None, "precision": "unknown",
                                  "basis": "unknown"},
                "interpretation_hint": "real_world_report", "delivery_state": "received",
                "parent_source_event_keys": [],
            }],
        })["accepted_event_ids"][0]

    def recall_payload(self, query):
        return {
            "query": query, "intent": "explore", "cue_event_ids": [],
            "additional_cues": [], "read_fence": default_read_fence(),
            "budget": default_budget(64000),
        }

    def recall(self, query):
        return self.action("recall", self.recall_payload(query))

    def drain(self):
        for count in range(20):
            if not self.service.work_once(self.principal, self.brain):
                return count
        pytest.fail("Background queue did not drain within twenty jobs")

    def dashboard(self):
        return self.service.dashboard(self.principal, self.brain)

    def forget(self, event_id, mode="erase_content"):
        return self.action("forget", {
            "idempotency_key": uid(), "selector": {"kind": "events", "ids": [event_id]},
            "mode": mode,
            "expected_scope_generation": self.dashboard()["brain"]["scope_generation"],
        })

    def archive(self):
        exported = self.action("export", {
            "include_events": True, "include_working_states": True,
            "include_assertions": True, "include_provenance": True,
            "include_tombstones": True, "budget": default_budget(64000),
        })
        return self.service.download_archive(self.principal, exported["archive_id"])

    def upload(self, document):
        return self.service.upload_archive(self.principal, self.brain, document)

    def restore(self, uploaded, mode="merge_authorized_brain"):
        return self.action("restore", {
            "uploaded_archive_ref": uploaded["archive_id"],
            "expected_manifest_sha256": uploaded["manifest_sha256"],
            "mode": mode, "apply_current_tombstones": True,
            "expected_scope_generation": self.dashboard()["brain"]["scope_generation"],
            "budget": default_budget(64000),
        })


@pytest.fixture
def runtime():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    return Runtime(MemoryService(Database(settings.database_url)), Principal(uid(), uid()))


@pytest.mark.parametrize("new_brain", [False, True])
def test_old_offline_archive_cannot_resurrect_erased_sources(runtime, new_brain):
    secret = "要删除的暗号是紫罗兰"
    retained = "保留的暗号是北极星"
    deleted_id = runtime.observe(secret, "private-secret")
    runtime.observe(retained, "retained-note")
    runtime.drain()
    offline = copy.deepcopy(runtime.archive()["document"])
    assert secret in serialized(offline)
    runtime.forget(deleted_id)

    if new_brain:
        runtime.brain = runtime.service.create_brain(runtime.principal, "Restored brain")["id"]
    uploaded = runtime.upload(offline)
    result = runtime.restore(uploaded, "new_brain" if new_brain else "merge_authorized_brain")
    assert result["excluded_deleted_count"] == 1
    runtime.drain()

    assert secret not in serialized(runtime.dashboard())
    assert secret not in serialized(runtime.recall("暗号"))
    exported = runtime.archive()["document"]
    assert secret not in serialized(exported)
    assert retained in serialized(exported)


def test_reuploaded_erased_archive_cannot_make_erased_content_downloadable(runtime):
    secret = "永久删除的私密句子是雨燕归巢"
    source_id = runtime.observe(secret, "erasable-source")
    runtime.drain()
    original = runtime.archive()
    offline = copy.deepcopy(original["document"])
    runtime.forget(source_id)
    assert secret not in serialized(runtime.service.download_archive(
        runtime.principal, original["archive_id"],
    ))

    uploaded = runtime.upload(offline)
    runtime.restore(uploaded)
    returned = runtime.service.download_archive(runtime.principal, uploaded["archive_id"])
    assert secret not in serialized(returned)


@pytest.mark.parametrize("cached_action", ["recall", "inspect", "prepare"])
def test_source_revision_invalidates_cached_memory_content(runtime, cached_action):
    old = "原始暗号是紫罗兰"
    updated = "更正后的暗号是北极星"
    runtime.observe(old, "editable-source")
    runtime.drain()
    memory = runtime.recall("暗号")["items"][0]["memory"]
    if cached_action == "recall":
        payload = runtime.recall_payload("暗号")
    elif cached_action == "inspect":
        payload = {
            "memory_ids": [memory["memory_id"]], "evidence_refs": memory["evidence_refs"],
            "include_successors": True, "include_corrections": True,
            "budget": default_budget(64000),
        }
    else:
        cue_id = runtime.observe("我的暗号是什么？", "current-query")
        payload = {
            "turn_id": uid(), "latest_input_event_ids": [cue_id],
            "read_fence": default_read_fence(), "budget": default_budget(64000),
            "capabilities": integration_capabilities(),
        }
    request = runtime.request(cached_action, payload)
    first = runtime.send(request)
    assert first["outcome"] == "completed", first
    assert old in serialized(first)
    assert old in serialized(runtime.send(request))

    runtime.observe(updated, "editable-source", revision=2)
    replayed = runtime.send(request)
    assert old not in serialized(replayed)
    assert replayed["outcome"] == "conflict", replayed
    assert replayed["error"]["code"] == "version_conflict"
    runtime.drain()
    fresh = runtime.recall("暗号")
    assert old not in serialized(fresh)
    assert updated in serialized(fresh)


def test_concurrent_commits_with_same_revision_have_exactly_one_winner(runtime):
    barrier = Barrier(2)

    def commit(label):
        request = runtime.request("commit", {
            "turn_id": label, "idempotency_key": uid(), "expected_working_revision": 0,
            "delivered_event_ids": [], "proposed_open_loops": [], "assertion_proposals": [],
            "proposed_items": [{
                "key": "next-task", "value": label, "knowledge_state": "unknown",
                "evidence_refs": [], "expires_at": None, "updated_at": now(),
            }],
        })
        barrier.wait(timeout=5)
        return runtime.send(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(commit, label) for label in ("first-writer", "second-writer")]
        responses = [future.result(timeout=15) for future in futures]
    winners = [response for response in responses if response["outcome"] == "completed"]
    losers = [response for response in responses if response["outcome"] != "completed"]
    assert len(winners) == len(losers) == 1, responses
    assert losers[0]["error"]["code"] == "version_conflict"
    assert losers[0]["error"]["current_revision"] == 1
    winner = winners[0]["result"]["working_state"]
    assert winner["revision"] == 1
    archived_states = runtime.archive()["document"]["working_states"]
    assert len(archived_states) == 1
    assert archived_states[0]["revision"] == 1
    assert archived_states[0]["items"] == winner["items"]


@pytest.mark.parametrize("mode", ["erase_content", "suppress_recall"])
def test_late_extraction_worker_cannot_publish_a_deleted_source(runtime, mode):
    secret = "积压任务中的私密暗号是青蛙"
    retained = "保留的日程是周五去书店"
    source_id = runtime.observe(secret, "queued-secret")
    runtime.observe(retained, "queued-retained")
    assert runtime.dashboard()["status"]["pending_jobs"] == 2
    runtime.forget(source_id, mode)
    assert secret not in serialized(runtime.recall("暗号"))

    assert runtime.drain() == 2
    dashboard = runtime.dashboard()
    assert dashboard["status"]["pending_jobs"] == 0
    assert secret not in serialized(dashboard)
    assert secret not in serialized(runtime.recall("暗号"))
    assert retained in serialized(runtime.recall("书店"))
    assert secret not in serialized(runtime.archive()["document"])
