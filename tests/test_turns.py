import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from membrain.config import Settings
from membrain.contracts import CONTRACT_VERSION
from membrain.helpers import DomainError, Principal, digest, now, uid
from membrain.service import MemoryService
from membrain.storage import Database
from membrain.turns import TurnStore

pytestmark = pytest.mark.integration


class Turns:
    def __init__(self, database):
        self.database = database
        self.service = MemoryService(database)
        self.store = TurnStore(database)
        self.principal = Principal(uid(), uid())
        self.brain = self.service.create_brain(self.principal, "Lifecycle journal tests")["id"]
        self.turn_id = uid()
        self.conversation = "测试会话"
        self.branch = "main"
        self.row = None
        self.token = None

    def call(self, method, **kwargs):
        return getattr(self.store, method)(self.principal, self.brain, self.turn_id, **kwargs)

    def begin(self, **kwargs):
        values = {"turn_id": self.turn_id, "conversation_id": self.conversation,
                  "branch_id": self.branch, "input_fingerprint": digest({"input": self.turn_id})}
        values.update(kwargs)
        self.row = self.store.begin(self.principal, self.brain, **values)
        return self.row

    def claim(self, **kwargs):
        self.row = self.call("claim", worker_id=uid(), **kwargs)
        self.token = self.row["lease_token"]
        return self.row

    def advance(self, stage, **kwargs):
        self.row = self.call("advance", stage=stage, lease_token=self.token,
                             expected_revision=self.row["revision"], **kwargs)
        return self.row

    def record_output(self, text="Private generated answer.", delivery_state="draft"):
        self.row = self.call("record_output", lease_token=self.token, expected_revision=self.row["revision"],
                             text=text, delivery_state=delivery_state)
        return self.row

    def action(self, action, payload, conversation=None):
        response = self.service.handle(self.principal, {
            "contract_version": CONTRACT_VERSION, "message_type": "request", "request_id": uid(),
            "action": action, "target": {"brain_ref": self.brain,
                "conversation_ref": conversation or self.conversation, "branch_ref": self.branch},
            "payload": payload,
        })
        assert response["outcome"] == "completed", response
        return response["result"]

    def observe(self, text="Private input never belongs in monitoring.", role="user", delivery="received",
                source_key=None, revision=1, conversation=None):
        result = self.action("observe", {"idempotency_key": uid(), "events": [{
            "producer_ref": "turn-test", "source_event_key": source_key or uid(), "source_revision": revision,
            "actor_ref": role, "actor_role": role, "event_kind": "message",
            "content": {"media_type": "text/plain", "text": text},
            "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
            "interpretation_hint": "real_world_report", "delivery_state": delivery,
        }]}, conversation)
        event_id = (result["accepted_event_ids"] + result["duplicate_event_ids"])[0]
        return {"event_id": event_id, "revision": revision}

    def prepare(self, context_refs=None, working_revision=0):
        self.begin()
        self.claim()
        self.advance("captured", input_event_refs=[self.observe()])
        generation = self.store.status(self.principal, self.brain)["dependency_generation"]
        return self.advance("prepared", context_event_refs=context_refs or [], context_fingerprint=digest("context"),
                            context_generation=generation, working_revision=working_revision, context_tokens=50)

    def generate(self, **kwargs):
        self.prepare(**kwargs)
        self.advance("generating")
        self.output_key = uid()
        self.output_ref = self.observe("Private generated answer.", "assistant", "draft", self.output_key)
        return self.advance("generated", output_event_refs=[self.output_ref])

    def deliver(self, **kwargs):
        self.generate(**kwargs)
        self.advance("delivering")
        self.output_ref = self.observe("Private generated answer.", "assistant", "delivered", self.output_key, 2)
        return self.advance("delivered", output_event_refs=[self.output_ref])

    def expire(self):
        with self.database.transaction(self.principal) as conn:
            conn.execute("UPDATE memory_runtime_turns SET lease_expires_at=clock_timestamp()-interval '1 second' "
                         "WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
                         (self.principal.tenant_id, self.brain, self.turn_id))


@pytest.fixture
def turns():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    return Turns(Database(settings.database_url))


def test_begin_is_durable_idempotent_and_binds_input_and_scope(turns):
    first = turns.begin()
    recovered = TurnStore(turns.database).get(turns.principal, turns.brain, turns.turn_id)
    assert recovered == first
    assert turns.begin() == first
    for changed in ({"input_fingerprint": digest("other")}, {"conversation_id": "other"}, {"branch_id": "other"}):
        with pytest.raises(DomainError, match="different input or scope"):
            turns.begin(**changed)
    assert len(turns.call("history")) == 1


def captured_recall(turns):
    turns.begin()
    turns.claim()
    turns.advance("captured", input_event_refs=[turns.observe()])
    source = turns.observe("Private historical detail for recall.", conversation="earlier")
    return {
        "lease_token": turns.token, "expected_revision": turns.row["revision"],
        "input_bytes": 100, "max_model_calls": 2, "max_input_bytes": 200,
        "context_generation": turns.store.status(turns.principal, turns.brain)["dependency_generation"],
        "source_refs": [source], "request_fingerprint": digest("recall request"),
    }


def recall_plan(row):
    return {
        "version": "evidence-recall-v1", "memory_versions": [], "recent_event_refs": [],
        "observed_event_refs": row["context_event_refs"], "outcome": "insufficient",
        "model_calls": row["recall_model_calls"], "search_calls": 0,
        "input_bytes": row["recall_input_bytes"], "elapsed_ms": 10,
        "coverage_limitations": [], "trace": [],
    }


def test_recall_reservation_is_durable_idempotent_and_cas_guarded(turns):
    params = captured_recall(turns)
    first = turns.call("reserve_recall", **params)
    assert first["stage"] == "captured"
    assert first["recall_model_calls"] == 1
    assert first["recall_input_bytes"] == 100
    turns.call("renew", lease_token=turns.token)
    assert turns.call("reserve_recall", **params)["revision"] == first["revision"]
    recovered = TurnStore(turns.database).get(turns.principal, turns.brain, turns.turn_id)
    assert recovered["recall_model_calls"] == 1
    assert recovered["context_event_refs"] == params["source_refs"]
    assert len(turns.call("history")) == 4
    with pytest.raises(DomainError, match="revision has changed"):
        turns.call("reserve_recall", **{**params, "request_fingerprint": digest("different request")})


def test_recall_reservation_budgets_survive_release_and_restart(turns):
    params = captured_recall(turns)
    first = turns.call("reserve_recall", **params)
    turns.call("release", lease_token=turns.token, expected_revision=first["revision"])
    turns.store = TurnStore(turns.database)
    claimed = turns.claim()
    params.update(lease_token=turns.token, expected_revision=claimed["revision"])
    with pytest.raises(DomainError, match="durable turn budget"):
        turns.call("reserve_recall", **{**params, "max_model_calls": 1})
    with pytest.raises(DomainError, match="durable turn budget"):
        turns.call("reserve_recall", **{**params, "input_bytes": 101})
    second = turns.call("reserve_recall", **params)
    assert (second["recall_model_calls"], second["recall_input_bytes"]) == (2, 200)
    assert second["context_event_refs"] == params["source_refs"]
    with pytest.raises(DomainError, match="durable turn budget"):
        turns.call("reserve_recall", **{**params, "expected_revision": second["revision"]})


def test_recall_reservation_replay_revalidates_sources_and_generation(turns):
    params = captured_recall(turns)
    first = turns.call("reserve_recall", **params)
    turns.action("forget", {
        "idempotency_key": uid(), "selector": {"kind": "events", "ids": [params["source_refs"][0]["event_id"]]},
        "mode": "erase_content", "expected_scope_generation": 1,
    })
    with pytest.raises(DomainError) as error:
        turns.call("reserve_recall", **params)
    assert error.value.code in {"context_stale", "source_unavailable"}
    row = turns.call("get")
    assert row["recall_model_calls"] == first["recall_model_calls"]
    assert row["recall_input_bytes"] == first["recall_input_bytes"]


def test_recall_reservation_rejects_changed_context_and_unavailable_refs_without_charge(turns):
    params = captured_recall(turns)
    with pytest.raises(DomainError, match="Memory changed"):
        turns.call("reserve_recall", **{**params, "context_generation": params["context_generation"] + 1})
    with pytest.raises(DomainError, match="no longer available"):
        turns.call("reserve_recall", **{**params, "source_refs": [{"event_id": uid(), "revision": 1}]})
    assert turns.call("get")["recall_model_calls"] == 0


def test_recall_plan_preserves_observed_sources_and_matches_charges(turns):
    params = captured_recall(turns)
    turns.row = turns.call("reserve_recall", **params)
    plan = recall_plan(turns.row)
    fields = {
        "context_event_refs": turns.row["context_event_refs"], "context_fingerprint": digest("context"),
        "context_generation": params["context_generation"], "working_revision": 0,
    }
    with pytest.raises(DomainError, match="final recall plan"):
        turns.advance("prepared", **fields)
    with pytest.raises(DomainError, match="retain observed recall sources"):
        turns.advance("prepared", **{**fields, "context_event_refs": []}, recall_plan=plan)
    for invalid in ({**plan, "model_calls": 0}, {**plan, "input_bytes": 0},
                    {**plan, "observed_event_refs": [{"event_id": uid(), "revision": 1}]}):
        with pytest.raises(DomainError, match="match charged usage"):
            turns.advance("prepared", **fields, recall_plan=invalid)
    with pytest.raises(DomainError, match="Invalid RecallPlan"):
        turns.advance("prepared", **fields, recall_plan={**plan, "reasoning": "Private recollection."})
    prepared = turns.advance("prepared", **fields, recall_plan=plan)
    assert prepared["recall_plan"] == plan
    serialized = json.dumps({"turn": turns.call("get"), "history": turns.call("history")})
    assert "Private" not in serialized
    assert "recall request" not in serialized
    with pytest.raises(DomainError, match="captured turn"):
        turns.call("reserve_recall", **{**params, "expected_revision": prepared["revision"]})


def test_two_workers_cannot_claim_the_same_turn(turns):
    turns.begin()
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        try:
            return turns.call("claim", worker_id=uid())
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("lease_conflict") == 1
    assert turns.call("get")["claim_count"] == 1


def test_lease_fencing_renewal_and_revision_cas(turns):
    turns.begin()
    first = turns.claim()
    renewed = turns.call("renew", lease_token=turns.token, expected_revision=first["revision"], lease_seconds=60)
    assert renewed["revision"] == first["revision"]
    assert len(turns.call("history")) == 2
    turns.expire()
    with pytest.raises(DomainError, match="expired"):
        turns.call("renew", lease_token=turns.token)
    second = turns.claim()
    assert second["fence"] == first["fence"] + 1
    with pytest.raises(DomainError, match="fenced"):
        turns.call("release", lease_token=first["lease_token"], expected_revision=second["revision"])
    with pytest.raises(DomainError, match="revision has changed"):
        turns.call("release", lease_token=second["lease_token"], expected_revision=first["revision"])


def test_repeated_transition_does_not_capture_twice_and_heartbeat_does_not_race_cas(turns):
    turns.begin()
    claimed = turns.claim()
    params = {"lease_token": turns.token, "expected_revision": claimed["revision"],
              "stage": "captured", "input_event_refs": [turns.observe()]}
    first = turns.call("advance", **params)
    turns.call("renew", lease_token=turns.token)
    repeated = turns.call("advance", **params)
    assert repeated["revision"] == first["revision"]
    assert len(turns.call("history")) == 3
    with pytest.raises(DomainError, match="revision has changed"):
        turns.call("advance", **{**params, "input_event_refs": [turns.observe("Different input.")]})


@pytest.mark.parametrize(("stage", "expected"), [("generating", "generation_uncertain"), ("delivering", "delivery_uncertain")])
def test_crash_during_external_side_effect_requires_reconciliation(turns, stage, expected):
    if stage == "generating":
        turns.prepare()
    else:
        turns.generate()
    turns.advance(stage)
    turns.expire()
    assert turns.call("get")["recovery_required"] == expected
    reclaimed = turns.claim()
    assert reclaimed["recovery_required"] == expected
    if stage == "generating":
        with pytest.raises(DomainError, match="advance in order"):
            turns.advance(stage)
    else:
        assert turns.advance(stage)["recovery_required"] is None
    assert turns.call("get")["generation_count"] == 1


def test_generated_draft_can_wait_without_claiming_delivery(turns):
    turns.generate()
    released = turns.call("release", lease_token=turns.token, expected_revision=turns.row["revision"])
    assert released["stage"] == "generated"
    assert released["recovery_required"] is None
    recovered = turns.claim()
    assert recovered["generation_count"] == 1
    source = turns.call("sources")["output_events"][0]
    assert source["observed"]["delivery_state"] == "draft"


def test_generation_and_delivery_stages_require_actual_matching_source_states(turns):
    turns.prepare()
    turns.advance("generating")
    user_source = turns.observe("A user statement cannot become an assistant draft.")
    with pytest.raises(DomainError, match="assistant draft"):
        turns.advance("generated", output_event_refs=[user_source])
    output = turns.observe("Generated draft.", "assistant", "draft")
    turns.advance("generated", output_event_refs=[output])
    turns.advance("delivering")
    with pytest.raises(DomainError, match="actual delivered"):
        turns.advance("delivered", output_event_refs=[output])
    assert turns.call("get")["stage"] == "delivering"


def test_context_can_recall_private_other_conversations_but_inputs_cannot(turns):
    historical = turns.observe("Old conversation source.", conversation="旧会话")
    turns.prepare(context_refs=[historical])
    assert turns.call("get")["sources_available"] is True
    assert turns.call("get")["context_event_refs"] == [historical]
    other = Turns(turns.database)
    other.begin()
    other.claim()
    foreign = other.observe(conversation="foreign")
    with pytest.raises(DomainError, match="outside its scope"):
        other.advance("captured", input_event_refs=[foreign])


def test_cross_branch_context_is_denied(turns):
    turns.branch = "another"
    historical = turns.observe()
    turns.branch = "main"
    with pytest.raises(DomainError, match="outside its scope"):
        turns.prepare(context_refs=[historical])


def test_deleted_context_blocks_resume_and_logs_never_copy_source_content(turns):
    private_text = "This exact secret must never enter a lifecycle journal."
    historical = turns.observe(private_text, conversation="old")
    turns.generate(context_refs=[historical])
    with turns.database.transaction(turns.principal) as conn:
        dependencies = conn.execute("SELECT input_event_id,output_event_id FROM memory_event_dependencies "
                                    "WHERE tenant_id=%s AND brain_id=%s",
                                    (turns.principal.tenant_id, turns.brain)).fetchall()
    assert {"input_event_id": historical["event_id"], "output_event_id": turns.output_ref["event_id"]} in dependencies
    turns.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [historical["event_id"]]},
                            "mode": "erase_content", "expected_scope_generation": 1})
    metadata = turns.call("get")
    assert metadata["sources_available"] is False
    assert historical in metadata["unavailable_event_refs"]
    with pytest.raises(DomainError, match="no longer available"):
        turns.call("sources")
    with pytest.raises(DomainError, match="erased"):
        turns.advance("delivering")
    serialized = json.dumps([metadata, turns.call("history"), turns.store.list(turns.principal, turns.brain)])
    assert private_text not in serialized
    assert "Private generated answer." not in serialized
    assert turns.token not in serialized
    assert "lease_token_digest" not in serialized


def test_stale_source_revision_cannot_be_used_as_current_context(turns):
    historical = turns.observe(source_key="editable")
    turns.observe("Changed message.", source_key="editable", revision=2)
    with pytest.raises(DomainError, match="changed"):
        turns.prepare(context_refs=[historical])


def test_delivery_checks_context_generation_and_working_revision(turns):
    turns.generate()
    turns.action("commit", {"turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
                            "delivered_event_ids": [], "proposed_items": [], "proposed_open_loops": [],
                            "assertion_proposals": []})
    with pytest.raises(DomainError, match="Working state changed"):
        turns.advance("delivering")
    another = Turns(turns.database)
    another.generate()
    unrelated = another.observe(source_key="change")
    another.observe("Edited unrelated source.", source_key="change", revision=2)
    assert unrelated
    with pytest.raises(DomainError, match="Memory changed"):
        another.advance("delivering")


def test_finish_is_atomic_idempotent_and_preserves_working_state(turns):
    item = {"key": "next-task", "value": "Unconfirmed working hypothesis.", "knowledge_state": "unknown",
            "evidence_refs": [], "expires_at": None, "updated_at": now()}
    turns.action("commit", {"turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
                            "delivered_event_ids": [], "proposed_items": [item], "proposed_open_loops": [],
                            "assertion_proposals": []})
    turns.deliver(working_revision=1)
    params = {"lease_token": turns.token, "expected_revision": turns.row["revision"]}
    completed = turns.call("finish", **params)
    assert completed["stage"] == "completed"
    assert completed["working_revision"] == 2
    assert completed["lease_active"] is False
    assert turns.call("finish", **params) == completed
    assert [entry["stage"] for entry in turns.call("history")][-2:] == ["committed", "completed"]
    with turns.database.transaction(turns.principal) as conn:
        saved = conn.execute("SELECT document FROM memory_working_states WHERE tenant_id=%s AND brain_id=%s",
                             (turns.principal.tenant_id, turns.brain)).fetchone()["document"]
    assert saved["revision"] == 2
    assert saved["items"] == [item]
    with pytest.raises(DomainError, match="terminal"):
        turns.claim()


def test_failed_checkpoint_rolls_back_working_commit_and_completion(turns, monkeypatch):
    turns.deliver()
    revision = turns.row["revision"]
    original = MemoryService._checkpoint

    def unavailable(*args):
        raise DomainError("dependency_unavailable", "Injected checkpoint failure.")

    monkeypatch.setattr(MemoryService, "_checkpoint", unavailable)
    with pytest.raises(DomainError, match="Injected"):
        turns.call("finish", lease_token=turns.token, expected_revision=revision)
    assert turns.call("get")["stage"] == "delivered"
    assert turns.call("get")["revision"] == revision
    with turns.database.transaction(turns.principal) as conn:
        assert conn.execute("SELECT revision FROM memory_working_states WHERE tenant_id=%s AND brain_id=%s",
                            (turns.principal.tenant_id, turns.brain)).fetchone() is None
    monkeypatch.setattr(MemoryService, "_checkpoint", original)
    assert turns.call("finish", lease_token=turns.token, expected_revision=revision)["working_revision"] == 1


def test_principal_isolation_and_database_rls(turns):
    turns.begin()
    for principal in (Principal(uid(), uid()), Principal(turns.principal.tenant_id, uid())):
        for method, args in (("get", (turns.turn_id,)), ("list", ()), ("status", ()), ("history", (turns.turn_id,))):
            with pytest.raises(DomainError, match="unavailable"):
                getattr(turns.store, method)(principal, turns.brain, *args)
        with turns.database.transaction(principal) as conn:
            assert conn.execute("SELECT turn_id FROM memory_runtime_turns WHERE brain_id=%s", (turns.brain,)).fetchall() == []
            assert conn.execute("SELECT turn_id FROM memory_runtime_turn_transitions WHERE brain_id=%s", (turns.brain,)).fetchall() == []


def test_metadata_validation_rejects_free_text_and_invalid_transitions(turns):
    with pytest.raises(DomainError, match="whitespace"):
        turns.begin(turn_id="secret arbitrary message")
    with pytest.raises(DomainError, match="SHA-256"):
        turns.begin(input_fingerprint="private content")
    turns.begin()
    turns.claim()
    ref = turns.observe()
    invalid = copy.deepcopy(ref)
    invalid["text"] = "Source content must never enter journal JSON."
    with pytest.raises(DomainError, match="only an identifier"):
        turns.advance("captured", input_event_refs=[invalid])
    with pytest.raises(DomainError, match="Counter"):
        turns.call("release", lease_token=turns.token, expected_revision=None)
    with pytest.raises(DomainError, match="valid advance"):
        turns.advance("completed")
    with pytest.raises(DomainError, match="diagnostic category"):
        turns.call("fail", lease_token=turns.token, expected_revision=turns.row["revision"], failure_code="private error text")
    failed = turns.call("fail", lease_token=turns.token, expected_revision=turns.row["revision"], failure_code="cancelled")
    assert failed["stage"] == "failed"
    assert turns.store.status(turns.principal, turns.brain)["stage_counts"] == {"failed": 1}


def test_output_capture_atomically_binds_sources_and_replays_without_duplicates(turns):
    turns.prepare()
    turns.advance("generating")
    draft_params = {"lease_token": turns.token, "expected_revision": turns.row["revision"],
                    "text": "Draft with private content.", "delivery_state": "draft"}
    draft = turns.call("record_output", **draft_params)
    assert draft["stage"] == "generated"
    assert turns.call("record_output", **draft_params) == draft
    with pytest.raises(DomainError, match="different text"):
        turns.call("record_output", **{**draft_params, "text": "Conflicting output."})
    turns.row = draft
    turns.advance("delivering")
    delivery_params = {"lease_token": turns.token, "expected_revision": turns.row["revision"],
                       "text": "Draft with", "delivery_state": "partially_delivered"}
    delivered = turns.call("record_output", **delivery_params)
    assert delivered["stage"] == "delivered"
    assert turns.call("record_output", **delivery_params) == delivered
    assert turns.call("sources")["output_events"][0]["observed"]["content"]["text"] == "Draft with"
    complete = turns.call("finish", lease_token=turns.token, expected_revision=delivered["revision"])
    assert turns.call("record_output", **draft_params) == complete
    assert turns.call("record_output", **delivery_params) == complete
    assert "Draft with private content." not in json.dumps(turns.call("history"))
    with turns.database.transaction(turns.principal) as conn:
        outputs = conn.execute("SELECT revision,is_current FROM memory_events WHERE tenant_id=%s AND brain_id=%s "
                               "AND document #>> '{observed,actor_role}'='assistant' ORDER BY revision",
                               (turns.principal.tenant_id, turns.brain)).fetchall()
    assert outputs == [{"revision": 1, "is_current": False}, {"revision": 2, "is_current": True}]


@pytest.mark.parametrize("delivery", [False, True])
def test_output_capture_rollback_keeps_source_and_journal_in_the_same_stage(turns, monkeypatch, delivery):
    turns.prepare()
    turns.advance("generating")
    if delivery:
        turns.record_output()
        turns.advance("delivering")
    before = turns.call("get")
    before_status = turns.store.status(turns.principal, turns.brain)
    original = turns.store._record

    def unavailable(*args):
        raise DomainError("dependency_unavailable", "Injected journal write failure.")

    monkeypatch.setattr(turns.store, "_record", unavailable)
    state = "delivered" if delivery else "draft"
    with pytest.raises(DomainError, match="Injected journal"):
        turns.record_output(delivery_state=state)
    assert turns.call("get") == before
    assert turns.store.status(turns.principal, turns.brain) == before_status
    with turns.database.transaction(turns.principal) as conn:
        outputs = conn.execute("SELECT revision FROM memory_events WHERE tenant_id=%s AND brain_id=%s "
                               "AND document #>> '{observed,actor_role}'='assistant'",
                               (turns.principal.tenant_id, turns.brain)).fetchall()
    assert outputs == ([{"revision": 1}] if delivery else [])
    monkeypatch.setattr(turns.store, "_record", original)
    assert turns.record_output(delivery_state=state)["stage"] == ("delivered" if delivery else "generated")


def test_source_correction_racing_output_capture_never_leaves_a_current_stale_draft(turns):
    context = turns.observe("Original fact.", source_key="editable-context", conversation="old")
    turns.prepare(context_refs=[context])
    turns.advance("generating")
    barrier = Barrier(2)

    def capture():
        barrier.wait(timeout=5)
        try:
            return turns.record_output("Output based on the original fact.")
        except DomainError as error:
            return error.code

    def correct():
        barrier.wait(timeout=5)
        return turns.observe("Corrected fact.", source_key="editable-context", revision=2, conversation="old")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(capture)
        second = executor.submit(correct)
        captured, corrected = first.result(timeout=10), second.result(timeout=10)
    assert corrected["revision"] == 2
    assert isinstance(captured, dict) or captured in ("source_unavailable", "context_stale")
    assert turns.call("get")["sources_available"] is False
    with turns.database.transaction(turns.principal) as conn:
        visible = conn.execute("SELECT id FROM memory_events WHERE tenant_id=%s AND brain_id=%s "
                               "AND document #>> '{observed,actor_role}'='assistant' AND is_current "
                               "AND NOT retired AND NOT deleted AND NOT suppressed",
                               (turns.principal.tenant_id, turns.brain)).fetchall()
    assert visible == []


def test_stale_context_generation_or_working_revision_blocks_draft_persistence(turns):
    turns.prepare()
    turns.advance("generating")
    turns.action("commit", {"turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
                            "delivered_event_ids": [], "proposed_items": [], "proposed_open_loops": [],
                            "assertion_proposals": []})
    with pytest.raises(DomainError, match="Working state changed"):
        turns.record_output()
    another = Turns(turns.database)
    another.prepare()
    another.advance("generating")
    another.observe("Unrelated original.", source_key="unrelated")
    another.observe("Unrelated correction.", source_key="unrelated", revision=2)
    with pytest.raises(DomainError, match="Memory changed"):
        another.record_output()
    for runtime in (turns, another):
        assert runtime.call("get")["output_event_refs"] == []
        assert runtime.call("get")["stage"] == "generating"


def test_cancelled_delivery_is_a_durable_terminal_receipt_without_delivered_content(turns):
    turns.prepare()
    turns.advance("generating")
    turns.record_output()
    turns.advance("delivering")
    params = {"lease_token": turns.token, "expected_revision": turns.row["revision"],
              "text": "", "delivery_state": "cancelled"}
    cancelled = turns.call("record_output", **params)
    assert cancelled["stage"] == "failed"
    assert cancelled["failure_code"] == "cancelled"
    assert cancelled["lease_active"] is False
    assert turns.call("record_output", **params) == cancelled
    source = turns.call("sources")["output_events"][0]
    assert source["observed"]["delivery_state"] == "cancelled"
    assert source["observed"]["content"]["text"] == ""


def test_output_capture_validates_utf8_size_and_current_lease_before_writing(turns):
    turns.prepare()
    turns.advance("generating")
    for text in ("", "汉" * 22000):
        with pytest.raises(DomainError, match="bounded text"):
            turns.record_output(text)
    turns.expire()
    with pytest.raises(DomainError, match="expired"):
        turns.record_output()
    assert turns.call("get")["output_event_refs"] == []


@pytest.mark.parametrize(("change", "code"), [
    ("source", "source_unavailable"), ("generation", "context_stale"), ("working", "version_conflict"),
])
def test_exact_delivery_authorization_replay_revalidates_current_state(turns, change, code):
    context = turns.observe("Earlier fact.", source_key="replay-context", conversation="old")
    turns.generate(context_refs=[context])
    params = {"stage": "delivering", "lease_token": turns.token, "expected_revision": turns.row["revision"]}
    authorized = turns.call("advance", **params)
    assert turns.call("advance", **params)["revision"] == authorized["revision"]
    if change == "source":
        turns.observe("Corrected fact.", source_key="replay-context", revision=2, conversation="old")
    elif change == "generation":
        turns.observe("Unrelated fact.", source_key="replay-unrelated")
        turns.observe("Unrelated correction.", source_key="replay-unrelated", revision=2)
    else:
        turns.action("commit", {"turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
                                "delivered_event_ids": [], "proposed_items": [], "proposed_open_loops": [],
                                "assertion_proposals": []})
    with pytest.raises(DomainError) as failure:
        turns.call("advance", **params)
    assert failure.value.code == code
    assert turns.call("get")["revision"] == authorized["revision"]
