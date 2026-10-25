import copy
import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_memory.helpers import Principal
from agent_memory.learning_allocations import ENCODING_PHASE, HISTORY_PHASE, allocation_metadata


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.lifecycle_learning")


def candidate(identifier="encoding", selected=ENCODING_PHASE, **changes):
    return {"id": identifier, "kind": "consolidate", "state": "queued", "available": True,
            "attempts": 0, "model_calls": 0, "input_bytes": 0, "has_operations": False,
            "payload": {**allocation_metadata(identifier, phase=selected,
                                                root_job_id="encoding" if selected == HISTORY_PHASE else identifier),
                        "from_sequence": 1, "through_sequence": 2,
                        "target": {"conversation_ref": "scope", "branch_ref": "main"}}, **changes}


def stage():
    return {"runtime": {"stage": "completed", "conversation_id": "scope", "branch_id": "main"},
            "sources": {"input_events": [{"event_id": "user", "revision": 1, "sequence": 1}],
                        "output_events": [{"event_id": "agent", "revision": 1, "sequence": 2}]}}


@pytest.mark.parametrize("turns,learned,calls,byte_batches", [(5, 2, 18, 9), (110, 104, 636, 318), (16, 16, 96, 48)])
def test_finite_allowance_counts_roots_once_including_both_phases(helper, turns, learned, calls, byte_batches):
    assert helper.semantic_allowance(turns, learned, SimpleNamespace(max_input_bytes=196608)) == {
        "max_model_calls": calls, "max_input_bytes": byte_batches * 196608}
    assert helper.semantic_allowance(turns, learned, SimpleNamespace(max_input_bytes=400000))["max_input_bytes"] == (
        turns * 400000 + learned * 393216)


def test_repair_selection_is_fixed_original_history_without_probes_or_baseline(helper):
    from examples.memory_benchmark_cases import long_history

    fixtures, diagnostic = helper.repair_selection(20261025)
    source = long_history(20261025, 100)
    assert fixtures == source["history"][:16]
    assert fixtures[3]["text"] == source["history"][3]["text"]
    assert diagnostic["source_dataset_sha256"] == source["sha256"]
    assert diagnostic["selected_history_turns"] == 16
    assert all(item["learn"] for item in fixtures)
    fixtures[0]["text"] = "changed locally"
    assert helper.repair_selection(20261025)[0][0] == source["history"][0]


@pytest.mark.parametrize("changes", [{"state": "failed"}, {"state": "deferred"}, {"attempts": 1},
                                     {"attempts": False}, {"model_calls": 1}, {"input_bytes": 1},
                                     {"has_operations": True}, {"available": False}])
def test_only_untouched_ready_jobs_are_eligible(helper, changes):
    assert helper.eligible(candidate())
    assert not helper.eligible(candidate(**changes))


def test_encoding_selection_uses_exact_current_sources_and_never_history(helper):
    root = candidate()
    assert helper.select_encoding([candidate("history", HISTORY_PHASE), root], stage()) == root
    for rows in ([candidate("history", HISTORY_PHASE)], [root, candidate("second")], [candidate(attempts=1)]):
        with pytest.raises(RuntimeError):
            helper.select_encoding(rows, stage())
    root["payload"]["through_sequence"] = 1
    with pytest.raises(RuntimeError):
        helper.select_encoding([root], stage())


def test_metadata_keeps_phase_and_scope_window_without_manifest(helper):
    row = candidate("history", HISTORY_PHASE)
    row["payload"]["history_obligation_manifest"] = {"private": "never expose"}
    value = helper.window_metadata(row)
    assert value["learning_phase"] == HISTORY_PHASE
    assert "private" not in repr(value) and "manifest" not in repr(value)


class Stop(Exception):
    pass


@pytest.fixture
def harness(helper, monkeypatch):
    state = SimpleNamespace(dispatched=[], claimed=[], saved=0, stopped=False, unknown=False,
                            queue=[], results={}, after_execute=None, boundaries=0, sql=[])

    @contextmanager
    def transaction(_):
        yield object()

    service = SimpleNamespace(database=SimpleNamespace(transaction=transaction), _brain=lambda *args: {"id": "brain"})
    trial = SimpleNamespace(principal=Principal("tenant", "owner"), current=stage(), learning_call_context=None,
                            report={"plan": {"learning_phase_policy": copy.deepcopy(helper.PHASE_POLICY)}})

    def save():
        state.saved += 1

    trial.save = save

    def boundary():
        state.boundaries += 1
        if state.unknown:
            raise Stop("unknown_provider_usage")
        failure = helper.execution_failure(trial.current)
        if failure:
            raise Stop(failure)
        if state.stopped:
            raise Stop("operator_stop")

    def claim(*args, job_id, **kwargs):
        state.claimed.append((job_id, kwargs))
        if job_id == "encoding":
            return candidate()
        return next(row for row in state.queue if row["id"] == job_id)

    def execute(principal, brain_id, row):
        state.dispatched.append(row["id"])
        result = {"state": "completed", "result": {"status": "completed"}, "job_id": row["id"],
                  "last_error": None, "model_calls": 2, "input_bytes": 200, "execution_error": None}
        if row["id"] == "encoding":
            result["result"]["learning_window"] = {"processing_succeeded": True}
        state.results[row["id"]] = result
        if state.after_execute:
            state.after_execute(row, result)

    store = SimpleNamespace(claim=claim, learning_progress=lambda *a, **k: {
        "learning_phases": {"counts_complete": True, "history_integration_open_count": 1,
                            "history_integration_deferred_count": 1}},
                            get=lambda *a: {"id": "encoding", "state": "completed"})
    monkeypatch.setattr(helper, "JobStore", lambda db: store)
    monkeypatch.setattr(helper, "LearningEngine", lambda service: SimpleNamespace(_execute=execute))
    monkeypatch.setattr(helper, "Trial", lambda *a: SimpleNamespace(
        job_result=lambda job_id, error: {**state.results[job_id], "execution_error": error}))
    monkeypatch.setattr(helper, "scoped_windows", lambda *a: [candidate()])
    monkeypatch.setattr(helper, "literal_jobs", lambda *a: [])
    monkeypatch.setattr(helper, "ready_history", lambda *a: [row for row in state.queue
                                                             if row["id"] not in state.dispatched][:a[-1]])
    monkeypatch.setattr(helper, "allocation_ledger", lambda *a: [{"outcome": "completed", "input_bytes": 100}])
    monkeypatch.setattr(helper, "reconsider", lambda *a: None)
    return state, trial, helper.PhaseLearning(trial, service, "brain", boundary=boundary)


def test_encoding_and_ready_history_are_separate_one_attempt_jobs(harness):
    state, trial, drain = harness
    state.queue = [candidate("history", HISTORY_PHASE)]
    drain.run()
    drain.capture()
    assert state.dispatched == ["encoding", "history"]
    assert all(options["max_attempts"] == 1 for _, options in state.claimed)
    assert [row["learning_phase"] for row in trial.current["learning_jobs"]] == [ENCODING_PHASE, HISTORY_PHASE]
    assert trial.report["learning_authorizations"] == {"brain": ["encoding"]}
    assert trial.current["learning_phase_checks"]["local_encoding_processed_successfully"]
    assert trial.current["learning_progress"]["learning_phases"]["history_integration_open_count"] == 1
    assert trial.current["learning_allocation_ledger"][0]["input_bytes"] == 100


def test_deferred_history_does_not_dispatch_or_invent_assessment(harness):
    state, trial, drain = harness
    drain.run()
    drain.capture()
    assert state.dispatched == ["encoding"]
    assert trial.current["learning_progress"]["learning_phases"]["history_integration_deferred_count"] == 1
    assert "history_integration_assessed_count" not in trial.current["learning_progress"]["learning_phases"]


@pytest.mark.parametrize("kind", ["operator_stop", "unknown_provider_usage", "provider_failure", "invalid_selected"])
def test_safe_job_boundary_stops_before_another_paid_job_and_retains_evidence(harness, kind):
    state, trial, drain = harness
    state.queue = [candidate("history", HISTORY_PHASE)]

    def after(row, result):
        if kind == "operator_stop":
            state.stopped = True
        elif kind == "unknown_provider_usage":
            state.unknown = True
            state.stopped = True
        else:
            result["state"] = "cancelled"
            result["result"]["status"] = "provider_unavailable" if kind == "provider_failure" else "partial"
            result["last_error"] = "provider_failed" if kind == "provider_failure" else "invalid_proposal"

    state.after_execute = after
    expected = {"provider_failure": "cognitive_processing_unavailable",
                "invalid_selected": "learning_processing_incomplete"}.get(kind, kind)
    with pytest.raises(Stop, match=expected):
        drain.run()
    assert state.dispatched == ["encoding"] and len(trial.current["learning_jobs"]) == 1


def test_preexisting_stop_does_not_read_or_claim_work(harness, helper, monkeypatch):
    state, trial, drain = harness
    state.stopped = True
    monkeypatch.setattr(helper, "scoped_windows", lambda *a: pytest.fail("Stopped before work discovery"))
    with pytest.raises(Stop, match="operator_stop"):
        drain.run()
    assert not state.claimed and "learning_authorizations" not in trial.report


def test_exception_after_job_dispatch_records_failure_and_never_dispatches_tail(harness):
    state, trial, drain = harness
    state.queue = [candidate("history", HISTORY_PHASE)]

    def fail(row, result):
        result["state"] = "cancelled"
        raise TimeoutError("Synthetic execution interruption")

    state.after_execute = fail
    with pytest.raises(Stop, match="learning_processing_incomplete"):
        drain.run()
    assert state.dispatched == ["encoding"]
    assert trial.current["learning_jobs"][0]["execution_error"] == {"type": "TimeoutError", "code": None}
    assert trial.learning_call_context is None


def test_claim_conflict_does_not_dispatch_selected_or_alternative_work(harness):
    state, trial, drain = harness
    drain.jobs.claim = lambda *a, **k: None
    with pytest.raises(RuntimeError, match="changed"):
        drain.run()
    assert not state.dispatched


def test_history_dispatch_is_bounded_without_minting_authorizations(harness):
    state, trial, drain = harness
    state.queue = [candidate(f"history-{index}", HISTORY_PHASE) for index in range(9)]
    drain.run()
    assert len(state.dispatched) == 9
    assert state.dispatched[-1] == "history-7" and "history-8" not in state.dispatched
    assert trial.current["history_dispatch_limit_reached"] is True
    assert trial.report["learning_authorizations"] == {"brain": ["encoding"]}


def test_duplicate_encoding_authorization_is_not_reexecuted(harness):
    state, trial, drain = harness
    trial.report["learning_authorizations"] = {"brain": ["encoding"]}
    with pytest.raises(RuntimeError, match="again"):
        drain.run()
    assert not state.dispatched


def test_new_history_root_outside_frozen_authorizations_is_rejected(harness):
    state, trial, drain = harness
    state.queue = [candidate("history", HISTORY_PHASE)]
    state.queue[0]["payload"]["authorization_root_job_id"] = "unapproved"
    with pytest.raises(RuntimeError, match="no recorded"):
        drain.run()
    assert state.dispatched == ["encoding"]


def test_valid_partial_completed_history_is_nonfatal_and_assessment_is_separate(helper):
    value = {"learning_jobs": [{"state": "completed", "result": {"status": "partial",
             "history_integration": {"assessed_count": 0, "published_count": 1}}}]}
    assert helper.execution_failure(value) is None
    value["learning_jobs"][0]["state"] = "cancelled"
    assert helper.execution_failure(value) == "learning_processing_incomplete"


def test_unknown_operation_takes_priority_over_provider_status(helper):
    value = {"learning_jobs": [{"last_error": "provider_uncertain", "state": "cancelled",
                               "result": {"status": "provider_unavailable"}}]}
    assert helper.execution_failure(value) == "unknown_provider_usage"


def test_scoped_window_query_is_authorized_and_has_no_recent_history_cutoff(helper):
    calls, authorized = [], []

    class Connection:
        def execute(self, query, arguments):
            calls.append((query, arguments))
            return []

    @contextmanager
    def transaction(principal):
        yield Connection()

    service = SimpleNamespace(database=SimpleNamespace(transaction=transaction),
                              _brain=lambda *args: authorized.append(args[-1]),
                              _params=lambda p, brain: (p.tenant_id, p.principal_id, brain))
    principal = Principal("tenant", "owner")
    assert helper.scoped_windows(service, principal, "brain", stage()) == []
    query, arguments = calls[0]
    assert authorized == ["brain"] and arguments == ("tenant", "owner", "brain", "scope", "main")
    assert "LIMIT" not in query and "owner_id" in query and "has_operations" in query


def test_history_selector_filters_recorded_roots_and_rejects_zero_counter_reserved_operations(helper, monkeypatch):
    recorded = []
    row = candidate("history", HISTORY_PHASE)

    def rows(*args, **kwargs):
        recorded.append((args, kwargs))
        return [row]

    monkeypatch.setattr(helper, "_rows", rows)
    assert helper.ready_history(None, None, "brain", ["encoding"], 1) == [row]
    args, kwargs = recorded[0]
    assert args[-1] == ("encoding-integration-v1", HISTORY_PHASE, ["encoding"])
    assert kwargs == {"limit": 1}
    assert "j.state='queued'" in args[-2] and "j.attempts=0" in args[-2]
    row["has_operations"] = True
    with pytest.raises(RuntimeError, match="prior paid"):
        helper.ready_history(None, None, "brain", ["encoding"], 1)


def test_literal_selection_includes_earlier_draft_of_same_delivered_event(helper, monkeypatch):
    current = stage()
    current["sources"]["output_events"][0]["revision"] = 2
    rows = [candidate("user-extract", kind="extract", payload={"event_id": "user", "revision": 1}),
            candidate("draft-extract", kind="extract", payload={"event_id": "agent", "revision": 1}),
            candidate("delivered-extract", kind="extract", payload={"event_id": "agent", "revision": 2})]
    monkeypatch.setattr(helper, "_rows", lambda *a, **k: rows)
    assert helper.literal_jobs(None, None, "brain", current, 4) == rows
    rows[-1]["payload"]["revision"] = 3
    with pytest.raises(RuntimeError, match="current-source"):
        helper.literal_jobs(None, None, "brain", current, 4)


@pytest.fixture
def runner(helper):
    return importlib.import_module("examples.evaluate_memory_lifecycle")


def test_v2_repair_plan_freezes_small_memory_only_diagnostic_and_original_sources(runner):
    planned = runner.plan("repair", seed=20261025)
    assert planned["version"] == "memory-lifecycle-evaluation-v2"
    assert planned["arms"] == ["memory"] and planned["dataset"] is None
    assert planned["fixtures"] == runner.long_history(20261025, 100)["history"][:16]
    assert planned["memory_arm_call_allocation"] == {"semantic": 96, "host": 16, "answer_review": 16}
    assert planned["per_arm_max_calls"] == 128
    assert planned["per_arm_max_reserved_output_tokens"] == 1769472
    assert planned["server_budget_policy"] == {
        "window_seconds": 2_592_000, "max_model_calls": 96, "max_input_bytes": 48 * 196608}
    assert planned["baseline_call_allocation"] == {} and planned["summary"]["updates"] == 0
    assert "later stages do not fund earlier scopes" in planned["learning_phase_policy"]["conversation_scope"]
    assert planned["code_fingerprints"]["examples/lifecycle_learning.py"] == runner.digest(
        (runner.ROOT / "examples/lifecycle_learning.py").read_text())


def test_repair_plan_command_does_not_open_database_or_provider(runner, monkeypatch, tmp_path, capsys):
    from test_memory_lifecycle_evaluation import no_live_access

    no_live_access(runner, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["eval", "--profile", "repair", "--plan", "--output", str(tmp_path / "unused")])
    runner.main()
    assert json.loads(capsys.readouterr().out)["repair_diagnostic"]["selected_history_turns"] == 16
    assert not (tmp_path / "unused").exists()


def test_repair_refuses_selection_rewrite_even_with_recomputed_plan_hash(runner):
    planned = runner.plan("repair")
    planned["fixtures"] = planned["fixtures"][:3]
    with pytest.raises(ValueError, match="selection changed"):
        runner.validate_frozen_plan({"plan": planned, "plan_sha256": runner.digest(planned)})


def test_history_windows_never_count_as_current_encoding_coverage(runner):
    value = stage()
    windows = [{"id": "history", "state": "completed", "learning_phase": "history_integration",
                "from_sequence": 1, "through_sequence": 2}]
    assert not runner.learning_queue_audit(value, windows)["checks"]["current_input_has_consolidation_window"]
    windows.extend({"id": str(index), "state": "completed", "learning_phase": "encoding",
                    "from_sequence": index + 10, "through_sequence": index + 10} for index in range(12))
    windows.append({"id": "encoding", "state": "queued", "learning_phase": "encoding",
                    "from_sequence": 1, "through_sequence": 2})
    audit = runner.learning_queue_audit(value, windows)
    assert audit["checks"]["current_input_has_consolidation_window"] and len(audit["windows"]) == 14


@pytest.mark.parametrize("reason", ["operator_stop", "unknown_provider_usage", "learning_processing_incomplete"])
def test_learn_preserves_normal_stop_reason_without_turn_exception_relabel(runner, monkeypatch, tmp_path, reason):
    from test_memory_lifecycle_evaluation import trial_fixture

    trial = trial_fixture(runner, tmp_path)
    trial.current.update(stage())
    captured = []

    class Drain:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            raise runner.EvaluationStopped(reason)

        def capture(self):
            captured.append(True)

    monkeypatch.setattr(runner, "PhaseLearning", Drain)
    trial.learn(None, "brain", {"ignored_recent_window"})
    assert captured == [True] and "error" not in trial.current
    assert trial.current["learning_stopped_reason"] == reason
    assert runner.stop_reason(trial.current, trial.meter) == reason


def test_learning_provider_record_carries_exact_job_phase_and_shared_root(runner, tmp_path):
    from test_memory_lifecycle_evaluation import trial_fixture

    from agent_memory.semantic import CognitiveRequest

    trial = trial_fixture(runner, tmp_path)
    trial.role = "consolidate"
    trial.learning_call_context = {"learning_job_id": "history-child", "learning_phase": "history_integration",
                                   "authorization_root_job_id": "original-encoding"}
    trial.provider = lambda request: '{"ok":true}'
    request = CognitiveRequest("test-request", "proposal", ({"role": "user", "content": "synthetic"},),
                               {"type": "object"}, 32, 10)
    trial.cognition(request)
    record = trial.current["cognitive_calls"][0]
    assert all(record[key] == value for key, value in trial.learning_call_context.items())
    assert record["state"] == "finished" and record["response"] == {"ok": True}
    assert runner.performance_metrics(trial.current)["learning_phase_calls"] == {"encoding": 0, "history_integration": 1}
