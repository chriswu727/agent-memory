import copy
import json
from dataclasses import replace

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_continuous_learning_acceptance import (
    NOTEBOOK,
    POLICY,
    ROUTINES,
    GroundedCognition,
    assert_funded_package,
    diagnostic,
    drain,
    encoding_for,
    objective_ids,
    operations,
    restarted_service,
)
from test_jobs import jobs as jobs
from test_learning import learning as learning
from test_learning_allocations import insert, params, reserve, row
from test_learning_windows import claim, execute
from test_learning_windows import jobs as learning_jobs

from agent_memory.contracts import validate
from agent_memory.helpers import DomainError, digest, uid
from agent_memory.history_attention import POLICY as ATTENTION_POLICY
from agent_memory.history_attention import model_manifest
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import (
    HISTORY_PHASE,
    allocation_metadata,
    freeze_input,
    phase,
)
from agent_memory.learning_funding import (
    PERMISSION_VERSION,
    funding_origin,
    funding_permission,
    ordinary_root_metadata,
    permitted_target,
)
from agent_memory.learning_integration import reconsider
from agent_memory.learning_phase_monitor import sanitize_phase_metadata
from agent_memory.runtime import BrainRuntime, DeliveryReceipt
from agent_memory.runtime_client import RuntimeClient


def scope(brain="brain", branch="main"):
    return {"visibility": "private", "access_policy_id": f"owner:{brain}",
            "policy_revision": 1, "branch_id": branch}


def root(*, permission=True):
    payload = {**allocation_metadata("donor"), "target": {"conversation_ref": "new", "branch_ref": "main"}}
    if permission:
        payload.update(ordinary_root_metadata(scope()))
    return {"id": "donor", "payload": payload}


@pytest.mark.parametrize("change", [
    {"branch_ref": "other"}, {"conversation_ref": None}, {"branch_ref": True}, {"extra": "scope"},
])
def test_permission_does_not_expand_branch_or_accept_malformed_target(change):
    assert not permitted_target(root(), {"conversation_ref": "old", "branch_ref": "main", **change}, brain_id="brain")


def test_only_explicit_private_ordinary_owner_root_has_cross_conversation_permission():
    target = {"conversation_ref": "old", "branch_ref": "main"}
    assert permitted_target(root(), target, brain_id="brain")
    assert not permitted_target(root(permission=False), target, brain_id="brain")
    assert not permitted_target(root(), target, brain_id="another-brain")
    with pytest.raises(DomainError):
        funding_permission({**scope(), "visibility": "shared"})
    copied = root()
    copied["payload"]["authorization_root_job_id"] = "original"
    assert not permitted_target(copied, target, brain_id="brain")


@pytest.mark.parametrize("funding", [None, [], {"version": PERMISSION_VERSION}, {
    "version": PERMISSION_VERSION, "authorization_root_job_id": "donor", "origin_job_id": "old",
    "private_manifest": "must never be exposed"}, {
    "version": PERMISSION_VERSION, "authorization_root_job_id": True, "origin_job_id": "old"},
])
def test_public_funding_summary_rejects_malformed_or_private_payload(funding):
    result = sanitize_phase_metadata({"learning_contract_version": "encoding-integration-v1",
        "learning_phase": HISTORY_PHASE, "phase_metadata_complete": True, "history_funding": funding})
    assert "history_funding" not in result and result["phase_metadata_complete"] is False


def test_public_summary_contract_preserves_legacy_absence_and_fixed_funding_identity():
    validate("LearningHistorySummaries", {})
    valid = {"history_funding": {"version": PERMISSION_VERSION,
                                 "authorization_root_job_id": "donor", "origin_job_id": "original"}}
    validate("LearningHistorySummaries", valid)
    with pytest.raises(DomainError):
        validate("LearningHistorySummaries", {**valid, "history_funding_permission": scope()})


@pytest.mark.integration
@pytest.mark.parametrize("field,value", [
    ("history_funding_permission", "permission"), ("history_attention_policy", ATTENTION_POLICY),
    ("target", {"conversation_ref": "other", "branch_ref": "main"}),
])
def test_legacy_root_cannot_gain_funding_or_attention_or_change_scope_after_creation(jobs, field, value):
    identifier = insert(jobs)
    if value == "permission":
        value = funding_permission(scope(jobs.brain))
    before = row(jobs, identifier)["payload"]
    with pytest.raises(psycopg.errors.CheckViolation), jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET payload=payload || %s WHERE tenant_id=%s AND owner_id=%s "
                     "AND brain_id=%s AND id=%s", (Jsonb({field: value}), *params(jobs), identifier))
    assert row(jobs, identifier)["payload"] == before


def authority_pair(harness, *, permission=True, branch="main", forged=False):
    old_root = insert(harness, state="completed")
    manifest = {"version": "bounded-history-manifest-v1", "input_refs": [{
        "event_id": "original-input", "revision": 1, "content_sha256": digest("original text")}],
        "input_fingerprint": digest("original input"), "through_sequence": 8,
        "objectives": [{"objective_id": "original-comparison", "target_memory_version": {
            "memory_id": "original-memory", "revision": 1, "content_sha256": digest("original memory")}}],
        "unresolved_discovery": False, "discovery_limitations": []}
    binding = {"encoding_parent_job_id": old_root, "encoding_input_fingerprint": manifest["input_fingerprint"],
               "history_cutoff_sequence": 8, "history_obligation_manifest": manifest}
    parent = insert(harness, selected_phase=HISTORY_PHASE, root=old_root, state="deferred",
                    frozen=False, extra=binding)
    grant = ordinary_root_metadata(scope(harness.brain)) if permission else {}
    donor = insert(harness, state="completed", extra={
        **grant, "target": {"conversation_ref": "donor", "branch_ref": "main"},
        "from_sequence": 9, "through_sequence": 16})
    origin = funding_origin(row(harness, parent), manifest)
    if forged:
        origin["obligation_fingerprint"] = digest("another obligation")
    child = insert(harness, selected_phase=HISTORY_PHASE, root=donor, frozen=False, extra={
        **binding, "target": {"conversation_ref": "chat", "branch_ref": branch},
        "history_funding_origin": origin, "integration_continuation_parent_job_id": parent})
    with harness.database.transaction(harness.principal) as conn:
        for identifier, fields in ((parent, {"integration_continuation_job_id": child}),
                                   (donor, {"history_allocation_job_id": child})):
            conn.execute("UPDATE memory_jobs SET payload=payload || %s WHERE tenant_id=%s AND owner_id=%s "
                         "AND brain_id=%s AND id=%s", (Jsonb(fields), *params(harness), identifier))
    return parent, donor, child


@pytest.mark.integration
@pytest.mark.parametrize("damage", ["legacy", "branch", "origin", "assignment", "paid_parent", "quarantined_parent"])
def test_foreign_root_requires_immutable_origin_unpaid_parent_and_exact_live_assignment(jobs, damage):
    parent, donor, child = authority_pair(jobs, permission=damage != "legacy",
                                         branch="other" if damage == "branch" else "main", forged=damage == "origin")
    if damage == "assignment":
        with jobs.database.transaction(jobs.principal) as conn:
            conn.execute("UPDATE memory_jobs SET payload=payload - 'history_allocation_job_id' "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s", (*params(jobs), donor))
    elif damage == "paid_parent":
        with jobs.database.transaction(jobs.principal) as conn:
            conn.execute("INSERT INTO memory_cognitive_operations(tenant_id,owner_id,brain_id,job_id,operation_id,"
                         "request_fingerprint,input_bytes,fence,outcome) VALUES(%s,%s,%s,%s,%s,%s,100,1,'uncertain')",
                         (*params(jobs), parent, uid(), digest("previous unknown request")))
    elif damage == "quarantined_parent":
        with jobs.database.transaction(jobs.principal) as conn:
            conn.execute("INSERT INTO memory_recovery_quarantine(tenant_id,owner_id,brain_id,object_kind,object_id,"
                         "recovery_epoch,prior_state) VALUES(%s,%s,%s,'job',%s,%s,'deferred')", (*params(jobs), parent, uid()))
    with jobs.database.transaction(jobs.principal) as conn:
        with pytest.raises(DomainError) as caught:
            freeze_input(conn, params(jobs), child, input_fingerprint=digest("exact recipient snapshot"))
    assert caught.value.code == "dependency_unavailable"
    assert row(jobs, child)["model_calls"] == 0


@pytest.mark.integration
def test_foreign_history_counts_unknown_calls_on_donor_and_cannot_use_a_second_pair(learning):
    jobs = learning
    jobs.store = JobStore(jobs.database)
    source = learning.observe("Original private input remains private.")["accepted_event_ids"][0]
    parent, donor, child = authority_pair(jobs)
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET source_event_ids=%s WHERE tenant_id=%s AND owner_id=%s "
                     "AND brain_id=%s AND id=%s", ([source], *params(jobs), child))
        freeze_input(conn, params(jobs), child, input_fingerprint=digest("exact recipient snapshot"))
    first, lease = reserve(jobs, child)
    jobs.store.settle_operation(jobs.principal, jobs.brain, child, lease_token=lease["lease_token"],
                                fence=lease["fence"], operation_id=first["operation_id"], outcome="uncertain")
    reserve(jobs, child, claim=lease)
    with pytest.raises(DomainError) as caught:
        reserve(jobs, child, claim=lease)
    assert caught.value.code == "budget_exceeded"
    assert row(jobs, parent)["model_calls"] == row(jobs, donor)["model_calls"] == 0
    assert row(jobs, child)["model_calls"] == 2
    assert jobs.store.get_operation(jobs.principal, jobs.brain, child, first["operation_id"])["outcome"] == "uncertain"


@pytest.mark.integration
@pytest.mark.parametrize("field", ["history_funding_origin", "history_obligation_manifest", "encoding_parent_job_id",
                                   "encoding_input_fingerprint", "history_cutoff_sequence", "from_sequence"])
def test_recipient_proof_and_original_obligation_are_immutable(jobs, field):
    _, _, child = authority_pair(jobs)
    with pytest.raises(psycopg.errors.CheckViolation), jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET payload=payload - %s WHERE tenant_id=%s AND owner_id=%s "
                     "AND brain_id=%s AND id=%s", (field, *params(jobs), child))


def turn(harness, memory, text, identifier, conversation, *, branch="main"):
    deliveries = []

    def deliver(request):
        deliveries.append(request.text)
        return DeliveryReceipt("delivered", request.text)

    runtime = BrainRuntime(memory, lambda _: "I received your report.", deliver=deliver, policy=POLICY)
    result = runtime.respond(text, message_id=identifier, conversation_id=conversation, branch_id=branch)
    assert result.stage == "completed" and len(deliveries) == 1
    return runtime.journal.sources(result.turn_id)


def configure(harness, *, sources=4):
    harness.service.semantic_processor.policy = replace(harness.service.semantic_processor.policy,
                                                        max_sources=sources, max_assertions=8)


def prepared_foreign_history(harness, memory):
    configure(harness)
    for index, fact in enumerate(ROUTINES[:3]):
        turn(harness, memory, fact, f"first-{index}", "old")
        drain(harness)
    old, = [job for job in learning_jobs(harness)
            if phase(job["payload"]) == HISTORY_PHASE and job["state"] == "deferred"]
    configure(harness, sources=8)
    sources = turn(harness, memory, NOTEBOOK, "donor", "new")
    for _ in range(30):
        donors = [job for job in learning_jobs(harness)
                  if job["payload"]["target"]["conversation_ref"] == "new" and job["state"] == "completed"
                  and phase(job["payload"]) != HISTORY_PHASE]
        if donors:
            with harness.database.transaction(harness.principal) as conn:
                brain = harness.service._brain(conn, harness.principal, harness.brain)
                reconsider(conn, harness.service, harness.principal, brain)
            previous = harness.job(old["id"])
            child = harness.job(previous["payload"]["integration_continuation_job_id"])
            assert child["state"] == "queued" and child["model_calls"] == 0
            return previous, encoding_for(harness, sources), child
        assert LearningEngine(harness.service).work_once(harness.principal, harness.brain)
    pytest.fail("No bounded donor encoding completed")


@pytest.mark.integration
@pytest.mark.parametrize("change", ["unrelated_generation", "erased_source", "explicit_recovery", "nonprivate_source"])
def test_foreign_grant_recovery_keeps_exact_unpaid_identity_and_never_resurrects_sources(learning, change):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        old, donor, child = prepared_foreign_history(learning, memory)
        before = len(cognition.requests)
        frozen = copy.deepcopy(child["payload"]["history_obligation_manifest"])
        if change == "explicit_recovery":
            job = claim(learning, child["id"])
            JobStore(learning.database).fail(learning.principal, learning.brain, child["id"],
                lease_token=job["lease_token"], fence=job["fence"], failure_code="context_stale", retry=False)
            recovered = RuntimeClient(memory).retry_learning(child["id"], request_id="explicit", expected_fence=job["fence"])
            recovered = learning.job(recovered["id"])
            assert "history_funding_origin" not in recovered["payload"]
            assert "history_funding_permission" not in recovered["payload"]
            assert recovered["payload"]["authorization_root_job_id"] == recovered["id"]
            assert recovered["payload"]["history_obligation_manifest"] == frozen
            assert recovered["payload"]["allocation_budget"] == {"max_model_calls": 2, "max_input_bytes": 196608}
            assert len(cognition.requests) == before
            return
        if change == "nonprivate_source":
            with learning.database.transaction(learning.principal) as conn:
                conn.execute("UPDATE memory_events SET document=jsonb_set(document,'{scope,visibility}',"
                             "'\"conversation\"'::jsonb) WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                             (*params(learning), frozen["input_refs"][0]["event_id"]))
            with pytest.raises(DomainError) as caught:
                execute(learning, claim(learning, child["id"]))
            assert caught.value.code == "dependency_unavailable"
            assert len(cognition.requests) == before and not operations(learning, child["id"])
            assert learning.job(child["id"])["state"] != "completed"
            return
        if change == "erased_source":
            event_id = frozen["input_refs"][0]["event_id"]
            memory.forget([event_id], kind="events", mode="erase_content", expected_scope_generation=1)
        else:
            with learning.database.transaction(learning.principal) as conn:
                conn.execute("UPDATE memory_brains SET dependency_generation=dependency_generation+1 "
                             "WHERE tenant_id=%s AND owner_id=%s AND id=%s", params(learning))
            with pytest.raises(DomainError) as caught:
                execute(learning, claim(learning, child["id"]))
            assert caught.value.code == "context_stale"
        assert len(cognition.requests) == before and not operations(learning, child["id"])
        for _ in range(3):
            with learning.database.transaction(learning.principal) as conn:
                brain = learning.service._brain(conn, learning.principal, learning.brain)
                reconsider(conn, learning.service, learning.principal, brain)
        original = learning.job(child["id"])
        if change == "erased_source":
            assert "integration_continuation_job_id" not in original["payload"]
            assert len(cognition.requests) == before
        else:
            replacement = learning.job(original["payload"]["integration_continuation_job_id"])
            assert replacement["state"] == "queued"
            assert replacement["payload"]["history_obligation_manifest"] == frozen
            assert replacement["payload"]["history_funding_origin"] == child["payload"]["history_funding_origin"]
            assert replacement["payload"]["authorization_root_job_id"] == donor["id"]
            assert learning.job(donor["id"])["payload"]["history_allocation_job_id"] == replacement["id"]
            assert execute(learning, claim(learning, replacement["id"]))["state"] == "completed"
            assert not operations(learning, child["id"]) and not operations(learning, old["id"])


@pytest.mark.integration
def test_current_private_root_repays_old_conversation_without_moving_evidence_after_restart(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        configure(learning)
        earlier_sources = []
        for index, fact in enumerate(ROUTINES[:3]):
            earlier_sources.append(turn(learning, memory, fact, f"first-{index}", "old"))
            drain(learning)
        second_sources = earlier_sources[-1]
        second = encoding_for(learning, second_sources)
        assert "history_integration_job_id" in second["result"], json.dumps(diagnostic(learning))
        selected = learning.job(second["result"]["history_integration_job_id"])
        assert selected["state"] == "completed", json.dumps(diagnostic(learning))
        deferred = [job for job in learning_jobs(learning)
                    if phase(job["payload"]) == HISTORY_PHASE and job["state"] == "deferred"]
        assert len(deferred) == 1, json.dumps(diagnostic(learning))
        old = deferred[0]
        original = copy.deepcopy(old["result"]["integration_manifest"])
        assert original["attention"]["admitted_count"] == 3
        assert original["objectives"] and not operations(learning, old["id"])
        original_ledger = operations(learning)
    with restarted_service(learning, cognition) as memory:
        configure(learning, sources=8)
        calls = len(cognition.requests)
        drain(learning)
        assert len(cognition.requests) == calls and operations(learning) == original_ledger
        donor_sources = turn(learning, memory, NOTEBOOK, "new-chat", "new")
        drain(learning)
        donor = encoding_for(learning, donor_sources)
        assert donor["payload"]["history_funding_permission"]["version"] == PERMISSION_VERSION
        old = learning.job(old["id"])
        assert "integration_continuation_job_id" in old["payload"], diagnostic(learning)
        paid = learning.job(old["payload"]["integration_continuation_job_id"])
        assert paid["state"] == "completed", json.dumps(diagnostic(learning))
        paid_selection = assert_funded_package(learning, paid, original)
        assert paid["payload"]["target"] == {"conversation_ref": "old", "branch_ref": "main"}
        assert paid["payload"]["authorization_root_job_id"] == donor["id"]
        assert paid["payload"]["encoding_parent_job_id"] == second["id"]
        assert donor["payload"]["history_allocation_job_id"] == paid["id"]
        assert not operations(learning, old["id"])
        assert all(operation in operations(learning) for operation in original_ledger)
        assert paid["model_calls"] == len(operations(learning, paid["id"])) == 1
        assert paid["result"]["history_integration"]["assessed_count"] == len(paid_selection["objectives"])
        assert {item["objective_id"] for item in paid["result"]["history_integration"]["objectives"]
                if item["assessed"]} == objective_ids(paid_selection)
        assert paid["result"]["history_integration"]["published_count"] == 0
        donor_ids = {event["event_id"] for key in ("input_events", "output_events") for event in donor_sources[key]}
        assert donor_ids.isdisjoint(paid["source_event_ids"])
        prior_ids = {event["event_id"] for sources in (*earlier_sources, second_sources)
                     for key in ("input_events", "output_events") for event in sources[key]}
        assert set(paid["source_event_ids"]) <= prior_ids
        prompts = [data for name, data in cognition.requests if name == "proposal"
                   and "integration_manifest" in data
                   and objective_ids(data["integration_manifest"]) & objective_ids(original)]
        assert len(prompts) == 1 and prompts[0]["integration_manifest"]["input_refs"] == original["input_refs"]
        assert prompts[0]["integration_manifest"] == model_manifest(paid_selection)
        assert {event["event_id"] for event in prompts[0]["sources"]}.isdisjoint(donor_ids)
        public = RuntimeClient(memory).job(paid["id"])["learning_summary"]
        expected_state = "deferred" if paid["result"]["remaining_manifest"]["objectives"] else "completed"
        assert public["history_integration_resolved"] and public["history_integration_state"] == expected_state
        assert public["history_integration_unassessed_objective_count"] == len(
            paid["result"]["remaining_manifest"]["objectives"])
        assert public["history_integration_open"] is bool(paid["result"]["remaining_manifest"]["objectives"])
        assert public["history_funding"] == {"version": PERMISSION_VERSION,
                                             "authorization_root_job_id": donor["id"], "origin_job_id": old["id"]}
        validate("LearningHistorySummaries", {key: public[key] for key in (
            "history_funding", "history_attention") if key in public})
        assert "history_obligation_manifest" not in json.dumps(public)
        assert RuntimeClient(memory).learning_progress()["learning_phases"]["counts_complete"]
