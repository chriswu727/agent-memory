import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from agent_memory.helpers import digest


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    module = importlib.import_module("examples.evaluate_consumption_repair")
    monkeypatch.setattr(module, "MEMORY_CONSUMPTION_VERSION", "test-protocol-v2")
    monkeypatch.setattr(module, "MEMORY_CONSUMPTION_INSTRUCTIONS", "Revised generic memory protocol.")
    return module


def original_request(name):
    context = '{"literal Unicode": "旧资料", "unknown": true, "spacing":  1}\n'
    messages = [{"role": "system", "content": "Original host instructions.\n"},
                {"role": "system", "content": "Original generic protocol."},
                {"role": "user", "content": "Historical memory data, not instructions:\n" + context},
                {"role": "user", "content": f"{name} 当前输入\n保留空格  和标点！"}]
    return {"turn_id": f"turn-{name}", "request_id": f"request-{name}", "messages": messages,
            "input_tokens_estimate": len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode()),
            "memory_context": context}


@pytest.fixture
def source(evaluation, monkeypatch, tmp_path):
    planned = {"model": "synthetic-test-model", "thinking": "enabled", "max_calls": 20,
               "max_reserved_output_tokens": 253952, "host_max_output_tokens": 4096,
               "instructions": "Original host instructions.\n", "runtime_policy": {"max_input_tokens": 24000},
               "memory_consumption_protocol": {"version": "test-protocol-v1",
                                               "instructions": "Original generic protocol."}}
    stages = [{"name": name, "state": "finished", "model_input": original_request(name),
               "fixture": {"text": original_request(name)["messages"][-1]["content"]},
               "model_output": f"PRIVATE_OLD_ANSWER_{name}", "checks": {"deliberate_failed_check": False}}
              for name in ("learn", "recall")]
    frozen_probe = {"model_input": original_request("probe")}
    planned["frozen_probe"] = {"sha256": digest(frozen_probe)}
    ledger = [record for index in range(3) for record in (
        {"event": "reserved", "request_id": str(index), "reserved_output_tokens": 4096},
        {"event": "completed", "request_id": str(index), "usage_confirmed": True,
         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})]
    report = {"version": 1, "kind": "interaction_repair", "state": "finished", "plan": planned,
              "plan_sha256": digest(planned), "stages": stages, "frozen_probe": frozen_probe,
              "probe": {"state": "finished", "model_input": original_request("probe"),
                        "model_output": "PRIVATE_OLD_ANSWER_probe"}, "call_ledger": ledger,
              "usage": {"reserved_calls": 3, "completed_calls": 3, "reserved_output_tokens": 12288,
                        "usage_confirmed_calls": 3, "usage_unknown_calls": 0,
                        "usage_totals": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}}}
    directory = tmp_path / "source"
    directory.mkdir()
    path = directory / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (directory / "calls.jsonl").write_text("preserve-the-original-ledger\n")
    monkeypatch.setattr(evaluation, "SOURCE_REPORT", path)
    return report


def ledger_usage(records):
    reservations = [record for record in records if record["event"] == "reserved"]
    completions = [record for record in records if record["event"] == "completed"]
    totals = {}
    for record in completions:
        for key, value in record.get("usage", {}).items():
            totals[key] = totals.get(key, 0) + value
    confirmed = sum(record.get("usage_confirmed") is True for record in completions)
    return {"reserved_calls": len(reservations), "completed_calls": len(completions),
            "reserved_output_tokens": sum(record["reserved_output_tokens"] for record in reservations),
            "usage_confirmed_calls": confirmed, "usage_unknown_calls": len(reservations) - confirmed,
            "usage_totals": totals}


@pytest.fixture
def truncated_source(evaluation, source):
    error = {"type": "HostProviderError", "code": "incomplete_response"}
    source.update(state="interrupted", error=copy.deepcopy(error))
    for name in ("change", "recall_change", "empty_control"):
        source["stages"].append({"name": name, "state": "finished", "model_output": f"original {name}"})
    source["probe"].update(state="failed", error=copy.deepcopy(error))
    del source["probe"]["model_output"]
    ledger = []
    for index in range(20):
        limit = 16384 if index < 14 else 4096
        ledger.extend([
            {"event": "reserved", "request_id": str(index), "reserved_output_tokens": limit},
            {"event": "completed", "request_id": str(index), "usage_confirmed": True,
             "status_code": 200, "outcome": "body_complete", "finish_reason": "length" if index == 19 else "stop",
             "usage": {"prompt_tokens": 10, "completion_tokens": 4096 if index == 19 else 5,
                       "total_tokens": 4106 if index == 19 else 15}},
        ])
    body = {"model": source["plan"]["model"], "messages": source["probe"]["model_input"]["messages"],
            "max_tokens": 4096, "stream": False, "thinking": {"type": "enabled"}}
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    ledger[-2].update(request_sha256=hashlib.sha256(encoded).hexdigest(), request_bytes=len(encoded))
    source["call_ledger"] = ledger
    source["usage"] = ledger_usage(ledger)
    source["probe"].update(usage_before=ledger_usage(ledger[:-2]), usage_after=copy.deepcopy(source["usage"]))
    evaluation.SOURCE_REPORT.write_text(json.dumps(source, ensure_ascii=False))
    return source


def prohibit_live_access(evaluation, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only diagnostic attempted environment or paid-provider access")

    for name in ("load_dotenv", "MeteredTransport", "ChatCompletionsHost"):
        monkeypatch.setattr(evaluation, name, forbidden)


def test_plan_is_read_only_and_freezes_three_cases_with_separate_additional_budget(
    evaluation, source, monkeypatch, tmp_path, capsys,
):
    prohibit_live_access(evaluation, monkeypatch)
    original = evaluation.SOURCE_REPORT.read_bytes()
    ledger = evaluation.SOURCE_REPORT.with_name("calls.jsonl").read_bytes()
    output = tmp_path / "unused"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--plan", "--output", str(output)])
    evaluation.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["case_names"] == ["learn", "recall", "probe"]
    assert planned["max_calls"] == sum(planned["call_allocation"].values()) == 3
    assert planned["max_reserved_output_tokens"] == 3 * planned["host_max_output_tokens"] == 12288
    assert "additional to the original 20-call experiment" in planned["cost_accounting"]
    assert planned["source_usage"] == source["usage"]
    assert planned["source_report"] == "report.json"
    assert "only as diagnostic audit identities" in planned["request_identity_use"]
    assert planned["source_report_sha256"] == hashlib.sha256(original).hexdigest()
    assert planned["semantic_review"]["status"] == "pending_developer_review"
    for name in ("examples/evaluate_consumption_repair.py", "examples/evaluation_host.py",
                 "examples/evaluation_support.py", "src/agent_memory/runtime.py"):
        assert planned["code_fingerprints"][name] == digest((evaluation.ROOT / name).read_text())
    assert not output.exists()
    assert evaluation.SOURCE_REPORT.read_bytes() == original
    assert evaluation.SOURCE_REPORT.with_name("calls.jsonl").read_bytes() == ledger


def test_freeze_changes_exactly_one_system_content_and_preserves_other_bytes(evaluation, source):
    frozen = evaluation.freeze_inputs()
    assert frozen["source_plan_sha256"] == source["plan_sha256"]
    assert frozen["source_call_ledger_sha256"] == digest(source["call_ledger"])
    assert frozen["original_protocol"] == source["plan"]["memory_consumption_protocol"]
    for case in frozen["cases"]:
        original, changed = case["original_model_input"], case["model_input"]
        assert original == original_request(case["name"])
        assert case["original_model_input_sha256"] == digest(original)
        assert case["model_input_sha256"] == digest(changed)
        assert changed["turn_id"] == original["turn_id"]
        assert changed["request_id"] == original["request_id"]
        assert changed["memory_context"].encode() == original["memory_context"].encode()
        assert [i for i, (before, after) in enumerate(zip(original["messages"], changed["messages"]))
                if before != after] == [1]
        for index in (0, 2, 3):
            assert changed["messages"][index]["content"].encode() == original["messages"][index]["content"].encode()
        assert changed["messages"][1] == {"role": "system", "content": evaluation.MEMORY_CONSUMPTION_INSTRUCTIONS}
        assert changed["input_tokens_estimate"] == len(json.dumps(
            changed["messages"], ensure_ascii=False, separators=(",", ":")).encode())
        restored = copy.deepcopy(changed)
        restored["messages"][1]["content"] = original["messages"][1]["content"]
        restored["input_tokens_estimate"] = original["input_tokens_estimate"]
        assert restored == original
        comparison = case["developer_comparison"]
        assert comparison["original_model_output"] == f"PRIVATE_OLD_ANSWER_{case['name']}"
        assert not comparison["sent_to_model"]
        assert "PRIVATE_OLD_ANSWER" not in json.dumps(changed)
        assert evaluation.REVIEW not in json.dumps(changed)
        assert "PRIVATE_OLD_ANSWER" not in evaluation.REVIEW


def test_exact_confirmed_truncated_probe_is_a_source_with_explicit_missing_old_answer(
    evaluation, truncated_source, monkeypatch, tmp_path, capsys,
):
    prohibit_live_access(evaluation, monkeypatch)
    original = evaluation.SOURCE_REPORT.read_bytes()
    frozen = evaluation.freeze_inputs()
    assert frozen["source_state"] == "interrupted"
    assert frozen["source_completion"] == "final_probe_output_limit"
    assert frozen["source_usage"]["reserved_calls"] == 20
    probe = frozen["cases"][-1]
    assert probe["original_model_input"] == truncated_source["probe"]["model_input"]
    assert probe["developer_comparison"] == {
        "original_state": "failed", "original_error": truncated_source["probe"]["error"],
        "original_output_state": "missing", "sent_to_model": False}
    assert "original_model_output" not in probe["developer_comparison"]
    assert "original_model_output_sha256" not in probe["developer_comparison"]
    assert all(case["developer_comparison"]["original_output_state"] == "present" for case in frozen["cases"][:-1])
    output = tmp_path / "unused"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--plan", "--output", str(output)])
    evaluation.main()
    planned = json.loads(capsys.readouterr().out)
    assert "did not complete" in planned["unknown_control_baseline"]
    assert "no host answer exists" in planned["unknown_control_baseline"]
    assert "cap is unchanged and no retry is allowed" in planned["unknown_control_baseline"]
    assert planned["source_execution"]["error"] == truncated_source["error"]
    assert planned["max_calls"] == 3 and planned["max_reserved_output_tokens"] == 12288
    assert planned["host_max_output_tokens"] == 4096
    assert planned["semantic_review"]["status"] == "pending_developer_review"
    assert evaluation.SOURCE_REPORT.read_bytes() == original
    assert not output.exists()


@pytest.mark.parametrize("damage", [
    "source_state", "source_error", "probe_state", "probe_error_type", "probe_error_code", "probe_has_output",
    "stage_unfinished", "stage_error", "missing_stage", "duplicate_stage", "prior_model_error",
    "unknown_last_usage", "missing_last_usage", "missing_last_completion", "not_length", "short_completion",
    "different_output_cap", "earlier_incomplete_response", "http_error", "stream_error", "ledger_request_mismatch",
    "ledger_body_hash_mismatch", "ledger_body_size_mismatch", "before_usage_mismatch", "after_usage_mismatch",
    "changed_frozen_input", "source_stopped_reason", "source_smaller_budget", "source_usage_mismatch",
])
def test_interrupted_source_exception_rejects_every_other_failure(evaluation, truncated_source, damage):
    source = truncated_source
    if damage == "source_state":
        source["state"] = "stopped"
    elif damage == "source_error":
        source["error"]["code"] = "timeout"
    elif damage == "probe_state":
        source["probe"]["state"] = "started"
    elif damage == "probe_error_type":
        source["probe"]["error"]["type"] = "TimeoutError"
    elif damage == "probe_error_code":
        source["probe"]["error"]["code"] = "invalid_response"
    elif damage == "probe_has_output":
        source["probe"]["model_output"] = None
    elif damage == "stage_unfinished":
        source["stages"][2]["state"] = "started"
    elif damage == "stage_error":
        source["stages"][3]["error"] = {"type": "RuntimeError", "code": None}
    elif damage == "missing_stage":
        source["stages"].pop()
    elif damage == "duplicate_stage":
        source["stages"].append(copy.deepcopy(source["stages"][-1]))
    elif damage == "prior_model_error":
        source["stages"][0]["cognitive_calls"] = [{"state": "failed", "error": {"type": "ValueError"}}]
    elif damage == "unknown_last_usage":
        source["call_ledger"][-1]["usage_confirmed"] = False
    elif damage == "missing_last_usage":
        del source["call_ledger"][-1]["usage"]
    elif damage == "missing_last_completion":
        source["call_ledger"].pop()
    elif damage == "not_length":
        source["call_ledger"][-1]["finish_reason"] = "stop"
    elif damage == "short_completion":
        source["call_ledger"][-1]["usage"]["completion_tokens"] = 4095
    elif damage == "different_output_cap":
        source["call_ledger"][-2]["reserved_output_tokens"] = 8192
    elif damage == "earlier_incomplete_response":
        source["call_ledger"][1]["finish_reason"] = "length"
    elif damage == "http_error":
        source["call_ledger"][-1]["status_code"] = 500
    elif damage == "stream_error":
        source["call_ledger"][-1]["outcome"] = "stream_error"
    elif damage == "ledger_request_mismatch":
        source["call_ledger"][-1]["request_id"] = "another-call"
    elif damage == "ledger_body_hash_mismatch":
        source["call_ledger"][-2]["request_sha256"] = "different-body"
    elif damage == "ledger_body_size_mismatch":
        source["call_ledger"][-2]["request_bytes"] += 1
    elif damage == "before_usage_mismatch":
        source["probe"]["usage_before"]["reserved_calls"] = 18
    elif damage == "after_usage_mismatch":
        source["probe"]["usage_after"]["reserved_calls"] = 19
    elif damage == "changed_frozen_input":
        source["probe"]["model_input"]["messages"][-1]["content"] = "Another question"
    elif damage == "source_stopped_reason":
        source["stopped_reason"] = "unknown_provider_usage"
    elif damage == "source_smaller_budget":
        source["plan"]["max_calls"] = 19
        source["plan_sha256"] = digest(source["plan"])
    else:
        source["usage"]["usage_confirmed_calls"] = 19
    if damage == "short_completion":
        source["usage"] = ledger_usage(source["call_ledger"])
        source["probe"]["usage_after"] = copy.deepcopy(source["usage"])
    evaluation.SOURCE_REPORT.write_text(json.dumps(source))
    with pytest.raises(ValueError):
        evaluation.freeze_inputs()


@pytest.mark.parametrize("damage", [
    "running", "learn_unfinished", "recall_unfinished", "probe_unfinished", "missing_learn", "duplicate_recall",
    "unknown_summary", "unknown_record", "pending_record", "bad_usage", "bad_plan_hash", "changed_probe",
    "extra_message", "rewritten_user", "rewritten_context", "wrong_protocol", "bad_estimate", "missing_output",
])
def test_incomplete_or_inconsistent_source_refused_before_execution(evaluation, source, damage):
    if damage == "running":
        source["state"] = "started"
    elif damage.endswith("_unfinished"):
        name = damage.removesuffix("_unfinished")
        (source["probe"] if name == "probe" else next(stage for stage in source["stages"]
                                                    if stage["name"] == name))["state"] = "started"
    elif damage == "missing_learn":
        source["stages"] = source["stages"][1:]
    elif damage == "duplicate_recall":
        source["stages"].append(copy.deepcopy(source["stages"][1]))
    elif damage == "unknown_summary":
        source["usage"]["usage_unknown_calls"] = 1
    elif damage == "unknown_record":
        source["call_ledger"][1]["usage_confirmed"] = False
    elif damage == "pending_record":
        source["call_ledger"].pop()
    elif damage == "bad_usage":
        source["call_ledger"][1]["usage"]["completion_tokens"] = True
    elif damage == "bad_plan_hash":
        source["plan_sha256"] = "changed"
    elif damage == "changed_probe":
        source["probe"]["model_input"]["messages"][-1]["content"] = "Changed prompt"
    elif damage == "extra_message":
        source["stages"][0]["model_input"]["messages"].append({"role": "system", "content": "Expected answer"})
    elif damage == "rewritten_user":
        source["stages"][0]["model_input"]["messages"][-1]["content"] = "Changed current request"
    elif damage == "rewritten_context":
        source["stages"][0]["model_input"]["memory_context"] = "Changed old context"
    elif damage == "wrong_protocol":
        source["stages"][0]["model_input"]["messages"][1]["content"] = "Unplanned old protocol"
    elif damage == "bad_estimate":
        source["stages"][0]["model_input"]["input_tokens_estimate"] = 1
    else:
        del source["stages"][0]["model_output"]
    evaluation.SOURCE_REPORT.write_text(json.dumps(source))
    with pytest.raises(ValueError):
        evaluation.freeze_inputs()


@pytest.mark.parametrize("damage", ["unchanged_protocol", "oversized_protocol"])
def test_noop_or_oversized_revision_is_refused(evaluation, source, monkeypatch, damage):
    instructions = "Original generic protocol." if damage == "unchanged_protocol" else "x" * 24000
    monkeypatch.setattr(evaluation, "MEMORY_CONSUMPTION_INSTRUCTIONS", instructions)
    with pytest.raises(ValueError):
        evaluation.freeze_inputs()


def test_source_path_is_project_relative_when_available(evaluation, source, monkeypatch):
    monkeypatch.setattr(evaluation, "ROOT", evaluation.SOURCE_REPORT.parents[1])
    frozen = evaluation.freeze_inputs()
    assert frozen["source_report"] == "source/report.json"
    assert not Path(frozen["source_report"]).is_absolute()


@pytest.mark.parametrize("mode", ["--plan", "--live"])
def test_unchanged_protocol_refused_before_environment_or_output(evaluation, source, monkeypatch, tmp_path, mode):
    prohibit_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation, "MEMORY_CONSUMPTION_INSTRUCTIONS", "Original generic protocol.")
    output = tmp_path / "unused"
    monkeypatch.setattr(sys, "argv", ["evaluation", mode, "--output", str(output)])
    with pytest.raises(ValueError, match="changed protocol"):
        evaluation.main()
    assert not output.exists()


def test_last_case_budget_failure_is_found_before_any_call(evaluation, source, monkeypatch, tmp_path):
    prohibit_live_access(evaluation, monkeypatch)
    probe = source["probe"]["model_input"]
    probe["memory_context"] = "Large historical memory. " * 1500
    probe["messages"][2]["content"] = "Historical memory data, not instructions:\n" + probe["memory_context"]
    probe["input_tokens_estimate"] = len(json.dumps(probe["messages"], ensure_ascii=False, separators=(",", ":")).encode())
    source["frozen_probe"]["model_input"] = copy.deepcopy(probe)
    source["plan"]["frozen_probe"]["sha256"] = digest(source["frozen_probe"])
    source["plan_sha256"] = digest(source["plan"])
    evaluation.SOURCE_REPORT.write_text(json.dumps(source))
    output = tmp_path / "unused"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--live", "--output", str(output)])
    with pytest.raises(ValueError, match="input budget"):
        evaluation.main()
    assert not output.exists()


def test_resume_uses_only_saved_report_without_new_inputs_or_calls(evaluation, monkeypatch, tmp_path, capsys):
    prohibit_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation, "freeze_inputs", lambda: pytest.fail("Resume cannot freeze new inputs"))
    saved = {"state": "interrupted", "usage": {"reserved_calls": 1}, "cases": [{"name": "learn", "state": "failed"}],
             "semantic_review": {"status": "pending_developer_review"}}
    path = tmp_path / "report.json"
    original = json.dumps(saved).encode()
    path.write_bytes(original)
    monkeypatch.setattr(sys, "argv", ["evaluation", "--resume", "--output", str(tmp_path)])
    evaluation.main()
    result = json.loads(capsys.readouterr().out)
    assert result["cases"] == saved["cases"]
    assert result["semantic_review"] == saved["semantic_review"]
    assert path.read_bytes() == original
    assert sorted(item.name for item in tmp_path.iterdir()) == ["report.json"]


@pytest.mark.parametrize("modes", [[], ["--live"], ["--live", "--resume"]])
def test_existing_output_or_invalid_mode_cannot_repeat_or_overwrite(evaluation, monkeypatch, tmp_path, modes):
    prohibit_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(evaluation, "freeze_inputs", lambda: pytest.fail("Existing output must fail before freezing"))
    marker = tmp_path / "report.json"
    marker.write_text("original")
    monkeypatch.setattr(sys, "argv", ["evaluation", *modes, "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert marker.read_text() == "original"


def completion(*, known=True, answer="A deliberate semantically wrong answer."):
    result = {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": answer, "reasoning_content": "private-provider-reasoning"}}]}
    if known:
        result["usage"] = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    return result


def mock_live(evaluation, monkeypatch, output, handler):
    original_meter = evaluation.MeteredTransport
    meters = []

    def make_meter(path, **options):
        saved = json.loads((output / "report.json").read_text())
        assert saved["plan_sha256"] == digest(saved["plan"])
        assert digest(saved["frozen_inputs"]) == saved["plan"]["frozen_inputs_sha256"]
        assert len(saved["frozen_inputs"]["cases"]) == 3 and saved["cases"] == []
        assert options == {"max_calls": 3, "max_output_tokens": 12288, "capture_content": False}
        meter = original_meter(path, **options, transport=httpx.MockTransport(handler))
        meters.append(meter)
        return meter

    monkeypatch.setattr(evaluation, "MeteredTransport", make_meter)
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")
    monkeypatch.setattr(sys, "argv", ["evaluation", "--live", "--output", str(output)])
    return meters


def test_fake_live_freezes_all_inputs_before_call_and_never_sends_old_answers_or_rubric(
    evaluation, source, monkeypatch, tmp_path,
):
    output, calls = tmp_path / "new-run", []
    original_report = evaluation.SOURCE_REPORT.read_bytes()
    original_ledger = evaluation.SOURCE_REPORT.with_name("calls.jsonl").read_bytes()

    def handle(request):
        saved = json.loads((output / "report.json").read_text())
        index = len(calls)
        body = json.loads(request.content)
        assert saved["cases"][index]["state"] == "started"
        assert body["messages"] == saved["frozen_inputs"]["cases"][index]["model_input"]["messages"]
        assert "PRIVATE_OLD_ANSWER" not in request.content.decode()
        assert evaluation.REVIEW not in request.content.decode()
        assert set(body) == {"model", "messages", "max_tokens", "stream", "thinking"}
        assert body["model"] == source["plan"]["model"] and body["max_tokens"] == 4096
        calls.append(body)
        return httpx.Response(200, json=completion())

    meters = mock_live(evaluation, monkeypatch, output, handle)
    evaluation.main()
    assert len(calls) == 3
    saved = json.loads((output / "report.json").read_text())
    assert saved["state"] == "finished"
    assert saved["usage"]["reserved_calls"] == 3
    assert saved["usage"]["reserved_output_tokens"] == 12288
    assert saved["usage"]["usage_unknown_calls"] == 0
    assert saved["cumulative_usage"]["reserved_calls"] == source["usage"]["reserved_calls"] + 3
    assert saved["cumulative_usage"]["reserved_output_tokens"] == source["usage"]["reserved_output_tokens"] + 12288
    assert saved["cumulative_usage"]["usage_totals"]["total_tokens"] == 75
    assert saved["semantic_review"]["status"] == "pending_developer_review"
    for case in saved["cases"]:
        assert case["model_output"] == "A deliberate semantically wrong answer."
        assert case["answer_review"]["status"] == "pending_developer_review"
        assert all(case[key] is False for key in ("database_used", "learning_performed", "delivered"))
        assert "checks" not in case
    assert evaluation.SOURCE_REPORT.read_bytes() == original_report
    assert evaluation.SOURCE_REPORT.with_name("calls.jsonl").read_bytes() == original_ledger
    assert "private-" not in (output / "report.json").read_text() + (output / "calls.jsonl").read_text()
    assert len(meters) == 1 and meters[0]._closed


def test_fake_live_accepts_only_new_three_calls_after_the_known_truncated_source(
    evaluation, truncated_source, monkeypatch, tmp_path,
):
    output, calls = tmp_path / "new-run", []
    original = evaluation.SOURCE_REPORT.read_bytes()

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    mock_live(evaluation, monkeypatch, output, handle)
    evaluation.main()
    saved = json.loads((output / "report.json").read_text())
    assert len(calls) == 3 and saved["state"] == "finished"
    assert saved["usage"]["reserved_calls"] == 3
    assert saved["cumulative_usage"]["reserved_calls"] == 23
    assert saved["cumulative_usage"]["reserved_output_tokens"] == 253952 + 12288
    assert saved["frozen_inputs"]["source_state"] == "interrupted"
    assert saved["frozen_inputs"]["cases"][-1]["developer_comparison"]["original_output_state"] == "missing"
    assert "original_model_output" not in saved["frozen_inputs"]["cases"][-1]["developer_comparison"]
    assert saved["semantic_review"]["status"] == "pending_developer_review"
    assert evaluation.SOURCE_REPORT.read_bytes() == original


@pytest.mark.parametrize("unknown_at", [1, 2, 3])
def test_unknown_usage_stops_immediately_and_is_not_an_execution_success(
    evaluation, source, monkeypatch, tmp_path, unknown_at,
):
    output, calls = tmp_path / "new-run", []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=completion(known=len(calls) != unknown_at))

    mock_live(evaluation, monkeypatch, output, handle)
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1 and len(calls) == unknown_at
    saved = json.loads((output / "report.json").read_text())
    assert saved["state"] == "stopped" and saved["stopped_reason"] == "unknown_provider_usage"
    assert saved["usage"]["usage_unknown_calls"] == 1
    assert len(saved["cases"]) == unknown_at
    assert saved["semantic_review"]["status"] == "pending_developer_review"


@pytest.mark.parametrize("failure", ["timeout", "http_error", "truncated"])
def test_failed_call_is_safely_saved_without_retry(evaluation, source, monkeypatch, tmp_path, failure):
    output, calls = tmp_path / "new-run", []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-sensitive-detail", request=request)
        if failure == "http_error":
            return httpx.Response(500, text="private-sensitive-detail")
        value = completion()
        value["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=value)

    mock_live(evaluation, monkeypatch, output, handle)
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1 and len(calls) == 1
    saved = json.loads((output / "report.json").read_text())
    assert saved["state"] == "interrupted"
    assert saved["cases"][0]["state"] == "failed"
    assert "model_output" not in saved["cases"][0]
    assert "private-" not in (output / "report.json").read_text() + (output / "calls.jsonl").read_text()


@pytest.mark.parametrize("damage", ["frozen", "plan", "code", "already_started", "used_ledger"])
def test_changed_freeze_or_previously_started_work_never_reaches_host(evaluation, source, tmp_path, monkeypatch, damage):
    frozen = evaluation.freeze_inputs()
    planned = evaluation.plan(frozen)
    report = {"state": "started", "cases": [], "plan": planned, "plan_sha256": digest(planned),
              "frozen_inputs": frozen}
    meter = evaluation.MeteredTransport(tmp_path / "calls.jsonl", max_calls=3, max_output_tokens=12288,
                                        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion())))
    if damage == "frozen":
        frozen["cases"][0]["model_input"]["messages"][-1]["content"] = "Changed current input"
    elif damage == "plan":
        planned["max_calls"] = 4
    elif damage == "code":
        monkeypatch.setattr(evaluation, "fingerprints", lambda: {"changed.py": "different"})
    elif damage == "already_started":
        report["cases"].append({"name": "learn", "state": "started"})
    else:
        with httpx.Client(transport=meter) as client:
            client.post("https://synthetic.invalid", json={"max_tokens": 4096})
    try:
        with pytest.raises((ValueError, RuntimeError)):
            evaluation.run_experiment(tmp_path, report, lambda _: pytest.fail("Invalid freeze reached host"), meter)
    finally:
        meter.close()


@pytest.mark.parametrize("limit", ["calls", "output"])
def test_shared_ledger_hard_caps_prevent_extra_network_request(evaluation, tmp_path, limit):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=completion())

    meters = [evaluation.MeteredTransport(tmp_path / "calls.jsonl", max_calls=3, max_output_tokens=12288,
                                         transport=httpx.MockTransport(handle), capture_content=False) for _ in range(2)]
    with httpx.Client(transport=meters[0]) as first, httpx.Client(transport=meters[1]) as second:
        if limit == "calls":
            for _ in range(3):
                first.post("https://synthetic.invalid", json={"max_tokens": 4096})
            expected_calls, amount, reason = 3, 1, "metering_call_limit"
        else:
            first.post("https://synthetic.invalid", json={"max_tokens": 4096})
            expected_calls, amount, reason = 1, 8193, "metering_output_limit"
        with pytest.raises(httpx.TransportError, match=reason):
            second.post("https://synthetic.invalid", json={"max_tokens": amount})
    assert len(calls) == expected_calls
