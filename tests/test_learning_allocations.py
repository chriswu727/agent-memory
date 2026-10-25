import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_jobs import jobs as jobs

from agent_memory.helpers import DomainError, digest, uid
from agent_memory.learning_allocations import (
    AUTHORITY_FIELDS,
    CONTRACT_VERSION,
    ENCODING_PHASE,
    ENCODING_REPLAN_VERSION,
    HISTORY_PHASE,
    RECOVERY_POLICY,
    allocation_metadata,
    freeze_input,
    phase,
)


def test_metadata_declares_separate_shared_and_phase_caps():
    metadata = allocation_metadata("root")
    assert metadata["learning_contract_version"] == CONTRACT_VERSION
    assert phase(metadata) == ENCODING_PHASE
    assert metadata["authorization_root_job_id"] == "root"
    assert metadata["allocation_budget"] == {"max_model_calls": 4, "max_input_bytes": 393216}
    assert metadata["phase_budget"] == {"max_model_calls": 2, "max_input_bytes": 196608}
    recovery = allocation_metadata("retry", phase=HISTORY_PHASE, recovery=True)
    assert phase(recovery) == HISTORY_PHASE
    assert recovery["allocation_policy"] == RECOVERY_POLICY
    assert recovery["allocation_budget"] == recovery["phase_budget"]
    assert phase({"from_sequence": 1}) is None


@pytest.mark.parametrize("key", AUTHORITY_FIELDS)
@pytest.mark.parametrize("value", [None, [], {}, True, "unsupported"])
def test_partial_or_malformed_authority_never_falls_back_to_legacy(key, value):
    with pytest.raises(DomainError) as error:
        phase({key: value})
    assert error.value.code == "dependency_unavailable"


@pytest.mark.parametrize("changes", [
    {"learning_contract_version": "future"}, {"learning_phase": []},
    {"authorization_root_job_id": "private identifier"}, {"allocation_input_fingerprint": "bad"},
    {"allocation_budget": {"max_model_calls": 5, "max_input_bytes": 393216}},
    {"phase_budget": {"max_model_calls": True, "max_input_bytes": 196608}},
    {"phase_budget": {"max_model_calls": 2.0, "max_input_bytes": 196608}},
])
def test_complete_but_invalid_metadata_fails_closed(changes):
    with pytest.raises(DomainError):
        phase({**allocation_metadata("root"), **changes})


def params(harness):
    return harness.principal.tenant_id, harness.principal.principal_id, harness.brain


def insert(harness, *, selected_phase=ENCODING_PHASE, root=None, recovery=False,
           frozen=True, state="queued", extra=None, identifier=None):
    identifier = identifier or uid()
    payload = {"target": {"conversation_ref": "chat", "branch_ref": "main"},
               "from_sequence": 1, "through_sequence": 8,
               **allocation_metadata(identifier, phase=selected_phase, root_job_id=root, recovery=recovery),
               **(extra or {})}
    with harness.database.transaction(harness.principal) as conn:
        conn.execute(
            "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,state,payload) "
            "VALUES(%s,%s,%s,%s,'consolidate',%s,%s)",
            (*params(harness), identifier, state, Jsonb(payload)),
        )
        if frozen:
            freeze_input(conn, params(harness), identifier, input_fingerprint=digest([selected_phase, identifier]))
    return identifier


def row(harness, identifier):
    with harness.database.transaction(harness.principal) as conn:
        return conn.execute(
            "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params(harness), identifier),
        ).fetchone()


def reserve(harness, identifier, *, input_bytes=100, operation_id=None, claim=None):
    claim = claim or harness.store.claim(harness.principal, harness.brain, worker_id=uid(), job_id=identifier)
    result = harness.store.reserve_operation(
        harness.principal, harness.brain, identifier, lease_token=claim["lease_token"], fence=claim["fence"],
        operation_id=operation_id or uid(), request_fingerprint=digest("request"), input_bytes=input_bytes,
        max_model_calls=100, max_input_bytes=1_000_000,
    )
    return result, claim


def replan_child(harness, root, **changes):
    identifier = insert(harness, root=root, frozen=False, extra={
        "encoding_replan_version": ENCODING_REPLAN_VERSION, "recovery_parent_job_id": root, **changes,
    })
    with harness.database.transaction(harness.principal) as conn:
        conn.execute("UPDATE memory_jobs SET state='cancelled',last_error='context_stale',payload=payload || %s "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb({"recovery_job_id": identifier}), *params(harness), root))
    return identifier


@pytest.mark.integration
def test_unreserved_replan_encoding_and_history_charge_original_four_call_allocation(jobs):
    root = insert(jobs)
    child = replan_child(jobs, root)
    with jobs.database.transaction(jobs.principal) as conn:
        freeze_input(conn, params(jobs), child, input_fingerprint=digest("replacement plan"))
    _, lease = reserve(jobs, child)
    reserve(jobs, child, claim=lease)
    jobs.store.complete(jobs.principal, jobs.brain, child, lease_token=lease["lease_token"], fence=lease["fence"])
    history = insert(jobs, selected_phase=HISTORY_PHASE, root=root)
    _, lease = reserve(jobs, history)
    reserve(jobs, history, claim=lease)
    with pytest.raises(DomainError) as error:
        reserve(jobs, history, claim=lease)
    assert error.value.code == "budget_exceeded"
    assert row(jobs, root)["model_calls"] == 0
    assert row(jobs, child)["model_calls"] == row(jobs, history)["model_calls"] == 2


@pytest.mark.integration
@pytest.mark.parametrize("change", [
    {"encoding_replan_version": "unknown"}, {"from_sequence": 2}, {"through_sequence": 9},
    {"target": {"conversation_ref": "other", "branch_ref": "main"}},
])
def test_replan_marker_exact_scope_and_window_are_required_before_freezing(jobs, change):
    root = insert(jobs)
    child = replan_child(jobs, root, **change)
    with jobs.database.transaction(jobs.principal) as conn:
        with pytest.raises(DomainError):
            freeze_input(conn, params(jobs), child, input_fingerprint=digest("unbound plan"))
    assert row(jobs, child)["model_calls"] == 0


@pytest.mark.integration
def test_replan_cannot_hide_retired_ancestor_unknown_reservation_even_with_zero_counters(jobs):
    root = insert(jobs)
    reserve(jobs, root)
    child = replan_child(jobs, root)
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET model_calls=0,input_bytes=0 "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s", (*params(jobs), root))
        with pytest.raises(DomainError):
            freeze_input(conn, params(jobs), child, input_fingerprint=digest("new plan"))
    assert row(jobs, child)["model_calls"] == 0


@pytest.mark.integration
def test_phase_input_frozen_before_calls_and_cannot_be_rebound(jobs):
    identifier = insert(jobs, frozen=False)
    claim = jobs.store.claim(jobs.principal, jobs.brain, worker_id=uid(), job_id=identifier)
    with pytest.raises(DomainError) as error:
        reserve(jobs, identifier, claim=claim)
    assert error.value.code == "dependency_unavailable"
    fingerprint = digest("exact plan")
    with jobs.database.transaction(jobs.principal) as conn:
        first = freeze_input(conn, params(jobs), identifier, input_fingerprint=fingerprint)
        assert freeze_input(conn, params(jobs), identifier, input_fingerprint=fingerprint) == first
    reserve(jobs, identifier, claim=claim)
    with jobs.database.transaction(jobs.principal) as conn:
        with pytest.raises(DomainError) as error:
            freeze_input(conn, params(jobs), identifier, input_fingerprint=digest("different plan"))
        assert error.value.code == "idempotency_conflict"
    assert row(jobs, identifier)["model_calls"] == 1


@pytest.mark.integration
def test_history_children_share_phase_and_root_ledgers_including_unknown_calls(jobs):
    root = insert(jobs)
    first, root_claim = reserve(jobs, root)
    reserve(jobs, root, claim=root_claim)
    jobs.store.fail(jobs.principal, jobs.brain, root, lease_token=root_claim["lease_token"],
                    fence=root_claim["fence"], failure_code="provider_uncertain", retry=False)
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_jobs SET state='completed' WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*params(jobs), root))
    children = [insert(jobs, selected_phase=HISTORY_PHASE, root=root) for _ in range(3)]
    history, history_claim = reserve(jobs, children[0])
    replay, _ = reserve(jobs, children[0], operation_id=history["operation_id"], claim=history_claim)
    assert replay["newly_reserved"] is False
    reserve(jobs, children[1])
    with pytest.raises(DomainError) as error:
        reserve(jobs, children[2])
    assert error.value.code == "budget_exceeded" and not error.value.retryable
    assert row(jobs, children[2])["model_calls"] == 0
    assert jobs.store.get_operation(jobs.principal, jobs.brain, root, first["operation_id"])["outcome"] == "uncertain"
    assert row(jobs, root)["payload"]["allocation_input_fingerprint"] != row(jobs, children[0])["payload"]["allocation_input_fingerprint"]


@pytest.mark.integration
def test_phase_byte_cap_is_shared_across_separate_jobs(jobs):
    root = insert(jobs, state="completed")
    first = insert(jobs, selected_phase=HISTORY_PHASE, root=root)
    second = insert(jobs, selected_phase=HISTORY_PHASE, root=root)
    reserve(jobs, first, input_bytes=196608)
    with pytest.raises(DomainError) as error:
        reserve(jobs, second, input_bytes=1)
    assert error.value.code == "budget_exceeded"
    assert row(jobs, second)["input_bytes"] == 0


@pytest.mark.integration
def test_concurrent_history_reservations_cannot_overspend_last_phase_call(jobs):
    root = insert(jobs, state="completed")
    reserve(jobs, insert(jobs, selected_phase=HISTORY_PHASE, root=root))
    children = [insert(jobs, selected_phase=HISTORY_PHASE, root=root) for _ in range(2)]
    claims = [jobs.store.claim(jobs.principal, jobs.brain, worker_id=uid(), job_id=child) for child in children]
    barrier = Barrier(2)

    def attempt(pair):
        identifier, claim = pair
        barrier.wait()
        try:
            return reserve(jobs, identifier, claim=claim)[0]["newly_reserved"]
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, zip(children, claims, strict=True)))
    assert results.count(True) == results.count("budget_exceeded") == 1


@pytest.mark.integration
@pytest.mark.parametrize("state", ["queued", "running", "failed", "cancelled", "superseded"])
def test_history_cannot_spend_after_incomplete_or_uncertain_encoding(jobs, state):
    root = insert(jobs, state=state)
    history = insert(jobs, selected_phase=HISTORY_PHASE, root=root)
    with pytest.raises(DomainError) as error:
        reserve(jobs, history)
    assert error.value.code == "dependency_unavailable"
    assert row(jobs, history)["model_calls"] == 0


@pytest.mark.integration
def test_shared_root_cannot_fund_another_conversation_or_recovery_phase(jobs):
    root = insert(jobs, state="completed")
    wrong_scope = insert(jobs, selected_phase=HISTORY_PHASE, root=root, frozen=False,
                         extra={"target": {"conversation_ref": "other", "branch_ref": "main"}})
    recovery = insert(jobs, state="completed", recovery=True)
    wrong_phase = insert(jobs, selected_phase=HISTORY_PHASE, root=recovery, recovery=True, frozen=False)
    for identifier in (wrong_scope, wrong_phase):
        with pytest.raises(DomainError) as error:
            with jobs.database.transaction(jobs.principal) as conn:
                freeze_input(conn, params(jobs), identifier, input_fingerprint=digest("plan"))
        assert error.value.code == "dependency_unavailable"
        assert row(jobs, identifier)["model_calls"] == 0


@pytest.mark.integration
@pytest.mark.parametrize("field,value", [
    ("authorization_root_job_id", "other"), ("learning_phase", HISTORY_PHASE),
    ("allocation_input_fingerprint", digest("changed")), ("learning_contract_version", None),
])
def test_database_freezes_authority_and_input_identity(jobs, field, value):
    identifier = insert(jobs)
    payload = copy.deepcopy(row(jobs, identifier)["payload"])
    payload[field] = value
    with pytest.raises(psycopg.errors.CheckViolation):
        with jobs.database.transaction(jobs.principal) as conn:
            conn.execute("UPDATE memory_jobs SET payload=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(payload), *params(jobs), identifier))


@pytest.mark.integration
def test_scoped_root_constraint_is_deferred_for_restore_order(jobs):
    root, child = uid(), uid()
    with jobs.database.transaction(jobs.principal) as conn:
        for identifier, selected_phase in ((child, HISTORY_PHASE), (root, ENCODING_PHASE)):
            conn.execute(
                "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload) "
                "VALUES(%s,%s,%s,%s,'consolidate',%s)",
                (*params(jobs), identifier, Jsonb(allocation_metadata(identifier, phase=selected_phase, root_job_id=root))),
            )
    assert row(jobs, child)["authorization_root_job_id"] == root
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert(jobs, selected_phase=HISTORY_PHASE, root=uid(), frozen=False)


@pytest.mark.integration
def test_legacy_operation_still_uses_original_job_budget(jobs):
    jobs.claim()
    assert jobs.reserve()["newly_reserved"]
    assert phase(row(jobs, jobs.job_id)["payload"]) is None


@pytest.mark.integration
def test_new_phase_explicit_recovery_preserves_exact_plan_and_gets_only_two_calls(jobs):
    source = "source-version-bound"
    parent = insert(jobs, selected_phase=HISTORY_PHASE, recovery=True, state="cancelled")
    frozen = {"source_snapshot": [{"event_id": source, "revision": 7}],
              "working_snapshot": {"private": "exact frozen request"}, "proposal_mode": "history-integration-v1"}
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_brains SET durable_sequence=8 WHERE tenant_id=%s AND owner_id=%s AND id=%s", params(jobs))
        conn.execute("UPDATE memory_jobs SET result=%s,source_event_ids=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (Jsonb(frozen), [source], *params(jobs), parent))
    child = jobs.store.retry_learning(jobs.principal, jobs.brain, parent, request_id="recover", expected_fence=0)
    replay = jobs.store.retry_learning(jobs.principal, jobs.brain, parent, request_id="recover", expected_fence=0)
    assert replay["id"] == child["id"]
    recovered = row(jobs, child["id"])
    assert recovered["result"] == frozen and recovered["source_event_ids"] == [source]
    assert phase(recovered["payload"]) == HISTORY_PHASE
    assert recovered["payload"]["authorization_root_job_id"] == child["id"]
    assert "allocation_input_fingerprint" not in recovered["payload"]
    assert recovered["payload"]["recovery_input_fingerprint"] == row(jobs, parent)["payload"]["allocation_input_fingerprint"]
    assert recovered["max_model_calls"] == 2 and recovered["max_input_bytes"] == 196608
    with jobs.database.transaction(jobs.principal) as conn:
        freeze_input(conn, params(jobs), child["id"], input_fingerprint=digest("new explicitly authorized policy"))
    _, claim = reserve(jobs, child["id"])
    reserve(jobs, child["id"], claim=claim)
    with pytest.raises(DomainError) as error:
        reserve(jobs, child["id"], claim=claim)
    assert error.value.code == "budget_exceeded"


@pytest.mark.integration
@pytest.mark.parametrize("selected_phase", [ENCODING_PHASE, HISTORY_PHASE])
def test_erased_frozen_phase_snapshot_does_not_authorize_an_unusable_retry(jobs, selected_phase):
    parent = insert(jobs, selected_phase=selected_phase, recovery=True, state="superseded")
    with pytest.raises(DomainError) as error:
        jobs.store.retry_learning(jobs.principal, jobs.brain, parent, request_id="recover", expected_fence=0)
    assert error.value.code == "context_stale"
    retained = row(jobs, parent)
    assert "recovery_job_id" not in retained["payload"]
    assert retained["model_calls"] == retained["input_bytes"] == 0
    with jobs.database.transaction(jobs.principal) as conn:
        assert conn.execute("SELECT count(*) AS count FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                            params(jobs)).fetchone()["count"] == 2
