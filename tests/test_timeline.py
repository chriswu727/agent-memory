import pytest

from membrain.config import Settings
from membrain.contracts import CONTRACT_VERSION
from membrain.helpers import DomainError, Principal, uid
from membrain.service import MemoryService
from membrain.storage import Database

pytestmark = pytest.mark.integration


class Brain:
    def __init__(self, service, principal):
        self.service, self.principal = service, principal
        self.brain = service.create_brain(principal, "Timeline brain")["id"]

    def action(self, action, payload, conversation="first"):
        response = self.service.handle(self.principal, {
            "message_type": "request", "contract_version": CONTRACT_VERSION, "request_id": uid(),
            "action": action, "payload": payload,
            "target": {"brain_ref": self.brain, "conversation_ref": conversation, "branch_ref": "main"},
        })
        assert response["outcome"] == "completed", response
        return response["result"]

    def say(self, text, *, conversation="first", role="user"):
        result = self.action("observe", {"idempotency_key": uid(), "events": [{
            "source_event_key": uid(), "source_revision": 1, "producer_ref": "timeline-test", "actor_ref": role,
            "actor_role": role, "event_kind": "message", "content": {"media_type": "text/plain", "text": text},
            "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
            "interpretation_hint": "unknown", "delivery_state": "delivered" if role == "assistant" else "received",
            "parent_source_event_keys": [],
        }]}, conversation)
        return result["accepted_event_ids"][0]

    def timeline(self, **options):
        return self.service.timeline(self.principal, self.brain, **options)


@pytest.fixture
def brain():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    return Brain(MemoryService(Database(settings.database_url)), Principal(uid(), uid()))


def test_timeline_returns_user_messages_newest_first_and_reports_completeness(brain):
    brain.say("I'm Priya; my left knee cannot take consecutive run days.")
    brain.say("Noted, rest days between runs.", role="assistant")
    brain.say("The race moved to December 7.", conversation="second")
    result = brain.timeline()
    assert [item["text"] for item in result["items"]] == [
        "The race moved to December 7.", "I'm Priya; my left knee cannot take consecutive run days."]
    assert result["complete"] is True
    assert {item["actor_role"] for item in result["items"]} == {"user"}
    assert result["items"][0]["conversation_id"] == "second" and result["items"][0]["recorded_at"]
    both = brain.timeline(roles=("user", "assistant"))
    assert len(both["items"]) == 3


def test_timeline_excludes_the_current_conversation_and_respects_budgets(brain):
    for index in range(5):
        brain.say(f"message {index} " + "x" * 90)
    brain.say("current conversation", conversation="now")
    excluded = brain.timeline(exclude_conversation="now")
    assert all(item["conversation_id"] != "now" for item in excluded["items"]) and excluded["complete"]
    budgeted = brain.timeline(exclude_conversation="now", max_characters=250)
    assert len(budgeted["items"]) == 2 and budgeted["complete"] is False
    assert budgeted["items"][0]["text"].startswith("message 4")
    assert brain.timeline(exclude_conversation="now", limit=3)["complete"] is False
    clipped = brain.timeline(exclude_conversation="now", max_item_characters=20)
    assert clipped["complete"] is False and all(len(item["text"]) <= 20 for item in clipped["items"])


def test_timeline_never_returns_erased_or_suppressed_messages(brain):
    erased = brain.say("My old address is 12 Secret Lane.")
    hidden = brain.say("I once failed a driving test.")
    brain.say("I'm vegetarian.")
    generation = brain.service.dashboard(brain.principal, brain.brain)["brain"]["scope_generation"]
    brain.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [erased]},
                            "mode": "erase_content", "expected_scope_generation": generation})
    generation = brain.service.dashboard(brain.principal, brain.brain)["brain"]["scope_generation"]
    brain.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [hidden]},
                            "mode": "suppress_recall", "expected_scope_generation": generation})
    texts = [item["text"] for item in brain.timeline()["items"]]
    assert texts == ["I'm vegetarian."]


def test_timeline_is_private_to_its_owner(brain):
    brain.say("Only the owner may read this.")
    stranger = Principal(uid(), uid())
    with pytest.raises(DomainError):
        brain.service.timeline(stranger, brain.brain)


@pytest.mark.parametrize("options", [{"roles": ()}, {"roles": ("system",)}, {"max_characters": 0},
                                     {"limit": 1001}, {"exclude_conversation": ""}, {"event_ids": []},
                                     {"event_ids": ["x"] * 101}, {"event_ids": [""]}])
def test_timeline_rejects_invalid_limits(brain, options):
    with pytest.raises(DomainError):
        brain.timeline(**options)


def test_timeline_returns_only_requested_messages_and_never_erased_ones(brain):
    kept = brain.say("The budget ceiling is $24,000 all-in.")
    reply = brain.say("Noted the ceiling.", role="assistant")
    erased = brain.say("My old PIN is 4321.")
    brain.say("Unrelated message.")
    generation = brain.service.dashboard(brain.principal, brain.brain)["brain"]["scope_generation"]
    brain.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [erased]},
                            "mode": "erase_content", "expected_scope_generation": generation})
    result = brain.timeline(roles=("user", "assistant"), event_ids=[kept, reply, erased, "missing"])
    assert [item["event_id"] for item in result["items"]] == [reply, kept]


def test_timeline_can_read_one_conversation(brain):
    brain.say("Saved note about Mom's walker.", conversation="membrain-notes", role="assistant")
    brain.say("A normal message.", conversation="chat")
    notes = brain.timeline(roles=("assistant",), conversation_id="membrain-notes")
    assert [item["text"] for item in notes["items"]] == ["Saved note about Mom's walker."]
    assert brain.timeline(roles=("user",), conversation_id="membrain-notes")["items"] == []


def test_ensure_brain_is_idempotent_and_never_takes_another_owners_brain(brain):
    brain_id = "user-" + uid()
    first = brain.service.ensure_brain(brain.principal, brain_id, "user qq:1001")
    assert first["id"] == brain_id
    assert brain.service.ensure_brain(brain.principal, brain_id, "renamed")["id"] == brain_id
    assert brain.service.timeline(brain.principal, brain_id)["items"] == []
    with pytest.raises(DomainError):
        brain.service.ensure_brain(Principal(uid(), uid()), brain_id, "stranger")
