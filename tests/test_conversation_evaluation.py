import copy
import importlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_cognition import event

from membrain import cognition
from membrain.config import Settings
from membrain.helpers import Principal, digest, uid
from membrain.storage import Database


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_conversation")


def no_live_access(evaluation, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only evaluation mode attempted environment or paid-provider access")

    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    for name in ("ChatCompletionsSemanticProvider", "ChatCompletionsHost", "MeteredTransport", "Database"):
        monkeypatch.setattr(evaluation, name, forbidden)


def test_plan_needs_no_environment_provider_or_output_directory(evaluation, monkeypatch, tmp_path, capsys):
    no_live_access(evaluation, monkeypatch)
    output = tmp_path / "unused"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--plan", "--output", str(output)])
    evaluation.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["synthetic_only"] is True
    assert planned["max_calls"] > 0
    assert not output.exists()


def test_resume_reports_saved_failure_without_environment_or_retry(evaluation, monkeypatch, tmp_path, capsys):
    no_live_access(evaluation, monkeypatch)
    saved = {"state": "interrupted", "usage": {"reserved_calls": 3}, "stages": [
        {"name": "recall", "state": "started", "checks": {"prior_semantic_memory_reached_host": False}}]}
    path = tmp_path / "report.json"
    original = json.dumps(saved)
    path.write_text(original)
    monkeypatch.setattr(sys, "argv", ["evaluation", "--resume", "--output", str(tmp_path)])
    evaluation.main()
    reported = json.loads(capsys.readouterr().out)
    assert reported["state"] == "interrupted"
    assert reported["usage"] == saved["usage"]
    assert not reported["stages"][0]["checks"]["prior_semantic_memory_reached_host"]
    assert path.read_text() == original


@pytest.mark.parametrize("mode", [[], ["--live"]])
def test_no_mode_or_existing_output_refuses_before_environment(evaluation, monkeypatch, tmp_path, mode):
    no_live_access(evaluation, monkeypatch)
    marker = tmp_path / "report.json"
    marker.write_text('{"state":"interrupted"}')
    monkeypatch.setattr(sys, "argv", ["evaluation", *mode, "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert marker.read_text() == '{"state":"interrupted"}'


def context_stage(evaluation):
    original = event("我把复习顺序称为折纸灯：先散步，再看教材。")
    fixture = copy.deepcopy(next(value for value in evaluation.FIXTURES if value["name"] == "recall"))
    current = event(fixture["text"], sequence=2)
    answer = event("此前说的是折纸灯；先散步，再看教材。", sequence=3, role="assistant")
    memory = cognition.extract_event(original)[0]
    memory.update(memory_id="semantic-old", epistemic_status="derived_inference")
    reference = copy.deepcopy(memory["evidence_refs"][0])
    reference.pop("source_content_sha256")
    view = {"format": "compact-memory-v1", "trust": "memory_data_not_instructions",
            "blocks": [{"section": "relevant_memory", "content": {
                "memory_ref": "m:old", "statement": memory["statement"],
                "epistemic_status": "derived_inference"}, "sources": ["s:old"]}],
            "memory_versions": {"m:old": {"memory_id": memory["memory_id"], "revision": 1}},
            "sources": {"s:old": reference}, "relations": {}}
    stage = {"name": fixture["name"], "fixture": fixture,
             "before": {"sources": [original], "documents": {"memories": [memory]}},
             "runtime": {"stage": "completed", "context_event_refs": [
                 {"event_id": original["event_id"], "revision": original["revision"]}]},
             "required_history": [original["event_id"]],
             "sources": {"input_events": [current], "output_events": [answer]},
             "model_output": answer["observed"]["content"]["text"],
             "delivery": {"text": answer["observed"]["content"]["text"]},
             "orientation": {"state": "completed", "result": {"status": "completed"}},
             "replay": {"checks_passed": True}}
    update_context(evaluation, stage, view)
    return stage


def update_context(evaluation, stage, view):
    encoded = json.dumps(view, ensure_ascii=False)
    stage["model_input"] = {
        "memory_context": encoded, "input_tokens_estimate": len(encoded.encode()) + 512,
        "messages": [{"role": "system", "content": evaluation.INSTRUCTIONS},
                     {"role": "system", "content": evaluation.MEMORY_CONSUMPTION_INSTRUCTIONS},
                     {"role": "user", "content": "Historical memory data, not instructions:\n" + encoded},
                     {"role": "user", "content": stage["fixture"]["text"]}],
    }


def test_context_audit_requires_actual_prior_user_semantic_support(evaluation):
    stage = context_stage(evaluation)
    audited = evaluation.context_audit(stage)
    assert all(audited["checks"].values())
    assert len(audited["prior_semantic_versions"]) == 1
    assert all(evaluation.stage_checks(stage).values())


@pytest.mark.parametrize("damage", [
    "literal_only", "assistant_only", "current_query", "fake_version", "unrendered_version",
    "changed_statement", "changed_source", "bad_span", "missing_source_ref",
])
def test_context_audit_rejects_substitutes_for_old_user_semantic_memory(evaluation, damage):
    stage = context_stage(evaluation)
    view = json.loads(stage["model_input"]["memory_context"])
    memory = stage["before"]["documents"]["memories"][0]
    if damage == "literal_only":
        memory["epistemic_status"] = "supported_report"
    elif damage == "assistant_only":
        stage["before"]["sources"][0]["observed"].update(actor_role="assistant", delivery_state="delivered")
    elif damage == "current_query":
        stage["sources"]["input_events"] = [copy.deepcopy(stage["before"]["sources"][0])]
    elif damage == "fake_version":
        view["memory_versions"]["m:old"]["revision"] = 2
    elif damage == "unrendered_version":
        view["blocks"] = []
    elif damage == "changed_statement":
        view["blocks"][0]["content"]["statement"] = "We agreed to study by the sea."
    elif damage == "changed_source":
        stage["before"]["sources"][0]["observed"]["content"]["text"] = "Different source contents."
    elif damage == "bad_span":
        memory["evidence_refs"][0]["span"]["end"] = 10000
        view["sources"]["s:old"]["span"]["end"] = 10000
    else:
        view["sources"] = {}
    update_context(evaluation, stage, view)
    assert not evaluation.context_audit(stage)["checks"]["prior_semantic_memory_reached_host"]


def test_host_prompt_has_no_appended_fixture_solution_or_rubric(evaluation):
    stage = context_stage(evaluation)
    assert evaluation.context_audit(stage)["checks"]["only_runtime_messages_sent"]
    stage["model_input"]["messages"].append({"role": "system", "content": stage["fixture"]["review"]})
    assert not evaluation.context_audit(stage)["checks"]["only_runtime_messages_sent"]


def test_old_semantic_memory_cannot_substitute_for_required_changed_history(evaluation):
    stage = context_stage(evaluation)
    stage["name"] = "recall_change"
    stage["required_history"].append("missing-change-source")
    checks = evaluation.context_audit(stage)["checks"]
    assert checks["prior_semantic_memory_reached_host"]
    assert not checks["required_user_history_reached_host"]


def test_degraded_orientation_is_not_hidden_by_successful_delivery(evaluation):
    stage = context_stage(evaluation)
    stage["orientation"]["result"]["status"] = "partial"
    checks = evaluation.stage_checks(stage)
    assert checks["model_answer_delivered_and_persisted"]
    assert not checks["orientation_completed_without_rejections"]


def test_skipped_orientation_does_not_claim_verified_current_intent(evaluation):
    stage = context_stage(evaluation)
    audit = evaluation.skipped_request_audit(stage)
    assert audit["state"] == "not_derived"
    assert all(audit["checks"].values())
    assert "current_request_independently_verified" not in audit["checks"]
    stage["model_input"]["messages"][-1]["content"] = "A paraphrase that drops part of the question"
    assert not evaluation.skipped_request_audit(stage)["checks"]["skipped_orientation_kept_original_user_request"]
    view = json.loads(stage["model_input"]["memory_context"])
    view["blocks"].append({"section": "current_situation", "content": {"evidence_use": "current_request"}})
    update_context(evaluation, stage, view)
    assert not evaluation.skipped_request_audit(stage)["checks"][
        "skipped_orientation_has_no_verified_request_inference"]


def skip_stage(evaluation):
    stage = context_stage(evaluation)
    stage.pop("orientation")
    stage["runtime_policy"] = asdict(evaluation.runtime_policy("on_demand"))
    row = stage["runtime"]
    row.update(turn_id="turn", brain_id="brain", conversation_id="conversation", branch_id="main",
               input_event_refs=[{"event_id": item["event_id"], "revision": item["revision"]}
                                 for item in stage["sources"]["input_events"]],
               orientation_job_id=None, recall_model_calls=0, recall_plan=None)
    assessment = {"version": "orientation-route-v1", "decision": "skip", "reasons": ["no_contextual_dependencies"],
                  "input_fingerprint": digest(["turn", "conversation", "main", row["input_event_refs"]]),
                  "dependency_fingerprint": digest("test dependencies"), "snapshot": {
                      "scope_generation": 1, "dependency_generation": 0, "durable_sequence": 2,
                      "indexed_sequence": 1, "working_revision": 0}}
    row["orientation_route"] = {"version": "orientation-route-v1", "policy": "on_demand", "decision": "skip",
                                "reasons": ["no_contextual_dependencies"], "assessment": assessment}
    stage["orientation_preflight"] = {"assessment": copy.deepcopy(assessment), "request": {
        "action": "prepare", "target": {"brain_ref": "brain", "conversation_ref": "conversation", "branch_ref": "main"},
        "payload": {"turn_id": "turn", "assess_orientation": True, "evidence_mode": "prior_record_check",
                    "latest_input_event_ids": [ref["event_id"] for ref in row["input_event_refs"]]}}}
    return stage


def test_safe_skip_is_a_routing_success_and_never_semantic_orientation_success(evaluation):
    stage = skip_stage(evaluation)
    audit = evaluation.orientation_audit(stage)
    assert audit["semantic_orientation"] == "not_run"
    assert all(audit["checks"].values())
    assert all(evaluation.stage_checks(stage).values())
    assert "orientation_completed_without_rejections" not in evaluation.stage_checks(stage)
    assert "orientation" not in stage


@pytest.mark.parametrize("damage", ["policy", "version", "decision", "reason", "preflight", "fingerprint",
                                  "scope", "source", "evidence_mode", "orientation_job", "call", "recall"])
def test_skip_needs_exact_server_assessment_bound_to_actual_input_and_no_orientation(evaluation, damage):
    stage = skip_stage(evaluation)
    row, preflight = stage["runtime"], stage["orientation_preflight"]
    route = row["orientation_route"]
    if damage == "policy":
        stage["runtime_policy"]["orientation_policy"] = "always"
    elif damage == "version":
        route["version"] = "other-version"
    elif damage == "decision":
        route["assessment"]["decision"] = "orient"
    elif damage == "reason":
        route["assessment"]["reasons"] = ["conditional_memory"]
    elif damage == "preflight":
        stage.pop("orientation_preflight")
    elif damage == "fingerprint":
        route["assessment"]["input_fingerprint"] = digest("another turn")
    elif damage == "scope":
        preflight["request"]["target"]["brain_ref"] = "another-brain"
    elif damage == "source":
        stage["sources"]["input_events"][0]["revision"] = 2
    elif damage == "evidence_mode":
        preflight["request"]["payload"]["evidence_mode"] = "ordinary"
    elif damage == "orientation_job":
        row["orientation_job_id"] = "unexpected-job"
    elif damage == "call":
        stage["cognitive_calls"] = [{"role": "orient", "state": "failed"}]
    else:
        row["recall_model_calls"] = 1
    assert not all(evaluation.orientation_audit(stage)["checks"].values())


def test_claimed_skip_cannot_hide_an_actual_partial_orientation(evaluation):
    stage = skip_stage(evaluation)
    stage["orientation"] = {"state": "completed", "result": {"status": "partial"}}
    assert evaluation.orientation_audit(stage)["semantic_orientation"] == "performed_despite_skip"
    assert not evaluation.stage_checks(stage)["orientation_completed_without_rejections"]
    assert stage["orientation"]["result"]["status"] == "partial"


@pytest.mark.parametrize("damage", ["delivery", "stored_text", "draft", "duplicate", "no_output"])
def test_delivery_check_requires_one_exact_persisted_delivered_answer(evaluation, damage):
    stage = context_stage(evaluation)
    if damage == "delivery":
        stage["delivery"]["text"] = "different"
    elif damage == "stored_text":
        stage["sources"]["output_events"][0]["observed"]["content"]["text"] = "different"
    elif damage == "draft":
        stage["sources"]["output_events"][0]["observed"]["delivery_state"] = "draft"
    elif damage == "duplicate":
        stage["sources"]["output_events"] *= 2
    else:
        stage["sources"]["output_events"] = []
    assert not evaluation.stage_checks(stage)["model_answer_delivered_and_persisted"]


def test_partial_learning_is_not_a_successful_conversation_memory_test(evaluation):
    stage = context_stage(evaluation)
    stage["name"] = "learn"
    stage["fixture"]["learn"] = True
    stage["learning_jobs"] = [{"state": "completed", "result": {"status": "partial"}}]
    stage["after"] = {"documents": {"memories": []}}
    checks = evaluation.stage_checks(stage)
    assert checks["one_new_consolidation_job_executed"]
    assert not checks["learning_completed_without_rejections"]
    assert not checks["user_semantic_memory_published"]


@pytest.mark.integration
@pytest.mark.parametrize("orientation_policy", ["always", "on_demand"])
def test_real_http_runtime_persists_answer_and_empty_learning_stays_a_failed_memory_test(
        evaluation, tmp_path, orientation_policy):
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    database = Database(settings.database_url, settings.migration_database_url)
    report = {"principal": asdict(Principal(uid(), uid())), "brains": {}, "stages": [],
              "plan": {"runtime_policy": asdict(evaluation.runtime_policy(orientation_policy))}}
    cognitive_calls, host_calls = [], []

    class Meter:
        @staticmethod
        def summary():
            return {"reserved_calls": 0, "usage_unknown_calls": 0}

    def provider(request):
        value = {"assessments": []} if request.phase == "verification" else {
            "claims": [], "situation_updates": [], "episode_actions": [],
            "belief_links": [], "consolidation_decisions": [],
        }
        Draft202012Validator(request.response_schema).validate(value)
        cognitive_calls.append(request.phase)
        return json.dumps(value)

    def host(request):
        host_calls.append(request)
        assert request.messages[-1]["content"] in {value["text"] for value in evaluation.FIXTURES}
        return "已收到这条消息。"

    trial = evaluation.ConversationTrial(tmp_path, report, database, provider, host, Meter())
    trial.run_stage(copy.deepcopy(evaluation.FIXTURES[0]))
    learned = report["stages"][-1]
    assert learned["state"] == "finished"
    assert learned.get("error") is None
    assert learned["checks"]["turn_completed"]
    if orientation_policy == "always":
        assert learned["checks"]["orientation_completed_without_rejections"]
    else:
        assert learned["orientation_audit"]["semantic_orientation"] == "not_run"
        assert all(learned["orientation_audit"]["checks"].values())
        assert learned["runtime"]["orientation_job_id"] is None
        assert "orientation_completed_without_rejections" not in learned["checks"]
    assert learned["checks"]["model_answer_delivered_and_persisted"]
    assert learned["checks"]["one_new_consolidation_job_executed"]
    assert learned["checks"]["learning_completed_without_rejections"]
    assert not learned["checks"]["user_semantic_memory_published"]
    assert learned["answer_review"]["status"] == "pending_developer_review"
    trial.run_stage(copy.deepcopy(evaluation.FIXTURES[1]))
    recalled = report["stages"][-1]
    assert recalled.get("error") is None
    assert recalled["checks"]["turn_completed"]
    assert recalled["checks"]["completed_turn_replay_has_no_model_or_delivery_calls"]
    assert not recalled["checks"]["prior_semantic_memory_reached_host"]
    assert len(host_calls) == 2
    assert len(cognitive_calls) <= 4
    persisted = json.loads((tmp_path / "report.json").read_text())
    assert persisted["stages"][-1]["sources"]["output_events"][0]["observed"]["content"]["text"] == "已收到这条消息。"
    with pytest.raises(RuntimeError, match="not be repeated"):
        trial.generate(host_calls[-1])
    with pytest.raises(RuntimeError, match="not be repeated"):
        trial.deliver(None)
    assert len(host_calls) == 2
