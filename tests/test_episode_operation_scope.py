import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb
from test_cognition import event, scope
from test_learning import learning as learning
from test_learning_recovery import seed_legacy_window
from test_learning_windows import claim as claim_job
from test_semantic import batch, encoded, episode, proposals, run, situation, verifier

from agent_memory.helpers import DomainError
from agent_memory.learning import LearningEngine, _learning_inputs
from agent_memory.learning_windows import SAMPLING_RESERVE_BYTES, select_window
from agent_memory.neighborhood import _Incomplete, _Selector
from agent_memory.semantic import (
    COMMUNICATION_INTENT_INPUT_VERSION,
    EPISODE_SCOPE_INPUT_VERSION,
    LATEST_INPUT_CONTRACT_VERSION,
    ORIENT_PROPOSAL_MODE,
    PROPOSAL_SCHEMA,
    SemanticPolicy,
    SemanticProcessor,
    proposal_data,
    request_payload,
)
from agent_memory.service import MemoryService


def ref(source):
    return {"event_id": source["event_id"], "revision": source["revision"]}


def capture(data, response=None):
    calls = []

    def propose(request):
        calls.append(request)
        return encoded(proposals() if response is None else response)

    result = SemanticProcessor(propose, verifier).process(data, reserve=lambda **_: None)
    return calls[0], result


def test_scope_metadata_preserves_history_and_only_exposes_local_episode_members():
    old = event("以前我偏好先散步。", scope=scope(conversation_id="old"))
    current = event("现在改为先看教材。", sequence=2)
    data = batch(old, current, learning_input_refs=(ref(current),))
    request, result = capture(data, proposals(episodes=[
        episode(old, proposal_id="recreated-history"), episode(current, proposal_id="new-local"),
    ]))
    body = json.loads(request.messages[-1]["content"])
    assert body["sources"] == [old, current]
    assert body["episode_operation_scope"] == data.scope
    assert body["episode_eligible_member_refs"] == body["learning_input_refs"] == [ref(current)]
    assert body["current_input_refs"] == [] and body["episodes"] == []
    assert result.status == "partial"
    assert result.rejected == ({"proposal_id": "recreated-history", "reason": "invalid_proposal"},)
    assert [item["proposal_id"] for item in result.episode_actions] == ["new-local"]


@pytest.mark.parametrize("violation", [
    "draft", "cancelled", "unknown", "receipt", "correction", "empty", "hash", "stale", "branch", "conversation",
])
def test_episode_eligible_refs_never_exceed_the_existing_source_gate(violation):
    good, invalid = event("可用来源。"), event("不可选来源。", sequence=2)
    if violation in {"draft", "cancelled", "unknown"}:
        invalid["observed"]["delivery_state"] = violation
    elif violation in {"receipt", "correction"}:
        invalid["observed"]["event_kind"] = "delivery_receipt" if violation == "receipt" else "source_correction"
    elif violation == "empty":
        invalid = event("", sequence=2)
    elif violation == "hash":
        invalid["content_sha256"] = "0" * 64
    elif violation == "stale":
        invalid["system_time"]["superseded_at"] = "2026-10-24T00:00:00Z"
    else:
        invalid["scope"]["branch_id" if violation == "branch" else "conversation_id"] = "other"
    body = proposal_data([invalid, good], (), {}, (), {}, (), scope=scope())
    assert body["sources"] == [invalid, good]
    assert body["episode_eligible_member_refs"] == [ref(good)]


def test_learning_refs_never_authorize_current_request_interpretation():
    source = event("你记得以前去过海边吗？")
    update = situation(source, evidence_use="current_request", value="核实过去是否去过海边。")
    request, result = capture(batch(source, learning_input_refs=(ref(source),)), proposals(updates=[update]))
    body = json.loads(request.messages[-1]["content"])
    assert body["learning_input_refs"] == [ref(source)] and body["current_input_refs"] == []
    assert result.status == "partial" and result.model_calls == 1 and not result.situation_updates


@pytest.mark.parametrize("propose_episode", [False, True])
def test_empty_learning_record_is_processable_but_cannot_supply_episode_evidence(propose_episode):
    source = event("")
    response = proposals(episodes=[episode(source)]) if propose_episode else proposals()
    request, result = capture(batch(source, learning_input_refs=(ref(source),)), response)
    body = json.loads(request.messages[-1]["content"])
    assert body["sources"] == [source] and body["learning_input_refs"] == [ref(source)]
    assert body["episode_eligible_member_refs"] == [] and not result.episode_actions
    assert result.model_calls == 1
    assert result.status == ("partial" if propose_episode else "completed")
    if propose_episode:
        assert result.rejected == ({"proposal_id": "episode-1", "reason": "invalid_proposal"},)


@pytest.mark.parametrize("violation", [
    "extra_key", "bool", "float", "missing", "duplicate", "foreign", "draft", "receipt", "current", "orient", "legacy",
])
def test_invalid_learning_metadata_is_rejected_before_spending(violation):
    source = event("当前学习内容。")
    references = [ref(source)]
    changes = {}
    if violation == "extra_key":
        references[0]["authority"] = "current_request"
    elif violation in {"bool", "float", "missing"}:
        references[0]["revision"] = {"bool": True, "float": 1.0, "missing": 2}[violation]
    elif violation == "duplicate":
        references *= 2
    elif violation == "foreign":
        source["scope"]["conversation_id"] = "other"
    elif violation == "draft":
        source["observed"]["delivery_state"] = "draft"
    elif violation == "receipt":
        source["observed"]["event_kind"] = "delivery_receipt"
    elif violation == "current":
        changes["current_input_refs"] = tuple(references)
    elif violation == "orient":
        changes["proposal_mode"] = ORIENT_PROPOSAL_MODE
    else:
        changes["input_contract_version"] = None
    with pytest.raises(DomainError, match="[Ll]earning"):
        run(batch(source, learning_input_refs=references, **changes), proposals(),
            reserve=lambda **_: pytest.fail("Malformed learning references must not spend"))


def test_legacy_contract_preserves_frozen_prompt_and_has_no_new_fields():
    source = event("历史输入。")
    legacy, _ = capture(batch(source, input_contract_version=None))
    body = json.loads(legacy.messages[-1]["content"])
    assert set(body) == {"sources", "existing_assertions", "working_state", "episodes", "sampling", "registry",
                         "belief_relations", "consolidation_plan", "current_input_refs"}
    assert hashlib.sha256(legacy.messages[0]["content"].encode()).hexdigest() == (
        "01b4bb0211bfd4a65a33bfcbb5834ae75c9802439d5d7c38afbaa3fb52da3bf9")
    current, _ = capture(batch(source))
    assert legacy.request_id != current.request_id and legacy.messages != current.messages
    expected, _ = request_payload("proposal", body, PROPOSAL_SCHEMA, legacy.max_output_tokens,
                                  input_contract_version=None)
    assert legacy.messages == expected


def test_legacy_orientation_wire_contract_does_not_change_with_episode_input_version():
    source = event("现在我要制定计划。")
    oriented = batch(source, proposal_mode=ORIENT_PROPOSAL_MODE, current_input_refs=(ref(source),),
                     input_contract_version=EPISODE_SCOPE_INPUT_VERSION)
    current, _ = capture(oriented)
    legacy, _ = capture(replace(oriented, input_contract_version=None))
    assert current.messages == legacy.messages and current.request_id == legacy.request_id
    assert not {"episode_operation_scope", "episode_eligible_member_refs", "learning_input_refs"} & json.loads(
        current.messages[-1]["content"]).keys()


def window_snapshot():
    source = event("本次窗口的输入。", sequence=7)
    snapshot = {"scope": scope(), "input_contract_version": EPISODE_SCOPE_INPUT_VERSION,
                "learning_input_refs": [ref(source)], "learning_window": {
                    "from_sequence": 7, "through_sequence": 9, "attempted_through_sequence": 8, "input_count": 1}}
    job = {"kind": "consolidate", "payload": {"from_sequence": 7, "through_sequence": 9,
            "target": {"conversation_ref": "conversation", "branch_ref": "main"}}}
    return snapshot, job, [source]


@pytest.mark.parametrize("violation", [
    "no_window", "job_kind", "job_range", "target", "window_range", "attempted", "bool_count", "count",
    "missing", "revision", "foreign", "branch", "sequence", "draft", "receipt", "current", "extra_key",
])
def test_frozen_learning_refs_require_the_exact_local_window(violation):
    snapshot, job, sources = window_snapshot()
    if violation == "no_window":
        snapshot.pop("learning_window")
    elif violation == "job_kind":
        job["kind"] = "orient"
    elif violation == "job_range":
        job["payload"]["from_sequence"] = 6
    elif violation == "target":
        job["payload"]["target"]["conversation_ref"] = "other"
    elif violation == "window_range":
        snapshot["learning_window"]["through_sequence"] = 10
    elif violation == "attempted":
        snapshot["learning_window"]["attempted_through_sequence"] = 10
    elif violation in {"count", "bool_count"}:
        snapshot["learning_window"]["input_count"] = 2 if violation == "count" else True
    elif violation == "missing":
        sources = []
    elif violation == "revision":
        snapshot["learning_input_refs"][0]["revision"] = True
    elif violation in {"foreign", "branch"}:
        sources[0]["scope"]["conversation_id" if violation == "foreign" else "branch_id"] = "other"
    elif violation == "sequence":
        sources[0]["sequence"] = 9
    elif violation == "draft":
        sources[0]["observed"]["delivery_state"] = "draft"
    elif violation == "receipt":
        sources[0]["observed"]["event_kind"] = "delivery_receipt"
    elif violation == "current":
        snapshot["current_input_refs"] = copy.deepcopy(snapshot["learning_input_refs"])
    else:
        snapshot["learning_input_refs"][0]["authority"] = "current_request"
    with pytest.raises(DomainError, match="Learning input window"):
        _learning_inputs(snapshot, job, sources)


def test_legacy_or_missing_learning_refs_are_never_inferred_from_sources():
    snapshot, job, sources = window_snapshot()
    assert _learning_inputs(snapshot, job, sources) == (ref(sources[0]),)
    legacy = {key: value for key, value in snapshot.items() if key != "input_contract_version"}
    assert _learning_inputs(legacy, job, sources) == ()
    snapshot.pop("learning_input_refs")
    assert _learning_inputs(snapshot, job, sources) == ()


class BudgetSources(MemoryService):
    def __init__(self, sources):
        self.sources = sources

    def execute(self, *_):
        return SimpleNamespace(fetchall=lambda: [
            {"id": source["event_id"], "revision": source["revision"], "sequence": source["sequence"], "document": source}
            for source in self.sources])

    def _params(self, *_):
        return "tenant", "owner", "brain"

    def _scope(self, *_):
        return scope()

    def _superseded_evidence(self, *_):
        return []

    def _rebind(self, document, *_):
        return copy.deepcopy(document)

    def _working(self, *_):
        return {"scope": scope(), "items": [], "open_loops": [], "revision": 0,
                "through_sequence": 0, "updated_at": "2026-10-24T00:00:00Z"}


@pytest.mark.parametrize("limiting_field", ["max_request_bytes", "max_input_bytes"])
def test_long_learning_ids_are_counted_before_selecting_a_processable_window(limiting_field):
    sources = [event("A complete learning record.", sequence=index, event_id=str(index) + "x" * 255)
               for index in range(1, 9)]
    fake = BudgetSources(sources)
    working, policy = fake._working(), SemanticPolicy()
    underestimated = proposal_data(sources, (), working, (), {}, ())
    before = request_payload("proposal", underestimated, PROPOSAL_SCHEMA, policy.max_output_tokens)[1]
    maximum = before + SAMPLING_RESERVE_BYTES
    policy = replace(policy, **{limiting_field: maximum})
    payload = {"from_sequence": 1, "through_sequence": 8,
               "target": {"conversation_ref": "conversation", "branch_ref": "main"}}
    references, window = select_window(fake, fake, None, {"id": "brain"}, payload, policy=policy)
    count = len(references)
    assert 0 < count < len(sources) and window["attempted_through_sequence"] == count
    calls = []
    processor = SemanticProcessor(lambda request: calls.append(request) or encoded(proposals()), policy=policy)
    selected = batch(*sources[:count], working_state=working, learning_input_refs=tuple(references))
    result = processor.process(selected, reserve=lambda **_: None)
    assert result.status == "completed" and result.model_calls == len(calls) == 1
    assert result.input_bytes + SAMPLING_RESERVE_BYTES <= maximum
    _, next_result = capture(batch(*sources[:count + 1], working_state=working,
                                   learning_input_refs=tuple(map(ref, sources[:count + 1]))))
    assert next_result.input_bytes + SAMPLING_RESERVE_BYTES > maximum
    oversized = processor.process(batch(*sources, working_state=working, learning_input_refs=tuple(map(ref, sources))),
                                  reserve=lambda **_: pytest.fail("Oversized request must not spend"))
    assert oversized.status == "budget_exhausted" and oversized.model_calls == 0


@pytest.mark.parametrize("version", [None, EPISODE_SCOPE_INPUT_VERSION, COMMUNICATION_INTENT_INPUT_VERSION])
def test_neighborhood_counts_exact_learning_refs_without_charging_historical_sources(version):
    sources = [event("A contextual record.", sequence=index, event_id=str(index) + "x" * 255)
               for index in range(1, 9)]
    fake = BudgetSources(sources)
    references = tuple(map(ref, sources[-2:])) if version is not None else ()
    selector = _Selector(fake, fake, None, {"id": "brain", "indexed_sequence": 8},
                         {"conversation_ref": "conversation", "branch_ref": "main"}, 8, SemanticPolicy(),
                         learning_input_refs=references, input_contract_version=version)
    events = {source["event_id"]: source for source in sources}
    measured = selector.request_bytes(events, {}, {})
    request, result = capture(batch(*sources, working_state=fake._working(), sampling=selector.sampling,
                                    learning_input_refs=references, input_contract_version=version))
    assert result.status == "completed" and result.input_bytes == measured
    if version is not None:
        assert json.loads(request.messages[-1]["content"])["learning_input_refs"] == list(references)
    selector.policy = replace(selector.policy, max_request_bytes=measured + SAMPLING_RESERVE_BYTES)
    selector.include(events)
    assert selector.selected_sources == events
    selector.policy = replace(selector.policy, max_request_bytes=measured + SAMPLING_RESERVE_BYTES - 1)
    with pytest.raises(_Incomplete, match="request_bytes_budget"):
        selector.include(events)


@pytest.mark.integration
def test_mixed_empty_and_nonempty_window_finishes_without_losing_either_record(learning):
    empty = learning.observe("")
    nonempty = learning.observe("我们正在讨论今天的复习安排。")
    requests = []

    def propose(request):
        body = json.loads(request.messages[-1]["content"])
        requests.append(body)
        source = next(source for source in body["sources"] if source["observed"]["content"]["text"])
        return encoded(proposals(episodes=[episode(source)]))

    def verify(request):
        return verifier(request)

    learning.service.semantic_processor = SemanticProcessor(propose, verify)
    selected = claim_job(learning)
    LearningEngine(learning.service)._execute(learning.principal, learning.brain, selected)
    job = learning.job(selected["id"])
    expected = [{"event_id": item["accepted_event_ids"][0], "revision": 1} for item in (empty, nonempty)]
    assert job["state"] == "completed" and job["result"]["status"] == "completed"
    assert job["result"]["learning_input_refs"] == expected
    window = job["result"]["learning_window"]
    assert window["input_count"] == 2 and window["processing_succeeded"] and not window["has_more"]
    assert window["processed_through_sequence"] == nonempty["durable_through_sequence"]
    assert requests[0]["learning_input_refs"] == expected
    assert requests[0]["episode_eligible_member_refs"] == expected[1:]
    assert len(job["result"]["publication"]["episode_versions"]) == 1


@pytest.mark.integration
def test_learning_window_freezes_new_metadata_and_request_across_resume(learning):
    learning.observe("早先的历史来源。", conversation="old")
    receipt = learning.observe("本轮窗口的独立学习内容。")
    selected = claim_job(learning)
    if selected["payload"]["target"]["conversation_ref"] != "chat":
        selected = claim_job(learning)
    engine = LearningEngine(learning.service)
    first, snapshot = engine._snapshot(learning.principal, learning.brain, selected)
    assert snapshot["input_contract_version"] == LATEST_INPUT_CONTRACT_VERSION
    assert first.learning_input_refs == ({"event_id": receipt["accepted_event_ids"][0], "revision": 1},)
    assert first.current_input_refs == ()
    learning.observe("后续输入不能加入冻结窗口。")
    resumed, frozen = engine._snapshot(learning.principal, learning.brain, selected)
    assert frozen == snapshot and resumed.learning_input_refs == first.learning_input_refs
    before, _ = capture(first)
    after, _ = capture(resumed)
    assert before.messages == after.messages and before.request_id == after.request_id


@pytest.mark.integration
@pytest.mark.parametrize("keep_internal_refs", [False, True])
def test_old_window_snapshot_keeps_legacy_wire_contract_on_resume(learning, keep_internal_refs):
    learning.observe("旧任务的冻结输入。")
    seed_legacy_window(learning)
    selected = claim_job(learning)
    engine = LearningEngine(learning.service)
    _, snapshot = engine._snapshot(learning.principal, learning.brain, selected)
    snapshot.pop("input_contract_version")
    if not keep_internal_refs:
        snapshot.pop("learning_input_refs")
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(snapshot), learning.principal.tenant_id, learning.brain, selected["id"]))
    resumed, frozen = engine._snapshot(learning.principal, learning.brain, selected)
    assert frozen == snapshot and resumed.input_contract_version is None and resumed.learning_input_refs == ()
    request, _ = capture(resumed)
    assert "episode_operation_scope" not in json.loads(request.messages[-1]["content"])
    assert hashlib.sha256(request.messages[0]["content"].encode()).hexdigest() == (
        "01b4bb0211bfd4a65a33bfcbb5834ae75c9802439d5d7c38afbaa3fb52da3bf9")
