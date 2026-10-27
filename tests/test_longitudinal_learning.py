import copy
import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from test_continuous_learning_acceptance import (
    NOTEBOOK,
    ROUTINES,
    GroundedCognition,
    operations,
    restarted_service,
    turn,
)
from test_learning import learning as learning
from test_learning_windows import jobs

from membrain.helpers import digest
from membrain.jobs import JobStore
from membrain.learning_allocations import ENCODING_PHASE, HISTORY_PHASE, phase

_SPEC = importlib.util.spec_from_file_location(
    "longitudinal_learning", Path(__file__).parents[1] / "examples" / "longitudinal_learning.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
process_learning = _MODULE.process_learning

pytestmark = pytest.mark.integration


def process(harness, sources, *, max_jobs=16, boundary=None, record=None):
    events, boundaries = [], []

    def before():
        boundaries.append(len(events))
        if boundary:
            boundary()

    def save(report):
        row = harness.job(report["job_id"])
        assert report["execution_authority"]["payload_sha256"] == digest(report["execution_authority"]["payload"])
        if report["record_type"] == "claimed":
            assert row["state"] == "running" and row["attempts"] == row["fence"] == 1
            assert operations(harness, row["id"]) == []
        else:
            assert events[-1]["record_type"] == "claimed" and events[-1]["job_id"] == report["job_id"]
        events.append(copy.deepcopy(report))
        if record:
            record(report)

    result = process_learning(harness.service, harness.principal, harness.brain, conversation_id="continuous",
                              current_sources=sources, boundary=before, record=save, max_jobs=max_jobs)
    assert len(events) == 2 * len(result["jobs"])
    assert boundaries == list(range(0, len(events), 2))
    assert len({item["job_id"] for item in result["jobs"]}) == len(result["jobs"])
    return result, events


def refs(sources):
    return {(item["event_id"], item["revision"])
            for name in ("input_events", "output_events") for item in sources[name]}


def encoded_jobs(result):
    return [item for item in result["jobs"] if item["learning_phase"] == ENCODING_PHASE]


def test_two_turns_in_one_conversation_use_real_independent_windows_and_durable_authority(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, first = turn(memory, ROUTINES[0], "first")
        first_result, _ = process(learning, first)
        _, second = turn(memory, ROUTINES[1], "second")
        second_result, _ = process(learning, second)
    first_encoding, second_encoding = encoded_jobs(first_result), encoded_jobs(second_result)
    assert len(first_encoding) == len(second_encoding) == 1
    assert first_encoding[0]["authorization_root_job_id"] != second_encoding[0]["authorization_root_job_id"]
    for result, sources in ((first_result, first), (second_result, second)):
        assert result["stop_reason"] is None and result["encoding_coverage"]["complete"]
        assert {(item["event_id"], item["revision"]) for item in result["encoding_coverage"]["covered_refs"]} == refs(sources)
        assert result["pending_debt"]["counts_complete"]
        for report in result["jobs"]:
            assert report["kind"] == "extract" or report["execution_authority"]["payload"]["target"]["conversation_ref"] == "continuous"
            if report["kind"] == "consolidate":
                assert report["model_calls"] <= 2 and report["input_bytes"] <= 196608


def test_two_delivered_exchanges_coalesce_without_requiring_one_allocation_per_turn(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, first = turn(memory, ROUTINES[0], "first")
        _, second = turn(memory, ROUTINES[1], "second")
        before = [row for row in jobs(learning) if phase(row["payload"]) == ENCODING_PHASE]
        assert len(before) == 1 and before[0]["attempts"] == 0
        result, _ = process(learning, second)
        encoded = encoded_jobs(result)
        assert len(encoded) == 1 and encoded[0]["job_id"] == before[0]["id"]
        actual_refs = {(item["event_id"], item["revision"]) for item in encoded[0]["result"]["learning_input_refs"]}
        assert refs(first) | refs(second) <= actual_refs
        assert result["encoding_coverage"]["complete"] and result["stop_reason"] is None
        before_operations, before_calls = operations(learning), len(cognition.requests)
        replay, _ = process(learning, second)
        assert replay["jobs"] == [] and replay["encoding_coverage"]["complete"]
        assert operations(learning) == before_operations and len(cognition.requests) == before_calls


def test_large_coalesced_window_follows_only_core_created_encoding_continuations(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        for index in range(5):
            _, sources = turn(memory, ROUTINES[index], f"turn-{index}")
        result, _ = process(learning, sources, max_jobs=32)
    encoded = encoded_jobs(result)
    assert len(encoded) == 2 and result["encoding_coverage"]["complete"] and result["stop_reason"] is None
    assert encoded[1]["execution_authority"]["payload"]["continuation_parent_job_id"] == encoded[0]["job_id"]
    assert encoded[0]["result"]["learning_window"]["has_more"]
    assert not encoded[1]["result"]["learning_window"]["has_more"]
    assert all(row["payload"]["allocation_policy"] == "encoding-integration-budget-v1" for row in jobs(learning))


@pytest.mark.parametrize("failure", ["invalid", "unknown"])
def test_failed_paid_encoding_stops_without_retrying_or_creating_recovery(learning, failure):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, sources = turn(memory, ROUTINES[0], "first")
        calls = []

        def unavailable(request):
            calls.append(request)
            if failure == "unknown":
                raise TimeoutError("Synthetic unknown outcome")
            return "invalid JSON"

        learning.service.semantic_processor.propose = unavailable
        result, _ = process(learning, sources)
        assert result["stop_reason"] == ("unknown_provider_usage" if failure == "unknown"
                                         else "cognitive_processing_unavailable")
        assert not result["encoding_coverage"]["complete"] and len(calls) == 1
        failed = encoded_jobs(result)[0]
        assert failed["model_calls"] == 1 and failed["state"] != "completed"
        prior_operations = operations(learning)
        prior_jobs = jobs(learning)
        repeated, _ = process(learning, sources)
        assert repeated["jobs"] == [] and repeated["stop_reason"] == result["stop_reason"]
        assert operations(learning) == prior_operations and jobs(learning) == prior_jobs and len(calls) == 1


def test_zero_brain_budget_remains_unchanged_and_is_reported_as_failed_processing(learning):
    cognition = GroundedCognition()
    store = JobStore(learning.database)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="finite-budget",
                                     expected_revision=0,
                                     policy={"window_seconds": 3600, "max_model_calls": 0, "max_input_bytes": 0})
    with restarted_service(learning, cognition) as memory:
        _, sources = turn(memory, ROUTINES[0], "first")
        before = store.cognitive_budget(learning.principal, learning.brain)
        result, _ = process(learning, sources)
    assert result["stop_reason"] == "cognitive_processing_unavailable"
    assert not result["encoding_coverage"]["complete"]
    assert store.cognitive_budget(learning.principal, learning.brain) == before
    assert cognition.requests == [] and operations(learning) == []


@pytest.mark.parametrize("failure", ["failed", "unknown"])
def test_history_failure_stops_even_when_the_current_exchange_was_encoded(learning, failure):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, first = turn(memory, " ".join(ROUTINES), "first")
        assert process(learning, first)[0]["stop_reason"] is None
        _, second = turn(memory, ROUTINES[0], "second")
        cognition.history_failure = failure
        result, _ = process(learning, second)
        expected = "unknown_provider_usage" if failure == "unknown" else "cognitive_processing_unavailable"
        assert result["encoding_coverage"]["complete"] and result["stop_reason"] == expected
        assert result["jobs"][-1]["learning_phase"] == HISTORY_PHASE
        assert result["pending_debt"]["learning_phases"]["history_integration_unassessed_objective_count"] > 0
        before_calls, before_operations = len(cognition.requests), operations(learning)
        repeated, _ = process(learning, second)
        assert repeated["jobs"] == [] and repeated["stop_reason"] == expected
        assert len(cognition.requests) == before_calls and operations(learning) == before_operations


def test_job_bound_stops_before_further_claims_and_keeps_pending_debt(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, sources = turn(memory, ROUTINES[0], "first")
        result, _ = process(learning, sources, max_jobs=1)
    assert len(result["jobs"]) == 1 and result["jobs"][0]["kind"] == "extract"
    assert result["dispatch_limit_reached"] and result["stop_reason"] == "learning_dispatch_limit"
    assert not result["encoding_coverage"]["complete"] and result["pending_debt"]["open_window_count"] == 1
    assert cognition.requests == [] and operations(learning) == []
    assert all(row["attempts"] == row["model_calls"] == 0 for row in jobs(learning))


@pytest.mark.parametrize("stop_at", ["boundary", "claimed", "finished"])
def test_callback_failure_prevents_any_later_job(learning, stop_at):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, sources = turn(memory, ROUTINES[0], "first")
        records = []

        def boundary():
            if stop_at == "boundary":
                raise RuntimeError("Stopped at boundary")

        def record(value):
            records.append(value)
            if value["record_type"] == stop_at:
                raise RuntimeError("Durable journal rejected further work")

        with pytest.raises(RuntimeError):
            process_learning(learning.service, learning.principal, learning.brain, conversation_id="continuous",
                              current_sources=sources, boundary=boundary, record=record)
    assert cognition.requests == [] and operations(learning) == []
    assert all(row["attempts"] == 0 for row in jobs(learning))
    assert len(records) == {"boundary": 0, "claimed": 1, "finished": 2}[stop_at]
    if records:
        attempted = learning.job(records[0]["job_id"])
        assert attempted["attempts"] == 1
        assert attempted["state"] == ("running" if stop_at == "claimed" else "completed")


@pytest.mark.parametrize("damage", ["conversation", "revision", "sequence", "hash"])
def test_source_scope_and_versions_are_verified_before_claiming(learning, damage):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, sources = turn(memory, ROUTINES[0], "first")
        sources = copy.deepcopy(sources)
        conversation = "different" if damage == "conversation" else "continuous"
        if damage == "revision":
            sources["output_events"][0]["revision"] -= 1
        elif damage == "sequence":
            sources["input_events"][0]["sequence"] += 1
        elif damage == "hash":
            sources["input_events"][0]["content_sha256"] = "f" * 64
        with pytest.raises(ValueError):
            process_learning(learning.service, learning.principal, learning.brain, conversation_id=conversation,
                              current_sources=sources, boundary=lambda: pytest.fail("No claim is permitted"),
                              record=lambda _: pytest.fail("No attempt is permitted"))
    assert cognition.requests == [] and operations(learning) == []


def test_an_older_exchange_cannot_execute_a_window_containing_later_input(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        _, first = turn(memory, ROUTINES[0], "first")
        turn(memory, ROUTINES[1], "second")
        result, _ = process(learning, first)
    assert result["jobs"] == [] and result["stop_reason"] == "current_encoding_not_ready"
    assert cognition.requests == [] and operations(learning) == []


def test_unrelated_conversation_is_not_drained(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        learning.observe("An unrelated private input.", conversation="different")
        unrelated = [row for row in jobs(learning) if row["payload"]["target"]["conversation_ref"] == "different"]
        _, sources = turn(memory, ROUTINES[0], "first")
        result, _ = process(learning, sources)
    assert result["stop_reason"] is None and result["encoding_coverage"]["complete"]
    assert all(learning.job(row["id"]) == row for row in unrelated)


def test_later_input_can_use_only_its_existing_allocation_to_fund_untouched_history(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                              max_sources=2)
        for index, fact in enumerate(ROUTINES[:3]):
            _, sources = turn(memory, fact, f"turn-{index}")
            result, _ = process(learning, sources)
            assert result["stop_reason"] is None
        old = [row for row in jobs(learning) if phase(row["payload"]) == HISTORY_PHASE and row["state"] == "deferred"]
        assert old
        prior = {row["id"]: copy.deepcopy(row) for row in old}
        learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                              max_sources=8, max_assertions=8)
        _, sources = turn(memory, NOTEBOOK, "donor")
        result, _ = process(learning, sources)
    assert result["stop_reason"] is None and result["encoding_coverage"]["complete"]
    donor = encoded_jobs(result)[0]
    funded = [row for row in result["jobs"] if row["learning_phase"] == HISTORY_PHASE
              and row["execution_authority"]["payload"].get("integration_continuation_parent_job_id") in prior]
    assert funded
    for report in funded:
        payload = report["execution_authority"]["payload"]
        assert payload["authorization_root_job_id"] == donor["authorization_root_job_id"]
        original = prior[payload["integration_continuation_parent_job_id"]]
        assert payload["encoding_parent_job_id"] == original["payload"]["encoding_parent_job_id"]
        assert payload["history_obligation_manifest"] == original["payload"]["history_obligation_manifest"]
        assert learning.job(original["id"])["model_calls"] == 0 and operations(learning, original["id"]) == []
    charged = [item for row in result["jobs"] for item in row["operations"]]
    assert len(charged) <= 4 and sum(item["input_bytes"] for item in charged) <= 393216
