"""Foreground turns and background learning on one brain at the same time.

In the 2026-10-27 A/B run (evaluations/v1-2026-10-27-ab-baselines.md) the default runtime needed 58 manual
interventions: learning windows ended in context_stale whenever a reply was delivered while they ran, and turns were
lost after one failed lease renewal.
"""
import threading
import time

import pytest
from test_continuous_learning_acceptance import ROUTINES, GroundedCognition, restarted_service
from test_learning import learning as learning
from test_learning_windows import jobs

from membrain.learning import LearningEngine
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimePolicy
from membrain.runtime_client import RuntimeClient

pytestmark = pytest.mark.integration


def respond(memory, text, message_id, conversation, policy):
    runtime = BrainRuntime(memory, lambda _: "I received your report.",
                           deliver=lambda request: DeliveryReceipt("delivered", request.text), policy=policy)
    return runtime.respond(text, message_id=message_id, conversation_id=conversation, branch_id="main")


def generation(harness):
    return harness.service.dashboard(harness.principal, harness.brain)["brain"]["dependency_generation"]


@pytest.mark.parametrize("orientation", ["always", "on_demand"])
@pytest.mark.parametrize("conversation", ["same", "other"])
def test_learning_window_completes_when_a_reply_is_delivered_during_its_model_call(learning, orientation, conversation):
    policy = RuntimePolicy(memory_context_tokens=60000, max_input_tokens=90000, orientation_policy=orientation)
    cognition = GroundedCognition()
    propose = cognition.propose
    state = {"memory": None, "during": None}

    def propose_during_a_turn(request):
        output = propose(request)
        if state["memory"] is not None and state["during"] is None and "learning_input_refs" in request.messages[-1]["content"]:
            state["during"] = respond(state["memory"], ROUTINES[1], "second",
                                      "first" if conversation == "same" else "second", policy).stage
        return output

    cognition.propose = propose_during_a_turn
    with restarted_service(learning, cognition) as memory:
        assert respond(memory, ROUTINES[0], "first", "first", policy).stage == "completed"
        state["memory"] = memory
        engine = LearningEngine(learning.service)
        for _ in range(40):
            if not engine.work_once(learning.principal, learning.brain):
                break
    assert state["during"] == "completed"
    consolidations = [row for row in jobs(learning) if row["kind"] == "consolidate"]
    first_window = min(consolidations, key=lambda row: row["payload"]["from_sequence"])
    assert first_window["state"] == "completed" and first_window["fence"] == 1
    assert all(row["state"] == "completed" and row["last_error"] is None for row in consolidations)


def test_recording_a_delivery_keeps_the_dependency_generation_but_a_source_edit_advances_it(learning):
    before = generation(learning)
    result = respond(learning.memory, "Plan my week.", "plan", "chat", RuntimePolicy(orientation_policy="on_demand"))
    assert result.stage == "completed"
    assert RuntimeClient(learning.memory).sources(result.turn_id)["output_events"][0]["revision"] == 2
    assert generation(learning) == before
    learning.observe("I run on Tuesdays.", source_key="edited", revision=1)
    assert generation(learning) == before
    learning.observe("I run on Thursdays.", source_key="edited", revision=2)
    assert generation(learning) == before + 1


def test_a_brain_row_held_past_the_turn_lock_timeout_is_reported_as_retryable_unavailability(learning):
    journal = RuntimeClient(learning.memory)
    row = journal.begin(turn_id="turn-" + "c" * 40, conversation_id="chat", branch_id="main",
                        input_fingerprint="a" * 64)
    row = journal.claim(row["turn_id"], worker_id="worker", expected_revision=row["revision"])
    held, release = threading.Event(), threading.Event()

    def hold():
        with learning.database.transaction(learning.principal) as conn:
            conn.execute("SELECT id FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s FOR UPDATE",
                         (learning.principal.tenant_id, learning.principal.principal_id, learning.brain))
            held.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        response = learning.memory._http.post(
            f"v1/brains/{learning.brain}/runtime/turns/{row['turn_id']}/renew",
            json={"lease_token": row["lease_token"], "lease_seconds": 30},
        )
        assert response.status_code == 503 and response.json()["error"]["retryable"] is True
        assert time.monotonic() - started < 5
    finally:
        release.set()
        thread.join()
    assert journal.renew(row["turn_id"], lease_token=row["lease_token"], lease_seconds=30)["stage"] == "started"
