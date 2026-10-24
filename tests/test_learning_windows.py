import copy
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from psycopg.types.json import Jsonb
from test_belief_semantic import SINCE, UNBOUNDED, link_verifier
from test_learning import learning as learning
from test_semantic import claim as proposed_claim
from test_semantic import encoded, proposals, reference

from agent_memory.helpers import DomainError, digest, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine, validate_snapshot
from agent_memory.learning_windows import (
    SAMPLING_RESERVE_BYTES,
    enqueue_window,
    pending_window,
    select_window,
)
from agent_memory.semantic import (
    PROPOSAL_SCHEMA,
    SemanticPolicy,
    SemanticProcessor,
    proposal_data,
    request_payload,
)

pytestmark = pytest.mark.integration


def jobs(harness):
    with harness.database.transaction(harness.principal) as conn:
        return conn.execute(
            "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND kind='consolidate' ORDER BY created_at,id",
            (harness.principal.tenant_id, harness.principal.principal_id, harness.brain),
        ).fetchall()


def claim(harness, job_id=None):
    return JobStore(harness.database).claim(harness.principal, harness.brain, worker_id=uid(),
                                           kinds=["consolidate"], job_id=job_id, lease_seconds=120)


def execute(harness, job=None):
    selected = job or claim(harness)
    assert selected is not None
    LearningEngine(harness.service)._execute(harness.principal, harness.brain, selected)
    return harness.job(selected["id"])


def drain(harness):
    for _ in range(160):
        if not LearningEngine(harness.service).work_once(harness.principal, harness.brain):
            return
    pytest.fail("Learning worker did not quiesce within its bounded test run")


def observations(harness, count):
    return [harness.observe(f"输入 {index}：我的独立安排编号是 {index}。", source_key=f"input-{index}")
            for index in range(1, count + 1)]


def processed_refs(harness):
    return [(ref["event_id"], ref["revision"]) for job in jobs(harness)
            if job["state"] == "completed"
            for ref in job["result"]["learning_input_refs"]]


def test_forty_input_burst_preserves_first_input_and_all_five_durable_windows_across_restart(learning):
    receipts = observations(learning, 40)
    assert len(jobs(learning)) == 1
    assert jobs(learning)[0]["payload"]["from_sequence"] == 1
    assert jobs(learning)[0]["payload"]["through_sequence"] == 40
    drain(learning)
    completed = jobs(learning)
    assert len(completed) == 5
    assert all(job["state"] == "completed" for job in completed)
    assert [job["result"]["learning_window"]["processed_through_sequence"] for job in completed] == [8, 16, 24, 32, 40]
    expected = [(receipt["accepted_event_ids"][0], 1) for receipt in receipts]
    assert Counter(processed_refs(learning)) == Counter(expected)
    assert expected[0][0] in {source["event_id"] for source in learning.snapshots[0]["sources"]}
    assert len(learning.calls) == 10
    assert completed[-1]["result"]["learning_window"]["has_more"] is False


def empty_assessment(harness):
    def propose(request):
        harness.calls.append(request.phase)
        harness.snapshots.append(json.loads(request.messages[-1]["content"]))
        return encoded(proposals())

    harness.service.semantic_processor = SemanticProcessor(propose, harness.verify)


def test_eight_long_inputs_use_smaller_complete_windows_without_truncating_text(learning):
    empty_assessment(learning)
    texts = [word * (16384 // len(word)) for word in
             ("orchard ", "bicycle ", "blanket ", "crystal ", "dolphin ", "emerald ", "feather ", "gallery ")]
    receipts = [learning.observe(text) for text in texts]
    originals = {receipt["accepted_event_ids"][0]: text for receipt, text in zip(receipts, texts, strict=True)}
    drain(learning)
    completed = jobs(learning)
    assert 1 < len(completed) < 8
    assert all(job["state"] == "completed" for job in completed)
    assert all(1 <= job["result"]["learning_window"]["input_count"] < 8 for job in completed)
    assert Counter(processed_refs(learning)) == Counter((identifier, 1) for identifier in originals)
    assert completed[-1]["result"]["learning_window"]["processed_through_sequence"] == 8
    assert completed[-1]["result"]["learning_window"]["has_more"] is False
    for request in learning.snapshots:
        for source in request["sources"]:
            assert source["observed"]["content"]["text"] == originals[source["event_id"]]


def test_single_oversized_input_stays_first_and_does_not_skip_to_a_small_later_input(learning):
    empty_assessment(learning)
    oversized = learning.observe("oversized " * 10000)
    learning.observe("A small later message.")
    stopped = execute(learning)
    assert stopped["state"] == "cancelled" and stopped["last_error"] == "budget_exceeded"
    assert stopped["result"]["learning_input_refs"] == [{"event_id": oversized["accepted_event_ids"][0], "revision": 1}]
    assert stopped["result"]["learning_window"]["attempted_through_sequence"] == 1
    assert stopped["result"]["learning_window"]["processed_through_sequence"] == 0
    assert stopped["result"]["learning_window"]["has_more"] is True
    assert learning.calls == [] and len(jobs(learning)) == 1


@pytest.mark.parametrize("limiting_field", ["max_request_bytes", "max_input_bytes"])
def test_byte_boundary_accepts_exact_prefix_and_keeps_next_input_at_one_byte_less(learning, limiting_field):
    receipts = [learning.observe("Complete first input."), learning.observe("Complete second input.")]
    current = jobs(learning)[0]
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        sources = learning.service._events(conn, learning.principal, brain,
                                           event_ids=[receipt["accepted_event_ids"][0] for receipt in receipts])
        working = learning.service._working(conn, learning.principal, brain, current["payload"]["target"], sources)
        data = proposal_data(sources, (), working, (), {}, (),
                             learning_input_refs=[{"event_id": source["event_id"], "revision": source["revision"]}
                                                  for source in sources])
        limit = request_payload("proposal", data, PROPOSAL_SCHEMA, SemanticPolicy().max_output_tokens)[1] + SAMPLING_RESERVE_BYTES
        at_limit, window = select_window(conn, learning.service, learning.principal, brain, current["payload"],
                                         policy=replace(SemanticPolicy(), **{limiting_field: limit}))
        below_limit, smaller = select_window(conn, learning.service, learning.principal, brain, current["payload"],
                                             policy=replace(SemanticPolicy(), **{limiting_field: limit - 1}))
    assert len(at_limit) == 2 and window["attempted_through_sequence"] == 2
    assert below_limit == at_limit[:1] and smaller["attempted_through_sequence"] == 1


def test_adaptive_prefix_keeps_coverage_across_deleted_input_and_other_conversation_sequence_gaps(learning):
    empty_assessment(learning)
    receipts = []
    for index, word in enumerate(("orchard ", "bicycle ", "blanket ", "crystal ", "dolphin ", "emerald ", "feather ", "gallery ")):
        receipts.append(learning.observe(word * 2048))
        if index == 2:
            learning.observe("A separate conversation cannot occupy this batch.", conversation="other")
    learning.memory.forget(receipts[1]["accepted_event_ids"], kind="events", mode="erase_content", expected_scope_generation=1)
    drain(learning)
    completed = [job for job in jobs(learning) if job["payload"]["target"]["conversation_ref"] == "chat"]
    references = [(ref["event_id"], ref["revision"]) for job in completed for ref in job["result"]["learning_input_refs"]]
    assert Counter(references) == Counter((receipt["accepted_event_ids"][0], 1)
                                          for index, receipt in enumerate(receipts) if index != 1)
    assert all(job["state"] == "completed" for job in completed)
    assert completed[-1]["result"]["learning_window"]["processed_through_sequence"] == 9
    assert completed[-1]["result"]["learning_window"]["has_more"] is False


def test_arrival_during_running_window_does_not_merge_over_its_pending_tail(learning):
    receipts = observations(learning, 16)
    first = claim(learning)
    receipts.append(learning.observe("后来新增第十七个安排。"))
    execute(learning, first)
    receipts.append(learning.observe("后来新增第十八个安排。"))
    pending = [job for job in jobs(learning) if job["state"] == "queued"]
    assert sorted((job["payload"]["from_sequence"], job["payload"]["through_sequence"]) for job in pending) == [(9, 16), (17, 18)]
    drain(learning)
    assert Counter(processed_refs(learning)) == Counter((receipt["accepted_event_ids"][0], 1) for receipt in receipts)


def test_new_arrival_extends_unclaimed_continuation_without_losing_its_start(learning):
    receipts = observations(learning, 16)
    execute(learning)
    receipts.append(learning.observe("第十七个安排。"))
    queued = [job for job in jobs(learning) if job["state"] == "queued"]
    assert len(queued) == 1
    assert (queued[0]["payload"]["from_sequence"], queued[0]["payload"]["through_sequence"]) == (9, 17)
    drain(learning)
    assert Counter(processed_refs(learning)) == Counter((receipt["accepted_event_ids"][0], 1) for receipt in receipts)


def test_snapshot_remains_frozen_when_index_progresses_and_new_input_arrives(learning):
    observations(learning, 8)
    selected = claim(learning)
    engine = LearningEngine(learning.service)
    first_batch, first_snapshot = engine._snapshot(learning.principal, learning.brain, selected)
    learning.observe("不应加入旧批次的新输入。")
    store = JobStore(learning.database)
    while literal := store.claim(learning.principal, learning.brain, worker_id=uid(), kinds=["extract"]):
        engine._execute(learning.principal, learning.brain, literal)
    resumed, snapshot = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    assert snapshot == first_snapshot
    assert resumed == first_batch
    assert learning.job(selected["id"])["payload"]["through_sequence"] == 8


def test_continuation_creation_and_old_worker_completion_are_idempotent(learning):
    observations(learning, 10)
    selected = claim(learning)
    completed = execute(learning, selected)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        child = enqueue_window(conn, learning.service, learning.principal, brain,
                               selected["payload"]["target"], 9, 10, origin=selected["id"])
    assert child == completed["payload"]["continuation_job_id"]
    assert len(jobs(learning)) == 2
    with pytest.raises(DomainError, match="lease"):
        JobStore(learning.database).complete(learning.principal, learning.brain, selected["id"],
                                             lease_token=selected["lease_token"], fence=selected["fence"])
    assert len(jobs(learning)) == 2


def make_available(harness, job_id):
    with harness.database.transaction(harness.principal) as conn:
        conn.execute("UPDATE memory_jobs SET available_at=clock_timestamp()-interval '1 second' "
                     "WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (harness.principal.tenant_id, harness.brain, job_id))


def test_brain_budget_defers_unprocessed_window_without_calls_or_continuation(learning):
    observations(learning, 10)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("INSERT INTO memory_cognitive_budgets(tenant_id,owner_id,brain_id,window_seconds,"
                     "max_model_calls,max_input_bytes) VALUES(%s,%s,%s,3600,0,2000000)",
                     (learning.principal.tenant_id, learning.principal.principal_id, learning.brain))
    deferred = execute(learning)
    window = deferred["result"]["learning_window"]
    assert deferred["state"] == "failed" and deferred["model_calls"] == 0
    assert window["processed_through_sequence"] == 0 and window["attempted_through_sequence"] == 8
    assert window["processing_succeeded"] is False and window["has_more"] is True
    assert (deferred["available_at"] - datetime.now(timezone.utc)).total_seconds() > 3500
    assert len(jobs(learning)) == 1 and learning.calls == []
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_cognitive_budgets SET max_model_calls=100 WHERE tenant_id=%s AND brain_id=%s",
                     (learning.principal.tenant_id, learning.brain))
    make_available(learning, deferred["id"])
    resumed = execute(learning)
    assert resumed["id"] == deferred["id"] and resumed["attempts"] == 2
    assert resumed["result"]["learning_window"]["processed_through_sequence"] == 8
    drain(learning)
    assert len(processed_refs(learning)) == 10


@pytest.mark.parametrize("failure", ["provider", "invalid"])
def test_failed_attempt_never_advances_or_restarts_with_a_fresh_budget(learning, failure):
    observations(learning, 10)

    def fail(request):
        learning.calls.append(request.phase)
        if failure == "provider":
            raise TimeoutError("private-provider-error")
        return "private-invalid-json"

    learning.service.semantic_processor = SemanticProcessor(fail, learning.verify)
    deferred = execute(learning)
    assert deferred["state"] == "cancelled" and deferred["model_calls"] == 1
    assert deferred["result"]["learning_window"]["processed_through_sequence"] == 0
    assert deferred["result"]["learning_window"]["processing_succeeded"] is False
    assert len(jobs(learning)) == 1
    drain(learning)
    assert learning.calls == ["proposal"] and len(jobs(learning)) == 1


def test_all_invalid_proposals_do_not_count_as_an_evaluated_window(learning):
    learning.observe("只有一个有效来源。")

    def propose(request):
        learning.calls.append(request.phase)
        source = json.loads(request.messages[-1]["content"])["sources"][0]
        invalid = proposed_claim(source)
        invalid["evidence_refs"][0]["event_id"] = "unavailable-source"
        return encoded(proposals(invalid))

    learning.service.semantic_processor = SemanticProcessor(propose, learning.verify)
    stopped = execute(learning)
    assert stopped["state"] == "cancelled" and stopped["last_error"] == "invalid_proposal"
    assert stopped["result"]["status"] == "partial"
    assert stopped["result"]["rejected"][0]["reason"] == "invalid_proposal"
    assert stopped["result"]["learning_window"]["processing_succeeded"] is False
    assert stopped["result"]["learning_window"]["processed_through_sequence"] == 0


def test_valid_proposal_rejected_by_verifier_finishes_assessment_without_publishing_claim(learning):
    learning.observe("这段证据不足以支持模型提出的结论。")

    def propose(request):
        source = json.loads(request.messages[-1]["content"])["sources"][0]
        return encoded(proposals(proposed_claim(source)))

    def reject(request):
        response = json.loads(learning.verify(request))
        for assessment in response["assessments"]:
            for verdict in assessment["relations"]:
                verdict["verdict"] = "unknown"
        return encoded(response)

    learning.service.semantic_processor = SemanticProcessor(propose, reject)
    completed = execute(learning)
    assert completed["state"] == "completed"
    assert completed["result"]["publication"]["memory_versions"] == []
    assert completed["result"]["learning_outcome"] == "no_accepted_proposals"
    assert completed["result"]["learning_window"]["processing_succeeded"] is True
    assert completed["result"]["learning_window"]["processed_through_sequence"] == 1


def test_retry_preserves_frozen_input_and_cumulative_configured_budget(learning):
    observations(learning, 8)

    def fail(request):
        learning.calls.append(request.phase)
        raise TimeoutError("private-provider-error")

    policy = replace(SemanticPolicy(), max_model_calls=4)
    learning.service.semantic_processor = SemanticProcessor(fail, learning.verify, policy=policy)
    failed = execute(learning)
    assert failed["state"] == "failed" and failed["max_model_calls"] == 4
    assert (failed["available_at"] - datetime.now(timezone.utc)).total_seconds() > 50
    learning.observe("在重试之前到达的新安排。")
    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify, policy=policy)
    make_available(learning, failed["id"])
    resumed = execute(learning, claim(learning, failed["id"]))
    assert resumed["model_calls"] == 3 and resumed["attempts"] == 2 and resumed["max_model_calls"] == 4
    assert resumed["result"]["source_snapshot"] == failed["result"]["source_snapshot"]
    assert resumed["result"]["learning_input_refs"] == failed["result"]["learning_input_refs"]


def test_fixed_request_budget_exhaustion_does_not_retry_the_same_oversized_snapshot(learning):
    learning.observe("必要输入超过显式设置的极小请求预算。")
    learning.service.semantic_processor = SemanticProcessor(
        learning.propose, learning.verify, policy=replace(SemanticPolicy(), max_request_bytes=100),
    )
    failed = execute(learning)
    assert failed["state"] == "cancelled" and failed["last_error"] == "budget_exceeded"
    assert failed["result"]["learning_window"]["processed_through_sequence"] == 0
    assert failed["model_calls"] == 0 and learning.calls == []
    make_available(learning, failed["id"])
    assert claim(learning, failed["id"]) is None


def test_expired_lease_reclaims_the_same_snapshot_and_keeps_unknown_call_charged(learning):
    observations(learning, 8)
    learning.service.semantic_processor = SemanticProcessor(
        learning.propose, learning.verify, policy=replace(SemanticPolicy(), max_model_calls=4),
    )
    first = claim(learning)
    _, snapshot = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, first)
    store = JobStore(learning.database)
    store.reserve_operation(learning.principal, learning.brain, first["id"], lease_token=first["lease_token"],
                            fence=first["fence"], operation_id="interrupted-proposal", request_fingerprint=digest("request"),
                            input_bytes=200, max_model_calls=4, max_input_bytes=196608)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                     "WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, first["id"]))
    learning.observe("重启后到达的新消息。")
    second = claim(learning, first["id"])
    assert second["id"] == first["id"] and second["fence"] == first["fence"] + 1
    completed = execute(learning, second)
    assert completed["model_calls"] == 3 and completed["attempts"] == 2
    assert completed["result"]["source_snapshot"] == snapshot["source_snapshot"]
    assert store.get_operation(learning.principal, learning.brain, first["id"], "interrupted-proposal")["outcome"] == "uncertain"
    with pytest.raises(DomainError):
        store.complete(learning.principal, learning.brain, first["id"], lease_token=first["lease_token"], fence=first["fence"])


def test_reclaimed_unknown_call_stops_before_provider_when_pair_budget_is_insufficient(learning):
    observations(learning, 1)
    first = claim(learning)
    LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, first)
    store = JobStore(learning.database)
    store.reserve_operation(learning.principal, learning.brain, first["id"], lease_token=first["lease_token"],
                            fence=first["fence"], operation_id="unknown-proposal", request_fingerprint=digest("request"),
                            input_bytes=200, max_model_calls=2, max_input_bytes=196608)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                     "WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, first["id"]))
    learning.service.semantic_processor = SemanticProcessor(
        learning.propose, learning.verify, policy=replace(SemanticPolicy(), max_model_calls=8),
    )
    stopped = execute(learning, claim(learning, first["id"]))
    assert stopped["state"] == "cancelled" and stopped["last_error"] == "budget_exceeded"
    assert stopped["model_calls"] == 1 and stopped["max_model_calls"] == 2
    assert stopped["result"]["learning_window"]["processed_through_sequence"] == 0
    assert learning.calls == []


def test_deleted_only_window_finishes_without_provider_call_or_source_resurrection(learning):
    receipt = learning.observe("将被删除的唯一输入。")
    learning.memory.forget(receipt["accepted_event_ids"], kind="events", mode="erase_content", expected_scope_generation=1)
    completed = execute(learning)
    assert completed["state"] == "completed"
    assert completed["result"]["status"] == "no_sources"
    assert completed["result"]["source_snapshot"] == []
    assert completed["result"]["learning_window"]["processed_through_sequence"] == 1
    assert completed["result"]["learning_window"]["has_more"] is False
    assert learning.calls == []


def test_automatic_relation_publication_flows_into_next_window_and_frozen_relationship_snapshot(learning):
    seen = []

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        seen.append(data)
        if not data["existing_assertions"]:
            source = data["sources"][0]
            return encoded(proposals(proposed_claim(source, statement="平时用户希望直接获得建议。",
                                                    conditions={"op": "always"}, valid_during=UNBOUNDED)))
        old = next((memory for memory in data["existing_assertions"]
                    if memory.get("verification", {}).get("method") == "semantic_model"
                    and memory["statement"] == "平时用户希望直接获得建议。"), None)
        source = next((event for event in data["sources"] if event["observed"]["source_event_key"] == "changed"), None)
        if old and source and not data["belief_relations"]:
            original = next(event for event in data["sources"] if event["event_id"] == old["evidence_refs"][0]["event_id"])
            new_claim = proposed_claim(source, statement="从2026年10月24日起用户希望先被倾听。",
                                       conditions={"op": "always"}, valid_during=SINCE)
            link = {"proposal_id": "change-link", "kind": "supersedes", "claim_proposal_id": new_claim["proposal_id"],
                    "target_memory_versions": [{"memory_id": old["memory_id"], "revision": old["revision"]}],
                    "evidence_refs": [reference(original), reference(source)],
                    "conditions": {"op": "always"}, "valid_during": SINCE, "world": "real_world"}
            return encoded(proposals(new_claim) | {"belief_links": [link]})
        return encoded(proposals())

    learning.service.semantic_processor = SemanticProcessor(propose, link_verifier)
    learning.observe("平时我希望直接获得建议。", source_key="old")
    initial = execute(learning)
    assert len(initial["result"]["publication"]["memory_versions"]) == 1
    learning.observe("从2026年10月24日起，我改为希望先听我说，不再马上给建议。", source_key="changed")
    for index in range(8):
        learning.observe(f"另一个独立事项编号 {index}。")
    published = execute(learning)
    relations = published["result"]["publication"]["belief_relation_versions"]
    assert len(relations) == 1
    assert published["result"]["learning_window"]["processed_through_sequence"] == 9
    continuation = claim(learning, published["payload"]["continuation_job_id"])
    engine = LearningEngine(learning.service)
    batch, frozen = engine._snapshot(learning.principal, learning.brain, continuation)
    assert frozen["dependency_generation"] > published["result"]["dependency_generation"]
    assert len(batch.belief_relations) == len(frozen["belief_relations"]) == 1
    assert frozen["belief_relation_snapshot"] == [{**relations[0], "content_sha256": digest(batch.belief_relations[0])}]
    resumed, retried = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, continuation)
    assert resumed.belief_relations == batch.belief_relations and retried == frozen
    final = execute(learning, continuation)
    assert final["result"]["learning_window"]["processed_through_sequence"] == 10
    assert seen[-1]["belief_relations"] == list(batch.belief_relations)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        tampered = copy.deepcopy(frozen["belief_relations"][0])
        tampered["kind"] = "contradicts"
        conn.execute("UPDATE memory_belief_relations SET document=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(tampered), learning.principal.tenant_id, learning.brain, tampered["relation_id"]))
        with pytest.raises(DomainError, match="relationship"):
            validate_snapshot(conn, learning.service, learning.principal, brain, frozen)


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_current_source_revisions_and_deletion_are_respected_before_batch_selection(learning, mutation):
    receipts = observations(learning, 10)
    original = receipts[0]["accepted_event_ids"][0]
    if mutation == "edit":
        learning.observe("第一条已修改的安排。", source_key="input-1", revision=2)
    else:
        learning.memory.forget([original], kind="events", mode="erase_content", expected_scope_generation=1)
    drain(learning)
    refs = processed_refs(learning)
    assert (original, 1) not in refs
    assert Counter(ref for ref in refs if ref[0] != original) == Counter(
        (receipt["accepted_event_ids"][0], 1) for receipt in receipts[1:])
    if mutation == "edit":
        assert (original, 2) in refs


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_invalidated_running_window_recovers_pending_tail_without_resurrecting_source(learning, mutation):
    receipts = observations(learning, 16)
    original = receipts[0]["accepted_event_ids"][0]
    selected = claim(learning)

    def mutate(_):
        learning.during_proposal = None
        if mutation == "edit":
            learning.observe("修改后的第一条安排。", source_key="input-1", revision=2)
        else:
            learning.memory.forget([original], kind="events", mode="erase_content", expected_scope_generation=1)

    learning.during_proposal = mutate
    with pytest.raises(DomainError):
        execute(learning, selected)
    assert learning.job(selected["id"])["state"] == "superseded"
    drain(learning)
    refs = processed_refs(learning)
    assert (original, 1) not in refs
    assert set((receipt["accepted_event_ids"][0], 1) for receipt in receipts[1:]).issubset(refs)
    assert learning.job(selected["id"])["payload"]["recovery_job_id"]
    count = len(jobs(learning))
    drain(learning)
    assert len(jobs(learning)) == count


def test_completed_batch_repair_preserves_separate_continuation_range_and_new_revision(learning):
    receipts = observations(learning, 16)
    original = receipts[0]["accepted_event_ids"][0]
    first = execute(learning)
    child = first["payload"]["continuation_job_id"]
    learning.observe("更正第一条安排，使用新版本。", source_key="input-1", revision=2)
    queued = learning.job(child)
    assert queued["state"] == "queued"
    assert (queued["payload"]["from_sequence"], queued["payload"]["through_sequence"]) == (9, 17)
    drain(learning)
    repaired_parent = learning.job(first["id"])
    replacement = learning.job(repaired_parent["payload"]["recovery_job_id"])
    assert (replacement["payload"]["from_sequence"], replacement["payload"]["through_sequence"]) == (1, 8)
    assert Counter(processed_refs(learning)) == Counter(
        [(receipt["accepted_event_ids"][0], 1) for receipt in receipts[1:]] + [(original, 2)])


def test_dependency_generation_change_invalidates_frozen_snapshot_but_legacy_remains_compatible(learning):
    observations(learning, 1)
    selected = claim(learning)
    _, snapshot = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, selected)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_brains SET dependency_generation=dependency_generation+1 WHERE tenant_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain))
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        with pytest.raises(DomainError, match="generation"):
            validate_snapshot(conn, learning.service, learning.principal, brain, snapshot)
        legacy = copy.deepcopy(snapshot)
        del legacy["dependency_generation"]
        assert validate_snapshot(conn, learning.service, learning.principal, brain, legacy)


@pytest.mark.parametrize("payload", [
    {"from_sequence": 0, "through_sequence": 1}, {"from_sequence": True, "through_sequence": 1},
    {"from_sequence": 2, "through_sequence": 1}, {"from_sequence": 1, "through_sequence": 2**63},
])
def test_window_boundaries_reject_invalid_sequences(payload):
    with pytest.raises(ValueError):
        pending_window(payload)
