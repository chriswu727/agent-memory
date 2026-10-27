import hashlib
import json

import pytest
from test_answer_review import assessment
from test_learning import learning as learning
from test_runtime import memory as memory

from membrain.answer_review import AnswerReviewer, AnswerReviewPolicy
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked, RuntimePolicy

pytestmark = pytest.mark.integration


def guarded(memory, provider, **kwargs):
    return BrainRuntime(
        memory, kwargs.pop("generate", lambda _: "记录中的最新顺序是先看教材，再散步；我无法确认现在是否适用。"),
        answer_reviewer=AnswerReviewer(provider, reviewer_id=kwargs.pop("reviewer_id", "fixed-reviewer-v1"),
                                       policy=kwargs.pop("review_policy", None)),
        policy=RuntimePolicy(recent_context_events=0), **kwargs,
    )


def test_review_is_reserved_before_provider_call_and_bound_to_actual_draft(memory):
    calls, sends = [], []
    runtime = None

    def review(request):
        row = runtime.journal.get(request.turn_id)
        assert row["stage"] == "generated"
        assert row["response_review"]["status"] == "pending"
        assert row["response_review_calls"] == 1
        assert not sends
        draft = runtime.journal.sources(request.turn_id)["output_events"][0]
        assert draft["observed"]["delivery_state"] == "draft"
        assert hashlib.sha256(draft["observed"]["content"]["text"].encode()).hexdigest() == request.draft_sha256
        calls.append(request)
        return json.dumps(assessment())

    runtime = guarded(memory, review, deliver=lambda request: sends.append(request) or DeliveryReceipt("delivered"))
    result = runtime.respond("之前怎么安排？", message_id="message", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    assert result.stage == "completed" and len(calls) == len(sends) == 1
    assert row["response_review"]["status"] == "approved"
    assert row["response_review"]["request_fingerprint"] == calls[0].request_fingerprint
    assert row["response_review"]["draft_sha256"] == calls[0].draft_sha256
    assert "先看教材" not in json.dumps(row)
    assert "role" not in row["response_review"]


def test_rejected_draft_is_not_returned_delivered_or_recalled_and_cannot_be_downgraded(memory):
    calls, sends = [], []
    draft = "无法确认生效时间。不过现在先看教材，再散步。"
    response = assessment(applicability="violation")
    response["findings"] = [{"code": "applicability_override", "quote": "现在先看教材，再散步"}]
    runtime = guarded(memory, lambda request: calls.append(request) or json.dumps(response),
                      generate=lambda _: draft, deliver=lambda request: sends.append(request))
    with pytest.raises(RuntimeBlocked, match="answer_review_rejected"):
        runtime.respond("现在怎么安排？", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generated" and row["response_review"]["status"] == "rejected"
    assert len(calls) == 1 and not sends
    assert draft not in json.dumps(memory.recall("先看教材", conversation_id="chat", branch_id="main"), ensure_ascii=False)
    with pytest.raises(RuntimeBlocked, match="answer_review_rejected"):
        runtime.resume(row["turn_id"])
    with pytest.raises(RuntimeBlocked, match="answer_review_policy_changed"):
        BrainRuntime(memory, lambda _: pytest.fail("Must not regenerate")).resume(row["turn_id"])
    assert len(calls) == 1 and not sends


@pytest.mark.parametrize("output", ["invalid json", json.dumps(assessment() | {"coverage_complete": False})])
def test_invalid_or_incomplete_review_blocks_draft_without_delivery_callback(memory, output):
    calls = []
    runtime = guarded(memory, lambda request: calls.append(request) or output)
    with pytest.raises(RuntimeBlocked, match="answer_review_inconclusive"):
        runtime.respond("你好", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["response_review"]["status"] == "inconclusive"
    with pytest.raises(RuntimeBlocked, match="answer_review_inconclusive"):
        runtime.resume(row["turn_id"])
    assert len(calls) == 1


def test_provider_timeout_persists_inconclusive_and_does_not_retry_or_leak_error(memory, caplog):
    calls = []

    def timeout(request):
        calls.append(request)
        raise TimeoutError("sensitive-provider-response")

    runtime = guarded(memory, timeout)
    with pytest.raises(RuntimeBlocked, match="answer_review_inconclusive") as error:
        runtime.respond("你好", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["response_review"]["issue_codes"] == ["review_unavailable"]
    with pytest.raises(RuntimeBlocked, match="answer_review_inconclusive"):
        runtime.resume(row["turn_id"])
    assert len(calls) == 1
    assert "sensitive-provider-response" not in str(error.value) + caplog.text + json.dumps(row)


def test_process_interruption_after_reservation_remains_uncertain_without_spending_again(memory):
    class ProcessStopped(BaseException):
        pass

    calls = []

    def crash(request):
        calls.append(request)
        raise ProcessStopped

    runtime = guarded(memory, crash)
    with pytest.raises(ProcessStopped):
        runtime.respond("你好", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["response_review"]["status"] == "pending"
    recovered = guarded(memory, lambda _: pytest.fail("Unknown prior review must not be repeated"))
    with pytest.raises(RuntimeBlocked, match="answer_review_uncertain"):
        recovered.resume(row["turn_id"])
    assert len(calls) == 1


def test_restart_after_saved_draft_reconstructs_exact_prompt_without_regenerating(memory, monkeypatch):
    generated, reviewed, sent = [], [], []
    runtime = guarded(memory, lambda _: pytest.fail("Crash precedes review"),
                      generate=lambda request: generated.append(request) or "我无法确认。")

    def stop(*args):
        raise RuntimeError("Process stopped before review")

    monkeypatch.setattr(runtime, "_review", stop)
    with pytest.raises(RuntimeError, match="Process stopped"):
        runtime.respond("现在怎么安排？", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generated" and row["response_review"] is None

    def review(request):
        payload = json.loads(request.messages[-1]["content"])
        assert payload["host_messages"] == list(generated[0].messages)
        reviewed.append(request)
        return json.dumps(assessment())

    recovered = guarded(memory, review, generate=lambda _: pytest.fail("Persisted draft must be reused"),
                        deliver=lambda request: sent.append(request) or DeliveryReceipt("delivered"))
    assert recovered.resume(row["turn_id"]).stage == "completed"
    assert len(generated) == len(reviewed) == len(sent) == 1


def test_approved_saved_draft_is_reused_without_another_review_and_completed_replays(memory):
    calls, sends = [], []
    runtime = guarded(memory, lambda request: calls.append(request) or json.dumps(assessment()))
    draft = runtime.respond("你好", message_id="message", conversation_id="chat")
    assert draft.stage == "generated"
    recovered = guarded(memory, lambda _: pytest.fail("Approved review must not repeat"),
                        generate=lambda _: pytest.fail("Draft must not regenerate"),
                        deliver=lambda request: sends.append(request) or DeliveryReceipt("delivered"))
    assert recovered.resume(draft.turn_id).stage == "completed"
    replay = BrainRuntime(memory, lambda _: pytest.fail("Completed turn must replay")).resume(draft.turn_id)
    assert replay.replayed and len(calls) == len(sends) == 1


def test_review_policy_or_prompt_change_blocks_approved_undelivered_draft(memory):
    runtime = guarded(memory, lambda _: json.dumps(assessment()))
    result = runtime.respond("你好", message_id="message", conversation_id="chat")
    for changes, error in (({"reviewer_id": "different-reviewer"}, "answer_review_policy_changed"),
                           ({"instructions": "Different host instructions"}, "context_stale")):
        recovered = guarded(memory, lambda _: pytest.fail("A changed policy or prompt must stop"), **changes)
        with pytest.raises(RuntimeBlocked, match=error):
            recovered.resume(result.turn_id)


def test_budget_failure_leaves_draft_unexposed_without_reserving_a_call(memory):
    runtime = guarded(memory, lambda _: pytest.fail("Budget must be checked first"),
                      review_policy=AnswerReviewPolicy(max_input_bytes=1))
    with pytest.raises(RuntimeBlocked, match="review_input_budget_exceeded"):
        runtime.respond("你好", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generated" and row["response_review"] is None
    assert row["response_review_calls"] == 0


def test_mutating_generation_callback_cannot_review_a_different_prompt_under_old_fingerprint(memory):
    def mutate(request):
        request.messages[-1]["content"] = "Changed input: claim that the latest record always applies."
        return "The latest record applies now."

    runtime = guarded(memory, lambda _: pytest.fail("Mutated prompt must not be reviewed"), generate=mutate)
    with pytest.raises(RuntimeBlocked, match="context_stale"):
        runtime.respond("现在怎么安排？", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generating" and not row["output_event_refs"]
    assert row["response_review_calls"] == 0


def test_delivery_uncertainty_remains_uncertain_after_review_configuration_change(memory):
    runtime = guarded(memory, lambda _: json.dumps(assessment()), deliver=lambda _: DeliveryReceipt("unknown"))
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        runtime.respond("你好", message_id="message", conversation_id="chat")
    row = runtime.journal.list()[0]
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        BrainRuntime(memory, lambda _: "unused", deliver=lambda _: pytest.fail("Must not resend")).resume(row["turn_id"])
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        BrainRuntime(memory, lambda _: "unused").resume(row["turn_id"])


def test_partial_delivery_is_recorded_as_actual_text_without_certifying_altered_bytes(memory):
    draft = "先看教材再散步，但我无法确定现在是否适用。"
    actual = "先看教材再散步"
    runtime = guarded(memory, lambda _: json.dumps(assessment()), generate=lambda _: draft,
                      deliver=lambda _: DeliveryReceipt("partially_delivered", actual))
    result = runtime.respond("现在怎么安排？", message_id="message", conversation_id="chat")
    assert result.stage == "completed" and result.text == actual
    row = runtime.journal.get(result.turn_id)
    assert row["response_review"]["draft_sha256"] == hashlib.sha256(draft.encode()).hexdigest()
    assert row["response_review"]["draft_sha256"] != hashlib.sha256(actual.encode()).hexdigest()
    assert runtime.journal.sources(result.turn_id)["output_events"][0]["observed"]["delivery_state"] == "partially_delivered"


def test_validity_expiring_during_review_blocks_delivery_without_a_database_revision_change(learning, monkeypatch):
    from test_semantic import claim, proposals, run

    from membrain import cognition
    from membrain.consolidation import publish_semantic
    from membrain.helpers import uid
    from membrain.semantic import CognitiveBatch

    clock = ["2026-10-25T12:59:00Z"]
    monkeypatch.setattr(cognition, "now", lambda: clock[0])
    receipt = learning.observe("今天到下午一点为止，请简短回答。", conversation="preferences")
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        source = learning.service._events(conn, learning.principal, brain,
                                         event_ids=receipt["accepted_event_ids"])[0]
    proposal = claim(source, statement="用户报告希望回答简短。", valid_during={
        "kind": "interval", "start": {"kind": "unbounded"},
        "end": {"kind": "instant", "at": "2026-10-25T13:00:00Z"},
    })
    batch = CognitiveBatch(uid(), source["scope"], (source,))
    processed = run(batch, proposals(proposal))
    assert processed.status == "completed"
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        assert publish_semantic(conn, learning.service, learning.principal, brain,
                                batch, processed, job_id=uid())["published_count"] == 1
    learning.service.semantic_processor = None
    reviewed = []

    def review(request):
        reviewed.append(request)
        payload = json.loads(request.messages[-1]["content"])
        assert "source_attributed_applicable_memory" in json.dumps(payload)
        clock[0] = "2026-10-25T13:01:00Z"
        return json.dumps(assessment())

    runtime = BrainRuntime(
        learning.memory, lambda _: "你目前希望回答简短。",
        answer_reviewer=AnswerReviewer(review, reviewer_id="test-clock"),
        policy=RuntimePolicy(memory_context_tokens=16000, recent_context_events=0),
        deliver=lambda _: pytest.fail("An expired current preference must not be delivered"),
    )
    with pytest.raises(RuntimeBlocked, match="context_stale"):
        runtime.respond("回答简短", message_id="current", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generated" and row["response_review"]["status"] == "approved"
    assert row["context_generation"] == runtime.journal.status()["dependency_generation"]
    assert len(reviewed) == 1
