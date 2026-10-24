import copy
import importlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest
from test_belief_semantic import SINCE, known, link_verifier, version
from test_cognition import event, scope
from test_semantic import claim, encoded, episode, proposals, reference

from agent_memory.helpers import digest
from agent_memory.semantic import CognitiveBatch, SemanticPolicy, SemanticProcessor, SemanticResult
from agent_memory.semantic_provider import ChatCompletionsSemanticProvider


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_episode_scope_repair")


def baseline_files(evaluation, tmp_path, monkeypatch):
    old = event("I prefer concise replies.", scope=scope(conversation_id="conversation-learn"))
    current = event("From October 24, 2026 I prefer detailed explanations, replacing concise replies.", 2,
                    scope=scope(conversation_id="conversation-change"))
    target = known(old)
    new_claim = claim(current, statement="The user prefers detailed explanations from October 24, 2026.",
                      conditions={"op": "always"}, valid_during=copy.deepcopy(SINCE))
    new_episode = episode(current, proposal_id="episode-current")
    old_episode = episode(old, proposal_id=evaluation.OLD_REJECTION["proposal_id"])
    link = {"proposal_id": "link-current", "kind": "supersedes", "claim_proposal_id": new_claim["proposal_id"],
            "target_memory_versions": [version(target)], "evidence_refs": [reference(old), reference(current)],
            "world": "real_world", "conditions": {"op": "always"}, "valid_during": copy.deepcopy(SINCE)}
    proposed = proposals(new_claim, episodes=[new_episode, old_episode]) | {"belief_links": [link]}
    calls = []

    def provider(request):
        response = encoded(proposed) if request.phase == "proposal" else link_verifier(request)
        calls.append({"request": asdict(request), "state": "finished", "role": "consolidate",
                      "schema_valid": True, "response": json.loads(response)})
        return response

    policy = SemanticPolicy(max_model_calls=2, max_output_tokens=16384, max_request_bytes=98304,
                            max_input_bytes=196608, max_wall_time_ms=240000)
    original_batch = CognitiveBatch("original", current["scope"], (old, current), (target,),
                                    input_contract_version=None)
    result = SemanticProcessor(provider, provider, policy).process(original_batch, reserve=lambda **_: None)
    assert result.status == "partial" and list(result.rejected) == [evaluation.OLD_REJECTION]
    data = json.loads(calls[0]["request"]["messages"][-1]["content"])
    snapshot = asdict(result) | {
        "scope": current["scope"], "proposal_mode": "general-v1",
        "source_snapshot": evaluation._versions(data["sources"], "event_id"),
        "memory_snapshot": evaluation._versions(data["existing_assertions"], "memory_id"),
        "episode_snapshot": [], "working_snapshot": data["working_state"],
        "sampling": data["sampling"], "consolidation_plan": data["consolidation_plan"],
        "belief_relations": [], "learning_input_refs": [{"event_id": current["event_id"], "revision": 1}],
        "learning_window": {"status": "processed", "processing_succeeded": True, "input_count": 1,
                            "from_sequence": 2, "through_sequence": 2},
    }
    stages = [{"name": name, "state": "finished"}
              for name in ("learn", "recall", "change", "recall_change", "empty_control")]
    stages[2].update(learning_job_id="original-job", cognitive_calls=calls, learning_jobs=[{
        "job_id": "original-job", "state": "completed", "execution_error": None, "result": snapshot,
    }])
    ledger, totals = [], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    output_caps = [16384] * 4 + [4096] * 5 + [8192] * 5
    for index, output_cap in enumerate(output_caps):
        usage = {"prompt_tokens": 10 if index < 13 else evaluation.BASE_TOKENS - 144,
                 "completion_tokens": 1, "total_tokens": 11 if index < 13 else evaluation.BASE_TOKENS - 143}
        for key, value in usage.items():
            totals[key] += value
        ledger.extend([{"event": "reserved", "request_id": str(index), "reserved_output_tokens": output_cap},
                       {"event": "completed", "request_id": str(index), "usage_confirmed": True, "usage": usage}])
    usage = {"reserved_calls": 14, "completed_calls": 14, "reserved_output_tokens": evaluation.BASE_OUTPUT,
             "usage_confirmed_calls": 14, "usage_unknown_calls": 0, "usage_totals": totals}
    source_plan = {"profile": "smoke", "model": evaluation.MODEL, "thinking": "enabled",
                   "runtime_policy": {"orientation_policy": "on_demand"}, "semantic_policy": asdict(policy)}
    source = {"version": 1, "state": "finished", "arm": "memory", "plan": source_plan,
              "plan_sha256": digest(source_plan), "stages": stages, "usage": usage, "call_ledger": ledger}
    source_path = tmp_path / "source-report.json"
    source_path.write_text(json.dumps(source, ensure_ascii=False))
    source_path.with_name("calls.jsonl").write_text("".join(encoded(record) + "\n" for record in ledger))
    monkeypatch.setattr(evaluation, "SOURCE_REPORT", source_path)
    corrected = copy.deepcopy(proposed)
    corrected["episode_actions"] = [corrected["episode_actions"][0]]
    return source, corrected


def mock_provider(evaluation, monkeypatch, output, proposed, *, unknown=False, invalid=False, failure=False,
                  after_response=None):
    requests = []

    def handle(request):
        report = json.loads((output / "report.json").read_text())
        assert report["plan_sha256"] == digest(report["plan"])
        assert report["plan"]["frozen_inputs_sha256"] == digest(report["frozen_inputs"])
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == evaluation.MODEL and body["thinking"] == {"type": "enabled"}
        assert body["max_tokens"] == 16384
        if failure:
            return httpx.Response(500, text="secret provider error")
        data = json.loads(body["messages"][-1]["content"])
        if "sources" in data:
            assert data["learning_input_refs"] and data["current_input_refs"] == []
            assert data["episode_operation_scope"]["conversation_id"] == "conversation-change"
            assert data["episode_eligible_member_refs"] == data["learning_input_refs"]
            assert len(data["sources"]) == 2 and len(data["existing_assertions"]) == 1
            content = "secret malformed response" if invalid else encoded(proposed)
        else:
            content = encoded({"assessments": [{
                "proposal_id": item["proposal"]["proposal_id"], "proposal_verdict": "supports",
                "link_verdict": "supports" if item["group"] == "belief_links" else None,
                "relations": [{"evidence_index": entry["evidence_index"], "verdict": "supports"}
                              for entry in item["evidence"]],
            } for item in data["proposals"]]})
        if after_response is not None:
            after_response(len(requests))
        response = {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": content, "reasoning_content": "secret reasoning",
        }}]}
        if not unknown:
            response["usage"] = {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
        return httpx.Response(200, json=response)

    def provider(*args, transport, **kwargs):
        assert (output / "report.json").is_file()
        transport._transport.close()
        transport._transport = httpx.MockTransport(handle)
        return ChatCompletionsSemanticProvider(*args, transport=transport, **kwargs)

    monkeypatch.setattr(evaluation, "load_dotenv", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-api-key")
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", provider)
    return requests


def test_plan_freezes_complete_original_batch_and_adds_only_new_metadata(evaluation, tmp_path, monkeypatch):
    source, _ = baseline_files(evaluation, tmp_path, monkeypatch)
    original = evaluation.SOURCE_REPORT.read_bytes()
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("Plan cannot load credentials"))
    monkeypatch.setattr(evaluation, "MeteredTransport", lambda *a, **k: pytest.fail("Plan cannot create a ledger"))
    frozen = evaluation.freeze_inputs()
    planned = evaluation.plan(frozen)
    batch = evaluation.batch_from_saved(frozen)
    revised = json.loads(planned["proposal_input"]["messages"][-1]["content"])
    assert {key: value for key, value in revised.items() if key not in evaluation.METADATA_KEYS} == frozen[
        "original_proposal_data"]
    assert batch.scope == source["stages"][2]["learning_jobs"][0]["result"]["scope"]
    assert batch.current_input_refs == () and batch.learning_input_refs
    assert batch.input_contract_version == evaluation.EPISODE_SCOPE_INPUT_VERSION
    assert evaluation.old_episode_rejected(batch, frozen)
    assert frozen["source_usage"]["usage_totals"]["total_tokens"] == 118460
    assert planned["max_calls"] == 2 and planned["max_reserved_output_tokens"] == 32768
    assert planned["semantic_review"]["state"] == "pending_developer_review"
    assert evaluation.SOURCE_REPORT.read_bytes() == original


@pytest.mark.parametrize("fault", ["unfinished", "unknown_usage", "wrong_total", "plan_hash", "source_hash",
                                   "working_snapshot", "learning_scope", "old_rejection", "current_authority"])
def test_changed_or_incomplete_original_is_rejected_before_attempt(evaluation, tmp_path, monkeypatch, fault):
    source, _ = baseline_files(evaluation, tmp_path, monkeypatch)
    stage = source["stages"][2]
    data = json.loads(stage["cognitive_calls"][0]["request"]["messages"][-1]["content"])
    if fault == "unfinished":
        source["state"] = "interrupted"
    elif fault == "unknown_usage":
        source["call_ledger"][-1]["usage_confirmed"] = False
    elif fault == "wrong_total":
        source["usage"]["usage_totals"]["total_tokens"] += 1
    elif fault == "plan_hash":
        source["plan_sha256"] = "0" * 64
    elif fault == "source_hash":
        data["sources"][0]["content_sha256"] = "0" * 64
    elif fault == "working_snapshot":
        stage["learning_jobs"][0]["result"]["working_snapshot"] = {"extra": True}
    elif fault == "learning_scope":
        stage["learning_jobs"][0]["result"]["learning_input_refs"] = [{"event_id": "event-1", "revision": 1}]
    elif fault == "old_rejection":
        stage["learning_jobs"][0]["result"]["rejected"] = []
    else:
        data["current_input_refs"] = [{"event_id": "event-2", "revision": 1}]
    stage["cognitive_calls"][0]["request"]["messages"][-1]["content"] = encoded(data)
    evaluation.SOURCE_REPORT.write_text(encoded(source))
    output = tmp_path / "attempt"
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No credentials before validation"))
    with pytest.raises(ValueError):
        evaluation.run_live(output)
    assert not output.exists()


def test_two_mock_calls_preserve_original_and_cumulate_usage_without_answer_leakage(evaluation, tmp_path, monkeypatch):
    _, proposed = baseline_files(evaluation, tmp_path, monkeypatch)
    original = evaluation.SOURCE_REPORT.read_bytes()
    output = tmp_path / "attempt"
    requests = mock_provider(evaluation, monkeypatch, output, proposed)
    report = evaluation.run_live(output)
    assert report["state"] == "finished" and report["result"]["status"] == "completed"
    assert all(report["review"]["checks"].values())
    assert report["review"]["semantic_review"]["state"] == "pending_developer_review"
    assert len(requests) == report["usage"]["reserved_calls"] == 2
    assert report["usage"]["reserved_output_tokens"] == 32768
    assert report["cumulative_usage"]["reserved_calls"] == 16
    assert report["cumulative_usage"]["usage_totals"]["total_tokens"] == 118482
    assert all(report["integrity_checks"].values()) and evaluation.SOURCE_REPORT.read_bytes() == original
    assert not any(report[key] for key in ("database_used", "published", "host_generated", "delivered"))
    serialized = encoded(requests)
    assert evaluation.OLD_REJECTION["proposal_id"] not in serialized
    assert "developer_comparison" not in serialized and evaluation.REVIEW not in serialized
    saved = (output / "report.json").read_text()
    assert not any(value in saved for value in ("secret-api-key", "secret reasoning", "secret malformed response"))
    with pytest.raises(ValueError, match="already exists"):
        evaluation.run_live(output)
    assert len(requests) == 2


@pytest.mark.parametrize("failure", [False, True])
def test_unknown_usage_stops_before_verification_and_cannot_resume_calls(evaluation, tmp_path, monkeypatch, failure):
    _, proposed = baseline_files(evaluation, tmp_path, monkeypatch)
    output = tmp_path / "attempt"
    requests = mock_provider(evaluation, monkeypatch, output, proposed, unknown=True, failure=failure)
    report = evaluation.run_live(output)
    assert report["state"] == "interrupted" and len(requests) == 1
    assert report["usage"]["usage_unknown_calls"] == report["cumulative_usage"]["usage_unknown_calls"] == 1
    assert report["cumulative_usage"]["reserved_calls"] == 15
    assert "secret provider error" not in (output / "report.json").read_text()
    assert evaluation.resume_report(output) == json.loads(encoded(report))
    with pytest.raises(ValueError):
        evaluation.run_live(output)
    assert len(requests) == 1


def test_schema_invalid_known_usage_is_preserved_without_retry_or_semantic_success(evaluation, tmp_path, monkeypatch):
    _, proposed = baseline_files(evaluation, tmp_path, monkeypatch)
    output = tmp_path / "attempt"
    requests = mock_provider(evaluation, monkeypatch, output, proposed, invalid=True)
    report = evaluation.run_live(output)
    assert report["state"] == "finished" and report["result"]["status"] == "invalid_response"
    assert len(requests) == 1 and report["usage"]["usage_unknown_calls"] == 0
    assert not report["review"]["checks"]["processor_completed_without_rejections"]
    assert "secret malformed response" not in (output / "report.json").read_text()


def test_resume_reads_only_saved_report_without_source_environment_or_ledger(evaluation, tmp_path, monkeypatch):
    saved = {"state": "interrupted", "semantic_review": {"state": "pending_developer_review"}}
    (tmp_path / "report.json").write_text(encoded(saved))
    monkeypatch.setattr(evaluation, "freeze_inputs", lambda: pytest.fail("No source access"))
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No credentials"))
    monkeypatch.setattr(evaluation, "MeteredTransport", lambda *a, **k: pytest.fail("No ledger"))
    assert evaluation.resume_report(tmp_path) == saved


def test_missing_new_input_contract_is_rejected_before_attempt(evaluation, tmp_path, monkeypatch):
    baseline_files(evaluation, tmp_path, monkeypatch)
    original = evaluation.proposal_data
    monkeypatch.setattr(evaluation, "proposal_data", lambda *args, **kwargs: original(
        *args, **(kwargs | {"input_contract_version": None})))
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: pytest.fail("No credentials before contract validation"))
    with pytest.raises(ValueError, match="scope and eligibility metadata"):
        evaluation.run_live(tmp_path / "attempt")
    assert not (tmp_path / "attempt").exists()


def test_original_ledger_change_blocks_next_phase_without_modifying_it_again(evaluation, tmp_path, monkeypatch):
    _, proposed = baseline_files(evaluation, tmp_path, monkeypatch)
    output = tmp_path / "attempt"
    path = evaluation.SOURCE_REPORT.with_name("calls.jsonl")
    original = path.read_bytes()

    def changed(_):
        path.write_bytes(original + b"\n")

    requests = mock_provider(evaluation, monkeypatch, output, proposed, after_response=changed)
    report = evaluation.run_live(output)
    assert report["state"] == "interrupted" and len(requests) == 1
    assert not report["integrity_checks"]["original_ledger_unchanged"]
    assert path.read_bytes() == original + b"\n"


def test_missing_key_preserves_zero_spend_attempt_and_original_cumulative_usage(evaluation, tmp_path, monkeypatch):
    baseline_files(evaluation, tmp_path, monkeypatch)
    monkeypatch.setattr(evaluation, "load_dotenv", lambda: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", lambda *a, **k: pytest.fail("No provider"))
    output = tmp_path / "attempt"
    report = evaluation.run_live(output)
    assert report["state"] == "interrupted" and report["usage"]["reserved_calls"] == 0
    assert report["cumulative_usage"]["reserved_calls"] == 14
    assert report["cumulative_usage"]["usage_totals"]["total_tokens"] == 118460
    with pytest.raises(ValueError, match="already exists"):
        evaluation.run_live(output)


@pytest.mark.parametrize("when", ["before_first", "after_first"])
def test_code_changes_stop_before_next_provider_call(evaluation, tmp_path, monkeypatch, when):
    _, proposed = baseline_files(evaluation, tmp_path, monkeypatch)
    output = tmp_path / "attempt"
    original_fingerprints = evaluation.fingerprints
    changed = False

    def fingerprints():
        result = original_fingerprints()
        return result | ({"changed": "1"} if changed else {})

    def after_response(_):
        nonlocal changed
        changed = True

    monkeypatch.setattr(evaluation, "fingerprints", fingerprints)
    requests = mock_provider(evaluation, monkeypatch, output, proposed, after_response=after_response)
    if when == "before_first":
        monkeypatch.setattr(evaluation, "load_dotenv", lambda: after_response(0))
    report = evaluation.run_live(output)
    assert report["state"] == "interrupted" and not report["integrity_checks"]["code_unchanged"]
    assert len(requests) == (0 if when == "before_first" else 1)


@pytest.mark.parametrize("fault", ["no_episode", "old_group", "no_preference", "no_link", "unverified", "wrong_target"])
def test_machine_oracle_requires_current_episode_preference_and_verified_replacement(evaluation, tmp_path, monkeypatch, fault):
    _, proposed = baseline_files(evaluation, tmp_path, monkeypatch)
    output = tmp_path / "attempt"
    mock_provider(evaluation, monkeypatch, output, proposed)
    report = evaluation.run_live(output)
    assert all(report["review"]["checks"].values())
    result = SemanticResult(**copy.deepcopy(report["result"]))
    if fault == "no_episode":
        result = replace(result, episode_actions=())
    elif fault == "old_group":
        result.episode_actions[0]["member_refs"] = [{"event_id": "event-1", "revision": 1}]
    elif fault == "no_preference":
        result = replace(result, claims=())
    elif fault == "no_link":
        result = replace(result, belief_links=())
    elif fault == "unverified":
        result.belief_links[0]["verification"]["state"] = "unknown"
    else:
        result.belief_links[0]["target_memory_versions"][0]["memory_id"] = "wrong-target"
    review = evaluation.review_result(evaluation.batch_from_saved(report["frozen_inputs"]), report["frozen_inputs"],
                                      result, report["responses"], report["verification_input"])
    assert not all(review["checks"].values())
    assert review["semantic_review"]["state"] == "pending_developer_review"
