import copy
import json

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_learning_integration import encode, setup_history, setup_legacy_history
from test_learning_windows import claim, execute, jobs

from membrain.helpers import DomainError, digest, uid
from membrain.jobs import JobStore
from membrain.learning_allocations import HISTORY_PHASE, phase
from membrain.learning_integration import reconsider
from membrain.learning_progress import learning_progress
from membrain.learning_windows import recover_windows

pytestmark = pytest.mark.integration


def tick(harness):
    with harness.database.transaction(harness.principal) as conn:
        brain = harness.service._brain(conn, harness.principal, harness.brain)
        recover_windows(conn, harness.service, harness.principal, brain)
        reconsider(conn, harness.service, harness.principal, brain)


def bump_generation(harness):
    with harness.database.transaction(harness.principal) as conn:
        conn.execute("UPDATE memory_brains SET dependency_generation=dependency_generation+1 "
                     "WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                     harness.service._params(harness.principal, harness.brain))


def history_jobs(harness):
    return [row for row in jobs(harness) if phase(row["payload"]) == HISTORY_PHASE]


def leaf(harness, identifier):
    seen = set()
    row = harness.job(identifier)
    while "integration_continuation_job_id" in row["payload"]:
        assert row["id"] not in seen and len(seen) < 8
        seen.add(row["id"])
        row = harness.job(row["payload"]["integration_continuation_job_id"])
    return row


def objectives(snapshot):
    return {item["objective_id"] for field in ("integration_manifest", "remaining_manifest")
            for item in snapshot[field]["objectives"]}


def operations(harness):
    with harness.database.transaction(harness.principal) as conn:
        return conn.execute(
            "SELECT job_id,operation_id,request_fingerprint,input_bytes,outcome FROM memory_cognitive_operations "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s ORDER BY job_id,operation_id",
            harness.service._params(harness.principal, harness.brain),
        ).fetchall()


def test_legacy_unpaid_stale_history_replans_with_same_authority_and_preserves_all_remaining_objectives(learning, monkeypatch):
    _, requests = setup_legacy_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    original = learning.job(parent["result"]["history_integration_job_id"])
    before = copy.deepcopy(original["result"])
    assert len(before["integration_manifest"]["objectives"]) == 3
    assert len(before["remaining_manifest"]["objectives"]) == 1
    previous_operations = operations(learning)
    stale_claim = claim(learning, original["id"])
    bump_generation(learning)
    with pytest.raises(DomainError) as caught:
        execute(learning, stale_claim)
    assert caught.value.code == "context_stale"
    assert learning.job(original["id"])["state"] == "cancelled" and len(requests) == 2
    tick(learning)
    replacement = leaf(learning, original["id"])
    assert replacement["id"] != original["id"] and replacement["state"] == "queued"
    assert replacement["payload"]["authorization_root_job_id"] == parent["id"]
    assert replacement["payload"]["target"] == original["payload"]["target"]
    assert replacement["payload"]["encoding_parent_job_id"] == parent["id"]
    assert replacement["payload"]["allocation_budget"] == original["payload"]["allocation_budget"]
    assert replacement["result"]["integration_manifest"]["input_refs"] == before["integration_manifest"]["input_refs"]
    assert replacement["result"]["integration_manifest"]["through_sequence"] == before["integration_manifest"]["through_sequence"]
    assert objectives(replacement["result"]) == objectives(before)
    assert replacement["result"]["dependency_generation"] == before["dependency_generation"] + 1
    assert replacement["payload"]["allocation_input_fingerprint"] != original["payload"]["allocation_input_fingerprint"]
    assert learning.job(original["id"])["payload"]["allocation_input_fingerprint"] == original["payload"]["allocation_input_fingerprint"]
    assert learning.job(parent["id"])["payload"]["history_allocation_job_id"] == replacement["id"]
    assert operations(learning) == previous_operations
    for _ in range(3):
        tick(learning)
    assert leaf(learning, original["id"])["id"] == replacement["id"] and len(history_jobs(learning)) == 2
    store = JobStore(learning.database)
    assert store.claim(learning.principal, learning.brain, worker_id=uid(), job_id=original["id"], max_attempts=100) is None
    with pytest.raises(DomainError) as caught:
        store.reserve_operation(learning.principal, learning.brain, original["id"],
                                lease_token=stale_claim["lease_token"], fence=stale_claim["fence"],
                                operation_id="stale-provider", request_fingerprint=digest("stale"), input_bytes=1,
                                max_model_calls=2, max_input_bytes=196608)
    assert caught.value.code == "lease_conflict"
    completed = execute(learning, claim(learning, replacement["id"]))
    assert completed["state"] == "completed" and len(requests) == 4
    untouched = leaf(learning, completed["id"])
    assert untouched["state"] == "deferred" and untouched["model_calls"] == 0
    assert {item["objective_id"] for item in untouched["result"]["integration_manifest"]["objectives"]} == {
        item["objective_id"] for item in before["remaining_manifest"]["objectives"]}
    assert len(operations(learning)) == 4
    assert "history_obligation_manifest" not in json.dumps(store.get(learning.principal, learning.brain, replacement["id"]))


def test_legacy_frozen_budget_defer_retains_private_obligation_when_stale_snapshot_was_cleared(learning, monkeypatch):
    _, requests = setup_legacy_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    original = learning.job(parent["result"]["history_integration_job_id"])
    store = JobStore(learning.database)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="pause-history", expected_revision=0,
                                     policy={"window_seconds": 3600, "max_model_calls": 2, "max_input_bytes": 2_000_000})
    waiting = execute(learning, claim(learning, original["id"]))
    assert waiting["state"] == "deferred" and waiting["model_calls"] == 0
    bump_generation(learning)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET result=NULL,state='cancelled',last_error='context_stale' "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*learning.service._params(learning.principal, learning.brain), original["id"]))
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="resume-history", expected_revision=1,
                                     policy={"window_seconds": 3600, "max_model_calls": 10, "max_input_bytes": 2_000_000})
    tick(learning)
    replacement = leaf(learning, original["id"])
    assert replacement["id"] != original["id"] and replacement["state"] == "queued"
    assert replacement["payload"]["authorization_root_job_id"] == parent["id"]
    assert replacement["result"]["integration_manifest"] == original["result"]["integration_manifest"]
    assert replacement["result"]["remaining_manifest"] == original["result"]["remaining_manifest"]
    assert len(requests) == 2 and len(operations(learning)) == 2
    assert execute(learning, claim(learning, replacement["id"]))["state"] == "completed"
    assert len(requests) == 4


@pytest.mark.parametrize("outcome,reset_counters", [
    ("reserved", False), ("uncertain", False), ("completed", False), ("reserved", True),
])
def test_any_recorded_history_operation_blocks_automatic_replanning(learning, monkeypatch, outcome, reset_counters):
    _, requests = setup_history(learning, monkeypatch)
    parent = encode(learning)
    original = learning.job(parent["result"]["history_integration_job_id"])
    job = claim(learning, original["id"])
    store = JobStore(learning.database)
    arguments = {"lease_token": job["lease_token"], "fence": job["fence"], "operation_id": "recorded-attempt"}
    store.reserve_operation(learning.principal, learning.brain, job["id"], **arguments,
                            request_fingerprint=digest("possibly sent input"), input_bytes=100,
                            max_model_calls=2, max_input_bytes=196608)
    if outcome != "reserved":
        store.settle_operation(learning.principal, learning.brain, job["id"], **arguments, outcome=outcome)
    bump_generation(learning)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_jobs SET state='cancelled',last_error='context_stale',result=NULL,"
                     "lease_token_digest=NULL,lease_expires_at=NULL,worker_id=NULL "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*learning.service._params(learning.principal, learning.brain), job["id"]))
        if reset_counters:
            conn.execute("UPDATE memory_jobs SET model_calls=0,input_bytes=0 "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (*learning.service._params(learning.principal, learning.brain), job["id"]))
    retained = operations(learning)
    for _ in range(3):
        tick(learning)
    assert len(history_jobs(learning)) == 1 and leaf(learning, original["id"])["id"] == original["id"]
    assert operations(learning) == retained and len(requests) == 2
    assert learning.job(parent["id"])["payload"]["history_allocation_job_id"] == original["id"]


@pytest.mark.parametrize("change", ["input_erasure", "input_correction", "endpoint_erasure", "endpoint_content"])
def test_never_paid_replan_cannot_substitute_unavailable_original_inputs_or_endpoints(learning, monkeypatch, change):
    memories, requests = setup_history(learning, monkeypatch)
    parent = encode(learning)
    original = learning.job(parent["result"]["history_integration_job_id"])
    event_id = original["result"]["integration_manifest"]["input_refs"][0]["event_id"]
    if change in {"input_erasure", "endpoint_erasure"}:
        identifier = event_id if change == "input_erasure" else memories[0]["evidence_refs"][0]["event_id"]
        learning.memory.forget([identifier], kind="events", mode="erase_content", expected_scope_generation=1)
    elif change == "input_correction":
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            old = learning.service._events(conn, learning.principal, brain, event_ids=[event_id])[0]
        learning.observe("The corrected input must not replace the original comparison.",
                         source_key=old["observed"]["source_event_key"], revision=2)
    else:
        bump_generation(learning)
        with learning.database.transaction(learning.principal) as conn:
            altered = copy.deepcopy(memories[0])
            altered["statement"] = "A materially different report."
            conn.execute("UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(altered), *learning.service._params(learning.principal, learning.brain), altered["memory_id"]))
            conn.execute("UPDATE memory_jobs SET state='cancelled',last_error='context_stale',result=NULL "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (*learning.service._params(learning.principal, learning.brain), original["id"]))
    for _ in range(3):
        tick(learning)
    assert leaf(learning, original["id"])["id"] == original["id"]
    assert len(history_jobs(learning)) == 1 and learning.job(original["id"])["model_calls"] == 0
    assert len(requests) == 2 and len(operations(learning)) == 2


def test_never_paid_history_scope_is_immutable_before_replanning(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch)
    parent = encode(learning)
    original = learning.job(parent["result"]["history_integration_job_id"])
    recorded = operations(learning)
    with pytest.raises(psycopg.errors.CheckViolation):
        with learning.database.transaction(learning.principal) as conn:
            conn.execute("UPDATE memory_jobs SET payload=jsonb_set(payload,'{target,conversation_ref}','\"elsewhere\"') "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (*learning.service._params(learning.principal, learning.brain), original["id"]))
    assert learning.job(original["id"])["payload"] == original["payload"]
    for _ in range(3):
        tick(learning)
    assert leaf(learning, original["id"])["id"] == original["id"]
    assert len(history_jobs(learning)) == 1 and learning.job(original["id"])["model_calls"] == 0
    assert operations(learning) == recorded and len(requests) == 2


def test_legacy_malformed_paid_selection_stays_open_while_independent_unattempted_remainder_can_progress(learning, monkeypatch):
    _, requests = setup_legacy_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    frozen = learning.job(child_id)["result"]
    original_propose = learning.service.semantic_processor.propose
    failed_objective = frozen["integration_manifest"]["objectives"][0]["objective_id"]

    def malformed_first_claim(request):
        response = json.loads(original_propose(request))
        if "integration_manifest" in json.loads(request.messages[-1]["content"]):
            response["claims"][0]["evidence_refs"][0]["source_content_sha256"] = "f" * 64
        return json.dumps(response)

    learning.service.semantic_processor.propose = malformed_first_claim
    partial = execute(learning, claim(learning, child_id))
    assert partial["state"] == "completed" and partial["result"]["status"] == "partial"
    assert any(item["reason"] == "invalid_proposal" for item in partial["result"]["rejected"])
    coverage = partial["result"]["history_integration"]
    assert next(item for item in coverage["objectives"] if item["objective_id"] == failed_objective)["assessed"] is False
    assert coverage["assessed_count"] < len(coverage["objectives"])
    remainder = leaf(learning, child_id)
    assert remainder["id"] != child_id and remainder["state"] == "deferred" and remainder["model_calls"] == 0
    assert remainder["result"]["integration_manifest"] == frozen["remaining_manifest"]
    before = operations(learning)
    learning.service.semantic_processor.propose = original_propose
    for _ in range(3):
        tick(learning)
    assert operations(learning) == before and len(requests) == 4
    donor = encode(learning)
    funded = leaf(learning, child_id)
    assert funded["state"] == "queued" and funded["payload"]["authorization_root_job_id"] == donor["id"]
    assert funded["result"]["integration_manifest"] == frozen["remaining_manifest"]
    assert execute(learning, claim(learning, funded["id"]))["state"] == "completed"
    assert learning.job(child_id)["result"]["history_integration"] == coverage
    historical = [body["integration_manifest"] for request_phase, body in requests
                  if request_phase == "proposal" and "integration_manifest" in body
                  and body["integration_manifest"]["input_fingerprint"] == frozen["integration_manifest"]["input_fingerprint"]]
    compared = [item["objective_id"] for manifest in historical for item in manifest["objectives"]]
    assert len(compared) == len(set(compared)) == 4 and compared.count(failed_objective) == 1
    with learning.database.transaction(learning.principal) as conn:
        progress = learning_progress(conn, learning.principal, learning.brain, configured=True)
    assert progress["learning_phases"]["counts_complete"] is True
    assert progress["learning_phases"]["unknown_count"] == 0
    assert progress["learning_phases"]["history_integration_open_count"] >= 1
    public = JobStore(learning.database).get(learning.principal, learning.brain, child_id)["learning_summary"]
    assert public["phase_metadata_complete"] is True
    assert public["history_integration"]["assessed_count"] < public["history_integration"]["objective_count"]


@pytest.mark.parametrize("failure", ["invalid_selected", "provider_timeout"])
def test_legacy_structured_paid_failure_delegates_only_never_attempted_comparisons(learning, monkeypatch, failure):
    _, requests = setup_legacy_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    frozen = learning.job(child_id)["result"]
    original_propose = learning.service.semantic_processor.propose

    def failed_selection(request):
        response = json.loads(original_propose(request))
        if "integration_manifest" in json.loads(request.messages[-1]["content"]):
            if failure == "provider_timeout":
                raise TimeoutError("Synthetic unavailable response")
            for candidate in response["claims"]:
                candidate["evidence_refs"][0]["source_content_sha256"] = "f" * 64
        return json.dumps(response)

    learning.service.semantic_processor.propose = failed_selection
    stopped = execute(learning, claim(learning, child_id))
    assert stopped["state"] == "cancelled" and stopped["model_calls"] == 1
    assert stopped["last_error"] == ("provider_failed" if failure == "provider_timeout" else "invalid_proposal")
    assert stopped["result"]["history_integration"]["assessed_count"] == 0
    retained_operations = [item for item in operations(learning) if item["job_id"] == child_id]
    remaining = leaf(learning, child_id)
    assert remaining["id"] != child_id and remaining["state"] == "deferred" and remaining["model_calls"] == 0
    assert remaining["result"]["integration_manifest"] == frozen["remaining_manifest"]
    learning.service.semantic_processor.propose = original_propose
    for _ in range(3):
        tick(learning)
    assert len(requests) == 3 and len(operations(learning)) == 3
    donor = encode(learning)
    funded = leaf(learning, child_id)
    assert funded["state"] == "queued" and funded["payload"]["authorization_root_job_id"] == donor["id"]
    assert funded["result"]["integration_manifest"] == frozen["remaining_manifest"]
    assert execute(learning, claim(learning, funded["id"]))["state"] == "completed"
    assert [item for item in operations(learning) if item["job_id"] == child_id] == retained_operations
    assert len(requests) == 7 and len(operations(learning)) == 7
    historical = [body["integration_manifest"] for request_phase, body in requests
                  if request_phase == "proposal" and "integration_manifest" in body
                  and body["integration_manifest"]["input_fingerprint"] == frozen["integration_manifest"]["input_fingerprint"]]
    compared = [item["objective_id"] for manifest in historical for item in manifest["objectives"]]
    assert len(compared) == len(set(compared)) == 4
    with learning.database.transaction(learning.principal) as conn:
        progress = learning_progress(conn, learning.principal, learning.brain, configured=True)
    assert progress["learning_phases"]["counts_complete"] is True
    assert progress["learning_phases"]["history_integration_open_count"] >= 1


def test_explicit_history_recovery_self_root_can_replan_before_its_first_call(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch)
    parent = encode(learning)
    original_id = parent["result"]["history_integration_job_id"]
    store = JobStore(learning.database)
    first = claim(learning, original_id)
    store.fail(learning.principal, learning.brain, original_id,
               lease_token=first["lease_token"], fence=first["fence"], failure_code="context_stale", retry=False)
    recovery = store.retry_learning(learning.principal, learning.brain, original_id,
                                    request_id="explicit-history-recovery", expected_fence=first["fence"])
    authorized = learning.job(recovery["id"])
    assert authorized["payload"]["authorization_root_job_id"] == authorized["id"]
    assert authorized["payload"]["allocation_budget"] == {"max_model_calls": 2, "max_input_bytes": 196608}
    bump_generation(learning)
    with pytest.raises(DomainError) as caught:
        execute(learning, claim(learning, authorized["id"]))
    assert caught.value.code == "context_stale" and len(requests) == 2
    tick(learning)
    replacement = leaf(learning, authorized["id"])
    assert replacement["id"] != authorized["id"] and replacement["state"] == "queued"
    assert replacement["payload"]["authorization_root_job_id"] == authorized["id"]
    assert replacement["payload"]["allocation_policy"] == authorized["payload"]["allocation_policy"]
    assert execute(learning, claim(learning, replacement["id"]))["state"] == "completed"
    assert learning.job(authorized["id"])["model_calls"] == learning.job(original_id)["model_calls"] == 0
    assert len(requests) == len(operations(learning)) == 4
