import copy

import pytest

from agent_memory.config import Settings
from agent_memory.contracts import CONTRACT_VERSION, validate
from agent_memory.helpers import Principal, uid
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


class Runtime:
    def __init__(self, service, principal):
        self.service = service
        self.principal = principal
        self.brain_id = service.create_brain(principal, "Storage contract tests")["id"]

    def request(self, action, payload):
        return {
            "message_type": "request", "contract_version": CONTRACT_VERSION,
            "request_id": uid(), "action": action,
            "target": {"brain_ref": self.brain_id, "conversation_ref": "private",
                       "branch_ref": "main"},
            "payload": payload,
        }

    def send(self, request):
        response = self.service.handle(self.principal, request)
        validate("ActionResponse", response)
        return response

    def observe_request(self, text, revision=1):
        return self.request("observe", {
            "idempotency_key": uid(), "events": [{
                "source_event_key": "editable-message", "source_revision": revision,
                "producer_ref": "storage-test", "actor_ref": "owner", "actor_role": "user",
                "event_kind": "message", "content": {"media_type": "text/plain", "text": text},
                "occurred_time": {"start": None, "end": None, "precision": "unknown",
                                  "basis": "unknown"},
                "interpretation_hint": "real_world_report", "delivery_state": "received",
            }],
        })

    def dashboard(self):
        return self.service.dashboard(self.principal, self.brain_id)


@pytest.fixture
def runtime():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    return Runtime(MemoryService(Database(settings.database_url)), Principal(uid(), uid()))


def test_observe_retries_preserve_one_event_and_one_outbox_job(runtime):
    request = runtime.observe_request("The agreed meeting place is the library.")
    first = runtime.send(request)
    assert first["outcome"] == "completed", first
    event_id = first["result"]["accepted_event_ids"][0]

    retry = copy.deepcopy(request)
    retry["request_id"] = uid()
    repeated = runtime.send(retry)
    assert repeated["request_id"] == retry["request_id"]
    assert repeated["result"] == first["result"]

    duplicate = copy.deepcopy(request)
    duplicate["request_id"] = uid()
    duplicate["payload"]["idempotency_key"] = uid()
    result = runtime.send(duplicate)
    assert result["result"]["accepted_event_ids"] == []
    assert result["result"]["duplicate_event_ids"] == [event_id]
    assert len(runtime.dashboard()["events"]) == 1
    assert len(runtime.dashboard()["jobs"]) == 1
    assert runtime.service.work_once(runtime.principal, runtime.brain_id) == 1
    assert runtime.service.work_once(runtime.principal, runtime.brain_id) == 0


def test_source_edit_preserves_history_and_rejects_version_reuse(runtime):
    first = runtime.send(runtime.observe_request("The meeting is at the library."))
    assert first["outcome"] == "completed", first
    event_id = first["result"]["accepted_event_ids"][0]
    runtime.service.work_once(runtime.principal, runtime.brain_id)
    old_memory_id = runtime.dashboard()["memories"][0]["memory_id"]

    updated = runtime.send(runtime.observe_request("The meeting is at the cafe.", revision=2))
    assert updated["outcome"] == "completed", updated
    assert updated["result"]["accepted_event_ids"] == [event_id]
    latest = runtime.dashboard()["events"][0]
    assert latest["revision"] == 2
    assert latest["previous_revision"] == 1
    assert old_memory_id not in {m["memory_id"] for m in runtime.dashboard()["memories"]}

    old_version = runtime.send(runtime.observe_request("The meeting is at the library."))
    assert old_version["error"]["code"] == "version_conflict"
    reused_version = runtime.send(runtime.observe_request("A conflicting edit.", revision=2))
    assert reused_version["error"]["code"] == "idempotency_conflict"

    with runtime.service.database.transaction(runtime.principal) as connection:
        rows = connection.execute(
            "SELECT revision,is_current,document FROM memory_events "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s ORDER BY revision",
            (runtime.principal.tenant_id, runtime.principal.principal_id, runtime.brain_id, event_id),
        ).fetchall()
    assert [(row["revision"], row["is_current"]) for row in rows] == [(1, False), (2, True)]
    assert rows[0]["document"]["observed"]["content"]["text"] == "The meeting is at the library."
    assert rows[1]["document"]["observed"]["content"]["text"] == "The meeting is at the cafe."


@pytest.mark.parametrize("scope_field", ["conversation_ref", "branch_ref"])
def test_source_identity_cannot_silently_cross_scope(runtime, scope_field):
    request = runtime.observe_request("A message belongs to its actual conversation.")
    first = runtime.send(request)
    assert first["outcome"] == "completed", first
    changed = copy.deepcopy(request)
    changed["request_id"] = uid()
    changed["payload"]["idempotency_key"] = uid()
    changed["target"][scope_field] = "another-scope"
    rejected = runtime.send(changed)
    assert rejected["outcome"] == "conflict"
    assert rejected["error"]["code"] == "idempotency_conflict"
    assert len(runtime.dashboard()["events"]) == 1


def test_checkpoint_is_idempotent_and_respects_cas_and_durable_coverage(runtime):
    observed = runtime.send(runtime.observe_request("We still need to choose a meeting time."))
    assert observed["outcome"] == "completed", observed
    request = runtime.request("checkpoint", {
        "expected_working_revision": 0, "through_sequence": 1, "reason": "compaction",
    })
    checkpoint = runtime.send(request)
    assert checkpoint["outcome"] == "completed", checkpoint
    assert checkpoint["result"]["compaction_safe"] is True
    assert checkpoint["result"]["restore_strategy"] == "state_plus_unindexed_tail"
    assert runtime.send(request)["result"] == checkpoint["result"]

    future = runtime.send(runtime.request("checkpoint", {
        "expected_working_revision": 0, "through_sequence": 2, "reason": "compaction",
    }))
    assert future["result"]["compaction_safe"] is False
    assert future["result"]["coverage"]["read_fence_satisfied"] is False

    committed = runtime.send(runtime.request("commit", {
        "turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
        "delivered_event_ids": [], "proposed_items": [], "proposed_open_loops": [],
        "assertion_proposals": [],
    }))
    assert committed["outcome"] == "completed", committed
    stale = runtime.send(runtime.request("checkpoint", {
        "expected_working_revision": 0, "through_sequence": 1, "reason": "shutdown",
    }))
    assert stale["error"]["code"] == "version_conflict"
    current = runtime.send(runtime.request("checkpoint", {
        "expected_working_revision": 1, "through_sequence": 1, "reason": "shutdown",
    }))
    assert current["outcome"] == "completed", current
    assert current["result"]["working_revision"] == 1
