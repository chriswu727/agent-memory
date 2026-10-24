import copy
import importlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from test_semantic import claim, event, proposals

from agent_memory.helpers import digest
from agent_memory.semantic import SemanticResult
from agent_memory.semantic_provider import ChatCompletionsSemanticProvider


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_temporal_repair")


def baseline_files(evaluation, directory):
    source = event(evaluation.FIXTURES[0]["text"])
    original = claim(source, statement="The user prefers walking before studying from the explicit start onward.",
                     claim_kind="reported_preference")
    original.pop("valid_during", None)
    stored = {**copy.deepcopy(original), "epistemic_status": "derived_inference",
              "valid_during": {"kind": "unknown"}}
    stages = [{"name": fixture["name"], "fixture": copy.deepcopy(fixture), "state": "finished"}
              for fixture in evaluation.FIXTURES]
    stages[0].update(
        brain_id=source["scope"]["brain_id"], sources={"input_events": [source]},
        runtime={"input_event_refs": [{"event_id": source["event_id"], "revision": source["revision"]}]},
        cognitive_calls=[{"role": "consolidate", "phase": "proposal", "schema_valid": True,
                          "response": proposals(original)}],
        after={"documents": {"memories": [stored]}},
    )
    ledger = []
    for index in range(18):
        ledger += [
            {"event": "reserved", "request_id": str(index),
             "reserved_output_tokens": 16384 if index < 13 else 4096,
             "request_sha256": "a" * 64, "request_bytes": 10},
            {"event": "completed", "request_id": str(index), "usage_confirmed": True,
             "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}},
        ]
    ledger_path = directory / "calls.jsonl"
    ledger_path.write_text("".join(json.dumps(item) + "\n" for item in ledger))
    meter = evaluation.MeteredTransport(ledger_path, transport=httpx.MockTransport(lambda _: pytest.fail("No call")))
    usage = meter.summary()
    meter.close()
    baseline = {"version": 1, "state": "finished", "plan": evaluation.plan(),
                "plan_sha256": digest(evaluation.plan()), "stages": stages, "usage": usage}
    (directory / "report.json").write_text(json.dumps(baseline, ensure_ascii=False))
    return baseline, source, ledger


def accepted(evaluation, source):
    proposed = claim(source, claim_kind="reported_preference", conditions={"op": "always"},
                     valid_during={"kind": "interval", "start": {"kind": "instant", "at": evaluation.EXPECTED_START},
                                   "end": {"kind": "unbounded"}})
    final = {**copy.deepcopy(proposed), "epistemic_status": "derived_inference", "verification": {
        "state": "supported_inference", "method": "semantic_model", "not_a_truth_certificate": True}}
    result = SemanticResult("completed", claims=(final,), model_calls=2)
    assessment = {"proposal_id": proposed["proposal_id"], "proposal_verdict": "supports", "link_verdict": None,
                  "relations": [{"evidence_index": 0, "verdict": "supports"}]}
    return result, {"proposal": proposals(proposed), "verification": {"assessments": [assessment]}}, {
        "proposals": [{"group": "claims", "proposal": copy.deepcopy(proposed)}]}


def test_freeze_uses_only_exact_original_user_source_and_preserves_old_failure(evaluation, tmp_path):
    baseline, source, _ = baseline_files(evaluation, tmp_path)
    original = (tmp_path / "report.json").read_bytes()
    _, frozen = evaluation.load_baseline(tmp_path)
    batch = evaluation.batch_from_saved(frozen)
    assert batch.sources == (source,) and batch.scope == source["scope"]
    assert not batch.existing_assertions and not batch.working_state and not batch.episodes
    assert "valid_during" not in frozen["baseline_target"]["proposal"]
    assert frozen["baseline_target"]["stored_memory"]["valid_during"] == {"kind": "unknown"}
    assert (tmp_path / "report.json").read_bytes() == original
    assert frozen["baseline_plan_sha256"] == baseline["plan_sha256"]


@pytest.mark.parametrize("fault", [
    "unfinished", "version", "model", "fixture", "missing_stage", "stage_unfinished",
    "source_text", "source_hash", "source_version", "runtime_ref", "no_old_unknown", "no_missing_field",
])
def test_changed_baseline_is_rejected_before_environment_or_provider(evaluation, tmp_path, monkeypatch, fault):
    baseline, source, _ = baseline_files(evaluation, tmp_path)
    stage = baseline["stages"][0]
    if fault == "unfinished":
        baseline["state"] = "started"
    elif fault == "version":
        baseline["version"] = 2
    elif fault == "model":
        baseline["plan"]["model"] = "other-model"
    elif fault == "fixture":
        baseline["stages"][4]["fixture"]["text"] += " extra"
    elif fault == "missing_stage":
        baseline["stages"].pop()
    elif fault == "stage_unfinished":
        baseline["stages"][1]["state"] = "started"
    elif fault == "source_text":
        source["observed"]["content"]["text"] += "extra"
        source["content_sha256"] = digest(source["observed"]["content"])
    elif fault == "source_hash":
        source["content_sha256"] = "0" * 64
    elif fault == "source_version":
        source["revision"] = 2
    elif fault == "runtime_ref":
        stage["runtime"]["input_event_refs"][0]["event_id"] = "other"
    elif fault == "no_old_unknown":
        stage["after"]["documents"]["memories"][0]["valid_during"] = {"kind": "interval"}
    else:
        stage["cognitive_calls"][0]["response"]["claims"][0]["valid_during"] = {"kind": "unknown"}
    (tmp_path / "report.json").write_text(json.dumps(baseline))
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No environment access"))
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", lambda *a, **k: pytest.fail("No provider"))
    with pytest.raises(ValueError):
        evaluation.run_live(tmp_path)
    assert not (tmp_path / evaluation.FOLLOWUP_NAME).exists()


@pytest.mark.parametrize("fault", ["unknown_usage", "extra_reservation", "ledger_total", "reported_usage", "missing_ledger"])
def test_changed_or_unconfirmed_ledger_blocks_before_environment(evaluation, tmp_path, monkeypatch, fault):
    baseline, _, ledger = baseline_files(evaluation, tmp_path)
    if fault == "unknown_usage":
        ledger[-1]["usage_confirmed"] = False
    elif fault == "extra_reservation":
        ledger.append({"event": "reserved", "request_id": "extra", "reserved_output_tokens": 8192})
    elif fault == "ledger_total":
        ledger[0]["reserved_output_tokens"] = 16385
    elif fault == "reported_usage":
        baseline["usage"]["usage_totals"]["total_tokens"] += 1
        (tmp_path / "report.json").write_text(json.dumps(baseline))
    ledger_path = tmp_path / "calls.jsonl"
    ledger_path.write_text("".join(json.dumps(item) + "\n" for item in ledger))
    if fault == "missing_ledger":
        ledger_path.unlink()
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No environment access"))
    with pytest.raises(ValueError):
        evaluation.run_live(tmp_path)
    assert not (tmp_path / evaluation.FOLLOWUP_NAME).exists()


@pytest.mark.parametrize("state", ["started", "interrupted", "finished"])
def test_existing_attempt_is_never_repeated(evaluation, tmp_path, monkeypatch, state):
    path = tmp_path / evaluation.FOLLOWUP_NAME
    path.write_text(json.dumps({"state": state}))
    monkeypatch.setattr(evaluation, "load_baseline", lambda *a: pytest.fail("Do not inspect another run"))
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No environment access"))
    with pytest.raises(ValueError):
        evaluation.run_live(tmp_path)
    assert json.loads(path.read_text())["state"] == state


def test_exclusive_report_creation_will_not_overwrite_racing_attempt(evaluation, tmp_path):
    path = tmp_path / evaluation.FOLLOWUP_NAME
    path.write_text("original")
    with pytest.raises(FileExistsError):
        evaluation._exclusive_save(path, {"new": True})
    assert path.read_text() == "original"


def test_required_validity_schema_is_checked_before_environment_or_attempt(evaluation, tmp_path, monkeypatch):
    baseline_files(evaluation, tmp_path)
    schema = copy.deepcopy(evaluation.PROPOSAL_SCHEMA)
    schema["properties"]["claims"]["items"]["required"].remove("valid_during")
    monkeypatch.setattr(evaluation, "PROPOSAL_SCHEMA", schema)
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No environment access"))
    with pytest.raises(ValueError, match="Typed validity"):
        evaluation.run_live(tmp_path)
    assert not (tmp_path / evaluation.FOLLOWUP_NAME).exists()


def test_frozen_source_hash_cannot_be_silently_changed(evaluation, tmp_path):
    baseline_files(evaluation, tmp_path)
    _, frozen = evaluation.load_baseline(tmp_path)
    frozen["batch"]["sources"][0]["revision"] = 2
    with pytest.raises(ValueError, match="modified"):
        evaluation.batch_from_saved(frozen)


def test_resume_is_read_only_even_for_interrupted_attempt(evaluation, tmp_path, monkeypatch):
    report = {"state": "interrupted", "usage_after": {"reserved_calls": 19}}
    path = tmp_path / evaluation.FOLLOWUP_NAME
    path.write_text(json.dumps(report))
    monkeypatch.setattr(evaluation, "load_baseline", lambda *a: pytest.fail("No baseline access"))
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No environment access"))
    monkeypatch.setattr(evaluation, "MeteredTransport", lambda *a, **k: pytest.fail("No ledger access"))
    assert evaluation.resume_report(tmp_path) == report


def test_typed_interval_is_checked_both_sides_of_boundary_but_semantic_review_remains_pending(evaluation, tmp_path):
    _, source, _ = baseline_files(evaluation, tmp_path)
    _, frozen = evaluation.load_baseline(tmp_path)
    result, responses, verification = accepted(evaluation, source)
    review = evaluation.temporal_review(evaluation.batch_from_saved(frozen), result, responses, verification)
    assert all(review["checks"].values())
    assert [p["value"] for p in review["accepted_temporal_claims"][0]["temporal_probes"]] == ["false", "true", "true"]
    assert review["developer_review"] == {"state": "pending"}
    assert "requires_developer_review" in review["semantic_quality"]


@pytest.mark.parametrize("fault", [
    "unknown_interval", "wrong_start", "unknown_end", "unchanged_legacy_time", "source_revision",
    "source_hash", "missing_source_segment", "whole_unknown", "premise_unknown", "duplicate_verdict",
    "text_changed", "verifier_missing_time", "condition", "counterexample", "unverified", "no_accepted_claim",
])
def test_temporal_oracle_rejects_incomplete_or_changed_support(evaluation, tmp_path, fault):
    _, source, _ = baseline_files(evaluation, tmp_path)
    _, frozen = evaluation.load_baseline(tmp_path)
    result, responses, verification = accepted(evaluation, source)
    final = result.claims[0]
    verdict = responses["verification"]["assessments"][0]
    if fault == "unknown_interval":
        final["valid_during"] = {"kind": "unknown"}
    elif fault == "wrong_start":
        final["valid_during"]["start"]["at"] = "2026-10-21T00:00:00Z"
    elif fault == "unknown_end":
        final["valid_during"]["end"] = {"kind": "unknown"}
    elif fault == "unchanged_legacy_time":
        final["valid_time"]["start"] = evaluation.EXPECTED_START
        final.pop("valid_during")
    elif fault == "source_revision":
        final["evidence_refs"][0]["event_revision"] = 2
    elif fault == "source_hash":
        final["evidence_refs"][0]["source_content_sha256"] = "0" * 64
    elif fault == "missing_source_segment":
        final["evidence_refs"][0]["span"]["end"] = 50
    elif fault == "whole_unknown":
        verdict["proposal_verdict"] = "unknown"
    elif fault == "premise_unknown":
        verdict["relations"][0]["verdict"] = "unknown"
    elif fault == "duplicate_verdict":
        verdict["relations"].append(copy.deepcopy(verdict["relations"][0]))
    elif fault == "text_changed":
        final["statement"] += " Extra invented detail."
    elif fault == "verifier_missing_time":
        verification["proposals"][0]["proposal"].pop("valid_during")
    elif fault == "condition":
        final["conditions"] = {"op": "unresolved", "label": "unknown-context"}
    elif fault == "counterexample":
        final["counterexamples"] = [final["evidence_refs"][0]]
    elif fault == "unverified":
        final["verification"]["state"] = "unknown"
    else:
        result = replace(result, claims=())
    review = evaluation.temporal_review(evaluation.batch_from_saved(frozen), result, responses, verification)
    assert not review["checks"]["verified_typed_preference_accepted"]


def test_partial_processor_result_is_preserved_even_when_temporal_subset_passes(evaluation, tmp_path):
    _, source, _ = baseline_files(evaluation, tmp_path)
    _, frozen = evaluation.load_baseline(tmp_path)
    result, responses, verification = accepted(evaluation, source)
    result = replace(result, status="partial", rejected=({"proposal_id": "other", "reason": "invalid_proposal"},))
    review = evaluation.temporal_review(evaluation.batch_from_saved(frozen), result, responses, verification)
    assert review["checks"]["verified_typed_preference_accepted"]
    assert review["processor_status"] == "partial" and review["rejected"] == list(result.rejected)


def install_mock_provider(evaluation, monkeypatch, tmp_path, proposed, *, invalid=False, status=200):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["max_tokens"] == 8192
        if status != 200:
            return httpx.Response(status, content=b"private-provider-error-body")
        payload = json.loads(body["messages"][-1]["content"])
        if "sources" in payload:
            content = "private-invalid-response" if invalid else json.dumps(proposed)
        else:
            content = json.dumps({"assessments": [{
                "proposal_id": entry["proposal"]["proposal_id"], "proposal_verdict": "supports", "link_verdict": None,
                "relations": [{"evidence_index": item["evidence_index"], "verdict": "supports"}
                              for item in entry["evidence"]],
            } for entry in payload["proposals"]]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": content, "reasoning_content": "private-reasoning"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}})

    def provider(*args, transport, **kwargs):
        frozen = json.loads((tmp_path / evaluation.FOLLOWUP_NAME).read_text())
        assert frozen["state"] == "started" and frozen["batch"]["sources"]
        transport._transport.close()
        transport._transport = httpx.MockTransport(handle)
        return ChatCompletionsSemanticProvider(*args, transport=transport, **kwargs)

    monkeypatch.setattr(evaluation, "load_dotenv", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-key")
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", provider)
    return requests


def test_two_mocked_calls_use_shared_ledger_and_leave_original_report_and_records_unchanged(evaluation, tmp_path, monkeypatch):
    _, source, original_records = baseline_files(evaluation, tmp_path)
    original_bytes = (tmp_path / "report.json").read_bytes()
    _, responses, _ = accepted(evaluation, source)
    calls = install_mock_provider(evaluation, monkeypatch, tmp_path, responses["proposal"])
    report = evaluation.run_live(tmp_path)
    assert report["state"] == "finished" and report["result"]["status"] == "completed"
    assert report["review"]["checks"]["verified_typed_preference_accepted"]
    assert report["usage_before"]["reserved_calls"] == 18 and report["usage_after"]["reserved_calls"] == 20
    assert report["usage_after"]["reserved_output_tokens"] == 249856
    assert report["call_ledger"][:36] == original_records and all(report["integrity_checks"].values())
    assert (tmp_path / "report.json").read_bytes() == original_bytes
    first = json.loads(calls[0]["messages"][-1]["content"])
    assert first["sources"] == [source] and not first["existing_assertions"]
    assert "expected_interval" not in first and "baseline_target" not in first
    assert not any("2026-10-19" in json.dumps(message) for call in calls for message in call["messages"])
    saved = (tmp_path / evaluation.FOLLOWUP_NAME).read_text()
    assert all(secret not in saved for secret in ("private-key", "private-reasoning", "private-invalid-response"))
    assert len(calls) == 2
    with pytest.raises(ValueError):
        evaluation.run_live(tmp_path)
    assert len(calls) == 2


def test_invalid_model_output_is_not_persisted_or_retried(evaluation, tmp_path, monkeypatch):
    baseline_files(evaluation, tmp_path)
    calls = install_mock_provider(evaluation, monkeypatch, tmp_path, {}, invalid=True)
    report = evaluation.run_live(tmp_path)
    assert report["state"] == "finished" and report["result"]["status"] == "invalid_response"
    assert report["responses"] == {} and report["invalid_response_phases"] == ["proposal"]
    assert not report["review"]["checks"]["verified_typed_preference_accepted"]
    assert len(calls) == 1 and report["usage_after"]["reserved_calls"] == 19
    saved = (tmp_path / evaluation.FOLLOWUP_NAME).read_text()
    assert all(secret not in saved for secret in ("private-key", "private-reasoning", "private-invalid-response"))


def test_missing_local_key_leaves_an_exclusive_interrupted_report_without_spending(evaluation, tmp_path, monkeypatch):
    baseline_files(evaluation, tmp_path)
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", lambda *a, **k: pytest.fail("No provider"))
    report = evaluation.run_live(tmp_path)
    assert report["state"] == "interrupted" and report["usage_after"] == report["usage_before"]
    with pytest.raises(ValueError):
        evaluation.run_live(tmp_path)


def test_provider_http_error_preserves_unknown_usage_without_body_or_retry(evaluation, tmp_path, monkeypatch):
    baseline_files(evaluation, tmp_path)
    calls = install_mock_provider(evaluation, monkeypatch, tmp_path, {}, status=500)
    report = evaluation.run_live(tmp_path)
    assert report["result"]["status"] == "provider_unavailable" and len(calls) == 1
    assert report["usage_after"]["usage_unknown_calls"] == 1
    assert report["integrity_checks"]["no_unknown_usage"] is False
    assert "private-provider-error-body" not in (tmp_path / evaluation.FOLLOWUP_NAME).read_text()
    with pytest.raises(ValueError):
        evaluation.run_live(tmp_path)
