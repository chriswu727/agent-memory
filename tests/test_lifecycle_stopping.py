import copy
import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from test_memory_lifecycle_evaluation import Meter, no_live_access
from test_memory_lifecycle_evaluation import evaluation as evaluation

from membrain.runtime import ModelRequest


def completed_stage(result=None):
    return {"runtime": {"stage": "completed"},
            "learning_jobs": [{"state": "completed", "result": result}] if result is not None else []}


@pytest.mark.parametrize("result", [
    {"status": "partial", "sampling": {"publication_restricted": True}},
    {"status": "partial", "learning_window": {"processing_succeeded": False}},
    {"status": "completed", "sampling": {"publication_restricted": True},
     "learning_window": {"processing_succeeded": True}},
])
def test_incomplete_learning_stops_even_when_status_alone_was_nonfatal(evaluation, result):
    assert evaluation.stop_reason(completed_stage(result), Meter()) == "learning_processing_incomplete"


def test_successful_partial_learning_and_orientation_restrictions_remain_nonfatal(evaluation):
    stage = completed_stage({"status": "partial", "sampling": {"publication_restricted": False},
                             "learning_window": {"processing_succeeded": True}})
    stage["orientation"] = {"result": {"status": "partial", "sampling": {"publication_restricted": True}}}
    stage["checks"] = {"all_proposals_accepted": False}
    before = copy.deepcopy(stage)
    assert evaluation.stop_reason(stage, Meter()) is None
    assert stage == before


@pytest.mark.parametrize("status", [
    "budget_exhausted", "provider_unavailable", "invalid_response", "verification_unavailable",
])
def test_existing_failure_priorities_win_over_incomplete_learning_and_stop(evaluation, tmp_path, status):
    (tmp_path / "STOP").touch()
    stage = completed_stage({"status": status, "sampling": {"publication_restricted": True}})
    meter = Meter()
    with pytest.raises(evaluation.EvaluationStopped) as error:
        evaluation.stage_boundary(tmp_path, meter, stage)
    assert error.value.reason == "cognitive_processing_unavailable"
    stage["runtime"]["stage"] = "failed"
    assert evaluation.stop_reason(stage, meter) == "runtime_not_completed"
    meter.unknown = 1
    assert evaluation.stop_reason(stage, meter) == "unknown_provider_usage"


def test_early_stop_writes_report_without_environment_or_providers_and_cannot_resume_paid_work(
    evaluation, monkeypatch, tmp_path, capsys,
):
    no_live_access(evaluation, monkeypatch)
    (tmp_path / "STOP").write_text("private operator note")
    monkeypatch.setattr(sys, "argv", ["eval", "--live", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1
    report = tmp_path / "report.json"
    original = report.read_bytes()
    saved = json.loads(original)
    assert saved["state"] == "stopped" and saved["stopped_reason"] == "operator_stop"
    assert saved["arms"] == {}
    assert "private operator note" not in original.decode()
    assert {path.name for path in tmp_path.iterdir()} == {"STOP", "report.json"}
    monkeypatch.setattr(sys, "argv", ["eval", "--resume", "--output", str(tmp_path)])
    evaluation.main()
    assert json.loads(capsys.readouterr().out)["stopped_reason"] == "operator_stop"
    (tmp_path / "STOP").unlink()
    monkeypatch.setattr(sys, "argv", ["eval", "--live", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert report.read_bytes() == original


def test_stop_cannot_overwrite_existing_evidence(evaluation, monkeypatch, tmp_path):
    no_live_access(evaluation, monkeypatch)
    (tmp_path / "STOP").touch()
    (tmp_path / "evidence.json").write_text("preserve")
    monkeypatch.setattr(sys, "argv", ["eval", "--live", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert (tmp_path / "evidence.json").read_text() == "preserve"
    assert not (tmp_path / "report.json").exists()


def mock_run(evaluation, monkeypatch, output, *, stop_at, arm="no_memory", unknown=False, result=None, history_count=1):
    events, meters = [], []
    transport_class = evaluation.MeteredTransport

    def reply(_):
        value = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "answer"}}]}
        if not unknown:
            value["usage"] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        return httpx.Response(200, json=value)

    def meter(*args, **kwargs):
        value = transport_class(*args, **kwargs, transport=httpx.MockTransport(reply))
        meters.append(value)
        return value

    def event(name):
        events.append(name)
        if name == stop_at:
            (output / "STOP").touch()

    class Trial:
        def __init__(self, directory, report, database, service, host, meter, reviewer, summary_host, **kwargs):
            self.directory, self.report, self.host, self.summary_host = directory, report, host, summary_host

        def run_stage(self, fixture):
            event("stage:" + fixture["name"])
            request = ModelRequest("turn", fixture["name"], ({"role": "user", "content": "question"},), 100, "{}")
            assert self.host(request) == "answer"
            self.report["stages"].append({"name": fixture["name"], "checks": {"completed": True},
                                          **completed_stage(copy.deepcopy(result))})

        def update_summary(self):
            request = ModelRequest("turn", "summary", ({"role": "user", "content": "summarize"},), 100, "{}")
            assert self.summary_host(request) == "answer"
            event("summary")

        def freeze_history(self):
            event("archive")

        def prepare_probe(self, fixture):
            event("prepare:" + fixture["name"])

        def save(self):
            evaluation.save(self.directory / "report.json", self.report)

    monkeypatch.setattr(evaluation, "MeteredTransport", meter)
    monkeypatch.setattr(evaluation, "LifecycleTrial", Trial)
    planned = evaluation.plan("long")
    planned["fixtures"] = planned["fixtures"][:history_count]
    report = evaluation.run_arm(output / arm, planned, arm, SimpleNamespace(dsn="unused"), "test-key")
    return report, events, meters, planned


@pytest.mark.parametrize("stop_at,arm,expected_events,calls", [
    ("stage:history-000", "rolling_summary", ["stage:history-000"], 1),
    ("summary", "rolling_summary", ["stage:history-000", "summary"], 2),
    ("archive", "no_memory", ["stage:history-000", "archive"], 1),
    ("prepare:order_change", "no_memory", ["stage:history-000", "archive", "prepare:order_change"], 1),
    ("stage:order_change", "no_memory", ["stage:history-000", "archive", "prepare:order_change", "stage:order_change"], 2),
])
def test_stage_stop_prevents_next_paid_work_and_preserves_actual_ledger(
    evaluation, monkeypatch, tmp_path, stop_at, arm, expected_events, calls,
):
    report, events, meters, planned = mock_run(evaluation, monkeypatch, tmp_path, stop_at=stop_at, arm=arm)
    assert report["state"] == "stopped" and report["stopped_reason"] == "operator_stop"
    assert events == expected_events
    assert report["usage"]["reserved_calls"] == report["usage"]["completed_calls"] == calls
    assert report["usage"]["usage_unknown_calls"] == 0
    assert report["usage"]["usage_totals"]["total_tokens"] == 2 * calls
    assert len(report["call_ledger"]) == 2 * calls
    assert all(meter._closed for meter in meters)
    saved = json.loads((tmp_path / arm / "report.json").read_text())
    assert saved == report and saved["plan_sha256"] == evaluation.digest(planned)


def test_unknown_usage_wins_over_operator_stop_with_ledger_retained(evaluation, monkeypatch, tmp_path):
    report, events, meters, _ = mock_run(evaluation, monkeypatch, tmp_path, stop_at="stage:history-000", unknown=True)
    assert report["state"] == "stopped" and report["stopped_reason"] == "unknown_provider_usage"
    assert events == ["stage:history-000"]
    assert report["usage"]["usage_unknown_calls"] == 1
    assert len(report["call_ledger"]) == 2 and all(meter._closed for meter in meters)


def test_stop_during_first_history_stage_finishes_it_without_starting_second(evaluation, monkeypatch, tmp_path):
    report, events, meters, planned = mock_run(
        evaluation, monkeypatch, tmp_path, stop_at="stage:history-000", history_count=2)
    assert report["stopped_reason"] == "operator_stop"
    assert len(planned["fixtures"]) == 2 and len(report["stages"]) == 1
    assert events == ["stage:history-000"]
    assert report["usage"]["completed_calls"] == 1 and all(meter._closed for meter in meters)


@pytest.mark.parametrize("result", [
    {"status": "partial", "sampling": {"publication_restricted": True}},
    {"status": "partial", "learning_window": {"processing_succeeded": False}},
])
def test_learning_failure_stops_before_baseline_summary(evaluation, monkeypatch, tmp_path, result):
    report, events, meters, _ = mock_run(
        evaluation, monkeypatch, tmp_path, stop_at=None, arm="rolling_summary", result=result)
    assert report["state"] == "stopped" and report["stopped_reason"] == "learning_processing_incomplete"
    assert events == ["stage:history-000"]
    assert report["stages"][0]["learning_jobs"][0]["result"] == result
    assert report["usage"]["reserved_calls"] == 1 and all(meter._closed for meter in meters)


def test_valid_partial_result_allows_remaining_probes(evaluation, monkeypatch, tmp_path):
    result = {"status": "partial", "sampling": {"publication_restricted": False},
              "learning_window": {"processing_succeeded": True}}
    report, events, meters, planned = mock_run(evaluation, monkeypatch, tmp_path, stop_at=None, result=result)
    assert report["state"] == "finished" and "stopped_reason" not in report
    assert len(report["stages"]) == 1 + len(planned["dataset"]["probes"])
    assert report["usage"]["reserved_calls"] == len(report["stages"])
    assert events[-1] == "stage:" + planned["dataset"]["probes"][-1]["name"]
    assert all(meter._closed for meter in meters)


def test_direct_arm_stop_does_not_construct_transport(evaluation, monkeypatch, tmp_path):
    no_live_access(evaluation, monkeypatch)
    (tmp_path / "STOP").touch()
    report = evaluation.run_arm(tmp_path / "memory", evaluation.plan(), "memory", None, None)
    assert report["state"] == "stopped" and report["stopped_reason"] == "operator_stop"
    assert report["usage"]["reserved_calls"] == 0 and report["call_ledger"] == []
    assert not (tmp_path / "memory" / "calls.jsonl").exists()


def test_stop_between_arms_retains_finished_arm_and_never_starts_next(evaluation, monkeypatch, tmp_path):
    output, seen = tmp_path / "run", []
    monkeypatch.setattr(evaluation.Settings, "from_env", lambda: SimpleNamespace(database_url="unused", migration_database_url=None))
    monkeypatch.setattr(evaluation, "Database", lambda *args: object())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    def run_arm(directory, planned, arm, database, key):
        seen.append(arm)
        directory.mkdir()
        report = {"state": "finished", "usage": {"reserved_calls": 7}, "stages": []}
        evaluation.save(directory / "report.json", report)
        (output / "STOP").touch()
        return report

    monkeypatch.setattr(evaluation, "run_arm", run_arm)
    monkeypatch.setattr(sys, "argv", ["eval", "--live", "--profile", "long", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1 and seen == ["memory"]
    saved = json.loads((output / "report.json").read_text())
    assert saved["state"] == "stopped" and saved["stopped_reason"] == "operator_stop"
    assert saved["arms"]["memory"]["state"] == "finished"
    assert saved["arms"]["memory"]["usage"]["reserved_calls"] == 7
