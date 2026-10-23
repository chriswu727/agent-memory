import copy

import pytest
from psycopg.types.json import Jsonb

from agent_memory import cognition
from agent_memory.client import default_budget, default_read_fence, integration_capabilities
from agent_memory.config import Settings
from agent_memory.contracts import CONTRACT_VERSION
from agent_memory.dynamics import access_state
from agent_memory.helpers import Principal, digest, now, uid
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


class Feedback:
    def __init__(self, database):
        self.database = database
        self.service = MemoryService(database)
        self.principal = Principal(uid(), uid())
        self.brain = self.service.create_brain(self.principal, "Feedback dynamics integration")["id"]
        self.target = {"brain_ref": self.brain, "conversation_ref": "chat", "branch_ref": "main"}

    @property
    def params(self):
        return self.principal.tenant_id, self.principal.principal_id, self.brain

    def send(self, action, payload, *, conversation="chat", branch="main"):
        return self.service.handle(self.principal, {
            "contract_version": CONTRACT_VERSION, "message_type": "request", "request_id": uid(),
            "action": action, "target": {**self.target, "conversation_ref": conversation, "branch_ref": branch},
            "payload": payload,
        })

    def action(self, action, payload, **scope):
        response = self.send(action, payload, **scope)
        assert response["outcome"] == "completed", response
        return response["result"]

    def observe(self, text, *, role="user", delivery=None, conversation="chat", branch="main",
                source_key=None, revision=1, interpretation="real_world_report", structured=None):
        receipt = self.action("observe", {"idempotency_key": uid(), "events": [{
            "producer_ref": "feedback-tests", "source_event_key": source_key or uid(), "source_revision": revision,
            "actor_ref": role, "actor_role": role, "event_kind": "tool_result" if role == "tool" else "message",
            "content": {"media_type": "application/json", "structured": structured} if structured is not None
            else {"media_type": "text/plain", "text": text},
            "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
            "interpretation_hint": interpretation,
            "delivery_state": delivery or ("delivered" if role == "assistant" else "received"),
        }]}, conversation=conversation, branch=branch)
        with self.database.transaction(self.principal) as conn:
            brain = self.service._brain(conn, self.principal, self.brain)
            return self.service._events(conn, self.principal, brain, event_ids=receipt["accepted_event_ids"])[0]

    def seed(self):
        event = self.observe("我喜欢松木香味。")
        with self.database.transaction(self.principal) as conn:
            brain = self.service._brain(conn, self.principal, self.brain)
            memory = cognition.extract_event(event)[0]
            self.service._publish(conn, self.principal, brain, memory)
        return event, memory

    def recall(self):
        return self.action("recall", {
            "query": "松木香味", "intent": "explore", "cue_event_ids": [], "additional_cues": [],
            "read_fence": default_read_fence(), "budget": default_budget(64000),
        })

    def select(self, memory, *, conversation="chat", branch="main", kind="context_selected", delivered=True):
        event = self.observe("我喜欢哪种香味，松木吗？", conversation=conversation, branch=branch)
        turn_id = uid()
        packet = self.action("prepare", {
            "turn_id": turn_id, "latest_input_event_ids": [event["event_id"]],
            "read_fence": default_read_fence(), "budget": default_budget(64000),
            "capabilities": integration_capabilities(),
        }, conversation=conversation, branch=branch)
        trace = next(item for item in packet["recall_traces"] if item["memory_id"] == memory["memory_id"])
        output = self.observe("你说过喜欢松木香味。", role="assistant", conversation=conversation, branch=branch,
                              delivery="delivered" if delivered else "draft")
        with self.database.transaction(self.principal) as conn:
            conn.execute(
                "INSERT INTO memory_runtime_turns(tenant_id,owner_id,brain_id,turn_id,conversation_id,branch_id,"
                "input_fingerprint,stage,input_event_refs,context_event_refs,output_event_refs) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (*self.params, turn_id, conversation, branch, digest(event["observed"]["content"]),
                 "completed" if delivered else "generated",
                 Jsonb([{"event_id": event["event_id"], "revision": event["revision"]}]),
                 Jsonb([{"event_id": ref["event_id"], "revision": ref["event_revision"]}
                        for ref in memory["evidence_refs"]]),
                 Jsonb([{"event_id": output["event_id"], "revision": output["revision"]}])),
            )
        if kind == "quoted":
            with self.database.transaction(self.principal) as conn:
                trace = {**trace, "kind": kind}
                conn.execute("UPDATE memory_traces SET document=%s WHERE tenant_id=%s AND owner_id=%s "
                             "AND brain_id=%s AND id=%s", (Jsonb(trace), *self.params, trace["trace_id"]))
        return trace

    def proposal(self, memory, recall_id, event=None, *, kind="user_confirmed"):
        return {"memory_id": memory["memory_id"], "recall_id": recall_id, "kind": kind,
                "feedback_event_id": event["event_id"] if event else None,
                "evidence_refs": [{"event_id": event["event_id"], "event_revision": event["revision"],
                                   "source_content_sha256": event["content_sha256"], "relationship": "supports"}]
                if event else []}

    def commit(self, *proposals, conversation="chat", branch="main"):
        with self.database.transaction(self.principal) as conn:
            row = conn.execute("SELECT revision FROM memory_working_states WHERE tenant_id=%s AND owner_id=%s "
                               "AND brain_id=%s AND conversation_id=%s AND branch_id=%s",
                               (*self.params, conversation, branch)).fetchone()
        return self.send("commit", {
            "turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": row["revision"] if row else 0,
            "delivered_event_ids": [], "proposed_items": [], "proposed_open_loops": [],
            "assertion_proposals": [], "recall_feedback": list(proposals),
        }, conversation=conversation, branch=branch)

    def access(self, memory):
        with self.database.transaction(self.principal) as conn:
            return conn.execute("SELECT * FROM memory_access_state WHERE tenant_id=%s AND owner_id=%s "
                                "AND brain_id=%s AND memory_id=%s AND memory_revision=%s",
                                (*self.params, memory["memory_id"], memory["revision"])).fetchone()

    def verified_tool(self, event):
        with self.database.transaction(self.principal) as conn:
            event = {**event, "origin_verification": "verified_producer"}
            conn.execute("UPDATE memory_events SET document=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                         "AND id=%s AND revision=%s", (Jsonb(event), *self.params, event["event_id"], event["revision"]))
        return event

    def invalidate(self, event, *, operation):
        if operation == "edit":
            self.observe("更正，我撤回这段表达。", source_key=event["observed"]["source_event_key"],
                         revision=event["revision"] + 1)
        else:
            with self.database.transaction(self.principal) as conn:
                generation = self.service._brain(conn, self.principal, self.brain)["scope_generation"]
            self.action("forget", {"selector": {"kind": "events", "ids": [event["event_id"]]},
                                   "mode": "erase_content", "expected_scope_generation": generation,
                                   "idempotency_key": uid()})


@pytest.fixture
def feedback():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    return Feedback(Database(settings.database_url))


@pytest.mark.parametrize("trace_kind", ["context_selected", "quoted"])
@pytest.mark.parametrize("kind,text,successes,failures", [
    ("user_confirmed", "对。", 1, 0),
    ("user_corrected", "不对。", 0, 1),
    ("observed_outcome", "香味测试已执行，结果已记录。", 0, 0),
])
def test_only_external_feedback_after_actual_selection_changes_access_and_utility(
    feedback, trace_kind, kind, text, successes, failures,
):
    _, memory = feedback.seed()
    selected = feedback.select(memory, kind=trace_kind)
    assert feedback.access(memory) is None
    event = feedback.observe(text, role="tool" if kind == "observed_outcome" else "user")
    if kind == "observed_outcome":
        event = feedback.verified_tool(event)
    response = feedback.commit(feedback.proposal(memory, selected["recall_id"], event, kind=kind))
    assert response["outcome"] == "completed", response
    usage = feedback.access(memory)
    assert (usage["external_uses"], usage["successes"], usage["failures"]) == (1, successes, failures)
    at = now()
    before, after = access_state(memory, None, at=at), access_state(memory, usage, at=at)
    assert after["activation"] > before["activation"]
    assert after["utility"] == pytest.approx((1 + successes) / (2 + successes + failures))
    with feedback.database.transaction(feedback.principal) as conn:
        stored = conn.execute("SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s "
                              "AND brain_id=%s AND id=%s AND revision=%s",
                              (*feedback.params, memory["memory_id"], memory["revision"])).fetchone()["document"]
    assert stored["dynamics"] == memory["dynamics"]
    assert stored["epistemic_status"] == memory["epistemic_status"]


@pytest.mark.parametrize("mode", ["candidate_hit", "unknown_recall", "wrong_conversation", "wrong_branch"])
def test_feedback_cannot_strengthen_memory_without_selection_in_the_same_recall_and_scope(feedback, mode):
    _, memory = feedback.seed()
    if mode == "candidate_hit":
        recall_id = feedback.recall()["recall_id"]
    else:
        recall_id = feedback.select(memory)["recall_id"]
    conversation = "other" if mode == "wrong_conversation" else "chat"
    branch = "fork" if mode == "wrong_branch" else "main"
    event = feedback.observe("对。", conversation=conversation, branch=branch)
    proposal = feedback.proposal(memory, uid() if mode == "unknown_recall" else recall_id, event)
    response = feedback.commit(proposal, conversation=conversation, branch=branch)
    assert response["outcome"] == "completed", response
    assert feedback.access(memory) is None


def test_model_estimate_and_repeated_candidate_hits_never_create_external_access(feedback):
    _, memory = feedback.seed()
    for _ in range(3):
        recalled = feedback.recall()
        response = feedback.commit(feedback.proposal(memory, recalled["recall_id"], kind="background_influence"))
        assert response["outcome"] == "completed", response
        assert response["result"]["recall_traces"][0]["source"] == "model_estimate"
    assert feedback.access(memory) is None


def test_reintroducing_a_topic_does_not_confirm_the_memory_was_useful(feedback):
    _, memory = feedback.seed()
    selected = feedback.select(memory)
    event = feedback.observe("我又想到松木香味。")
    response = feedback.commit(feedback.proposal(memory, selected["recall_id"], event, kind="user_reintroduced"))
    assert response["outcome"] == "completed", response
    assert response["result"]["recall_traces"][0]["kind"] == "user_reintroduced"
    assert feedback.access(memory) is None


@pytest.mark.parametrize("kind", ["candidate_hit", "context_selected", "quoted"])
def test_client_cannot_forge_internal_selection(feedback, kind):
    _, memory = feedback.seed()
    response = feedback.commit(feedback.proposal(memory, uid(), kind=kind))
    assert response["outcome"] == "rejected", response
    assert response["error"]["code"] == "invalid_evidence"
    assert feedback.access(memory) is None


def test_same_external_event_counts_once_within_batch_across_requests_and_recall_ids(feedback):
    _, memory = feedback.seed()
    first = feedback.select(memory)
    event = feedback.observe("对。")
    proposal = feedback.proposal(memory, first["recall_id"], event)
    assert feedback.commit(proposal, copy.deepcopy(proposal))["outcome"] == "completed"
    assert feedback.commit(proposal)["outcome"] == "completed"
    second = feedback.select(memory)
    feedback.commit(feedback.proposal(memory, second["recall_id"], event))
    usage = feedback.access(memory)
    assert (usage["external_uses"], usage["successes"], usage["failures"], usage["revision"]) == (1, 1, 0, 1)
    with feedback.database.transaction(feedback.principal) as conn:
        applied = conn.execute("SELECT count(*) AS count FROM memory_traces WHERE tenant_id=%s AND owner_id=%s "
                               "AND brain_id=%s AND document->>'feedback_event_id'=%s AND applied_at IS NOT NULL",
                               (*feedback.params, event["event_id"])).fetchone()["count"]
    assert applied == 1


@pytest.mark.parametrize("role", ["assistant", "tool"])
def test_assistant_and_unverified_tool_cannot_confirm_external_use(feedback, role):
    _, memory = feedback.seed()
    selected = feedback.select(memory)
    event = feedback.observe("对。", role=role)
    kind = "observed_outcome" if role == "tool" else "user_confirmed"
    response = feedback.commit(feedback.proposal(memory, selected["recall_id"], event, kind=kind))
    assert response["outcome"] == "rejected", response
    assert feedback.access(memory) is None


def test_verified_structured_tool_result_can_record_external_use(feedback):
    _, memory = feedback.seed()
    selected = feedback.select(memory)
    event = feedback.verified_tool(feedback.observe(None, role="tool", structured={"status": "succeeded"}))
    response = feedback.commit(feedback.proposal(memory, selected["recall_id"], event, kind="observed_outcome"))
    assert response["outcome"] == "completed", response
    usage = feedback.access(memory)
    assert (usage["external_uses"], usage["successes"], usage["failures"]) == (1, 0, 0)


@pytest.mark.parametrize("scope", ["conversation", "branch"])
def test_external_feedback_event_must_belong_to_the_selected_context(feedback, scope):
    _, memory = feedback.seed()
    selected = feedback.select(memory)
    event = feedback.observe("对。", conversation="other" if scope == "conversation" else "chat",
                             branch="fork" if scope == "branch" else "main")
    feedback.commit(feedback.proposal(memory, selected["recall_id"], event))
    assert feedback.access(memory) is None


@pytest.mark.parametrize("text,interpretation,delivery", [
    ("对吗？我喜欢松木香味吗？", "real_world_report", "received"),
    ("对，如果我喜欢松木香味。", "hypothetical", "received"),
    ("对。", "quotation", "received"),
    ("对。", "real_world_report", "draft"),
    ("对不起，我不认可。", "real_world_report", "received"),
])
def test_confirmation_requires_actual_current_expression_not_a_question_quote_or_draft(
    feedback, text, interpretation, delivery,
):
    _, memory = feedback.seed()
    selected = feedback.select(memory)
    event = feedback.observe(text, interpretation=interpretation, delivery=delivery)
    feedback.commit(feedback.proposal(memory, selected["recall_id"], event))
    assert feedback.access(memory) is None


def test_historical_confirmation_cannot_be_attached_to_a_new_recall(feedback):
    _, memory = feedback.seed()
    historical = feedback.observe("对。")
    selected = feedback.select(memory)
    feedback.commit(feedback.proposal(memory, selected["recall_id"], historical))
    assert feedback.access(memory) is None


@pytest.mark.parametrize("mode", ["undelivered", "newer_output", "no_host_turn"])
def test_feedback_requires_the_selected_turn_to_be_the_latest_delivered_response(feedback, mode):
    _, memory = feedback.seed()
    selected = feedback.select(memory, delivered=mode != "undelivered")
    if mode == "newer_output":
        feedback.observe("下一件事是安排今天的任务。", role="assistant")
    if mode == "no_host_turn":
        with feedback.database.transaction(feedback.principal) as conn:
            conn.execute("DELETE FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                         "AND turn_id=%s", (*feedback.params, selected["turn_id"]))
    event = feedback.observe("对。")
    feedback.commit(feedback.proposal(memory, selected["recall_id"], event))
    assert feedback.access(memory) is None


@pytest.mark.parametrize("invalidated_source", ["memory", "feedback"])
@pytest.mark.parametrize("operation", ["edit", "erase"])
def test_source_change_or_erasure_clears_derived_access_state(feedback, invalidated_source, operation):
    source, memory = feedback.seed()
    selected = feedback.select(memory)
    event = feedback.observe("对。")
    assert feedback.commit(feedback.proposal(memory, selected["recall_id"], event))["outcome"] == "completed"
    assert feedback.access(memory)["external_uses"] == 1
    feedback.invalidate(source if invalidated_source == "memory" else event, operation=operation)
    assert feedback.access(memory) is None
