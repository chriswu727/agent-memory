import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from test_semantic import encoded, proposals, situation, verifier

from membrain.api import create_app
from membrain.client import MemoryActionError, MemoryClient, default_budget
from membrain.config import Settings
from membrain.helpers import Principal, digest, uid
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked
from membrain.runtime_client import RuntimeClient
from membrain.semantic import SemanticProcessor
from membrain.service import MemoryService
from membrain.storage import Database

pytestmark = pytest.mark.integration


class Learning:
    def __init__(self, database):
        self.database = database
        self.principal = Principal(uid(), uid())
        self.calls, self.snapshots = [], []
        self.during_proposal = None
        self.service = MemoryService(database, semantic_processor=SemanticProcessor(self.propose, self.verify))
        self.brain = self.service.create_brain(self.principal, "Cognitive lifecycle integration")["id"]
        self.memory = None

    def propose(self, request):
        self.calls.append(request.phase)
        data = json.loads(request.messages[-1]["content"])
        self.snapshots.append(data)
        if self.during_proposal:
            self.during_proposal(data)
        source = next(item for item in reversed(data["sources"]) if item["observed"]["actor_role"] == "user")
        return encoded(proposals(updates=[situation(source, value=source["observed"]["content"]["text"])]))

    def verify(self, request):
        self.calls.append(request.phase)
        return verifier(request)

    def observe(self, text, *, conversation="chat", branch="main", source_key=None, revision=1):
        return self.memory.observe(
            text, conversation_id=conversation, branch_id=branch, source_event_key=source_key or uid(),
            producer_ref="learning-test", source_revision=revision, interpretation_hint="real_world_report",
        )["result"]

    def capture(self, text="现在我要制定复习计划。", *, conversation="chat", branch="main"):
        journal = RuntimeClient(self.memory)
        turn_id = "turn-" + uid()
        row = journal.begin(turn_id=turn_id, conversation_id=conversation, branch_id=branch,
                            input_fingerprint=digest(text))
        row = journal.claim(turn_id, worker_id=uid(), expected_revision=row["revision"])
        token = row["lease_token"]
        receipt = self.observe(text, conversation=conversation, branch=branch, source_key=turn_id)
        row = journal.advance(turn_id, lease_token=token, expected_revision=row["revision"], stage="captured",
                              input_event_refs=[{"event_id": receipt["accepted_event_ids"][0], "revision": 1}],
                              durable_sequence=receipt["durable_through_sequence"])
        return journal, row, token

    def job(self, job_id):
        with self.database.transaction(self.principal) as conn:
            return conn.execute("SELECT * FROM memory_jobs WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                                (self.principal.tenant_id, self.brain, job_id)).fetchone()

    def working(self, conversation="chat", branch="main"):
        with self.database.transaction(self.principal) as conn:
            row = conn.execute("SELECT document FROM memory_working_states WHERE tenant_id=%s AND brain_id=%s "
                               "AND conversation_id=%s AND branch_id=%s",
                               (self.principal.tenant_id, self.brain, conversation, branch)).fetchone()
            return row["document"] if row else None


@pytest.fixture
def learning():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    harness = Learning(Database(settings.database_url))
    settings = Settings(database_url=settings.database_url, api_keys={
        hashlib.sha256(b"learning-test").hexdigest(): harness.principal,
    })
    with TestClient(create_app(settings, service=harness.service)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path, headers=dict(request.headers),
                                      content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        with MemoryClient("http://testserver", "learning-test", harness.brain,
                          transport=httpx.MockTransport(forward)) as memory:
            harness.memory = memory
            yield harness


def delivered(_):
    return DeliveryReceipt("delivered")


def test_automatic_orientation_enters_current_context_and_commits_inferred_state_after_delivery(learning):
    prompts = []

    def generate(request):
        prompts.append(request)
        assert learning.working() is None
        return "我们先安排今天的复习。"

    runtime = BrainRuntime(learning.memory, generate, deliver=delivered)
    result = runtime.respond("现在我要制定复习计划。", message_id="first", conversation_id="chat")
    assert result.stage == "completed"
    assert learning.calls == ["proposal", "verification"]
    packet = json.loads(prompts[0].memory_context)
    current = [block for block in packet["blocks"] if block["section"] == "current_situation"]
    assert len(current) == 1
    assert current[0]["content"]["use"] == "fallible_current_context_not_historical_evidence"
    state = learning.working()
    assert len(state["items"]) == 1
    item = state["items"][0]
    assert item["key"] == "situation:current_goal"
    assert item["knowledge_state"] == "inferred"
    assert item["value"]["verification"]["not_a_truth_certificate"] is True
    row = runtime.journal.get(result.turn_id)
    job = learning.job(row["orientation_job_id"])
    assert job["state"] == "completed" and job["model_calls"] == 2
    assert set((ref["event_id"], ref["revision"]) for ref in job["result"]["observed_event_refs"]).issubset(
        {(ref["event_id"], ref["revision"]) for ref in row["context_event_refs"]}
    )
    assert runtime.respond("现在我要制定复习计划。", message_id="first", conversation_id="chat").replayed
    assert learning.calls == ["proposal", "verification"]


def test_repeated_orientation_reuses_persisted_plan_and_budget(learning):
    journal, row, token = learning.capture()
    first = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    second = journal.orient(row["turn_id"], lease_token=token, expected_revision=first["revision"])
    assert first["orientation_job_id"] == second["orientation_job_id"]
    assert learning.calls == ["proposal", "verification"]
    job = learning.job(first["orientation_job_id"])
    with learning.database.transaction(learning.principal) as conn:
        count = conn.execute("SELECT count(*) AS count FROM memory_cognitive_operations WHERE tenant_id=%s "
                             "AND brain_id=%s AND job_id=%s",
                             (learning.principal.tenant_id, learning.brain, job["id"])).fetchone()["count"]
    assert count == job["model_calls"] == 2
    assert first["context_event_refs"] == second["context_event_refs"]


def test_resume_reconstructs_prepared_orientation_without_new_model_charges(learning):
    original = []

    def uncertain(request):
        original.append(request)
        raise TimeoutError("Generation outcome uncertain")

    runtime = BrainRuntime(learning.memory, uncertain, deliver=delivered)
    with pytest.raises(TimeoutError):
        runtime.respond("现在我要制定复习计划。", message_id="first", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generating"

    def retry(request):
        assert request.messages == original[0].messages
        assert request.request_id == original[0].request_id
        return "恢复后的回答。"

    resumed = BrainRuntime(learning.memory, retry, deliver=delivered, generation_is_idempotent=True)
    assert resumed.resume(row["turn_id"]).stage == "completed"
    assert learning.calls == ["proposal", "verification"]
    assert learning.job(row["orientation_job_id"])["model_calls"] == 2


def test_provider_failure_is_recorded_without_repeated_calls_or_false_state(learning):
    def fail(_):
        raise TimeoutError("Private provider failure detail")

    learning.during_proposal = fail
    runtime = BrainRuntime(learning.memory, lambda _: "普通回答。", deliver=delivered)
    result = runtime.respond("现在我要制定复习计划。", message_id="first", conversation_id="chat")
    assert result.stage == "completed"
    assert learning.working()["items"] == []
    job = learning.job(runtime.journal.get(result.turn_id)["orientation_job_id"])
    assert job["model_calls"] == 1
    assert job["result"]["status"] == "provider_unavailable"
    assert "Private provider" not in json.dumps(job["result"])
    assert runtime.respond("现在我要制定复习计划。", message_id="first", conversation_id="chat").replayed
    assert learning.calls == ["proposal"]


@pytest.mark.parametrize("allowed_calls", [0, 1])
def test_exhausted_brain_budget_does_not_block_delivery_or_commit_unverified_situation(learning, allowed_calls):
    with learning.database.transaction(learning.principal) as conn:
        conn.execute(
            "INSERT INTO memory_cognitive_budgets(tenant_id,owner_id,brain_id,window_seconds,max_model_calls,max_input_bytes) "
            "VALUES(%s,%s,%s,3600,%s,2000000)",
            (learning.principal.tenant_id, learning.principal.principal_id, learning.brain, allowed_calls),
        )
    prompts = []

    def generate(request):
        prompts.append(request)
        return "先从你当前的问题开始。"

    runtime = BrainRuntime(learning.memory, generate, deliver=delivered)
    result = runtime.respond("现在我要制定复习计划。", message_id="limited", conversation_id="chat")
    assert result.stage == "completed"
    assert learning.calls == ["proposal"][:allowed_calls]
    assert learning.working()["items"] == []
    assert not any(block["section"] == "current_situation" for block in json.loads(prompts[0].memory_context)["blocks"])
    job = learning.job(runtime.journal.get(result.turn_id)["orientation_job_id"])
    assert job["state"] == "completed"
    assert job["result"]["status"] == "budget_exhausted"
    assert job["result"]["situation_updates"] == []
    assert job["model_calls"] == allowed_calls
    assert runtime.respond("现在我要制定复习计划。", message_id="limited", conversation_id="chat").replayed
    assert len(prompts) == 1 and len(learning.calls) == allowed_calls


def test_source_can_change_in_another_transaction_during_provider_call_but_cannot_publish(learning):
    journal, row, token = learning.capture("原先的私密复习安排。")
    changed = []

    def revise(_):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(learning.observe, "修改后的复习安排。", source_key=row["turn_id"], revision=2)
            changed.append(future.result(timeout=5))

    learning.during_proposal = revise
    with pytest.raises(httpx.HTTPStatusError) as error:
        journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    assert error.value.response.status_code == 409
    assert changed
    current = journal.get(row["turn_id"])
    assert not current["sources_available"]
    job = learning.job(current["orientation_job_id"])
    assert job["result"] is None
    assert job["state"] == "superseded"
    assert learning.working() is None


@pytest.mark.parametrize("operation", ["correct", "forget"])
def test_correction_and_erasure_remove_orientation_content_and_prevent_replay(learning, operation):
    journal, row, token = learning.capture("私密暗号 sandalwood-718，今天准备复习。")
    for _ in range(2):
        learning.service.work_once(learning.principal, learning.brain)
    oriented = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    assert "sandalwood-718" in json.dumps(learning.job(oriented["orientation_job_id"])["result"])
    status = journal.status()
    if operation == "forget":
        learning.memory.forget([row["input_event_refs"][0]["event_id"]], kind="events", mode="erase_content",
                               expected_scope_generation=status["scope_generation"])
    else:
        recalled = learning.memory.recall("sandalwood-718", conversation_id="chat", branch_id="main")["result"]
        memory = recalled["items"][0]["memory"]
        correction = learning.observe("更正，我撤回这个私密暗号。")
        learning.memory.correct(memory["memory_id"], expected_revision=memory["revision"],
                                expected_dependency_generation=status["dependency_generation"],
                                correction_event_ids=correction["accepted_event_ids"], operation="retract",
                                conversation_id="chat", branch_id="main")
    job = learning.job(oriented["orientation_job_id"])
    assert job["result"] is None and job["state"] == "superseded"
    with pytest.raises((MemoryActionError, httpx.HTTPStatusError, RuntimeBlocked)):
        learning.memory.prepare(row["turn_id"], conversation_id="chat", branch_id="main",
                                latest_input_event_ids=[row["input_event_refs"][0]["event_id"]])
    monitor = learning.memory._request("GET", f"v1/brains/{learning.brain}/runtime/jobs/{job['id']}")
    assert "sandalwood-718" not in json.dumps(monitor)


def test_orientation_sources_are_local_to_conversation_and_branch(learning):
    learning.observe("另一会话的私密内容。", conversation="other")
    learning.observe("另一个分支的私密内容。", branch="fork")
    runtime = BrainRuntime(learning.memory, lambda _: "回答。", deliver=delivered)
    runtime.respond("主分支当前目标。", message_id="main", conversation_id="chat")
    assert [source["observed"]["content"]["text"] for source in learning.snapshots[-1]["sources"]] == ["主分支当前目标。"]
    runtime.respond("分支当前目标。", message_id="fork", conversation_id="chat", branch_id="fork")
    texts = [source["observed"]["content"]["text"] for source in learning.snapshots[-1]["sources"]]
    assert set(texts) == {"另一个分支的私密内容。", "分支当前目标。"}


@pytest.mark.parametrize("mismatch", ["conversation", "branch", "input"])
def test_prepare_cannot_reuse_another_scope_or_input_with_an_existing_orientation(learning, mismatch):
    journal, row, token = learning.capture("原分支私密情境 juniper-471。")
    journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    conversation = "different" if mismatch == "conversation" else "chat"
    branch = "fork" if mismatch == "branch" else "main"
    receipt = learning.observe("不同的当前输入。", conversation=conversation, branch=branch)
    with pytest.raises(MemoryActionError):
        learning.memory.prepare(row["turn_id"], conversation_id=conversation, branch_id=branch,
                                latest_input_event_ids=receipt["accepted_event_ids"], budget=default_budget(8000))
