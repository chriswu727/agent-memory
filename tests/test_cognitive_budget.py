from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from test_jobs import jobs as jobs
from test_learning import learning as learning

from agent_memory.cognitive_budget import DEFAULT_POLICY
from agent_memory.helpers import DomainError, Principal, uid
from agent_memory.jobs import JobStore
from agent_memory.runtime_client import RuntimeClient
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


def status(jobs):
    return jobs.store.cognitive_budget(jobs.principal, jobs.brain)


def configure(jobs, **options):
    values = {"request_id": uid(), "expected_revision": status(jobs)["revision"],
              "policy": dict(DEFAULT_POLICY), **options}
    return jobs.store.configure_cognitive_budget(jobs.principal, jobs.brain, **values)


def test_default_read_is_read_only_and_configuration_replay_survives_reopen(jobs):
    before = status(jobs)
    assert before["policy"] == DEFAULT_POLICY
    assert before["revision"] == 0 and not before["is_explicit"]
    assert before["used"] == {"model_calls": 0, "input_bytes": 0}
    with jobs.database.transaction(jobs.principal) as conn:
        assert conn.execute("SELECT 1 FROM memory_cognitive_budgets WHERE tenant_id=%s AND brain_id=%s",
                            (jobs.principal.tenant_id, jobs.brain)).fetchone() is None
    request = {"request_id": "budget-1", "expected_revision": 0,
               "policy": {"window_seconds": 7200, "max_model_calls": 200, "max_input_bytes": 4_000_000}}
    first = configure(jobs, **request)
    assert first["configuration"] == {"revision": 1, "is_explicit": True, "policy": request["policy"]}
    jobs.store = JobStore(Database(jobs.database.dsn))
    assert configure(jobs, **request) == first
    configure(jobs, policy={**DEFAULT_POLICY, "max_model_calls": 20})
    assert status(jobs)["revision"] == 2
    assert configure(jobs, **request) == first
    assert status(jobs)["revision"] == 2
    with pytest.raises(DomainError, match="another change"):
        configure(jobs, **{**request, "policy": DEFAULT_POLICY})
    with pytest.raises(DomainError, match="current revision"):
        configure(jobs, expected_revision=0)


def test_authorized_policy_survives_defaults_without_raising_job_caps(jobs):
    configure(jobs, policy={"window_seconds": 3600, "max_model_calls": 101, "max_input_bytes": 4_000_000})
    jobs.claim(lease_seconds=300)
    for _ in range(101):
        jobs.reserve(input_bytes=20_000, max_model_calls=102, max_input_bytes=2_040_000)
    current = status(jobs)
    assert current["revision"] == 1
    assert current["used"] == {"model_calls": 101, "input_bytes": 2_020_000}
    assert current["remaining"] == {"model_calls": 0, "input_bytes": 1_980_000}
    with pytest.raises(DomainError, match="background budget window"):
        jobs.reserve(input_bytes=20_000, max_model_calls=102, max_input_bytes=2_040_000)
    configure(jobs, policy={"window_seconds": 3600, "max_model_calls": 200, "max_input_bytes": 5_000_000})
    jobs.reserve(input_bytes=20_000, max_model_calls=999, max_input_bytes=5_000_000)
    with pytest.raises(DomainError, match="Durable job budget"):
        jobs.reserve(input_bytes=1, max_model_calls=999, max_input_bytes=5_000_000)


def test_lower_limits_keep_unknown_reservations_and_zero_stops_new_calls(jobs):
    jobs.claim()
    operation = jobs.reserve()
    jobs.call("settle_operation", **jobs.lease(), operation_id=operation["operation_id"], outcome="uncertain")
    configure(jobs, policy={**DEFAULT_POLICY, "max_model_calls": 0, "max_input_bytes": 0})
    current = status(jobs)
    assert current["used"] == {"model_calls": 1, "input_bytes": 100}
    assert current["remaining"] == {"model_calls": 0, "input_bytes": 0}
    assert not jobs.reserve(operation_id=operation["operation_id"])["newly_reserved"]
    with pytest.raises(DomainError, match="background budget window"):
        jobs.reserve()
    assert jobs.store.cognitive_usage(jobs.principal, jobs.brain)["usage_unknown_count"] == 1
    assert jobs.call("get")["model_calls"] == 1


@pytest.mark.parametrize("usage,known", [
    ({"input_tokens": 12, "total_tokens": 12}, True),
    ({"input_tokens": 12, "total_tokens": 13}, False),
    ({"input_tokens": True, "total_tokens": 1}, False),
    ({"input_tokens": 12}, False),
    (None, False),
])
def test_embedding_reservations_share_limits_and_have_distinct_usage(jobs, usage, known):
    from psycopg.types.json import Jsonb

    jobs.claim()
    jobs.reserve(operation_id="embedding:1")
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_cognitive_operations SET outcome='completed',usage=%s "
                     "WHERE tenant_id=%s AND brain_id=%s",
                     (Jsonb(usage) if usage is not None else None, jobs.principal.tenant_id, jobs.brain))
    configure(jobs, policy={**DEFAULT_POLICY, "max_model_calls": 1})
    with pytest.raises(DomainError, match="background budget window"):
        jobs.reserve()
    result = jobs.store.cognitive_usage(jobs.principal, jobs.brain)
    assert result["scope"] == status(jobs)["scope"] == "cognition_and_embeddings"
    assert result["reservation_count"] == 1
    assert result["counts_complete"] is known
    assert result["phases"][0]["phase"] == "embedding"
    assert result["reported_tokens"] == {"input_tokens": 12 if known else 0, "output_tokens": 0,
                                         "total_tokens": 12 if known else 0}


def test_policy_change_preserves_history_when_window_is_shortened_and_extended(jobs):
    jobs.claim()
    jobs.reserve()
    with jobs.database.transaction(jobs.principal) as conn:
        conn.execute("UPDATE memory_cognitive_operations SET created_at=clock_timestamp()-interval '2 hours' "
                     "WHERE tenant_id=%s AND brain_id=%s", (jobs.principal.tenant_id, jobs.brain))
    configure(jobs, policy={**DEFAULT_POLICY, "window_seconds": 10800})
    assert status(jobs)["used"]["model_calls"] == 1
    configure(jobs, policy=DEFAULT_POLICY)
    assert status(jobs)["used"]["model_calls"] == 0
    configure(jobs, policy={**DEFAULT_POLICY, "window_seconds": 10800})
    assert status(jobs)["used"]["model_calls"] == 1
    assert jobs.store.cognitive_usage(jobs.principal, jobs.brain)["reservation_count"] == 1


def test_legacy_tightening_changes_revision_but_unchanged_reservations_do_not(jobs):
    jobs.claim()
    jobs.reserve()
    assert status(jobs)["revision"] == 0
    jobs.reserve(window_max_model_calls=50)
    current = status(jobs)
    assert current["revision"] == 1 and not current["is_explicit"]
    assert current["policy"]["max_model_calls"] == 50
    with pytest.raises(DomainError, match="current revision"):
        configure(jobs, expected_revision=0)
    jobs.reserve(window_max_model_calls=99)
    assert status(jobs)["revision"] == 1


def test_nondefault_initial_reservation_invalidates_previously_read_default_revision(jobs):
    assert status(jobs)["revision"] == 0
    jobs.claim()
    jobs.reserve(window_max_model_calls=3)
    assert status(jobs)["revision"] == 1
    with pytest.raises(DomainError, match="current revision"):
        configure(jobs, expected_revision=0)


def test_concurrent_changes_have_one_revision_winner(jobs):
    barrier = Barrier(2)

    def change(index):
        barrier.wait()
        try:
            return configure(jobs, expected_revision=0, policy={**DEFAULT_POLICY, "max_model_calls": index})
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, (10, 20)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "version_conflict" in results
    assert status(jobs)["revision"] == 1


def test_replay_and_reservations_serialize_against_budget_changes(jobs):
    request = {"request_id": uid(), "expected_revision": 0, "policy": DEFAULT_POLICY}
    barrier = Barrier(2)

    def replay(_):
        barrier.wait()
        return configure(jobs, **request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(replay, (0, 1))
    assert first == second and status(jobs)["revision"] == 1
    jobs.claim()
    barrier = Barrier(2)

    def change():
        barrier.wait()
        return configure(jobs, policy={**DEFAULT_POLICY, "max_model_calls": 0})

    def reserve():
        barrier.wait()
        try:
            return jobs.reserve()
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        updated, reservation = pool.submit(change), pool.submit(reserve)
        assert updated.result()["configuration"]["policy"]["max_model_calls"] == 0
        result = reservation.result()
    assert status(jobs)["used"]["model_calls"] == (0 if result == "budget_exceeded" else 1)
    with pytest.raises(DomainError, match="background budget window"):
        jobs.reserve()


@pytest.mark.parametrize("principal_kind", ["other_tenant", "other_owner"])
def test_policy_and_receipts_are_isolated(jobs, principal_kind):
    configure(jobs, request_id="private-request")
    principal = Principal(uid() if principal_kind == "other_tenant" else jobs.principal.tenant_id, uid())
    with pytest.raises(DomainError, match="unavailable"):
        jobs.store.cognitive_budget(principal, jobs.brain)
    with pytest.raises(DomainError, match="unavailable"):
        jobs.store.configure_cognitive_budget(principal, jobs.brain, request_id="private-request",
                                              expected_revision=0, policy=DEFAULT_POLICY)
    with jobs.database.transaction(principal) as conn:
        for table in ("memory_cognitive_budgets", "memory_cognitive_budget_changes"):
            assert conn.execute(f"SELECT * FROM {table} WHERE brain_id=%s", (jobs.brain,)).fetchall() == []


def test_http_python_sdk_can_read_and_configure_without_learning_provider_calls(learning):
    client = RuntimeClient(learning.memory)
    assert client.cognitive_budget()["revision"] == 0
    request = {"request_id": "configure-test", "expected_revision": 0, **DEFAULT_POLICY}
    result = client.configure_cognitive_budget(**request)
    assert result["configuration"]["revision"] == 1
    assert client.configure_cognitive_budget(**request) == result
    assert client.cognitive_budget()["is_explicit"]
    assert learning.calls == []
    assert client.list() == []


@pytest.mark.parametrize("change", [
    {"request_id": " "}, {"expected_revision": -1}, {"expected_revision": True},
    {"expected_revision": 2**63 - 1}, {"request_id": "x" * 257}, {"unexpected": 1},
    {"policy": {**DEFAULT_POLICY, "max_model_calls": True}},
    {"policy": {**DEFAULT_POLICY, "max_model_calls": "200"}},
    {"policy": {**DEFAULT_POLICY, "max_model_calls": 1_000_001}},
    {"policy": {**DEFAULT_POLICY, "max_input_bytes": 1_000_000_000_001}},
    {"policy": {**DEFAULT_POLICY, "window_seconds": 0}},
    {"policy": {**DEFAULT_POLICY, "window_seconds": 2_592_001}},
    {"policy": {"window_seconds": 3600}}, {"policy": {**DEFAULT_POLICY, "extra": 1}},
])
def test_http_rejects_invalid_policy_without_writes(learning, change):
    request = {"request_id": "configuration", "expected_revision": 0, "policy": DEFAULT_POLICY, **change}
    with pytest.raises(httpx.HTTPStatusError) as error:
        learning.memory._request("PUT", f"v1/brains/{learning.brain}/runtime/cognitive-budget", json=request)
    assert error.value.response.status_code == 422
    assert RuntimeClient(learning.memory).cognitive_budget()["revision"] == 0
    assert learning.calls == []
