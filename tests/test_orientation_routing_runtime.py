import copy
import json

import httpx
import pytest
from test_answer_review import assessment
from test_contextual_recall import CONDITION, UNBOUNDED
from test_learning import learning as learning
from test_learning_windows import jobs
from test_recall_control import decision
from test_semantic import claim, proposals, run

from agent_memory.answer_review import AnswerReviewer
from agent_memory.client import MemoryActionError, default_read_fence
from agent_memory.consolidation import publish_semantic
from agent_memory.helpers import digest, uid
from agent_memory.orientation import orientation_route
from agent_memory.recall_control import RecallController
from agent_memory.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked, RuntimePolicy
from agent_memory.semantic import CognitiveBatch
from agent_memory.turns import TurnStore

pytestmark = pytest.mark.integration


def prepare(learning, row, **changes):
    arguments = {"conversation_id": row["conversation_id"], "branch_id": row["branch_id"],
                 "latest_input_event_ids": [ref["event_id"] for ref in row["input_event_refs"]],
                 "read_fence": default_read_fence(row["durable_sequence"]),
                 "evidence_mode": "prior_record_check", "assess_orientation": True,
                 "recent_event_limit": 0}
    return learning.memory.prepare(row["turn_id"], **(arguments | changes))["result"]


def route_for(packet):
    proof = packet["orientation_assessment"]
    assert proof["decision"] == "skip"
    return orientation_route("on_demand", "skip", proof["reasons"], proof)


def advance_prepared(journal, row, token, packet, route=None):
    references = {(ref["event_id"], ref["event_revision"])
                  for block in packet["blocks"] for ref in block["evidence_refs"]}
    return journal.advance(
        row["turn_id"], lease_token=token, expected_revision=row["revision"], stage="prepared",
        context_event_refs=[{"event_id": key, "revision": revision} for key, revision in sorted(references)],
        context_fingerprint=digest("Prepared context for server route verification"),
        context_generation=packet["coverage"]["dependency_generation"],
        working_revision=packet["working_state"]["revision"], context_tokens=100,
        orientation_route=route_for(packet) if route is None else route,
    )


def unpublished_preference(learning, *, conditional=True):
    receipt = learning.observe("备考期间，我希望回答简短。", conversation="preferences")
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        source = learning.service._events(
            conn, learning.principal, brain, event_ids=receipt["accepted_event_ids"],
        )[0]
    batch = CognitiveBatch(uid(), source["scope"], (source,))
    result = run(batch, proposals(claim(
        source, statement="用户报告在备考期间希望回答简短。" if conditional else "用户希望回答简短。",
        conditions=copy.deepcopy(CONDITION) if conditional else {"op": "always"},
        valid_during=copy.deepcopy(UNBOUNDED),
    )))
    assert result.status == "completed" and len(result.claims) == 1

    def publish():
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            before = {key: brain[key] for key in (
                "scope_generation", "dependency_generation", "durable_sequence", "indexed_sequence",
            )}
            publication = publish_semantic(
                conn, learning.service, learning.principal, brain, batch, result, job_id=uid(),
            )
            current = learning.service._brain(conn, learning.principal, learning.brain)
            assert {key: current[key] for key in before} == before
        assert publication["published_count"] == 1 and not publication["belief_relation_versions"]
        return publication["memory_versions"][0]

    return publish


def adaptive(learning, generate, **kwargs):
    return BrainRuntime(
        learning.memory, generate,
        policy=RuntimePolicy(orientation_policy="on_demand", recent_context_events=0), **kwargs,
    )


@pytest.mark.parametrize("changes", [
    {"conversation_id": "other-conversation"}, {"branch_id": "other-branch"},
    {"latest_input_event_ids": []},
])
def test_server_assessment_requires_bound_input_and_scope(learning, changes):
    _, row, _ = learning.capture()
    with pytest.raises(MemoryActionError) as error:
        prepare(learning, row, **changes)
    assert error.value.code == "invalid_evidence"


def test_server_assessment_rejects_another_turns_valid_input(learning):
    _, first, _ = learning.capture(conversation="first")
    _, second, _ = learning.capture(conversation="second")
    with pytest.raises(MemoryActionError) as error:
        prepare(learning, first, latest_input_event_ids=[second["input_event_refs"][0]["event_id"]])
    assert error.value.code == "invalid_evidence"


def test_server_assessment_rejects_changed_input_revision(learning):
    _, row, _ = learning.capture()
    learning.observe("更正后的当前输入。", source_key=row["turn_id"], revision=2)
    with pytest.raises(MemoryActionError) as error:
        prepare(learning, row)
    assert error.value.code == "invalid_evidence"


@pytest.mark.parametrize("forgery", ["missing_preflight", "altered_proof", "wrong_turn"])
def test_client_cannot_forge_or_transfer_server_skip_authorization(learning, forgery):
    journal, row, token = learning.capture()
    packet = prepare(learning, row)
    route = copy.deepcopy(route_for(packet))
    if forgery == "missing_preflight":
        with learning.database.transaction(learning.principal) as conn:
            conn.execute(
                "UPDATE memory_runtime_turns SET orientation_preflight=NULL "
                "WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
                (learning.principal.tenant_id, learning.brain, row["turn_id"]),
            )
    elif forgery == "altered_proof":
        route["assessment"]["dependency_fingerprint"] = "0" * 64
    else:
        _, other, _ = learning.capture(conversation="other")
        route = route_for(prepare(learning, other))
    with pytest.raises(httpx.HTTPStatusError) as error:
        advance_prepared(journal, row, token, packet, route)
    assert error.value.response.status_code == 409
    assert error.value.response.json()["error"]["code"] == "context_stale"
    assert journal.get(row["turn_id"])["stage"] == "captured"
    assert learning.calls == []


def test_preflight_is_server_internal_and_reading_same_state_does_not_change_turn_revision(learning):
    journal, row, _ = learning.capture()
    first, second = prepare(learning, row), prepare(learning, row)
    assert first["orientation_assessment"] == second["orientation_assessment"]
    public = journal.get(row["turn_id"])
    assert public["revision"] == row["revision"] and "orientation_preflight" not in public
    with learning.database.transaction(learning.principal) as conn:
        private = conn.execute(
            "SELECT orientation_preflight FROM memory_runtime_turns "
            "WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
            (learning.principal.tenant_id, learning.brain, row["turn_id"]),
        ).fetchone()["orientation_preflight"]
    assert private["assessment"] == second["orientation_assessment"]
    assert private["request"]["payload"]["latest_input_event_ids"] == [row["input_event_refs"][0]["event_id"]]
    assert "现在我要制定复习计划" not in json.dumps(private, ensure_ascii=False)


@pytest.mark.parametrize("next_stage", ["prepared", "generating"])
def test_new_conditional_assertion_without_generation_change_invalidates_skip_before_spending(learning, next_stage):
    publish = unpublished_preference(learning)
    journal, row, token = learning.capture("备考回答简短的偏好是什么？")
    packet = prepare(learning, row)
    assert packet["orientation_assessment"]["decision"] == "skip"
    if next_stage == "generating":
        row = advance_prepared(journal, row, token, packet)
    publish()
    with pytest.raises(httpx.HTTPStatusError) as error:
        if next_stage == "prepared":
            advance_prepared(journal, row, token, packet)
        else:
            journal.advance(row["turn_id"], lease_token=token, expected_revision=row["revision"], stage="generating")
    assert error.value.response.status_code == 409
    assert error.value.response.json()["error"]["code"] == "context_stale"
    saved = journal.get(row["turn_id"])
    assert saved["stage"] == row["stage"] and saved["generation_count"] == 0
    assert learning.calls == []


def test_fast_path_preserves_capture_queue_answer_review_and_delivery_without_deriving_goal(learning):
    prompts, reviews, delivered = [], [], []
    text = "现在我要制定复习计划。"
    reviewer = AnswerReviewer(lambda request: reviews.append(request) or json.dumps(assessment()),
                              reviewer_id="routing-test-reviewer")
    runtime = adaptive(
        learning, lambda request: prompts.append(request) or "收到，我们开始制定计划。",
        answer_reviewer=reviewer,
        deliver=lambda request: delivered.append(request.text) or DeliveryReceipt("delivered"),
    )
    result = runtime.respond(text, message_id="fast", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    assert result.stage == "completed" and row["orientation_job_id"] is None
    assert row["orientation_route"]["decision"] == "skip"
    assert row["response_review"]["status"] == "approved" and row["response_review_calls"] == 1
    assert len(prompts) == len(reviews) == len(delivered) == 1 and learning.calls == []
    assert prompts[0].messages[-1] == {"role": "user", "content": text}
    sources = runtime.journal.sources(result.turn_id)
    assert sources["input_events"][0]["observed"]["content"]["text"] == text
    sequence = sources["input_events"][0]["sequence"]
    assert any(job["state"] == "queued" and job["payload"]["from_sequence"] <= sequence
               <= job["payload"]["through_sequence"] for job in jobs(learning))
    assert learning.working()["items"] == [] and learning.working()["open_loops"] == []
    with learning.database.transaction(learning.principal) as conn:
        assert conn.execute("SELECT count(*) AS count FROM memory_jobs WHERE tenant_id=%s "
                            "AND brain_id=%s AND kind='orient'",
                            (learning.principal.tenant_id, learning.brain)).fetchone()["count"] == 0
    replay = runtime.respond(text, message_id="fast", conversation_id="chat")
    assert replay.replayed and len(prompts) == len(reviews) == len(delivered) == 1


def test_unconditional_verified_preference_uses_fast_path_and_remains_in_host_context(learning):
    version = unpublished_preference(learning, conditional=False)()
    prompts = []
    runtime = adaptive(learning, lambda request: prompts.append(request) or "你希望回答简短。")
    result = runtime.respond("回答简短的偏好是什么？", message_id="unconditional", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    assert row["orientation_route"]["decision"] == "skip" and learning.calls == []
    assert version in json.loads(prompts[0].memory_context)["memory_versions"].values()


def test_conditional_memory_forces_orientation_before_host_generation(learning):
    unpublished_preference(learning)()

    def generate(_):
        assert learning.calls == ["proposal", "verification"]
        return "我会根据当前情境回答。"

    runtime = adaptive(learning, generate)
    result = runtime.respond("我正在备考，希望回答简短。", message_id="conditional", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    assert row["orientation_route"]["decision"] == "orient"
    assert "conditional_memory" in row["orientation_route"]["reasons"]
    assert learning.job(row["orientation_job_id"])["state"] == "completed"


def test_conditional_insert_during_host_generation_blocks_delivery_without_answer_reviewer(learning):
    publish = unpublished_preference(learning)
    delivered = []

    def generate(_):
        publish()
        return "将要交付的回答。"

    runtime = adaptive(learning, generate, deliver=lambda request: delivered.append(request.text))
    with pytest.raises(RuntimeBlocked, match="context_stale"):
        runtime.respond("备考回答简短的偏好是什么？", message_id="during-host", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generated" and row["response_review"] is None
    assert row["orientation_route"]["decision"] == "skip" and row["orientation_job_id"] is None
    assert not delivered and learning.calls == []


def test_direct_delivery_cannot_bypass_skip_revalidation_after_new_conditional_claim(learning):
    publish = unpublished_preference(learning)
    runtime = adaptive(learning, lambda _: "当前回答。")
    result = runtime.respond("备考回答简短的偏好是什么？", message_id="before-delivery", conversation_id="chat")
    assert result.stage == "generated"
    publish()
    row = runtime.journal.claim(result.turn_id, worker_id=uid())
    with pytest.raises(httpx.HTTPStatusError) as error:
        runtime.journal.advance(result.turn_id, lease_token=row["lease_token"],
                                expected_revision=row["revision"], stage="delivering")
    assert error.value.response.status_code == 409
    assert error.value.response.json()["error"]["code"] == "context_stale"


def test_prepared_route_policy_cannot_change_on_resume(learning, monkeypatch):
    original = TurnStore.advance

    def stop_before_generation(store, principal, brain_id, turn_id, **kwargs):
        if kwargs["stage"] == "generating":
            raise RuntimeError("Interrupted after prepared")
        return original(store, principal, brain_id, turn_id, **kwargs)

    runtime = adaptive(learning, lambda _: pytest.fail("No generation before interruption"))
    with monkeypatch.context() as patch:
        patch.setattr(TurnStore, "advance", stop_before_generation)
        with pytest.raises(RuntimeError, match="Interrupted after prepared"):
            runtime.respond("你好。", message_id="resume", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "prepared" and row["orientation_route"]["decision"] == "skip"
    changed = BrainRuntime(learning.memory, lambda _: pytest.fail("Policy change must not spend"))
    with pytest.raises(RuntimeBlocked, match="orientation_policy_changed"):
        changed.resume(row["turn_id"])
    recovered = adaptive(learning, lambda _: "你好。")
    assert recovered.resume(row["turn_id"]).stage == "generated" and learning.calls == []


def test_existing_orientation_job_is_resumed_without_selecting_skip_or_repeating_inference(learning):
    journal, row, token = learning.capture()
    row = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    job_id = row["orientation_job_id"]
    assert learning.calls == ["proposal", "verification"]
    journal.release(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    runtime = adaptive(learning, lambda _: "继续处理当前计划。")
    assert runtime.resume(row["turn_id"]).stage == "generated"
    saved = journal.get(row["turn_id"])
    assert saved["orientation_job_id"] == job_id
    assert saved["orientation_route"]["decision"] == "orient"
    assert learning.calls == ["proposal", "verification"]


def test_recall_controller_forces_orientation_even_with_empty_history(learning):
    decisions = []

    def choose(request):
        assert learning.calls == ["proposal", "verification"]
        decisions.append(request)
        return decision(outcome="not_needed")

    runtime = adaptive(learning, lambda _: "我们开始制定计划。", recall_controller=RecallController(choose))
    result = runtime.respond("现在我要制定复习计划。", message_id="controller", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    assert row["orientation_route"]["decision"] == "orient"
    assert row["orientation_route"]["reasons"] == ["recall_controller"]
    assert row["orientation_job_id"] is not None and row["recall_model_calls"] == len(decisions) == 1


def test_existing_working_goal_requires_fresh_orientation(learning):
    first = BrainRuntime(learning.memory, lambda _: "我们开始制定计划。",
                         deliver=lambda _: DeliveryReceipt("delivered"))
    assert first.respond("现在我要制定复习计划。", message_id="goal", conversation_id="chat").stage == "completed"
    assert learning.working()["items"]
    learning.calls.clear()
    runtime = adaptive(learning, lambda _: "我们继续当前计划。")
    result = runtime.respond("现在继续制定复习计划。", message_id="continue", conversation_id="chat")
    row = runtime.journal.get(result.turn_id)
    assert row["orientation_route"]["decision"] == "orient"
    assert "working_state" in row["orientation_route"]["reasons"]
    assert learning.calls == ["proposal", "verification"]


@pytest.mark.parametrize("stage", ["prepared", "generating"])
def test_idempotent_transition_retries_recheck_skip_authorization(learning, stage):
    publish = unpublished_preference(learning)
    journal, captured, token = learning.capture("备考回答简短的偏好是什么？")
    packet = prepare(learning, captured)
    prepared = advance_prepared(journal, captured, token, packet)
    current = prepared
    if stage == "generating":
        current = journal.advance(captured["turn_id"], lease_token=token,
                                  expected_revision=prepared["revision"], stage="generating")
    publish()
    with pytest.raises(httpx.HTTPStatusError) as error:
        if stage == "prepared":
            advance_prepared(journal, captured, token, packet)
        else:
            journal.advance(captured["turn_id"], lease_token=token,
                            expected_revision=prepared["revision"], stage="generating")
    assert error.value.response.status_code == 409
    assert error.value.response.json()["error"]["code"] == "context_stale"
    saved = journal.get(captured["turn_id"])
    assert saved["stage"] == stage and saved["revision"] == current["revision"]
    assert saved["generation_count"] == current["generation_count"]
    assert learning.calls == []


def test_delivered_turn_finishes_after_policy_change_without_duplicate_delivery(learning, monkeypatch):
    delivered = []

    def interrupted(*args, **kwargs):
        raise RuntimeError("Interrupted after delivery")

    runtime = adaptive(learning, lambda _: "你好。",
                       deliver=lambda request: delivered.append(request.text) or DeliveryReceipt("delivered"))
    with monkeypatch.context() as patch:
        patch.setattr(TurnStore, "finish", interrupted)
        with pytest.raises(RuntimeError, match="Interrupted after delivery"):
            runtime.respond("你好。", message_id="delivered-policy", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "delivered" and delivered == ["你好。"]
    recovered = BrainRuntime(learning.memory, lambda _: pytest.fail("Already generated"),
                             deliver=lambda _: pytest.fail("Already delivered"))
    assert recovered.resume(row["turn_id"]).stage == "completed"
    assert delivered == ["你好。"] and learning.calls == []


def test_uncertain_delivery_has_priority_over_changed_orientation_policy(learning):
    attempts = []

    def uncertain(request):
        attempts.append(request.text)
        raise RuntimeError("Transport ended without a receipt")

    runtime = adaptive(learning, lambda _: "你好。", deliver=uncertain)
    with pytest.raises(RuntimeError, match="without a receipt"):
        runtime.respond("你好。", message_id="uncertain-policy", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "delivering"
    recovered = BrainRuntime(learning.memory, lambda _: pytest.fail("Must not regenerate"),
                             deliver=lambda _: pytest.fail("Must not redeliver"))
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        recovered.resume(row["turn_id"])
    assert attempts == ["你好。"] and learning.calls == []
