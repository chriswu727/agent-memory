import importlib.util
import json
from pathlib import Path

import pytest
from test_continuous_learning_acceptance import NOTEBOOK, GroundedCognition, restarted_service
from test_learning import learning as learning

from agent_memory.runtime import BrainRuntime, DeliveryReceipt, RuntimePolicy

_SPEC = importlib.util.spec_from_file_location(
    "longitudinal_learning", Path(__file__).parents[1] / "examples" / "longitudinal_learning.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
process_learning = _MODULE.process_learning

pytestmark = pytest.mark.integration


def test_new_model_callback_uses_persisted_history_in_a_new_session_after_reconstruction(learning):
    first_calls, second_calls = [], []

    def first_model(request):
        first_calls.append(request)
        return "I understand where you keep it."

    def second_model(request):
        second_calls.append(request)
        context = json.loads(request.memory_context)
        assert "blue folder" in json.dumps(context)
        assert source_id in json.dumps(context)
        return "You said you keep your study notebook in a blue folder."

    policy = RuntimePolicy(orientation_policy="on_demand", recent_context_events=6)
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        runtime = BrainRuntime(memory, first_model, deliver=lambda _: DeliveryReceipt("delivered"), policy=policy)
        first = runtime.respond(NOTEBOOK, message_id="report", conversation_id="first-session")
        sources = runtime.journal.sources(first.turn_id)
        source_id = sources["input_events"][0]["event_id"]
        processed = process_learning(
            learning.service, learning.principal, learning.brain, conversation_id="first-session",
            current_sources=sources, boundary=lambda: None, record=lambda _: None,
        )
        assert processed["stop_reason"] is None and processed["encoding_coverage"]["complete"]

    with restarted_service(learning, cognition) as memory:
        runtime = BrainRuntime(memory, second_model, deliver=lambda _: DeliveryReceipt("delivered"), policy=policy)
        second = runtime.respond("Where do I keep my study notebook?", message_id="return",
                                 conversation_id="second-session")
        assert second.stage == "completed" and "blue folder" in second.text
        assert len(first_calls) == len(second_calls) == 1
        assert runtime.resume(first.turn_id).replayed
        assert runtime.resume(second.turn_id).replayed
        assert len(first_calls) == len(second_calls) == 1
