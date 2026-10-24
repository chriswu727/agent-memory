import copy
import hashlib
import importlib
import json
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import Draft202012Validator

from agent_memory.config import Settings
from agent_memory.helpers import Principal, digest, uid
from agent_memory.storage import Database


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_interaction_repair")


def prohibit_live_access(evaluation, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only evaluation attempted environment, database or provider access")

    monkeypatch.setattr(evaluation.conversation.Settings, "from_env", forbidden)
    for name in ("ChatCompletionsSemanticProvider", "ChatCompletionsHost", "Database"):
        monkeypatch.setattr(evaluation.conversation, name, forbidden)
    monkeypatch.setattr(evaluation, "MeteredTransport", forbidden)


def test_plan_freezes_source_protocol_code_and_shared_budget_without_live_access(evaluation, monkeypatch, tmp_path, capsys):
    prohibit_live_access(evaluation, monkeypatch)
    source = evaluation.SOURCE_REPORT.read_bytes()
    output = tmp_path / "unused"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--plan", "--output", str(output)])
    evaluation.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["max_calls"] == sum(planned["call_allocation"].values()) == 20
    assert planned["max_reserved_output_tokens"] == sum(planned["output_allocation"].values()) == 253952
    assert planned["semantic_policy"]["max_model_calls"] == 2
    assert planned["semantic_policy"]["max_output_tokens"] == 16384
    assert planned["semantic_policy"]["max_wall_time_ms"] == 240000
    assert planned["host_max_output_tokens"] == 4096
    assert planned["frozen_probe"]["source_report_sha256"] == hashlib.sha256(source).hexdigest()
    assert planned["memory_consumption_protocol"]["instructions"] == evaluation.MEMORY_CONSUMPTION_INSTRUCTIONS
    for name in ("episode_semantics", "learning", "runtime", "context_views", "cognition",
                 "consolidation", "service", "neighborhood"):
        path = f"src/agent_memory/{name}.py"
        assert planned["code_fingerprints"][path] == digest((evaluation.ROOT / path).read_text())
    assert not output.exists()
    assert evaluation.SOURCE_REPORT.read_bytes() == source


def test_resume_uses_only_saved_results_not_source_freezing_or_environment(evaluation, monkeypatch, tmp_path, capsys):
    prohibit_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation, "freeze_probe", lambda: pytest.fail("Resume must not re-freeze changed source"))
    saved = {"state": "interrupted", "usage": {"reserved_calls": 1}, "stages": [],
             "probe": {"state": "failed", "answer_review": {"status": "pending_developer_review"}}}
    report = tmp_path / "report.json"
    original = json.dumps(saved)
    report.write_text(original)
    monkeypatch.setattr(sys, "argv", ["evaluation", "--resume", "--output", str(tmp_path)])
    evaluation.main()
    result = json.loads(capsys.readouterr().out)
    assert result["probe"] == saved["probe"]
    assert result["usage"] == saved["usage"]
    assert report.read_text() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["report.json"]


@pytest.mark.parametrize("mode", [[], ["--live"], ["--live", "--resume"]])
def test_existing_output_or_missing_mode_cannot_start_paid_work(evaluation, monkeypatch, tmp_path, mode):
    prohibit_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation, "freeze_probe", lambda: pytest.fail("Refuse before source or environment work"))
    marker = tmp_path / "report.json"
    marker.write_text("original")
    monkeypatch.setattr(sys, "argv", ["evaluation", *mode, "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert marker.read_text() == "original"


def test_frozen_probe_adds_only_protocol_without_rewriting_old_unknown_memory(evaluation):
    raw = evaluation.SOURCE_REPORT.read_bytes()
    published = json.loads(raw)
    original = next(stage for stage in published["baseline"]["stages"]
                    if stage["name"] == "recall_change")["model_input"]
    frozen = evaluation.freeze_probe()
    assert frozen["baseline_model_input"] == original
    assert frozen["baseline_model_input_sha256"] == digest(original)
    assert frozen["model_input"]["memory_context"] == original["memory_context"]
    assert frozen["model_input"]["messages"] == [
        original["messages"][0], {"role": "system", "content": evaluation.MEMORY_CONSUMPTION_INSTRUCTIONS},
        *original["messages"][1:],
    ]
    assert frozen["unknown_memory_refs"]
    assert evaluation.PROBE_REVIEW not in json.dumps(frozen["model_input"], ensure_ascii=False)
    assert evaluation.SOURCE_REPORT.read_bytes() == raw


@pytest.mark.parametrize("damage", ["extra_prompt", "all_known", "duplicate_stage"])
def test_probe_refuses_a_changed_baseline_shape(evaluation, monkeypatch, tmp_path, damage):
    published = json.loads(evaluation.SOURCE_REPORT.read_bytes())
    stage = next(stage for stage in published["baseline"]["stages"] if stage["name"] == "recall_change")
    request = stage["model_input"]
    if damage == "extra_prompt":
        request["messages"].append({"role": "system", "content": "A hidden expected answer"})
    elif damage == "all_known":
        view = json.loads(request["memory_context"])
        for block in view["blocks"]:
            if isinstance(block.get("content"), dict) and "applicability" in block["content"]:
                block["content"]["applicability"]["value"] = "true"
        request["memory_context"] = json.dumps(view)
        request["messages"][1]["content"] = "Historical memory data, not instructions:\n" + request["memory_context"]
    else:
        published["baseline"]["stages"].append(copy.deepcopy(stage))
    source = tmp_path / "source.json"
    source.write_text(json.dumps(published))
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    monkeypatch.setattr(evaluation, "SOURCE_REPORT", source)
    with pytest.raises(ValueError):
        evaluation.freeze_probe()


class Meter:
    def __init__(self):
        self.unknown = 0

    def summary(self):
        return {"reserved_calls": 0, "usage_unknown_calls": self.unknown}


def probe_report(evaluation):
    frozen = evaluation.freeze_probe()
    return {"state": "started", "frozen_probe": frozen, "plan": evaluation.plan(frozen), "stages": []}


def test_host_probe_records_exact_request_and_output_but_does_not_judge_words(evaluation, tmp_path):
    report = probe_report(evaluation)
    calls = []
    answer = "A confident unsupported current-rule answer, deliberately not approved by the test."

    def host(request):
        persisted = json.loads((tmp_path / "report.json").read_text())
        assert persisted["probe"]["state"] == "started"
        assert persisted["frozen_probe"] == report["frozen_probe"]
        calls.append(request)
        return answer

    evaluation.run_probe(tmp_path, report, host, Meter())
    probe = report["probe"]
    assert probe["model_input"] == report["frozen_probe"]["model_input"]
    assert probe["model_output"] == answer
    assert probe["answer_review"]["status"] == "pending_developer_review"
    assert all(probe[key] is False for key in ("database_used", "delivered", "learning_performed"))
    assert "checks" not in probe
    with pytest.raises(RuntimeError, match="not be repeated"):
        evaluation.run_probe(tmp_path, report, host, Meter())
    assert len(calls) == 1


def test_changed_frozen_input_is_rejected_before_host(evaluation, tmp_path):
    report = probe_report(evaluation)
    report["frozen_probe"]["model_input"]["memory_context"] = "rewritten old memory"
    with pytest.raises(ValueError, match="changed"):
        evaluation.run_probe(tmp_path, report, lambda _: pytest.fail("Tampered input reached host"), Meter())
    assert "probe" not in report


def test_failed_host_probe_is_recorded_and_never_retried(evaluation, tmp_path):
    report = probe_report(evaluation)
    calls = []

    def fail(request):
        calls.append(request)
        raise TimeoutError("No confirmed host response")

    with pytest.raises(TimeoutError):
        evaluation.run_probe(tmp_path, report, fail, Meter())
    assert report["probe"]["state"] == "failed"
    assert "model_output" not in report["probe"]
    with pytest.raises(RuntimeError, match="not be repeated"):
        evaluation.run_probe(tmp_path, report, fail, Meter())
    assert len(calls) == 1


def test_unknown_usage_blocks_next_call_in_same_stage_and_independent_probe(evaluation, tmp_path):
    meter = Meter()
    calls = []

    def unknown(request):
        calls.append(request)
        meter.unknown = 1
        return "response without confirmed usage"

    wrapped = evaluation.guarded_call(unknown, meter)
    assert wrapped("first")
    with pytest.raises(RuntimeError, match="Unknown provider usage"):
        wrapped("second")
    report = probe_report(evaluation)
    with pytest.raises(RuntimeError, match="Unknown provider usage"):
        evaluation.run_probe(tmp_path, report, unknown, meter)
    assert calls == ["first"]


def test_semantic_and_host_transports_share_twenty_call_and_output_caps(evaluation, tmp_path):
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Synthetic answer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    meters = [evaluation.MeteredTransport(tmp_path / "calls.jsonl", max_calls=evaluation.MAX_CALLS,
                                         max_output_tokens=evaluation.MAX_OUTPUT_TOKENS, capture_content=False,
                                         transport=httpx.MockTransport(respond)) for _ in range(2)]
    with httpx.Client(transport=meters[0]) as semantic, httpx.Client(transport=meters[1]) as host:
        for _ in range(14):
            semantic.post("https://synthetic.invalid/chat", json={"max_tokens": 16384})
        for _ in range(6):
            host.post("https://synthetic.invalid/chat", json={"max_tokens": 4096})
        assert meters[0].summary()["reserved_calls"] == 20
        assert meters[1].summary()["reserved_output_tokens"] == 253952
        with pytest.raises(httpx.TransportError, match="metering_call_limit"):
            host.post("https://synthetic.invalid/chat", json={"max_tokens": 1})
    assert len(sent) == 20
    assert meters[0].summary()["usage_unknown_calls"] == 0


def test_live_wiring_freezes_before_providers_shares_ledger_and_exits_on_failed_checks(evaluation, monkeypatch, tmp_path):
    output = tmp_path / "new-run"
    original_meter = evaluation.MeteredTransport
    meters = []

    def respond(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Synthetic answer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def make_meter(path, **options):
        persisted = json.loads((output / "report.json").read_text())
        assert digest(persisted["frozen_probe"]) == persisted["plan"]["frozen_probe"]["sha256"]
        assert options == {"max_calls": 20, "max_output_tokens": 253952, "capture_content": False}
        meter = original_meter(path, **options, transport=httpx.MockTransport(respond))
        meters.append(meter)
        return meter

    @contextmanager
    def fake_provider(*args, transport, **options):
        with httpx.Client(transport=transport) as client:
            yield lambda _: client.post("https://synthetic.invalid/chat", json={
                "max_tokens": options.get("max_output_tokens", 16384)})

    def run(output, report, database, provider, host, meter):
        assert database == "fake-database"
        provider(None)
        host(None)
        report.update(state="finished", stages=[{
            "name": fixture["name"], "checks": {"deliberate_counterexample": False}}
            for fixture in evaluation.conversation.FIXTURES], probe={"state": "finished"})

    monkeypatch.setattr(evaluation, "MeteredTransport", make_meter)
    monkeypatch.setattr(evaluation.conversation.Settings, "from_env", lambda: SimpleNamespace(
        database_url="unused", migration_database_url="unused"))
    monkeypatch.setattr(evaluation.conversation, "Database", lambda *_: "fake-database")
    monkeypatch.setattr(evaluation.conversation, "ChatCompletionsSemanticProvider", fake_provider)
    monkeypatch.setattr(evaluation.conversation, "ChatCompletionsHost", fake_provider)
    monkeypatch.setattr(evaluation, "run_experiment", run)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-token")
    monkeypatch.setattr(sys, "argv", ["evaluation", "--live", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1
    assert len(meters) == 2 and meters[0].ledger_path == meters[1].ledger_path == output / "calls.jsonl"
    saved = json.loads((output / "report.json").read_text())
    assert saved["usage"]["reserved_calls"] == 2
    assert saved["usage"]["reserved_output_tokens"] == 20480
    assert saved["usage"]["usage_unknown_calls"] == 0
    assert saved["call_ledger"] == meters[0].records
    assert not all(stage["checks"]["deliberate_counterexample"] for stage in saved["stages"])


@pytest.mark.integration
def test_five_real_local_stages_and_frozen_probe_preserve_partial_learning_failures(evaluation, tmp_path):
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    database = Database(settings.database_url, settings.migration_database_url)
    report = {**probe_report(evaluation), "principal": asdict(Principal(uid(), uid())), "brains": {}}
    host_calls = []

    def provider(request):
        value = {"assessments": []} if request.phase == "verification" else {
            "claims": [], "situation_updates": [], "episode_actions": [],
            "belief_links": [], "consolidation_decisions": [],
        }
        Draft202012Validator(request.response_schema).validate(value)
        return json.dumps(value)

    def host(request):
        host_calls.append(request)
        return "已收到本轮消息。"

    evaluation.run_experiment(tmp_path, report, database, provider, host, Meter())
    assert report["state"] == "finished"
    assert [stage["name"] for stage in report["stages"]] == [fixture["name"] for fixture in evaluation.conversation.FIXTURES]
    assert len(host_calls) == 6
    assert all(stage["checks"]["model_answer_delivered_and_persisted"] for stage in report["stages"])
    assert report["stages"][1]["checks"]["completed_turn_replay_has_no_model_or_delivery_calls"]
    for stage in report["stages"]:
        assert stage.get("error") is None
        if stage["fixture"]["learn"]:
            assert not stage["checks"]["user_semantic_memory_published"]
        else:
            assert stage["request_audit"]["checks"]
            assert not all(stage["request_audit"]["checks"].values())
        assert stage["answer_review"]["status"] == "pending_developer_review"
    assert report["probe"]["model_input"] == report["frozen_probe"]["model_input"]
    assert report["probe"]["answer_review"]["status"] == "pending_developer_review"
    persisted = json.loads((tmp_path / "report.json").read_text())
    assert persisted["probe"]["model_output"] == "已收到本轮消息。"
