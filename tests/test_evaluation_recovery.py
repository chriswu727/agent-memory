import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_deepseek")


def forbid(*args, **kwargs):
    pytest.fail("This path must not create a provider, access settings, or execute a paid job")


def saved_report(evaluation, **changes):
    return {
        "version": 1, "model": "deepseek-v4-pro", "thinking": "enabled", "synthetic_only": True,
        "code_commit": "offline-fixture", "principal": {"tenant_id": "tenant", "principal_id": "owner"},
        "scenarios": {"temporal": {
            "brain_id": "brain", "fixture": evaluation.SCENARIOS["temporal"], "stages": [],
        }}, **changes,
    }


def invoke(evaluation, monkeypatch, output, *arguments):
    monkeypatch.setattr(sys, "argv", ["evaluate_deepseek", "--output", str(output), *arguments])
    return evaluation.main()


def offline_settings(evaluation, monkeypatch):
    monkeypatch.setattr(evaluation.Settings, "from_env", lambda: SimpleNamespace(
        database_url="offline", migration_database_url="offline",
    ))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-token")


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_terminal_paid_job_resume_only_reads_saved_result(evaluation, monkeypatch, state):
    trial = evaluation.Trial(SimpleNamespace(database=object()), object(), "brain")
    expected = {"job_id": "existing", "state": state, "model_calls": 2}
    seen = []
    monkeypatch.setattr(trial, "job_result", lambda job_id: seen.append(job_id) or expected)
    monkeypatch.setattr(evaluation, "JobStore", forbid)
    monkeypatch.setattr(evaluation, "LearningEngine", forbid)
    assert trial.learn(forbid, "existing") == expected
    assert seen == ["existing"]


@pytest.mark.parametrize("state", ["queued", "running", "superseded", "unknown"])
def test_nonterminal_or_unknown_job_never_restarts_paid_execution(evaluation, monkeypatch, state):
    trial = evaluation.Trial(SimpleNamespace(database=object()), object(), "brain")
    monkeypatch.setattr(trial, "job_result", lambda _: {"state": state})
    monkeypatch.setattr(evaluation, "JobStore", forbid)
    monkeypatch.setattr(evaluation, "LearningEngine", forbid)
    with pytest.raises(RuntimeError, match="requires inspection"):
        trial.learn(forbid, "existing")


def learning_fakes(evaluation, monkeypatch, *, consolidation=True, execution_error=None):
    events = []
    jobs = [{"id": "extract-1", "kind": "extract"}, {"id": "extract-2", "kind": "extract"}]

    class Jobs:
        def __init__(self, database):
            pass

        def claim(self, principal, brain_id, *, kinds, **kwargs):
            events.append(("claim", kinds))
            if kinds == ["extract", "rebuild"]:
                return jobs.pop(0) if jobs else None
            return {"id": "paid-1", "kind": "consolidate"} if consolidation else None

    class Engine:
        def __init__(self, service):
            pass

        def _execute(self, principal, brain_id, job):
            events.append(("execute", job["id"]))
            if job["kind"] == "consolidate":
                assert ("checkpoint", job["id"]) in events
                if execution_error:
                    raise execution_error

    monkeypatch.setattr(evaluation, "JobStore", Jobs)
    monkeypatch.setattr(evaluation, "LearningEngine", Engine)
    trial = evaluation.Trial(SimpleNamespace(database=object()), object(), "brain")
    monkeypatch.setattr(trial, "job_result", lambda job_id, error=None: {
        "job_id": job_id, "state": "completed", "execution_error": error,
    })
    return trial, events


def test_new_stage_indexes_then_checkpoints_before_exactly_one_paid_job(evaluation, monkeypatch):
    trial, events = learning_fakes(evaluation, monkeypatch)
    result = trial.learn(lambda identifier: events.append(("checkpoint", identifier)))
    assert result["job_id"] == "paid-1"
    assert [event for event in events if event[0] == "execute"] == [
        ("execute", "extract-1"), ("execute", "extract-2"), ("execute", "paid-1"),
    ]
    assert events.index(("checkpoint", "paid-1")) < events.index(("execute", "paid-1"))


def test_checkpoint_failure_prevents_paid_execution(evaluation, monkeypatch):
    trial, events = learning_fakes(evaluation, monkeypatch)

    def checkpoint(_):
        raise OSError("checkpoint unavailable")

    with pytest.raises(OSError, match="checkpoint unavailable"):
        trial.learn(checkpoint)
    assert ("execute", "paid-1") not in events


def test_absent_new_job_never_repeats_a_completed_stage(evaluation, monkeypatch):
    trial, events = learning_fakes(evaluation, monkeypatch, consolidation=False)
    with pytest.raises(RuntimeError, match="refusing to repeat"):
        trial.learn(forbid)
    assert not any(event == ("execute", "paid-1") for event in events)


def test_execution_error_is_recorded_without_retry_or_private_message(evaluation, monkeypatch):
    trial, events = learning_fakes(evaluation, monkeypatch,
                                  execution_error=RuntimeError("private provider detail"))
    result = trial.learn(lambda identifier: events.append(("checkpoint", identifier)))
    assert result["execution_error"] == {"type": "RuntimeError", "code": None}
    assert events.count(("execute", "paid-1")) == 1
    assert "private" not in json.dumps(result)


def test_default_cli_cannot_read_dotenv_or_construct_provider(evaluation, monkeypatch, tmp_path):
    monkeypatch.setattr(evaluation.Settings, "from_env", forbid)
    monkeypatch.setattr(evaluation, "MeteredTransport", forbid)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbid)
    with pytest.raises(SystemExit) as error:
        invoke(evaluation, monkeypatch, tmp_path)
    assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limit", ["0", "-1", "300001"])
def test_stage_timeout_limit_is_validated_before_settings_or_billing(evaluation, monkeypatch, tmp_path, limit):
    monkeypatch.setattr(evaluation.Settings, "from_env", forbid)
    with pytest.raises(SystemExit) as error:
        invoke(evaluation, monkeypatch, tmp_path, "--live", "--max-wall-time-ms", limit)
    assert error.value.code == 2


@pytest.mark.parametrize("arguments", [[], ["--live"], ["--resume"], ["--recheck", "--resume", "--live"]])
def test_diagnosed_continuation_requires_explicit_live_resume(evaluation, monkeypatch, tmp_path, arguments):
    monkeypatch.setattr(evaluation.Settings, "from_env", forbid)
    with pytest.raises(SystemExit) as error:
        invoke(evaluation, monkeypatch, tmp_path, *arguments, "--continue-after-failure", "diagnosed")
    assert error.value.code == 2


@pytest.mark.parametrize("continue_diagnosed", [False, True])
def test_diagnosed_continuation_never_rewrites_failed_stage_or_repeats_paid_work(
    evaluation, monkeypatch, tmp_path, continue_diagnosed,
):
    from contextlib import nullcontext

    offline_settings(evaluation, monkeypatch)
    report = saved_report(evaluation)
    failed_stage = {"index": 0, "checks": {"failed": False}, "job": {"job_id": "already-paid"}}
    report["scenarios"]["temporal"]["stages"] = [failed_stage]
    evaluation.save(tmp_path / "report.json", report)
    monkeypatch.setattr(evaluation, "MeteredTransport", lambda *a, **k: object())
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", lambda *a, **k: nullcontext(object()))
    monkeypatch.setattr(evaluation, "Database", lambda *a: object())
    monkeypatch.setattr(evaluation, "MemoryService", lambda *a, **k: object())
    monkeypatch.setattr(evaluation, "Trial", lambda *a: SimpleNamespace(learn=forbid, observe=forbid))
    args = ["--live", "--resume"]
    if continue_diagnosed:
        invoke(evaluation, monkeypatch, tmp_path, *args, "--continue-after-failure", "diagnosed retained evidence")
    else:
        with pytest.raises(SystemExit) as error:
            invoke(evaluation, monkeypatch, tmp_path, *args)
        assert error.value.code == 2
    saved = json.loads((tmp_path / "report.json").read_text())["scenarios"]["temporal"]
    assert saved["stages"] == [failed_stage]
    assert bool(saved.get("diagnosed_continuations")) is continue_diagnosed


def test_recheck_uses_only_report_and_never_settings_database_or_api(evaluation, monkeypatch, tmp_path):
    report = saved_report(evaluation)
    report["scenarios"]["temporal"]["stages"] = [{"index": 0, "checks": {"old": False}}]
    evaluation.save(tmp_path / "report.json", report)
    monkeypatch.setattr(evaluation.Settings, "from_env", forbid)
    monkeypatch.setattr(evaluation, "Database", forbid)
    monkeypatch.setattr(evaluation, "MeteredTransport", forbid)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbid)
    monkeypatch.setattr(evaluation, "stage_checks", lambda *args: {"rechecked": True})
    invoke(evaluation, monkeypatch, tmp_path, "--recheck")
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["scenarios"]["temporal"]["stages"][0]["checks"] == {"rechecked": True}
    assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.parametrize("extra", [["--live"], ["--resume"]])
def test_recheck_cannot_accidentally_turn_into_live_run(evaluation, monkeypatch, tmp_path, extra):
    evaluation.save(tmp_path / "report.json", saved_report(evaluation))
    monkeypatch.setattr(evaluation.Settings, "from_env", forbid)
    with pytest.raises(SystemExit) as error:
        invoke(evaluation, monkeypatch, tmp_path, "--recheck", *extra)
    assert error.value.code == 2


@pytest.mark.parametrize("case", ["missing_report", "orphaned_ledger", "existing_report"])
def test_missing_or_ambiguous_run_identity_blocks_provider(evaluation, monkeypatch, tmp_path, case):
    offline_settings(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation, "MeteredTransport", forbid)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbid)
    arguments = ["--live"]
    if case == "missing_report":
        arguments.append("--resume")
    elif case == "orphaned_ledger":
        (tmp_path / "calls.jsonl").write_text("reserved-call-placeholder\n")
    else:
        evaluation.save(tmp_path / "report.json", saved_report(evaluation))
    with pytest.raises(SystemExit) as error:
        invoke(evaluation, monkeypatch, tmp_path, *arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("case", ["version", "model", "thinking", "synthetic", "fixture", "pending"])
def test_changed_run_configuration_cannot_spend_on_resume(evaluation, monkeypatch, tmp_path, case):
    offline_settings(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation, "MeteredTransport", forbid)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbid)
    report = saved_report(evaluation)
    if case == "synthetic":
        report["synthetic_only"] = False
    elif case in {"version", "model", "thinking"}:
        report[case] = "changed"
    elif case == "fixture":
        report["scenarios"]["temporal"]["fixture"] = [["changed"]]
    else:
        report["scenarios"]["temporal"]["pending"] = {"index": 1, "job_id": "previous"}
    evaluation.save(tmp_path / "report.json", report)
    with pytest.raises(SystemExit) as error:
        invoke(evaluation, monkeypatch, tmp_path, "--live", "--resume")
    assert error.value.code == 2


def test_process_interruption_after_paid_job_resumes_without_reobserving_or_repaying(
    evaluation, monkeypatch, tmp_path,
):
    offline_settings(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation.subprocess, "check_output", lambda *args, **kwargs: "offline-commit")
    monkeypatch.setattr(evaluation, "Database", lambda *args: object())
    calls = {"created_brains": 0, "observations": 0, "paid": 0, "restored": 0}
    interrupted = True

    class Service:
        def __init__(self, *args, **kwargs):
            pass

        def create_brain(self, *args):
            calls["created_brains"] += 1
            return {"id": "brain"}

    class Meter:
        def __init__(self, *args, **kwargs):
            pass

        def summary(self):
            return {"reserved_calls": calls["paid"]}

    class Provider:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Trial:
        def __init__(self, *args):
            pass

        def observe(self, *args):
            calls["observations"] += 1
            return {"accepted_event_ids": ["event-1"]}

        def learn(self, checkpoint, job_id=None):
            nonlocal interrupted
            if job_id is None:
                checkpoint("paid-job")
                stored = json.loads((tmp_path / "report.json").read_text())
                pending = stored["scenarios"]["temporal"]["pending"]
                assert pending["job_id"] == "paid-job"
                assert pending["input_event_ids"] == ["event-1"]
                calls["paid"] += 1
                if interrupted:
                    interrupted = False
                    raise KeyboardInterrupt
            else:
                assert job_id == "paid-job"
                calls["restored"] += 1
            return {"state": "completed", "job_id": "paid-job", "result": {"status": "completed"}}

        def documents(self):
            return {"memories": [], "relations": []}

        def inspect_time(self, *args):
            return {"at": args[0]}

    for name, value in (("MemoryService", Service), ("MeteredTransport", Meter),
                         ("ChatCompletionsSemanticProvider", Provider), ("Trial", Trial)):
        monkeypatch.setattr(evaluation, name, value)
    monkeypatch.setattr(evaluation, "stage_checks", lambda *args: {"offline_fixture": True})
    with pytest.raises(KeyboardInterrupt):
        invoke(evaluation, monkeypatch, tmp_path, "--live")
    invoke(evaluation, monkeypatch, tmp_path, "--live", "--resume")
    report = json.loads((tmp_path / "report.json").read_text())
    scenario = report["scenarios"]["temporal"]
    assert len(scenario["stages"]) == 1 and "pending" not in scenario
    assert calls == {"created_brains": 1, "observations": 1, "paid": 1, "restored": 1}
    invoke(evaluation, monkeypatch, tmp_path, "--live", "--resume")
    assert calls == {"created_brains": 1, "observations": 1, "paid": 1, "restored": 1}
