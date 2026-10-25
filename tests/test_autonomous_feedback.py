import copy
import hashlib
import importlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_feedback_dynamics import Feedback

from agent_memory import prepared_selection
from agent_memory.api import create_app
from agent_memory.client import MemoryClient, default_budget, integration_capabilities
from agent_memory.config import Settings
from agent_memory.feedback import observe_feedback
from agent_memory.helpers import uid
from agent_memory.runtime import BrainRuntime, DeliveryReceipt, RuntimePolicy
from agent_memory.storage import Database

pytestmark = pytest.mark.integration
QUESTION = "我喜欢哪种香味，松木吗？"


@pytest.fixture
def autonomous():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    data = Feedback(Database(settings.database_url))
    local = Settings(database_url=settings.database_url, api_keys={
        hashlib.sha256(b"feedback-test").hexdigest(): data.principal,
    })
    with TestClient(create_app(local, service=data.service)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path,
                                      headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        with MemoryClient("http://testserver", "feedback-test", brain_id=data.brain,
                          transport=httpx.MockTransport(forward)) as client:
            calls, sends = [], []

            def runtime(answer):
                def generate(request):
                    calls.append(request)
                    return answer

                def deliver(request):
                    sends.append(request)
                    return DeliveryReceipt("delivered")

                return BrainRuntime(client, generate, deliver=deliver,
                                    policy=RuntimePolicy(orientation_policy="on_demand", recent_context_events=0))

            yield SimpleNamespace(data=data, client=client, runtime=runtime, calls=calls, sends=sends)


def confirm(client, text="yes", **changes):
    return client.observe(text, conversation_id="chat", branch_id="main",
                          source_event_key="feedback", producer_ref="feedback-client", **changes)["result"]


@pytest.mark.parametrize("reply,successes,failures", [("yes", 1, 0), ("wrong", 0, 1), ("对。", 1, 0), ("不对。", 0, 1)])
def test_next_runtime_user_reply_updates_only_bound_memory_without_extra_model_calls(autonomous, reply, successes, failures):
    data = autonomous.data
    _, memory = data.seed()
    first_runtime = autonomous.runtime(memory["statement"])
    first = first_runtime.respond(QUESTION, message_id="question", conversation_id="chat")
    saved = first_runtime.journal.get(first.turn_id)["prepared_memory_selection"]
    assert saved["state"] == "complete"
    assert memory["memory_id"] in {item["memory_id"] for item in saved["memories"]}
    assert memory["statement"] not in json.dumps(saved, ensure_ascii=False)
    assert data.access(memory) is None
    second_runtime = autonomous.runtime("Acknowledged.")
    second_runtime.respond(reply, message_id="reply", conversation_id="chat")
    usage = data.access(memory)
    assert (usage["external_uses"], usage["successes"], usage["failures"]) == (1, successes, failures)
    assert (usage["last_reinforcement_at"] is None) == bool(failures)
    assert len(autonomous.calls) == len(autonomous.sends) == 2
    replay = second_runtime.respond(reply, message_id="reply", conversation_id="chat")
    assert replay.replayed and len(autonomous.calls) == len(autonomous.sends) == 2
    assert data.access(memory)["external_uses"] == 1
    contribution, = data.contributions(memory)
    with data.database.transaction(data.principal) as conn:
        event = conn.execute("SELECT document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                             "AND id=%s AND is_current", (*data.params, contribution["feedback_event_id"])).fetchone()["document"]
        persisted = conn.execute("SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s "
                                 "AND brain_id=%s AND id=%s AND is_current",
                                 (*data.params, memory["memory_id"])).fetchone()["document"]
    assert event["observed"]["interpretation_hint"] == "unknown"
    assert persisted["epistemic_status"] == memory["epistemic_status"]
    assert persisted["dynamics"] == memory["dynamics"]


def test_bound_selection_survives_revalidation_prepares_and_removed_transient_cache(autonomous):
    data = autonomous.data
    _, memory = data.seed()
    runtime = autonomous.runtime(memory["statement"])
    result = runtime.respond(QUESTION, message_id="question", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    original = copy.deepcopy(row["prepared_memory_selection"])
    empty = autonomous.client.prepare(
        result.turn_id, conversation_id="chat", branch_id="main",
        latest_input_event_ids=[ref["event_id"] for ref in row["input_event_refs"]],
        budget=default_budget(800), capabilities=integration_capabilities(),
    )["result"]
    assert empty["recall_traces"] == []
    with data.database.transaction(data.principal) as conn:
        conn.execute("UPDATE memory_requests SET response=NULL WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                     "AND action='prepare'", data.params)
        conn.execute("DELETE FROM memory_traces WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s", data.params)
    confirm(autonomous.client)
    assert data.access(memory)["external_uses"] == 1
    assert runtime.journal.get(result.turn_id)["prepared_memory_selection"] == original
    assert len(autonomous.calls) == len(autonomous.sends) == 1


@pytest.mark.parametrize("answer", ["The meeting is at three.", "{} Do you want an appointment?", "You like pine scent."])
def test_unrelated_or_paraphrased_delivery_does_not_receive_automatic_credit(autonomous, answer):
    data = autonomous.data
    _, memory = data.seed()
    autonomous.runtime(answer.format(memory["statement"])).respond(QUESTION, message_id="question", conversation_id="chat")
    confirm(autonomous.client)
    assert data.access(memory) is None


def test_identical_statements_from_distinct_selected_memories_remain_ambiguous(autonomous):
    data = autonomous.data
    _, first = data.seed()
    _, second = data.seed()
    runtime = autonomous.runtime(first["statement"])
    result = runtime.respond(QUESTION, message_id="question", conversation_id="chat")
    selection = runtime.journal.get(result.turn_id)["prepared_memory_selection"]
    assert {first["memory_id"], second["memory_id"]}.issubset({item["memory_id"] for item in selection["memories"]})
    confirm(autonomous.client)
    assert data.access(first) is None and data.access(second) is None


@pytest.mark.parametrize("changes", [
    {"interpretation_hint": "quotation"}, {"interpretation_hint": "hypothetical"},
    {"interpretation_hint": "roleplay"}, {"delivery_state": "draft"}, {"role": "assistant"},
])
def test_non_original_confirmation_context_is_captured_without_reinforcement(autonomous, changes):
    data = autonomous.data
    _, memory = data.seed()
    autonomous.runtime(memory["statement"]).respond(QUESTION, message_id="question", conversation_id="chat")
    assert confirm(autonomous.client, **changes)["accepted_event_ids"]
    assert data.access(memory) is None


def test_imported_confirmation_cannot_gain_credit_even_when_reconsidered_directly(autonomous):
    data = autonomous.data
    _, memory = data.seed()
    autonomous.runtime(memory["statement"]).respond(QUESTION, message_id="question", conversation_id="chat")
    incoming = {
        "producer_ref": "archive-import", "source_event_key": uid(), "source_revision": 1,
        "actor_ref": "user", "actor_role": "user", "event_kind": "message",
        "content": {"media_type": "text/plain", "text": "yes"},
        "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
        "interpretation_hint": "real_world_report", "delivery_state": "received",
    }
    with data.database.transaction(data.principal) as conn:
        brain = data.service._brain(conn, data.principal, data.brain)
        event_id, _, _ = data.service._append_event(conn, data.principal, brain, data.target, incoming, imported=True)
        observe_feedback(conn, data.service, data.principal, brain, data.target, event_id)
    assert data.access(memory) is None


def test_observation_replay_and_feedback_revision_have_independent_idempotency(autonomous):
    data = autonomous.data
    _, memory = data.seed()
    autonomous.runtime(memory["statement"]).respond(QUESTION, message_id="question", conversation_id="chat")
    first = confirm(autonomous.client)
    replay = confirm(autonomous.client)
    assert replay["duplicate_event_ids"] == first["accepted_event_ids"] or replay == first
    assert data.access(memory)["external_uses"] == 1
    confirm(autonomous.client, "wrong", source_revision=2)
    usage = data.access(memory)
    assert (usage["external_uses"], usage["successes"], usage["failures"]) == (1, 0, 1)
    assert usage["last_reinforcement_at"] is None
    rows = data.contributions(memory)
    assert [row["feedback_event_revision"] for row in rows] == [1, 2]
    assert rows[0]["invalidated_at"] is not None and rows[1]["invalidated_at"] is None
    with data.database.transaction(data.principal) as conn:
        brain = data.service._brain(conn, data.principal, data.brain)
        event, = data.service._events(conn, data.principal, brain, event_ids=first["accepted_event_ids"])
    data.invalidate(event, operation="erase")
    assert data.access(memory) is None


@pytest.mark.parametrize("interruption", ["unrelated_user", "other_scope", "erase_source"])
def test_feedback_requires_current_sources_and_immediate_matching_conversation(autonomous, interruption):
    data = autonomous.data
    source, memory = data.seed()
    autonomous.runtime(memory["statement"]).respond(QUESTION, message_id="question", conversation_id="chat")
    if interruption == "unrelated_user":
        data.observe("We are now discussing appointments.")
    if interruption == "erase_source":
        data.invalidate(source, operation="erase")
    if interruption == "other_scope":
        data.observe("yes", conversation="other")
    else:
        confirm(autonomous.client)
    assert data.access(memory) is None


def test_tail_only_selection_does_not_publish_an_assertion_to_create_credit(autonomous):
    data = autonomous.data
    source = data.observe("我喜欢松木香味。")
    runtime = autonomous.runtime(source["observed"]["content"]["text"])
    result = runtime.respond(QUESTION, message_id="question", conversation_id="chat")
    assert runtime.journal.get(result.turn_id)["prepared_memory_selection"]["memories"]
    confirm(autonomous.client)
    with data.database.transaction(data.principal) as conn:
        for table in ("memory_assertions", "memory_feedback_contributions", "memory_access_state"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table} WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                                data.params).fetchone()["n"] == 0
    assert len(autonomous.calls) == len(autonomous.sends) == 1


@pytest.mark.parametrize("summary", ["", "A private rolling summary unrelated to selected source records."])
def test_existing_baseline_runtime_delivers_custom_context_without_automatic_credit(autonomous, summary, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    baseline = importlib.import_module("examples.evaluate_memory_lifecycle").BaselineRuntime
    data = autonomous.data
    _, memory = data.seed()
    calls = []

    def generate(request):
        calls.append(request)
        return memory["statement"]

    runtime = baseline(autonomous.client, generate, deliver=lambda _: DeliveryReceipt("delivered"),
                       frozen_summary=summary, policy=RuntimePolicy(orientation_policy="on_demand"))
    result = runtime.respond(QUESTION, message_id="baseline", conversation_id="chat")
    assert result.stage == "completed" and len(calls) == 1
    assert runtime.journal.get(result.turn_id)["prepared_memory_selection"] == {
        "version": "prepared-memory-selection-v1", "state": "unavailable", "unavailable_reason": "custom_context",
    }
    context = json.loads(calls[0].memory_context)
    assert bool(context["blocks"]) == bool(summary)
    if summary:
        assert context["blocks"][0]["content"]["statement"] == summary
    confirm(autonomous.client)
    assert data.access(memory) is None


def test_custom_prompt_cannot_claim_bound_context_that_actual_messages_omit(autonomous):
    class WithoutMemoryMessage(BrainRuntime):
        def _prompt(self, turn_id, text, packet):
            original = super()._prompt(turn_id, text, packet)
            return replace(original, messages=({"role": "user", "content": text},))

    data = autonomous.data
    _, memory = data.seed()
    runtime = WithoutMemoryMessage(autonomous.client, lambda _: memory["statement"],
                                   deliver=lambda _: DeliveryReceipt("delivered"),
                                   policy=RuntimePolicy(orientation_policy="on_demand"))
    result = runtime.respond(QUESTION, message_id="custom", conversation_id="chat")
    assert result.stage == "completed"
    assert runtime.journal.get(result.turn_id)["prepared_memory_selection"]["unavailable_reason"] == "custom_context"
    confirm(autonomous.client)
    assert data.access(memory) is None


def test_idempotent_generation_recovery_retains_original_binding_after_cache_loss(autonomous):
    data = autonomous.data
    _, memory = data.seed()
    calls = []

    def interrupted(request):
        calls.append(request)
        raise TimeoutError("Generation result was not received")

    policy = RuntimePolicy(orientation_policy="on_demand", recent_context_events=0)
    runtime = BrainRuntime(autonomous.client, interrupted, policy=policy)
    with pytest.raises(TimeoutError):
        runtime.respond(QUESTION, message_id="interrupted", conversation_id="chat")
    row, = runtime.journal.list()
    original = copy.deepcopy(row["prepared_memory_selection"])
    assert row["stage"] == "generating" and original["memories"]
    with data.database.transaction(data.principal) as conn:
        conn.execute("UPDATE memory_requests SET response=NULL WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                     "AND action='prepare'", data.params)

    def resumed(request):
        assert request.request_id == calls[0].request_id and request.messages == calls[0].messages
        return memory["statement"]

    restarted = BrainRuntime(autonomous.client, resumed, deliver=lambda _: DeliveryReceipt("delivered"),
                             generation_is_idempotent=True, policy=policy)
    assert restarted.resume(row["turn_id"]).stage == "completed"
    assert restarted.journal.get(row["turn_id"])["prepared_memory_selection"] == original
    confirm(autonomous.client)
    assert data.access(memory)["external_uses"] == 1


def test_current_memory_revision_must_still_match_actual_prepared_version(autonomous):
    data = autonomous.data
    _, memory = data.seed()
    autonomous.runtime(memory["statement"]).respond(QUESTION, message_id="question", conversation_id="chat")
    replacement = {**copy.deepcopy(memory), "revision": memory["revision"] + 1}
    with data.database.transaction(data.principal) as conn:
        brain = data.service._brain(conn, data.principal, data.brain)
        conn.execute("UPDATE memory_assertions SET is_current=false WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                     "AND id=%s", (*data.params, memory["memory_id"]))
        data.service._publish(conn, data.principal, brain, replacement)
    confirm(autonomous.client)
    assert data.access(memory) is None and data.access(replacement) is None


def test_legacy_manual_turn_does_not_gain_automatic_feedback_permission(autonomous):
    data = autonomous.data
    _, memory = data.seed()
    data.select(memory)
    confirm(autonomous.client)
    assert data.access(memory) is None


def test_bound_feedback_order_uses_durable_stages_and_sequences_across_clock_skew(autonomous, monkeypatch):
    data = autonomous.data
    _, memory = data.seed()
    monkeypatch.setattr(prepared_selection, "now", lambda: "2099-01-01T00:00:00Z")
    runtime = autonomous.runtime(memory["statement"])
    result = runtime.respond(QUESTION, message_id="question", conversation_id="chat")
    assert runtime.journal.get(result.turn_id)["prepared_memory_selection"]["prepared_at"].startswith("2099")
    confirm(autonomous.client)
    assert data.access(memory)["external_uses"] == 1
