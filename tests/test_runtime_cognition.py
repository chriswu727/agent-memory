import copy
import hashlib
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_turns import turns as turns

from membrain.api import create_app
from membrain.client import MemoryClient
from membrain.config import Settings
from membrain.helpers import DomainError, Principal, digest, now, uid
from membrain.runtime import (
    BrainRuntime,
    DeliveryReceipt,
    RuntimeBlocked,
    RuntimePolicy,
    _Lease,
)
from membrain.runtime_client import RuntimeClient


def test_runtime_client_orientation_is_a_leased_turn_operation():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"stage": "captured", "orientation_job_id": "job"})

    with MemoryClient("http://memory.test", "key", "brain", transport=httpx.MockTransport(handle)) as memory:
        result = RuntimeClient(memory).orient("turn", lease_token="lease", expected_revision=3)
    assert result["orientation_job_id"] == "job"
    assert requests[0].url.path == "/v1/brains/brain/runtime/turns/turn/orient"
    assert json.loads(requests[0].content) == {"lease_token": "lease", "expected_revision": 3}


def test_orientation_and_job_monitor_require_authentication_and_redact_content():
    calls = []
    principal = Principal("tenant", "owner")

    def orient(received_principal, brain_id, turn_id, **payload):
        calls.append(("orient", received_principal, brain_id, turn_id, payload))
        return {"turn_id": turn_id, "stage": "captured", "revision": 4, "orientation_job_id": "job",
                "result": {"text": "private-result"}, "lease_token": "private-lease"}

    def job(received_principal, brain_id, job_id):
        calls.append(("get", received_principal, brain_id, job_id))
        return {"id": job_id, "brain_id": brain_id, "kind": "orient", "state": "failed",
                "last_error": "private-provider-error", "model_calls": 1, "input_bytes": 10,
                "payload": {"text": "private-input"}, "result": {"text": "private-result"},
                "lease_token": "private-lease", "hidden_reasoning": "private-reasoning"}

    jobs = SimpleNamespace(get=job, list=lambda p, b, limit: [job(p, b, "job")])
    settings = Settings(database_url="unused", api_keys={hashlib.sha256(b"key").hexdigest(): principal})
    base = "/v1/brains/brain/runtime"
    body = {"lease_token": "lease", "expected_revision": 3}
    headers = {"Authorization": "Bearer key"}
    with TestClient(create_app(settings, service=SimpleNamespace(orient_turn=orient), jobs=jobs)) as client:
        assert client.post(base + "/turns/turn/orient", json=body).status_code == 401
        assert client.get(base + "/jobs").status_code == 401
        assert client.get(base + "/jobs/job").status_code == 401
        assert calls == []
        response = client.post(base + "/turns/turn/orient", json=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["orientation_job_id"] == "job"
        assert calls[-1] == ("orient", principal, "brain", "turn", body)
        for path in ("/jobs?limit=1", "/jobs/job"):
            response = client.get(base + path, headers=headers)
            assert response.status_code == 200
            assert "private-" not in response.text
            assert "internal_error" in response.text
        assert client.get(base + "/jobs?limit=0", headers=headers).status_code == 422
        assert client.post(base + "/turns/turn/orient", json={**body, "job_id": "chosen-by-client"},
                           headers=headers).status_code == 422


class Journal:
    def __init__(self, row):
        self.row = copy.deepcopy(row)
        self.calls = []

    def claim(self, turn_id, **kwargs):
        self.row.update(revision=self.row["revision"] + 1, lease_token="lease")
        return copy.deepcopy(self.row)

    def renew(self, turn_id, **kwargs):
        self.calls.append("renew")
        return copy.deepcopy(self.row)

    def orient(self, turn_id, **kwargs):
        assert kwargs["expected_revision"] == self.row["revision"]
        self.calls.append("orient")
        self.row.update(revision=self.row["revision"] + 1, orientation_job_id="job")
        return copy.deepcopy(self.row)

    def get(self, turn_id):
        return copy.deepcopy(self.row)

    def release(self, turn_id, **kwargs):
        self.calls.append("release")
        return copy.deepcopy(self.row)


def captured(stage="captured"):
    return {"turn_id": "turn", "stage": stage, "revision": 3, "sources_available": True,
            "conversation_id": "chat", "branch_id": "main", "orientation_job_id": None,
            "lease_token": "lease"}


@pytest.mark.parametrize("stage,should_orient", [("captured", True), ("prepared", False), ("generating", False)])
def test_orientation_precedes_generation_and_is_not_replanned_after_preparation(stage, should_orient):
    row = captured(stage)
    journal = Journal(row)
    runtime = BrainRuntime(SimpleNamespace(brain_id="brain"), lambda _: pytest.fail("No model callback"))
    runtime.journal = journal

    def generate(lease, text, scope):
        journal.calls.append("generate")
        assert lease.row["orientation_job_id"] == ("job" if should_orient else None)
        journal.row["stage"] = lease.row["stage"] = "generated"

    runtime._generate = generate
    runtime._output = lambda _: "Durable output"
    result = runtime._run(row, "Current input")
    assert result.stage == "generated"
    assert journal.calls.count("orient") == int(should_orient)
    if should_orient:
        assert journal.calls.index("orient") < journal.calls.index("generate")


def test_captured_orientation_retry_reconciles_the_existing_job():
    row = captured()
    row["orientation_job_id"] = "job"
    journal = Journal(row)
    with _Lease(journal, row, 30) as lease:
        lease.orient()
    assert journal.calls.count("orient") == 1
    assert lease.row["orientation_job_id"] == "job"


def test_generation_retains_sources_seen_by_orientation_but_not_selected_for_prompt():
    runtime = BrainRuntime(SimpleNamespace(brain_id="brain"), lambda _: "Local callback output")
    reference = {"event_id": "orientation-observation", "revision": 2}
    row = {**captured(), "context_event_refs": [reference]}
    prepared = {"packet_id": "prepared-packet", "blocks": [],
                "coverage": {"read_fence_satisfied": True, "dependency_generation": 0},
                "working_state": {"revision": 0}}
    runtime._recall = lambda *args: (prepared, None)
    transitions = []

    def advance(stage, **fields):
        transitions.append((stage, fields))
        row.update(stage=stage, **fields)

    lease = SimpleNamespace(row=row, check=lambda: None, advance=advance,
                            record_output=lambda text, status: None)
    runtime._generate(lease, "Current input", {"conversation_id": "chat", "branch_id": "main"})
    assert transitions[0][0] == "prepared"
    assert transitions[0][1]["context_event_refs"] == [reference]


@pytest.mark.integration
@pytest.mark.parametrize("has_counterexample", [False, True])
def test_default_runtime_recalls_verified_preference_from_a_prior_instruction(learning, has_counterexample):
    from test_semantic import claim, proposals, reference, run

    from membrain.consolidation import publish_semantic
    from membrain.semantic import CognitiveBatch

    receipt = learning.observe("请用简短回答。", conversation="preferences")
    source_ids = receipt["accepted_event_ids"]
    if has_counterexample:
        source_ids += learning.observe("请用详细回答。", conversation="preferences")["accepted_event_ids"]
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        sources = learning.service._events(conn, learning.principal, brain, event_ids=source_ids)
    source = sources[0]
    counterexamples = [reference(sources[1], "contradicts")] if has_counterexample else []
    proposal = claim(source, statement="用户希望回答简短。", conditions={"op": "always"},
                     counterexamples=counterexamples)
    batch = CognitiveBatch(uid(), source["scope"], tuple(sources))
    processed = run(batch, proposals(proposal))
    assert processed.status == "completed"
    assert processed.claims[0]["verification"]["state"] == (
        "contested" if has_counterexample else "supported_inference"
    )
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        published = publish_semantic(conn, learning.service, learning.principal, brain,
                                     batch, processed, job_id=uid())
    version = published["memory_versions"][0]
    learning.service.semantic_processor = None
    prompts = []
    runtime = BrainRuntime(learning.memory, lambda request: prompts.append(request) or "你希望回答简短。",
                           deliver=lambda _: DeliveryReceipt("delivered"),
                           policy=RuntimePolicy(recent_context_events=0))
    result = runtime.respond("我的回答偏好是什么？", message_id="recall", conversation_id="new-chat")
    assert result.stage == "completed"
    view = json.loads(prompts[0].memory_context)
    assert version in view["memory_versions"].values()
    assert proposal["statement"] in prompts[0].memory_context
    assert "请用简短回答。" not in prompts[0].memory_context
    assert {reference["event_id"] for reference in view["sources"].values()} == set(source_ids)


@pytest.mark.integration
@pytest.mark.parametrize("time_bound", ["start", "end"])
@pytest.mark.parametrize("retry_at,should_resume", [
    ("2026-10-25T12:01:00Z", True),
    ("2026-10-25T13:01:00Z", False),
])
def test_semantic_context_recovery_rechecks_validity_without_timestamp_churn(
    learning, monkeypatch, retry_at, should_resume, time_bound,
):
    from test_semantic import claim, proposals, run

    from membrain import cognition
    from membrain.consolidation import publish_semantic
    from membrain.semantic import CognitiveBatch

    clock = ["2026-10-25T12:00:00Z"]
    monkeypatch.setattr(cognition, "now", lambda: clock[0])
    receipt = learning.observe("现在我希望回答简短。", conversation="preferences")
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        source = learning.service._events(conn, learning.principal, brain,
                                         event_ids=receipt["accepted_event_ids"])[0]
    validity = {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"},
                time_bound: {"kind": "instant", "at": "2026-10-25T13:00:00Z"}}
    proposal = claim(source, statement="用户报告希望回答简短。", valid_during=validity)
    batch = CognitiveBatch(uid(), source["scope"], (source,))
    processed = run(batch, proposals(proposal))
    assert processed.status == "completed"
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        published = publish_semantic(conn, learning.service, learning.principal, brain,
                                     batch, processed, job_id=uid())
    assert published["published_count"] == 1
    learning.service.semantic_processor = None
    requests = []

    def uncertain(request):
        requests.append(request)
        assert proposal["statement"] in request.memory_context
        raise TimeoutError("Generation outcome is unknown")

    policy = RuntimePolicy(memory_context_tokens=16000, recent_context_events=0)
    runtime = BrainRuntime(learning.memory, uncertain, policy=policy)
    with pytest.raises(TimeoutError):
        runtime.respond("回答简短", message_id="current", conversation_id="chat")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generating"
    clock[0] = retry_at

    def recover(request):
        requests.append(request)
        assert request.messages == requests[0].messages
        assert request.request_id == requests[0].request_id
        return "好的。"

    resumed = BrainRuntime(learning.memory, recover, policy=policy, generation_is_idempotent=True,
                           deliver=lambda _: DeliveryReceipt("delivered"))
    if should_resume:
        assert resumed.resume(row["turn_id"]).stage == "completed"
        assert len(requests) == 2
    else:
        with pytest.raises(RuntimeBlocked, match="context_stale"):
            resumed.resume(row["turn_id"])
        assert len(requests) == 1


def test_orientation_network_wait_does_not_prevent_lease_renewal():
    row = captured()
    journal = Journal(row)
    renewed_during_call = threading.Event()
    orient_started = threading.Event()
    original_renew = journal.renew

    def renew(*args, **kwargs):
        if orient_started.is_set():
            renewed_during_call.set()
        return original_renew(*args, **kwargs)

    def orient(*args, **kwargs):
        orient_started.set()
        assert renewed_during_call.wait(2), "Orientation blocked the heartbeat"
        return {**row, "revision": 4, "orientation_job_id": "job"}

    journal.renew, journal.orient = renew, orient
    with _Lease(journal, row, 0.03) as lease:
        assert lease.orient()["orientation_job_id"] == "job"


def http_error(status):
    request = httpx.Request("POST", "http://memory.test/renew")
    return httpx.HTTPStatusError("renew", request=request, response=httpx.Response(status, request=request))


@pytest.mark.parametrize("failure", [http_error(503), httpx.ConnectError("refused")])
def test_heartbeat_survives_a_transient_renewal_failure_while_the_lease_is_still_valid(failure):
    # A background learning publication can hold the brain row past the turn's 2-second lock timeout, which the
    # service reports as 503. The 2026-10-27 A/B lost turns permanently after one such renewal.
    row = captured()
    journal = Journal(row)
    original_renew = journal.renew
    outcomes = iter([failure])
    recovered = threading.Event()

    def renew(*args, **kwargs):
        error = next(outcomes, None)
        if error is not None:
            raise error
        recovered.set()
        return original_renew(*args, **kwargs)

    journal.renew = renew
    with _Lease(journal, row, 0.6) as lease:
        assert recovered.wait(2), "The heartbeat stopped after one transient failure"
        assert lease.error is None
        lease.stopped.set()


@pytest.mark.parametrize("failure,seconds", [(http_error(409), 0.6), (http_error(422), 0.6), (http_error(503), 0.06)])
def test_heartbeat_stops_on_a_lease_rejection_or_once_the_lease_must_have_expired(failure, seconds):
    row = captured()
    journal = Journal(row)

    def renew(*args, **kwargs):
        raise failure

    journal.renew = renew
    with _Lease(journal, row, seconds) as lease:
        lease.thread.join(5)
        assert not lease.thread.is_alive() and lease.error is failure
        with pytest.raises(RuntimeBlocked, match="lease_lost"):
            lease.check()


def make_job(turns, *, kind="orient", turn_id=None, brain_id=None):
    job_id = uid()
    with turns.database.transaction(turns.principal) as conn:
        conn.execute(
            "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state) "
            "VALUES(%s,%s,%s,%s,%s,%s,'queued')",
            (turns.principal.tenant_id, turns.principal.principal_id, brain_id or turns.brain,
             job_id, kind, Jsonb({"turn_id": turn_id or turns.turn_id})),
        )
    return job_id


def capture_turn(turns):
    turns.begin()
    turns.claim()
    turns.advance("captured", input_event_refs=[turns.observe()])


def bind(turns, job_id, **changes):
    arguments = {"lease_token": turns.token, "expected_revision": turns.row["revision"], "job_id": job_id}
    arguments.update(changes)
    return turns.call("bind_orientation", **arguments)


@pytest.mark.integration
def test_orientation_binding_is_durable_idempotent_and_cannot_switch_jobs(turns):
    capture_turn(turns)
    job = make_job(turns)
    original_revision = turns.row["revision"]
    turns.row = bind(turns, job)
    assert turns.row["orientation_job_id"] == job
    assert turns.row["revision"] == original_revision + 1
    assert bind(turns, job, expected_revision=original_revision) == turns.row
    assert bind(turns, job) == turns.row
    assert turns.call("get")["orientation_job_id"] == job
    with pytest.raises(DomainError) as error:
        bind(turns, make_job(turns))
    assert error.value.code == "idempotency_conflict"


@pytest.mark.integration
@pytest.mark.parametrize("mismatch", ["kind", "turn", "brain", "lease", "revision"])
def test_orientation_binding_rejects_wrong_scope_or_stale_owner(turns, mismatch):
    capture_turn(turns)
    values = {}
    if mismatch == "kind":
        values["kind"] = "extract"
    elif mismatch == "turn":
        values["turn_id"] = "different-turn"
    elif mismatch == "brain":
        values["brain_id"] = turns.service.create_brain(turns.principal, "Other brain")["id"]
    job = make_job(turns, **values)
    arguments = {}
    if mismatch == "lease":
        turns.expire()
    elif mismatch == "revision":
        arguments["expected_revision"] = turns.row["revision"] - 1
    with pytest.raises(DomainError):
        bind(turns, job, **arguments)
    assert turns.call("get")["orientation_job_id"] is None


@pytest.mark.integration
def test_prepared_turn_cannot_bind_a_new_orientation(turns):
    turns.prepare()
    with pytest.raises(DomainError) as error:
        bind(turns, make_job(turns))
    assert error.value.code == "stage_conflict"


@pytest.mark.integration
def test_orientation_sources_are_durable_and_cannot_be_dropped_before_generation(turns):
    capture_turn(turns)
    job = make_job(turns)
    turns.row = bind(turns, job)
    source = turns.observe("Historical context seen only by orientation.", conversation="earlier")
    with turns.database.transaction(turns.principal) as conn:
        conn.execute("UPDATE memory_jobs SET state='completed' WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (turns.principal.tenant_id, turns.brain, job))
    generation = turns.store.status(turns.principal, turns.brain)["dependency_generation"]
    payload = {"lease_token": turns.token, "expected_revision": turns.row["revision"], "job_id": job,
               "source_refs": [source], "context_generation": generation}
    turns.row = turns.call("record_orientation_sources", **payload)
    assert turns.row["context_event_refs"] == [source]
    assert turns.call("record_orientation_sources", **payload) == turns.row
    with pytest.raises(DomainError) as error:
        turns.advance("prepared", context_event_refs=[], context_fingerprint=digest("context"),
                      context_generation=generation, working_revision=0, context_tokens=50)
    assert error.value.code == "invalid_evidence"


@pytest.mark.integration
@pytest.mark.parametrize("invalid", ["incomplete", "generation", "source_revision"])
def test_orientation_source_binding_rejects_incomplete_or_stale_plans(turns, invalid):
    capture_turn(turns)
    job = make_job(turns)
    turns.row = bind(turns, job)
    if invalid != "incomplete":
        with turns.database.transaction(turns.principal) as conn:
            conn.execute("UPDATE memory_jobs SET state='completed' WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (turns.principal.tenant_id, turns.brain, job))
    source = turns.row["input_event_refs"][0]
    generation = turns.store.status(turns.principal, turns.brain)["dependency_generation"]
    with pytest.raises(DomainError):
        turns.call("record_orientation_sources", lease_token=turns.token,
                   expected_revision=turns.row["revision"], job_id=job,
                   source_refs=[{**source, "revision": 999}] if invalid == "source_revision" else [source],
                   context_generation=generation + 1 if invalid == "generation" else generation)
    assert turns.call("get")["context_event_refs"] == []


@pytest.mark.integration
def test_finish_commits_verified_orientation_updates_without_a_model_call(turns, monkeypatch):
    from membrain import learning

    capture_turn(turns)
    turns.row = bind(turns, make_job(turns))
    source = turns.call("sources")["input_events"][0]
    item = {
        "key": "active-goal", "value": "Continue the current task", "knowledge_state": "inferred",
        "evidence_refs": [{"event_id": source["event_id"], "event_revision": source["revision"],
                           "source_content_sha256": source["content_sha256"], "relationship": "context_only"}],
        "expires_at": None, "updated_at": now(),
    }
    calls = []

    def updates(conn, service, principal, brain, row):
        calls.append(row["orientation_job_id"])
        return [item]

    monkeypatch.setattr(learning, "orientation_updates", updates)
    generation = turns.store.status(turns.principal, turns.brain)["dependency_generation"]
    turns.advance("prepared", context_event_refs=[], context_fingerprint=digest("context"),
                  context_generation=generation, working_revision=0, context_tokens=50)
    turns.advance("generating")
    turns.record_output("Delivered answer")
    turns.advance("delivering")
    turns.record_output("Delivered answer", "delivered")
    result = turns.call("finish", lease_token=turns.token, expected_revision=turns.row["revision"])
    assert result["stage"] == "completed"
    assert calls == [turns.row["orientation_job_id"]]
    with turns.database.transaction(turns.principal) as conn:
        saved = conn.execute(
            "SELECT document FROM memory_working_states WHERE tenant_id=%s AND brain_id=%s "
            "AND conversation_id=%s AND branch_id=%s",
            (turns.principal.tenant_id, turns.brain, turns.conversation, turns.branch),
        ).fetchone()["document"]
    assert saved["items"] == [item]
    replay = turns.call("finish", lease_token=turns.token, expected_revision=turns.row["revision"])
    assert replay["stage"] == "completed"
    assert len(calls) == 1


@pytest.mark.integration
def test_failed_orientation_does_not_block_delivery_completion(turns, monkeypatch):
    from membrain import learning

    capture_turn(turns)
    job = make_job(turns)
    turns.row = bind(turns, job)
    with turns.database.transaction(turns.principal) as conn:
        conn.execute("UPDATE memory_jobs SET state='failed',last_error='provider_failed' "
                     "WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (turns.principal.tenant_id, turns.brain, job))
    monkeypatch.setattr(learning, "orientation_updates", lambda *args: [])
    generation = turns.store.status(turns.principal, turns.brain)["dependency_generation"]
    turns.advance("prepared", context_event_refs=[], context_fingerprint=digest("context"),
                  context_generation=generation, working_revision=0, context_tokens=50)
    turns.advance("generating")
    turns.record_output("Delivered answer")
    turns.advance("delivering")
    turns.record_output("Delivered answer", "delivered")
    assert turns.call("finish", lease_token=turns.token, expected_revision=turns.row["revision"])["stage"] == "completed"
