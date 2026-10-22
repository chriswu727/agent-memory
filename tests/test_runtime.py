import hashlib
import json
import os

import httpx
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.client import MemoryClient
from agent_memory.config import Settings
from agent_memory.helpers import Principal, uid
from agent_memory.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked, RuntimePolicy
from agent_memory.runtime_client import RuntimeClient

pytestmark = pytest.mark.integration


@pytest.fixture
def memory():
    load_dotenv()
    dsn = os.environ.get("MEMORY_DATABASE_URL")
    if not dsn:
        pytest.skip("A dedicated PostgreSQL database is required")
    settings = Settings(database_url=dsn, api_keys={
        hashlib.sha256(b"runtime-test").hexdigest(): Principal(uid(), uid()),
    })
    with TestClient(create_app(settings)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path,
                                      headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, content=response.content,
                                  headers=dict(response.headers))
        with MemoryClient("http://testserver", "runtime-test", transport=httpx.MockTransport(forward)) as client:
            client.brain_id = client.create_brain("Headless runtime integration")['id']
            yield client


def delivered(request):
    return DeliveryReceipt("delivered")


def test_automatic_turn_recall_and_restart_without_memory_tools(memory):
    prompts, sends = [], []

    def generate(request):
        prompts.append(request)
        return "备考时我们先聊十分钟。"

    def send(request):
        sends.append(request)
        return DeliveryReceipt("delivered")

    runtime = BrainRuntime(memory, generate, deliver=send, instructions="Keep this host instruction unchanged.")
    first = runtime.respond("备考期间，晚上先聊十分钟，再安排复习。", message_id="one", conversation_id="first")
    assert first.stage == "completed"
    assert prompts[0].messages[0]["content"] == "Keep this host instruction unchanged."
    assert prompts[0].messages[-1]["content"] == "备考期间，晚上先聊十分钟，再安排复习。"
    assert runtime.capabilities()["automatic_recall"]
    assert runtime.capabilities()["guarantee_level"] == "partial_automation"
    restarted = BrainRuntime(memory, generate, deliver=send)
    replay = restarted.respond("备考期间，晚上先聊十分钟，再安排复习。", message_id="one", conversation_id="first")
    assert replay.replayed and replay.text == first.text
    assert len(sends) == len(prompts) == 1
    second = restarted.respond("备考时晚上怎么安排？", message_id="two", conversation_id="second")
    assert second.stage == "completed"
    assert "十分钟" in prompts[-1].memory_context
    assert prompts[-1].input_tokens_estimate <= restarted.policy.max_input_tokens
    rows = restarted.journal.list()
    assert len(rows) == 2
    diagnostic = json.dumps([rows, restarted.journal.status(), restarted.journal.history(first.turn_id)])
    assert "十分钟" not in diagnostic
    assert "lease_token" not in diagnostic
    sources = restarted.journal.sources(first.turn_id)
    assert sources["output_events"][0]["observed"]["delivery_state"] == "delivered"


def test_draft_requires_delivery_and_is_not_recalled(memory):
    runtime = BrainRuntime(memory, lambda _: "The undispatched code is alpaca-987.")
    result = runtime.respond("Hello", message_id="one", conversation_id="chat")
    assert result.stage == "generated"
    recalled = memory.recall("alpaca-987", conversation_id="chat", branch_id="main")
    assert "alpaca-987" not in json.dumps(recalled)
    recovered = BrainRuntime(memory, lambda _: pytest.fail("Must use persisted draft"), deliver=delivered)
    assert recovered.resume(result.turn_id).stage == "completed"


def test_uncertain_delivery_never_resends_without_idempotency(memory):
    sends = []

    def send(request):
        sends.append(request)
        return DeliveryReceipt("unknown")

    runtime = BrainRuntime(memory, lambda _: "A reply", deliver=send)
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        runtime.respond("Hello", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "delivering"
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        runtime.resume(row["turn_id"])
    assert len(sends) == 1
    resolved = BrainRuntime(memory, lambda _: pytest.fail("Must not regenerate"),
                            deliver=delivered, delivery_is_idempotent=True)
    assert resolved.resume(row["turn_id"]).stage == "completed"


def test_generation_crash_requires_idempotent_recovery(memory):
    calls = []

    def crash(request):
        calls.append(request.request_id)
        raise TimeoutError("Model response unknown")

    runtime = BrainRuntime(memory, crash, deliver=delivered)
    with pytest.raises(TimeoutError):
        runtime.respond("Hello", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    with pytest.raises(RuntimeBlocked, match="generation_uncertain"):
        runtime.resume(row["turn_id"])
    assert len(calls) == 1

    def recovered(request):
        assert request.request_id == calls[0]
        return "A reply"

    restarted = BrainRuntime(memory, recovered, deliver=delivered, generation_is_idempotent=True)
    assert restarted.resume(row["turn_id"]).stage == "completed"


def test_partial_delivery_only_learns_actual_text(memory):
    runtime = BrainRuntime(memory, lambda _: "Sent sentence. Unsent secret zebra-123.",
                           deliver=lambda _: DeliveryReceipt("partially_delivered", "Sent sentence."))
    result = runtime.respond("Hello", message_id="one", conversation_id="chat")
    assert result.stage == "completed" and result.text == "Sent sentence."
    assert "zebra-123" not in json.dumps(memory.recall("zebra-123", conversation_id="chat", branch_id="main"))


def test_cancelled_delivery_is_terminal_and_never_learned(memory):
    runtime = BrainRuntime(memory, lambda _: "Undelivered answer.",
                           deliver=lambda _: DeliveryReceipt("cancelled"))
    with pytest.raises(RuntimeBlocked, match="delivery_cancelled"):
        runtime.respond("Hello", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "failed"
    with pytest.raises(RuntimeBlocked, match="cancelled"):
        runtime.resume(row["turn_id"])


def test_deletion_during_generation_blocks_delivery_and_output_storage(memory):
    journal = RuntimeClient(memory)

    def generate(_):
        row = journal.list()[0]
        memory.forget([row["input_event_refs"][0]["event_id"]], kind="events", mode="erase_content",
                      expected_scope_generation=journal.status()["scope_generation"])
        return "A stale answer based on erased input."

    runtime = BrainRuntime(memory, generate, deliver=lambda _: pytest.fail("Must not send stale output"))
    with pytest.raises(RuntimeBlocked, match="source_unavailable"):
        runtime.respond("Private source text", message_id="one", conversation_id="chat")
    assert journal.list()[0]["output_event_refs"] == []
    assert "A stale answer" not in json.dumps(memory.dashboard())


def test_erasure_cascades_to_generated_answers_and_archives(memory):
    runtime = BrainRuntime(memory, lambda _: "Private code xylophone-334 confirmed.", deliver=delivered)
    first = runtime.respond("Private code xylophone-334", message_id="one", conversation_id="chat")
    first_row = runtime.journal.get(first.turn_id)
    second = runtime.respond("What is the private code?", message_id="two", conversation_id="chat")
    archive_id = memory.export()["result"]["archive_id"]
    original = memory.download_archive(archive_id)
    assert original["document"]["event_dependencies"]
    memory.forget([first_row["input_event_refs"][0]["event_id"]], kind="events", mode="erase_content",
                  expected_scope_generation=runtime.journal.status()["scope_generation"])
    for result in (first, second):
        with pytest.raises((RuntimeBlocked, httpx.HTTPStatusError)):
            runtime.resume(result.turn_id)
    assert "xylophone-334" not in json.dumps(memory.download_archive(archive_id))
    assert "xylophone-334" not in json.dumps(memory.dashboard())
    for row in runtime.journal.list():
        assert not row["sources_available"]


def test_archives_preserve_dependencies_for_erasure_after_restore(memory):
    runtime = BrainRuntime(memory, lambda _: "Private code magnolia-552.", deliver=delivered)
    result = runtime.respond("Private code magnolia-552", message_id="one", conversation_id="chat")
    source_id = runtime.journal.get(result.turn_id)["input_event_refs"][0]["event_id"]
    archive = memory.download_archive(memory.export()["result"]["archive_id"])
    memory.brain_id = memory.create_brain("Restored brain")["id"]
    uploaded = memory.upload_archive(archive["document"])
    memory.restore(uploaded["archive_id"], expected_manifest_sha256=uploaded["manifest_sha256"],
                   expected_scope_generation=RuntimeClient(memory).status()["scope_generation"], mode="new_brain")
    memory.forget([source_id], kind="events", mode="erase_content",
                  expected_scope_generation=RuntimeClient(memory).status()["scope_generation"])
    assert "magnolia-552" not in json.dumps(memory.dashboard())
    assert "magnolia-552" not in json.dumps(memory.download_archive(archive["archive_id"]))


def test_total_prompt_budget_blocks_model_call(memory):
    runtime = BrainRuntime(memory, lambda _: pytest.fail("Oversized prompt must not reach model"),
                           instructions="x" * 2000, policy=RuntimePolicy(max_input_tokens=1000))
    with pytest.raises(RuntimeBlocked, match="input_budget_exceeded"):
        runtime.respond("Hello", message_id="one", conversation_id="chat")
    assert runtime.journal.list()[0]["stage"] == "captured"


def test_lost_output_response_recovers_without_regeneration(memory, monkeypatch):
    calls = []
    runtime = BrainRuntime(memory, lambda request: calls.append(request) or "Persisted answer.", deliver=delivered)
    original = runtime.journal.record_output

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["delivery_state"] == "draft":
            raise TimeoutError("Draft committed but HTTP response lost")
        return result

    monkeypatch.setattr(runtime.journal, "record_output", lose_response)
    with pytest.raises(TimeoutError):
        runtime.respond("Hello", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generated"
    resumed = BrainRuntime(memory, lambda _: pytest.fail("Must not regenerate"), deliver=delivered)
    assert resumed.resume(row["turn_id"]).stage == "completed"
    assert len(calls) == 1


def test_lost_delivery_record_response_recovers_without_resending(memory, monkeypatch):
    sends = []
    runtime = BrainRuntime(memory, lambda _: "Answer.",
                           deliver=lambda request: sends.append(request) or DeliveryReceipt("delivered"))
    original = runtime.journal.record_output

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["delivery_state"] == "delivered":
            raise TimeoutError("Receipt committed but HTTP response lost")
        return result

    monkeypatch.setattr(runtime.journal, "record_output", lose_response)
    with pytest.raises(TimeoutError):
        runtime.respond("Hello", message_id="one", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "delivered"
    resumed = BrainRuntime(memory, lambda _: pytest.fail("Must not regenerate"),
                            deliver=lambda _: pytest.fail("Must not resend"))
    assert resumed.resume(row["turn_id"]).stage == "completed"
    assert len(sends) == 1


def test_source_edit_retires_generated_output(memory):
    runtime = BrainRuntime(memory, lambda _: "Old value birch-888.", deliver=delivered)
    result = runtime.respond("Old value birch-888", message_id="one", conversation_id="chat")
    memory.observe("New value cedar-999", source_event_key=f"{result.turn_id}:input",
                   producer_ref="brain-runtime", source_revision=2, conversation_id="chat", branch_id="main")
    assert not runtime.journal.get(result.turn_id)["sources_available"]
    assert "birch-888" not in json.dumps(memory.recall("birch-888", conversation_id="chat", branch_id="main"))


def test_archive_filter_checks_dependency_sources_absent_from_archive(memory):
    runtime = BrainRuntime(memory, lambda _: "Source secret tulip-775.", deliver=delivered)
    result = runtime.respond("Source secret tulip-775", message_id="one", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    source_id = row["input_event_refs"][0]["event_id"]
    archive = memory.download_archive(memory.export()["result"]["archive_id"])["document"]
    archive["events"] = [event for event in archive["events"] if event["event_id"] != source_id]
    archive["memories"], archive["working_states"] = [], []
    old_output = archive["events"][0]["event_id"]
    imported_output = "derived-import-" + uid()
    archive["events"][0]["event_id"] = imported_output
    for edge in archive["event_dependencies"]:
        if edge["output_event_id"] == old_output:
            edge["output_event_id"] = imported_output
    memory.forget([source_id], kind="events", mode="erase_content",
                  expected_scope_generation=runtime.journal.status()["scope_generation"])
    uploaded = memory.upload_archive(archive)
    assert "tulip-775" not in json.dumps(memory.download_archive(uploaded["archive_id"]))


def test_erasure_filters_working_only_archive(memory):
    runtime = BrainRuntime(memory, lambda _: "Acknowledged.", deliver=delivered)
    result = runtime.respond("Source secret willow-664", message_id="one", conversation_id="chat")
    archive = memory.download_archive(memory.export()["result"]["archive_id"])["document"]
    source = runtime.journal.sources(result.turn_id)["input_events"][0]
    archive["working_states"][0]["items"] = [{
        "key": "private-note", "value": "Source secret willow-664", "knowledge_state": "grounded",
        "evidence_refs": [{"event_id": source["event_id"], "event_revision": source["revision"],
                           "source_content_sha256": source["content_sha256"], "relationship": "supports"}],
        "expires_at": None, "updated_at": source["system_time"]["recorded_at"],
    }]
    archive["events"], archive["memories"], archive["event_dependencies"] = [], [], []
    uploaded = memory.upload_archive(archive)
    memory.forget([source["event_id"]], kind="events", mode="erase_content",
                  expected_scope_generation=runtime.journal.status()["scope_generation"])
    filtered = memory.download_archive(uploaded["archive_id"])["document"]
    assert filtered["working_states"] == []
    assert "willow-664" not in json.dumps(filtered)
