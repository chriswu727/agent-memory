import httpx
import pytest
from test_learning import learning as learning

from agent_memory.runtime import BrainRuntime, DeliveryReceipt
from agent_memory.turns import TurnStore

pytestmark = pytest.mark.integration


def test_external_dependency_change_after_delivery_cannot_commit_stale_orientation(learning, monkeypatch):
    original_finish = TurnStore.finish
    delivered = []

    def change_before_finish(turns, principal, brain_id, turn_id, **kwargs):
        with learning.database.transaction(principal) as conn:
            brain = learning.service._brain(conn, principal, brain_id)
            learning.service._advance(conn, principal, brain)
        return original_finish(turns, principal, brain_id, turn_id, **kwargs)

    def deliver(request):
        delivered.append(request.text)
        return DeliveryReceipt("delivered")

    monkeypatch.setattr(TurnStore, "finish", change_before_finish)
    runtime = BrainRuntime(learning.memory, lambda _: "我们先讨论复习安排。", deliver=deliver)
    with pytest.raises(httpx.HTTPStatusError) as error:
        runtime.respond("我现在要准备复习。", message_id="generation-after-delivery", conversation_id="chat")
    assert error.value.response.status_code == 409
    assert delivered == ["我们先讨论复习安排。"]
    assert learning.working() is None
    assert learning.calls == ["proposal", "verification"]
