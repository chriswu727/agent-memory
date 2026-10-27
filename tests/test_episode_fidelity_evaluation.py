import copy
import importlib
import json
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from membrain.helpers import digest
from membrain.semantic_provider import ChatCompletionsSemanticProvider


@pytest.fixture(scope="module")
def evaluation():
    sys.path.insert(0, str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_episode_fidelity")


@pytest.fixture(scope="module")
def original_inputs(evaluation):
    return evaluation.freeze_inputs()


@pytest.fixture
def frozen(evaluation, original_inputs, monkeypatch):
    value = copy.deepcopy(original_inputs)
    monkeypatch.setattr(evaluation, "freeze_inputs", lambda: copy.deepcopy(value))
    return value


def install_provider(evaluation, monkeypatch, output, *, failure=None, all_supported=False,
                     unknown_modality=False, after_response=None):
    requests = []

    def handle(request):
        report = json.loads((output / "report.json").read_bytes())
        assert report["calls"][-1]["state"] == "reserved"
        assert json.loads((output / "plan.json").read_bytes()) == report["plan"]
        assert json.loads((output / "inputs.json").read_bytes()) == report["frozen_inputs"]
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        case = report["frozen_inputs"]["cases"][index // 2]
        version = evaluation.VERSIONS[index % 2]
        saved = case["requests"][version]
        assert body["messages"] == saved["messages"]
        assert body["model"] == evaluation.MODEL
        assert body["thinking"] == {"type": "enabled"}
        assert body["max_tokens"] == evaluation.OUTPUT_PER_CALL
        assert body["response_format"] == {"type": "json_object"}
        assert "developer_expectation" not in str(body)
        data = json.loads(body["messages"][-1]["content"])
        item, = data["proposals"]
        expected = case["developer_expectation"]["by_contract"][version]
        verdict = "supports" if all_supported or expected == "accept" else "unknown"
        assessment = {"proposal_id": item["proposal"]["proposal_id"], "proposal_verdict": verdict,
                      "link_verdict": None,
                      "relations": [{"evidence_index": entry["evidence_index"], "verdict": "supports"}
                                    for entry in item["evidence"]]}
        if version == evaluation.EPISODE_FIDELITY_INPUT_VERSION:
            assessment["organization_fidelity"] = dict.fromkeys(evaluation.FACETS, verdict)
            if unknown_modality:
                assessment["organization_fidelity"]["modality"] = "unknown"
        content = json.dumps({"assessments": [assessment]})
        if failure == "invalid_json":
            content = "not valid JSON"
        elif failure == "incomplete":
            content = '{"assessments":[]}'
        elif failure == "extra_field":
            assessment["unrequested_reasoning"] = "A schema violation"
            content = json.dumps({"assessments": [assessment]})
        if after_response:
            after_response(len(requests), report)
        if failure == "http":
            return httpx.Response(500, text="private provider error details")
        result = {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": content, "reasoning_content": "hidden reasoning must not be saved"}}]}
        if failure != "usage_absent":
            result["usage"] = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        if failure == "usage_partial":
            result["usage"].pop("prompt_tokens")
        return httpx.Response(200, json=result)

    def factory(*args, transport, **kwargs):
        assert (output / "plan.json").exists() and (output / "inputs.json").exists()
        transport._transport.close()
        transport._transport = httpx.MockTransport(handle)
        return ChatCompletionsSemanticProvider(*args, transport=transport, **kwargs)

    monkeypatch.setattr(evaluation, "load_dotenv", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", factory)
    return requests


def test_plan_preserves_exact_recorded_sources_and_proposals_without_oracle(evaluation, original_inputs, monkeypatch):
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("Plan must not read credentials"))
    monkeypatch.setattr(evaluation, "MeteredTransport", lambda *a, **k: pytest.fail("Plan must not open HTTP"))
    frozen = evaluation.freeze_inputs()
    assert frozen == original_inputs == evaluation.freeze_inputs()
    with tarfile.open(evaluation.LONG_ARCHIVE) as archive:
        source = json.load(archive.extractfile(evaluation.LONG_MEMBER))
    original_call = next(call for call in source["stages"][0]["cognitive_calls"]
                         if call["request"]["phase"] == "proposal")
    recorded = frozen["cases"][0]
    assert recorded["proposal"] == original_call["response"]["episode_actions"][0]
    assert recorded["sources"] == json.loads(original_call["request"]["messages"][-1]["content"])["sources"]
    smoke = json.loads(evaluation.SMOKE_REPORT.read_bytes())["experiment"]
    original_call = next(call for call in smoke["stages"][0]["cognitive_calls"]
                         if call["request"]["phase"] == "proposal" and call["role"] == "consolidate")
    recorded = frozen["cases"][2]
    assert recorded["proposal"] == original_call["response"]["episode_actions"][0]
    assert recorded["sources"] == json.loads(original_call["request"]["messages"][-1]["content"])["sources"]
    planned = evaluation.plan(frozen)
    assert planned["max_calls"] == 16 and planned["max_reserved_output_tokens"] == 131072
    assert planned["frozen_inputs_sha256"] == digest(frozen)
    assert planned["semantic_review"]["state"] == "pending_developer_review"
    assert len(frozen["cases"]) == 8
    for case in frozen["cases"]:
        old, new = [case["requests"][version] for version in evaluation.VERSIONS]
        assert old["phase"] == new["phase"] == "verification"
        assert old["messages"][-1] == new["messages"][-1]
        assert "organization_fidelity" not in old["response_schema"]["properties"]["assessments"]["items"]["properties"]
        assert "organization_fidelity" in new["response_schema"]["properties"]["assessments"]["items"]["properties"]
        for request in (old, new):
            entry, = json.loads(request["messages"][-1]["content"])["proposals"]
            assert entry["proposal"] == case["proposal"]
            assert [item["source"] for item in entry["evidence"]] == case["sources"]
            assert case["developer_expectation"]["rubric"] not in str(request)
            assert "developer_expectation" not in str(request)
            assert request["timeout_seconds"] == 240
    assert {case["developer_expectation"]["by_contract"][evaluation.VERSIONS[0]]
            for case in frozen["cases"]} == {"accept", "reject"}


def test_real_provider_wiring_saves_sixteen_bound_requests_and_known_usage(evaluation, frozen, tmp_path, monkeypatch):
    output = tmp_path / "diagnostic"
    requests = install_provider(evaluation, monkeypatch, output)
    report = evaluation.run_live(output)
    assert report["state"] == "finished" and report["frozen_integrity_verified"]
    assert len(requests) == len(report["calls"]) == 16
    assert report["usage"] == {"reserved_calls": 16, "completed_calls": 16, "reserved_output_tokens": 131072,
                                "usage_confirmed_calls": 16, "usage_unknown_calls": 0,
                                "usage_totals": {"prompt_tokens": 160, "completion_tokens": 32, "total_tokens": 192}}
    assert len(report["call_ledger"]) == 32
    for call in report["calls"]:
        case = next(case for case in frozen["cases"] if case["case_id"] == call["case_id"])
        assert call["gate_outcome"] == case["developer_expectation"]["by_contract"][call["contract"]]
        assert call["request_sha256"] == digest(case["requests"][call["contract"]])
        assert call["semantic_review"] == "pending_developer_review"
    serialized = json.dumps(report)
    assert "hidden reasoning must not be saved" not in serialized and "synthetic-test-key" not in serialized
    assert not any(report[key] for key in ("published", "database_used", "host_generated", "delivered"))
    assert evaluation.resume_report(output) == report
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("Existing attempts cannot load credentials"))
    with pytest.raises(ValueError, match="already exists"):
        evaluation.run_live(output)


def test_false_acceptances_are_not_hidden_or_relabelled_as_semantic_success(evaluation, frozen, tmp_path, monkeypatch):
    output = tmp_path / "diagnostic"
    install_provider(evaluation, monkeypatch, output, all_supported=True)
    report = evaluation.run_live(output)
    assert report["state"] == "finished"
    assert all(call["gate_outcome"] == "accept" for call in report["calls"])
    assert frozen["cases"][0]["developer_expectation"]["by_contract"][evaluation.VERSIONS[0]] == "reject"
    assert report["calls"][0]["expectation_comparison"] == {
        "expected": "reject", "matches": False, "independent_semantic_review_required": True}
    assert report["semantic_review"]["state"] == "pending_developer_review"
    assert "semantic_passed" not in report and "all_checks_passed" not in report


def test_new_gate_rejects_unknown_facet_even_with_supported_whole_proposal(evaluation, frozen, tmp_path, monkeypatch):
    output = tmp_path / "diagnostic"
    install_provider(evaluation, monkeypatch, output, all_supported=True, unknown_modality=True)
    report = evaluation.run_live(output)
    assert report["state"] == "finished"
    assert all(call["gate_outcome"] == ("accept" if index % 2 == 0 else "reject")
               for index, call in enumerate(report["calls"]))


@pytest.mark.parametrize("failure", ["invalid_json", "incomplete", "extra_field", "http",
                                     "usage_absent", "usage_partial"])
def test_uncertain_or_invalid_first_response_stops_without_retry(evaluation, frozen, tmp_path, monkeypatch, failure):
    output = tmp_path / "diagnostic"
    requests = install_provider(evaluation, monkeypatch, output, failure=failure)
    report = evaluation.run_live(output)
    assert report["state"] == "interrupted" and len(requests) == len(report["calls"]) == 1
    assert report["usage"]["reserved_calls"] == 1
    assert report["calls"][0]["state"] != "finished"
    assert report["frozen_integrity_verified"]
    assert "private provider error details" not in json.dumps(report)
    assert report["semantic_review"]["state"] == "pending_developer_review"


@pytest.mark.parametrize("mutation", ["plan_file", "inputs_file", "code", "sources", "request"])
def test_drift_stops_after_first_paid_boundary(evaluation, frozen, tmp_path, monkeypatch, mutation):
    output = tmp_path / "diagnostic"

    def change(count, report):
        if count != 1:
            return
        if mutation in {"plan_file", "inputs_file"}:
            (output / ("plan.json" if mutation == "plan_file" else "inputs.json")).write_text("{}")
        elif mutation == "code":
            monkeypatch.setattr(evaluation, "fingerprints", lambda: {})
        elif mutation == "sources":
            original = evaluation.sha
            monkeypatch.setattr(evaluation, "sha", lambda raw: "0" * 64 if len(raw) > 100000 else original(raw))
        else:
            original = evaluation.replay

            def changed(*args, **kwargs):
                request, result = original(*args, **kwargs)
                request["messages"][0]["content"] += " changed"
                return request, result

            monkeypatch.setattr(evaluation, "replay", changed)

    requests = install_provider(evaluation, monkeypatch, output, after_response=change)
    report = evaluation.run_live(output)
    assert report["state"] == "interrupted" and len(requests) == 1
    assert report["usage"]["reserved_calls"] == 1
    assert report["frozen_integrity_verified"] is (mutation == "request")


def test_reentry_with_persisted_reservation_never_repeats_provider(evaluation, frozen, tmp_path, monkeypatch):
    output = tmp_path / "diagnostic"
    install_provider(evaluation, monkeypatch, output)
    report = evaluation.run_live(output)
    report.update(state="started", calls=[])
    with evaluation.MeteredTransport(output / "calls.jsonl", transport=httpx.MockTransport(
            lambda _: pytest.fail("Reentry must not call provider"))) as meter:
        with pytest.raises(ValueError, match="repeated"):
            evaluation.run_experiment(output, report, lambda _: pytest.fail("No provider"), meter)


def test_cli_plan_resume_and_missing_mode_never_load_env_or_open_provider(evaluation, frozen, tmp_path,
                                                                       monkeypatch, capsys):
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("Offline modes cannot read credentials"))
    monkeypatch.setattr(evaluation, "MeteredTransport", lambda *a, **k: pytest.fail("Offline modes cannot open HTTP"))
    monkeypatch.setattr(sys, "argv", ["evaluate_episode_fidelity", "--plan"])
    evaluation.main()
    assert json.loads(capsys.readouterr().out)["plan"]["max_calls"] == 16
    report = {"state": "interrupted", "semantic_review": {"state": "pending_developer_review"}}
    (tmp_path / "report.json").write_text(json.dumps(report))
    monkeypatch.setattr(sys, "argv", ["evaluate_episode_fidelity", "--resume", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["state"] == "interrupted"
    assert evaluation.resume_report(tmp_path) == report
    monkeypatch.setattr(sys, "argv", ["evaluate_episode_fidelity"])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
