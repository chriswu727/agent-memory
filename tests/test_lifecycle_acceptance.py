import copy
import json
import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from test_memory_lifecycle_evaluation import evaluation as evaluation
from test_memory_lifecycle_evaluation import no_live_access, trial_fixture

from membrain.runtime import ModelRequest, TurnResult


def test_acceptance_plan_is_fixed_memory_only_without_review_or_live_access(
        evaluation, monkeypatch, tmp_path, capsys):
    no_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["eval", "--plan", "--profile", "acceptance", "--orientation-policy", "on_demand"])
    evaluation.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["version"] == "memory-lifecycle-acceptance-v1"
    assert planned["arms"] == ["memory"]
    assert len(planned["fixtures"]) == 100 and len(planned["dataset"]["probes"]) == 12
    assert planned["fixtures"] == evaluation.long_history(history_turns=100)["history"]
    assert planned["memory_arm_call_allocation"] == {"semantic": 624, "host": 112}
    assert planned["per_arm_max_calls"] == 736
    assert planned["per_arm_max_reserved_output_tokens"] == 624 * 16384 + 112 * 4096
    assert planned["review_policy"] is planned["reviewer_id"] is planned["summary"] is None
    assert planned["baseline_call_allocation"] == {}
    assert planned["acceptance_policy"]["checkpoint_after"] == 16
    assert planned["acceptance_policy"]["replay_stages"] == ["history-015", "history-099"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("turns", [99, 101, 104, True, 100.0])
def test_acceptance_cannot_be_sliced_or_extended(evaluation, turns):
    with pytest.raises(ValueError, match="exactly 100"):
        evaluation.plan("acceptance", history_turns=turns)


def test_twelve_frozen_questions_have_six_exact_source_oracles_outside_history(evaluation):
    data = evaluation.acceptance_history()
    original = evaluation.long_history(history_turns=100)
    assert data["probes"][:6] == original["probes"]
    assert len({probe["brain"] for probe in data["probes"]}) == 12
    assert len({probe["text"] for probe in data["probes"]}) == 12
    assert all(not probe["learn"] for probe in data["probes"])
    assert not ({probe["text"] for probe in data["probes"]} & {item["text"] for item in data["history"]})
    for probe in data["probes"][6:]:
        oracle = dict(data["oracle"][probe["name"]])
        assert oracle.pop("paraphrase_of") == probe["name"].removesuffix("_paraphrase")
        assert oracle == original["oracle"][probe["name"].removesuffix("_paraphrase")]
    evaluation.validate_dataset(data)
    data["probes"][-1]["text"] += "tampered"
    with pytest.raises(ValueError, match="changed"):
        evaluation.validate_dataset(data)


@pytest.mark.parametrize("damage", ["arm", "history", "review", "checkpoint", "budget"])
def test_acceptance_execution_contract_is_validated_even_with_rehashed_plan(evaluation, damage):
    planned = evaluation.plan("acceptance")
    if damage == "arm":
        planned["arms"].append("no_memory")
    elif damage == "history":
        planned["fixtures"] = planned["fixtures"][:16]
    elif damage == "review":
        planned["reviewer_id"] = "unauthorized"
    elif damage == "checkpoint":
        planned["acceptance_policy"]["checkpoint_after"] = 15
    else:
        planned["per_arm_max_calls"] += 1
    with pytest.raises(ValueError, match="acceptance plan changed"):
        evaluation.validate_frozen_plan({"plan": planned, "plan_sha256": evaluation.digest(planned)})


def acceptance_trial(evaluation, tmp_path):
    trial = trial_fixture(evaluation, tmp_path)
    trial.report["plan"] = evaluation.plan("acceptance")
    trial.report["plan_sha256"] = evaluation.digest(trial.report["plan"])
    return trial


def test_acceptance_constructs_no_reviewer_and_records_no_fictitious_approval(evaluation, tmp_path, monkeypatch):
    import membrain.answer_review as module

    trial = acceptance_trial(evaluation, tmp_path)
    monkeypatch.setattr(module, "AnswerReviewer", lambda *a, **kw: pytest.fail("No answer reviewer"))
    received = []
    monkeypatch.setattr(evaluation, "BrainRuntime", lambda *a, **kw: received.append(kw) or object())
    trial.make_runtime(object(), lambda _: "unused", deliver=lambda _: None)
    assert "answer_reviewer" not in received[0]
    assert trial.review_audit({"runtime": {"response_review": None}}) == {}
    with pytest.raises(RuntimeError, match="unauthorized"):
        trial.review_audit({"answer_review_calls": [{"state": "finished"}]})


def test_replay_uses_fresh_runtime_and_identical_sources_without_paid_callbacks(evaluation, tmp_path, monkeypatch):
    trial = acceptance_trial(evaluation, tmp_path)
    trial.meter.records = [{"settled": True}]
    stage = trial.current
    stage.update(brain_id="brain", runtime={"turn_id": "turn"}, model_output="Stored answer", checks={},
                 sources={"input_events": [{"id": "source"}], "output_events": [{"id": "delivered"}]})
    memory = SimpleNamespace(brain_id=None)

    @contextmanager
    def client():
        yield memory

    def runtime(actual, generate, **kwargs):
        assert actual is memory and actual.brain_id == "brain"
        with pytest.raises(RuntimeError, match="must not generate"):
            generate(None)
        with pytest.raises(RuntimeError, match="must not generate"):
            kwargs["deliver"](None)
        return SimpleNamespace(resume=lambda identifier: TurnResult(identifier, "completed", "Stored answer", True),
                               journal=SimpleNamespace(sources=lambda _: copy.deepcopy(stage["sources"])))

    monkeypatch.setattr(trial, "archive_client", client)
    monkeypatch.setattr(evaluation, "BrainRuntime", runtime)
    trial.replay_completed()
    assert stage["replay"]["checks_passed"] and trial.meter.calls == 0
    assert stage["checks"]["completed_turn_replay_has_no_model_or_delivery_calls"]


def checkpoint_trial(evaluation, tmp_path):
    directory = tmp_path / "memory"
    directory.mkdir()
    trial = acceptance_trial(evaluation, directory)
    trial.report["stages"] = [{"name": item["name"], "runtime": {"stage": "completed"},
                                "sources": {"original": item["text"]}, "learning_jobs": []}
                               for item in trial.report["plan"]["fixtures"][:16]]
    trial.meter.records = [{"event": "preserved"}]
    return trial


@pytest.mark.parametrize("decision", ["continue", "stop"])
def test_checkpoint_waits_without_calls_and_exact_release_preserves_stage_and_ledger(
        evaluation, tmp_path, monkeypatch, decision):
    trial = checkpoint_trial(evaluation, tmp_path)
    before = copy.deepcopy(trial.report["stages"])

    def waiting(_):
        checkpoint = json.loads((tmp_path / "checkpoint-016.json").read_text())
        saved = json.loads((tmp_path / "memory/report.json").read_text())
        assert saved["state"] == "waiting_for_checkpoint_review"
        assert saved["usage"]["reserved_calls"] == trial.meter.calls == 0
        assert len(saved["stages"]) == 16 and saved["stages"] == before
        evaluation.save(tmp_path / "checkpoint-016-decision.json",
                        {"checkpoint_sha256": checkpoint["sha256"], "decision": decision})

    monkeypatch.setattr(evaluation.time, "sleep", waiting)
    if decision == "stop":
        with pytest.raises(evaluation.EvaluationStopped, match="operator_stop"):
            evaluation.acceptance_checkpoint(trial)
    else:
        evaluation.acceptance_checkpoint(trial)
        assert trial.report["state"] == "started"
    assert trial.report["stages"] == before and trial.meter.records == [{"event": "preserved"}]
    assert trial.report["checkpoint_audit"]["state"] == (
        "operator_released" if decision == "continue" else "operator_stopped")


@pytest.mark.parametrize("unknown", [False, True])
def test_stop_while_waiting_preserves_unknown_priority(evaluation, tmp_path, monkeypatch, unknown):
    trial = checkpoint_trial(evaluation, tmp_path)

    def waiting(_):
        (tmp_path / "STOP").touch()
        trial.meter.unknown = int(unknown)

    monkeypatch.setattr(evaluation.time, "sleep", waiting)
    with pytest.raises(evaluation.EvaluationStopped) as error:
        evaluation.acceptance_checkpoint(trial)
    assert error.value.reason == ("unknown_provider_usage" if unknown else "operator_stop")
    assert trial.report["checkpoint_audit"]["state"] == "pending"
    assert len(trial.report["stages"]) == 16


@pytest.mark.parametrize("kind", ["wrong_hash", "extra", "duplicate", "oversized", "symlink", "fifo"])
def test_checkpoint_rejects_unbound_or_unsafe_release_file(evaluation, tmp_path, kind):
    path = tmp_path / "decision.json"
    value = {"checkpoint_sha256": "f" * 64, "decision": "continue"}
    if kind == "wrong_hash":
        value["checkpoint_sha256"] = "0" * 64
    if kind == "extra":
        value["extra"] = True
    if kind == "symlink":
        destination = tmp_path / "real"
        destination.write_text(json.dumps(value))
        path.symlink_to(destination)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_text(json.dumps(value) if kind not in {"duplicate", "oversized"} else (
            '{"decision":"stop","decision":"continue","checkpoint_sha256":"' + "f" * 64 + '"}'
            if kind == "duplicate" else " " * 1025))
    with pytest.raises((ValueError, OSError)):
        evaluation.checkpoint_decision(path, "f" * 64)


@pytest.mark.parametrize("checkpoint_action", ["continue", "stop", "marker", "malformed"])
def test_acceptance_control_flow_preserves_one_ledger_without_paid_reviewer(
        evaluation, tmp_path, monkeypatch, checkpoint_action):
    transports, events, probes, replays = [], [], [], []
    original_meter = evaluation.MeteredTransport

    def response(_):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "answer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def metered(*args, **kwargs):
        result = original_meter(*args, **kwargs, transport=httpx.MockTransport(response))
        transports.append(result)
        return result

    class Trial:
        def __init__(self, directory, report, database, provider, host, meter, reviewer, summary_host, **kwargs):
            assert reviewer is summary_host is None
            self.output, self.report, self.host, self.meter = directory, report, host, meter

        def run_stage(self, fixture):
            events.append(fixture["name"])
            assert self.host(ModelRequest("turn", fixture["name"], ({"role": "user", "content": fixture["text"]},),
                                          100, "{}")) == "answer"
            self.current = {"name": fixture["name"], "runtime": {"stage": "completed"}, "checks": {},
                            "semantic_review": {"state": "pending_independent_review"}}
            self.report["stages"].append(self.current)

        def replay_completed(self):
            replays.append((self.current["name"], self.meter.summary()["reserved_calls"]))

        def freeze_history(self):
            assert len(events) == 100
            self.report["history_checkpoint"] = {"archive": "same-trained-archive"}

        def prepare_probe(self, fixture):
            assert not fixture["learn"]
            assert self.report["history_checkpoint"] == {"archive": "same-trained-archive"}
            probes.append(fixture["brain"])

        def save(self):
            evaluation.save(self.output / "report.json", self.report)

    def release(_):
        assert events == [f"history-{i:03d}" for i in range(16)]
        assert transports[0].summary()["reserved_calls"] == 16
        checkpoint = json.loads((tmp_path / "checkpoint-016.json").read_text())
        if checkpoint_action == "marker":
            (tmp_path / "STOP").touch()
        else:
            evaluation.save(tmp_path / "checkpoint-016-decision.json", {
                "checkpoint_sha256": "wrong" if checkpoint_action == "malformed" else checkpoint["sha256"],
                "decision": "stop" if checkpoint_action == "stop" else "continue"})

    monkeypatch.setattr(evaluation, "MeteredTransport", metered)
    monkeypatch.setattr(evaluation, "LifecycleTrial", Trial)
    monkeypatch.setattr(evaluation, "ChatCompletionsRecallReasoner", lambda *a, **kw: pytest.fail("No reviewer provider"))
    monkeypatch.setattr(evaluation.time, "sleep", release)
    planned = evaluation.plan("acceptance")
    result = evaluation.run_arm(tmp_path / "memory", planned, "memory", SimpleNamespace(dsn="unused"), "fake-key")
    if checkpoint_action != "continue":
        assert result["state"] == ("interrupted" if checkpoint_action == "malformed" else "stopped")
        if checkpoint_action != "malformed":
            assert result["stopped_reason"] == "operator_stop"
        assert len(events) == len(set(events)) == result["usage"]["reserved_calls"] == 16
        assert not probes and replays == [("history-015", 16)]
        assert len(result["call_ledger"]) == 32 and all(item._closed for item in transports)
        assert json.loads((tmp_path / "memory/report.json").read_text()) == result
        return
    assert result["state"] == "finished", result.get("error")
    assert result["semantic_review"] == "pending_independent_review"
    assert len(events) == len(set(events)) == 112
    assert len(probes) == len(set(probes)) == 12
    assert replays == [("history-015", 16), ("history-099", 100)]
    assert result["usage"]["reserved_calls"] == result["usage"]["usage_confirmed_calls"] == 112
    assert result["usage"]["usage_unknown_calls"] == 0 and len(result["call_ledger"]) == 224
    assert all(item._closed for item in transports)
    assert {item.ledger_path for item in transports} == {tmp_path / "memory/calls.jsonl"}


def test_acceptance_stop_before_setup_avoids_environment_database_and_providers(
        evaluation, tmp_path, monkeypatch):
    no_live_access(evaluation, monkeypatch)
    (tmp_path / "STOP").touch()
    monkeypatch.setattr(sys, "argv", ["eval", "--live", "--profile", "acceptance", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["stopped_reason"] == "operator_stop" and report["arms"] == {}
