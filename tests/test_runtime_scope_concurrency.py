from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_runtime import memory as memory

from membrain.client import MemoryClient
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked
from membrain.runtime_client import RuntimeClient

pytestmark = pytest.mark.integration


def second_host(memory):
    return MemoryClient("http://testserver", "runtime-test", memory.brain_id,
                        transport=memory._http._transport, trust_env=False)


def test_two_hosts_cannot_capture_or_spend_on_distinct_turns_in_one_scope(memory):
    entered, proceed = Event(), Event()
    generated, sent = [], []

    def generate_first(request):
        generated.append(request.turn_id)
        entered.set()
        assert proceed.wait(20), "The concurrent test did not release its generator"
        return "First host reply."

    def deliver(request):
        sent.append(request.turn_id)
        return DeliveryReceipt("delivered")

    first = BrainRuntime(memory, generate_first, deliver=deliver)
    with second_host(memory) as other:
        second = BrainRuntime(other, lambda request: generated.append(request.turn_id) or "Second host reply.",
                              deliver=deliver)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(first.respond, "First host input.", message_id="one", conversation_id="shared")
            try:
                assert entered.wait(20), "The first host did not reach generation"
                before = RuntimeClient(other).status()
                events_before = other.dashboard()["events"]
                with pytest.raises(RuntimeBlocked) as blocked:
                    second.respond("Second host input.", message_id="two", conversation_id="shared")
                assert blocked.value.code == "scope_conflict"
                after = RuntimeClient(other).status()
                for field in ("durable_sequence", "turn_count", "pending_job_count", "dependency_generation"):
                    assert after[field] == before[field]
                assert other.dashboard()["events"] == events_before
                assert len(generated) == 1 and sent == []
                assert len(RuntimeClient(other).list()) == 1
            finally:
                proceed.set()
            assert result.result(timeout=20).stage == "completed"
        assert second.respond("Second host input.", message_id="two", conversation_id="shared").stage == "completed"
        assert len(generated) == len(sent) == 2


def test_completed_replay_remains_available_while_next_turn_owns_scope(memory):
    calls, sends = [], []

    def generate(request):
        calls.append(request.turn_id)
        return "A durable answer."

    def deliver(request):
        sends.append(request.turn_id)
        return DeliveryReceipt("delivered")

    first = BrainRuntime(memory, generate, deliver=deliver)
    completed = first.respond("First input.", message_id="one", conversation_id="shared")
    with second_host(memory) as other:
        draft = BrainRuntime(other, generate).respond("Second input.", message_id="two", conversation_id="shared")
        assert draft.stage == "generated"
        replay = first.respond("First input.", message_id="one", conversation_id="shared")
        assert replay.replayed and replay.stage == "completed" and replay.turn_id == completed.turn_id
        assert len(calls) == 2 and len(sends) == 1
        with pytest.raises(RuntimeBlocked) as blocked:
            first.respond("Third input.", message_id="three", conversation_id="shared")
        assert blocked.value.code == "scope_conflict"
        assert RuntimeClient(other).get(draft.turn_id)["scope_reserved"] is True


@pytest.mark.parametrize("conversation,branch", [("other", "main"), ("shared", "alternative")])
def test_different_scope_can_reach_generation_while_a_draft_is_pending(memory, conversation, branch):
    first = BrainRuntime(memory, lambda _: "Unsent first answer.")
    draft = first.respond("First input.", message_id="one", conversation_id="shared")
    with second_host(memory) as other:
        independent = BrainRuntime(other, lambda _: "Unsent independent answer.")
        result = independent.respond("Independent input.", message_id="two",
                                     conversation_id=conversation, branch_id=branch)
        assert result.stage == "generated"
        assert first.journal.get(draft.turn_id)["scope_reserved"] is True
        assert independent.journal.get(result.turn_id)["scope_reserved"] is True
