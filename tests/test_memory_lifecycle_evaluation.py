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

from agent_memory.answer_review import AnswerReviewer, AnswerReviewPolicy
from agent_memory.helpers import Principal, digest, uid
from agent_memory.runtime import DeliveryRequest, ModelRequest


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_memory_lifecycle")


def no_live_access(evaluation, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A read-only command attempted provider, database or environment access")

    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    for name in ("Database", "MeteredTransport", "ChatCompletionsHost", "ChatCompletionsRecallReasoner"):
        monkeypatch.setattr(evaluation, name, forbidden)


def test_smoke_plan_freezes_review_budget_and_whole_chain_without_live_access(evaluation, monkeypatch, tmp_path, capsys):
    no_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["eval", "--plan", "--output", str(tmp_path / "unused")])
    evaluation.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["arms"] == ["memory"]
    assert planned["per_arm_max_calls"] == 28
    assert planned["per_arm_max_reserved_output_tokens"] == 356352
    assert planned["memory_arm_call_allocation"] == {"semantic": 18, "host": 5, "answer_review": 5}
    assert planned["review_policy"]["max_output_tokens"] == 8192
    assert planned["runtime_policy"]["orientation_policy"] == "always"
    assert [x["name"] for x in planned["fixtures"]] == ["learn", "recall", "change", "recall_change", "empty_control"]
    assert planned["code_fingerprints"]["src/agent_memory/answer_review.py"] == digest(
        (evaluation.ROOT / "src/agent_memory/answer_review.py").read_text())
    assert not (tmp_path / "unused").exists()


def test_on_demand_plan_changes_only_policy_and_preserves_comparison_budget(evaluation):
    always, on_demand = evaluation.plan(), evaluation.plan(orientation_policy="on_demand")
    assert on_demand["runtime_policy"]["orientation_policy"] == "on_demand"
    on_demand["runtime_policy"]["orientation_policy"] = "always"
    assert on_demand == always


def test_cli_freezes_explicit_orientation_policy_without_live_access(evaluation, monkeypatch, tmp_path, capsys):
    no_live_access(evaluation, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["eval", "--plan", "--orientation-policy", "on_demand",
                                      "--output", str(tmp_path / "unused")])
    evaluation.main()
    assert json.loads(capsys.readouterr().out)["runtime_policy"]["orientation_policy"] == "on_demand"


def test_performance_separates_paid_foreground_work_from_learning_and_keeps_failures(evaluation):
    stage = {"runtime": {"orientation_route": {"decision": "skip"}},
             "timings": {"runtime_seconds": 5, "learning_seconds": 30, "total_seconds": 36},
             "cognitive_calls": [{"role": "consolidate", "elapsed_seconds": 29, "state": "failed"}],
             "host_calls": [{"elapsed_seconds": 2}], "answer_review_calls": [{"elapsed_seconds": 3}],
             "usage_delta": {"reserved_calls": 3, "usage_unknown_calls": 1}}
    measured = evaluation.performance_metrics(stage)
    assert measured["provider_calls"] == {"orientation": 0, "learning": 1, "host": 1, "answer_review": 1, "summary": 0}
    assert measured["provider_elapsed_seconds"]["learning"] == 29
    assert measured["timings"]["runtime_seconds"] == 5
    assert measured["usage_delta"]["usage_unknown_calls"] == 1
    assert measured["orientation_decision"] == "skip"
    assert stage["cognitive_calls"][0]["state"] == "failed"


def test_learning_queue_audit_requires_exact_source_sequence_coverage_not_success(evaluation):
    stage = {"sources": {"input_events": [{"event_id": "current", "sequence": 17}]}}
    window = {"id": "job", "state": "cancelled", "learning_phase": "encoding",
              "from_sequence": 17, "through_sequence": 18}
    audited = evaluation.learning_queue_audit(stage, [window])
    assert audited["checks"]["current_input_has_consolidation_window"]
    assert audited["windows"][0]["state"] == "cancelled"
    assert not evaluation.learning_queue_audit(stage, [{**window, "from_sequence": 18}])["checks"][
        "current_input_has_consolidation_window"]
    assert not evaluation.learning_queue_audit({}, [window])["checks"]["current_input_has_consolidation_window"]


def test_resume_preserves_failure_and_never_reopens_ledgers_or_environment(evaluation, monkeypatch, tmp_path, capsys):
    no_live_access(evaluation, monkeypatch)
    original = '{"state":"interrupted","arms":{"memory":{"usage":{"reserved_calls":17}}}}'
    report = tmp_path / "report.json"
    report.write_text(original)
    monkeypatch.setattr(evaluation, "plan", lambda *a, **k: pytest.fail("Resume must not regenerate a plan"))
    monkeypatch.setattr(sys, "argv", ["eval", "--resume", "--output", str(tmp_path)])
    evaluation.main()
    assert json.loads(capsys.readouterr().out)["state"] == "interrupted"
    assert report.read_text() == original
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("arguments", [[], ["--live"], ["--live", "--resume"]])
def test_existing_directory_and_invalid_modes_refuse_before_credentials(evaluation, monkeypatch, tmp_path, arguments):
    no_live_access(evaluation, monkeypatch)
    marker = tmp_path / "evidence.json"
    marker.write_text("preserve")
    monkeypatch.setattr(sys, "argv", ["eval", *arguments, "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2
    assert marker.read_text() == "preserve"


def test_long_plan_has_reproducible_hidden_probes_and_equal_nontransferable_caps(evaluation):
    planned = evaluation.plan("long", seed=735, history_turns=104)
    other = evaluation.plan("long", seed=736, history_turns=104)
    dataset = planned["dataset"]
    assert dataset == evaluation.long_history(735, 104)
    assert dataset != other["dataset"]
    assert len(dataset["history"]) == 104 and len(dataset["probes"]) == 6
    assert len({x["name"] for x in dataset["history"] + dataset["probes"]}) == 110
    assert planned["per_arm_max_calls"] == 856
    assert planned["server_budget_policy"] == {
        "window_seconds": 2_592_000, "max_model_calls": 636,
        "max_input_bytes": 318 * planned["semantic_policy"]["max_input_bytes"],
    }
    assert planned["arms"] == ["memory", "rolling_summary", "no_memory"]
    assert all(sum(values.values()) <= planned["per_arm_max_calls"]
               for values in planned["baseline_call_allocation"].values())
    encoded_history = [x["text"] for x in dataset["history"]]
    assert all(probe["text"] not in encoded_history for probe in dataset["probes"])
    names = {x["name"] for x in dataset["history"]}
    assert all(set(case["source_turns"]) <= names for case in dataset["oracle"].values())
    assert dataset["oracle"]["cue_childhood"]["unestablished"] == ["school city"]
    assert dataset["oracle"]["false_presupposition"]["source_turns"] == []
    evaluation.validate_dataset(dataset)


@pytest.mark.parametrize("damage", ["source", "probe", "oracle", "hash"])
def test_frozen_dataset_rejects_tampering(evaluation, damage):
    dataset = evaluation.long_history()
    if damage == "source":
        dataset["history"][0]["text"] += "changed"
    elif damage == "probe":
        dataset["probes"][0]["text"] += "answer hint"
    elif damage == "oracle":
        dataset["oracle"]["order_change"]["supported"] = ["rewritten after result"]
    else:
        dataset["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        evaluation.validate_dataset(dataset)


@pytest.mark.parametrize("turns", [99, 129, True, 100.0])
def test_long_history_size_is_bounded_and_not_a_short_demo(evaluation, turns):
    with pytest.raises(ValueError):
        evaluation.long_history(history_turns=turns)


def test_baseline_prompt_contains_only_its_frozen_summary_and_current_input(evaluation):
    request = evaluation.baseline_request("turn", "brain", "Current question", "Previous attributed report",
                                          instructions="Host rules", policy=evaluation.conversation.RUNTIME_POLICY)
    assert request.messages[-1] == {"role": "user", "content": "Current question"}
    context = json.loads(request.memory_context)
    assert context["blocks"][0]["content"]["epistemic_status"] == "unverified_rolling_summary"
    assert context["sources"] == {} and context["memory_versions"] == {}
    empty = evaluation.baseline_request("turn", "brain", "Current question", "",
                                        instructions="Host rules", policy=evaluation.conversation.RUNTIME_POLICY)
    assert json.loads(empty.memory_context)["blocks"] == []
    with pytest.raises(ValueError, match="budget"):
        evaluation.baseline_request("turn", "brain", "Current", "x" * 24000,
                                    instructions="Host", policy=evaluation.conversation.RUNTIME_POLICY)


class Meter:
    def __init__(self):
        self.calls, self.unknown = 0, 0

    def summary(self):
        return {"reserved_calls": self.calls, "completed_calls": self.calls,
                "reserved_output_tokens": self.calls * 8192, "usage_unknown_calls": self.unknown,
                "usage_totals": {"total_tokens": self.calls * 3}}


def trial_fixture(evaluation, tmp_path, *, arm="memory", **kwargs):
    report = {"arm": arm, "plan": evaluation.plan(), "principal": asdict(Principal(uid(), uid())),
              "stages": [], "brains": {}}
    report["plan_sha256"] = digest(report["plan"])
    trial = evaluation.LifecycleTrial(tmp_path, report, None, None, None, Meter(), None, **kwargs)
    stage = {"name": "test", "fixture": {"text": "Original user message"}}
    trial.current = stage
    trial.report["stages"].append(stage)
    return trial


def sample_review():
    prompt = ModelRequest("turn", "generate", ({"role": "user", "content": "A question"},), 100, "{}")
    reviewer = AnswerReviewer(lambda _: "", reviewer_id="test", policy=AnswerReviewPolicy(max_output_tokens=8192))
    return reviewer.prepare(prompt, "An uncertain answer"), prompt


def accepted_assessment():
    return {"coverage_complete": True,
            "checks": {key: "consistent" for key in ("current_input_fidelity", "memory_fidelity",
                                                      "applicability", "uncertainty", "action_claims")},
            "findings": []}


def test_review_request_is_frozen_before_call_and_invalid_response_is_not_stored(evaluation, tmp_path):
    trial = trial_fixture(evaluation, tmp_path)
    request, _ = sample_review()

    def provider(actual):
        saved = json.loads((tmp_path / "report.json").read_text())
        record = saved["stages"][0]["answer_review_calls"][0]
        assert record["state"] == "started"
        assert record["request"]["messages"] == list(actual.messages)
        trial.meter.calls += 1
        return '{"unexpected":"private unstructured response"}'

    trial.review_provider = provider
    trial.review(request)
    record = trial.current["answer_review_calls"][0]
    assert record["schema_valid"] is False
    assert "response" not in record
    assert "private unstructured response" not in (tmp_path / "report.json").read_text()
    assert record["usage_delta"]["reserved_calls"] == 1
    assert record["elapsed_seconds"] >= 0
    with pytest.raises(RuntimeError, match="not be repeated"):
        trial.review(request)


@pytest.mark.parametrize("damage", ["plan", "code_before", "code_during"])
def test_paid_call_refuses_changed_plan_or_code(evaluation, tmp_path, monkeypatch, damage):
    trial = trial_fixture(evaluation, tmp_path)
    source = tmp_path / "implementation.py"
    source.write_text("frozen")
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    trial.report["plan"]["code_fingerprints"] = {source.name: digest("frozen")}
    trial.report["plan_sha256"] = digest(trial.report["plan"])
    invoked = []

    def provider(request):
        invoked.append(request)
        source.write_text("changed")
        return json.dumps(accepted_assessment())

    trial.review_provider = provider
    if damage == "plan":
        trial.report["plan"]["model"] = "changed"
    elif damage == "code_before":
        source.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        trial.review(sample_review()[0])
    assert len(invoked) == (1 if damage == "code_during" else 0)
    if invoked:
        assert trial.current["answer_review_calls"][0]["state"] == "failed"
        assert trial.current["answer_review_calls"][0]["response"] == accepted_assessment()


@pytest.mark.parametrize("quarantined,excluded", [(0, 0), (1, 0), (0, 1)])
def test_history_checkpoint_uses_real_client_envelopes_and_rejects_incomplete_restore(
        evaluation, tmp_path, monkeypatch, quarantined, excluded):
    trial = trial_fixture(evaluation, tmp_path)
    trial.report["brains"]["trained"] = "history-brain"
    restored = []

    class Memory:
        def export(self, **kwargs):
            return {"action": "export", "outcome": "completed",
                    "result": {"archive_id": "frozen-archive", "manifest_sha256": digest(self.document())}}

        def document(self):
            return {"source_brain_id": self.brain_id, "archive_origin": "local_export", "events": [],
                    "memories": [], "dependency_memories": [], "belief_relations": [],
                    "cognitive_views": {"episodes": [], "members": [], "associations": []},
                    "event_dependencies": [], "tombstones": [], "retired_event_ids": [],
                    "excluded_deleted_event_ids": []}

        def download_archive(self, identifier):
            return {"archive_id": identifier, "manifest_sha256": digest(self.document()), "document": self.document()}

        def create_brain(self, name):
            return {"id": "isolated-probe"}

        def restore(self, archive_id, **kwargs):
            restored.append((self.brain_id, archive_id, kwargs))
            return {"action": "restore", "outcome": "completed", "result": {
                "read_enabled": True, "quarantined_event_count": quarantined, "excluded_deleted_count": excluded}}

    @contextmanager
    def client():
        yield Memory()

    monkeypatch.setattr(trial, "archive_client", client)
    trial.freeze_history()
    assert trial.report["history_checkpoint"]["archive"]["archive_id"] == "frozen-archive"
    fixture = {"name": "probe", "brain": "probe-brain"}
    if quarantined or excluded:
        with pytest.raises(RuntimeError, match="incomplete"):
            trial.prepare_probe(fixture)
    else:
        trial.prepare_probe(fixture)
    assert restored == [("isolated-probe", "frozen-archive", {
        "expected_manifest_sha256": trial.report["history_checkpoint"]["archive"]["manifest_sha256"],
        "expected_scope_generation": 1, "mode": "new_brain",
        "budget": evaluation.default_budget(64000)})]
    assert trial.report["probe_restores"][0]["result"]["result"]["quarantined_event_count"] == quarantined
    with pytest.raises(RuntimeError, match="reused"):
        trial.prepare_probe(fixture)


def test_held_out_source_audit_cannot_pass_with_fluent_answer_and_no_relevant_memory(evaluation):
    history = [{"name": "history-000", "sources": {"input_events": [{"event_id": "original-user"}]}}]
    stage = {"model_output": "A fluent answer", "context_audit": {"prior_semantic_versions": []}}
    oracle = {"source_turns": ["history-000"]}
    assert evaluation.held_out_source_audit(stage, history, oracle)["checks"] == {
        "held_out_sources_resolved": True, "held_out_relevant_history_reached_host": False}
    stage["context_audit"]["prior_semantic_versions"] = [{"prior_user_supports": [{"event_id": "original-user"}]}]
    assert all(evaluation.held_out_source_audit(stage, history, oracle)["checks"].values())
    assert not any(evaluation.held_out_source_audit(stage, [], oracle)["checks"].values())


def test_unknown_usage_and_failed_calls_cannot_be_retried(evaluation, tmp_path):
    trial = trial_fixture(evaluation, tmp_path)
    request, _ = sample_review()
    calls = []

    def provider(actual):
        calls.append(actual)
        trial.meter.unknown = 1
        raise TimeoutError("private provider detail")

    trial.review_provider = provider
    with pytest.raises(TimeoutError):
        trial.review(request)
    assert trial.current["answer_review_calls"][0]["state"] == "failed"
    assert "private provider detail" not in (tmp_path / "report.json").read_text()
    with pytest.raises(RuntimeError, match="Unknown provider usage"):
        trial.review(request)
    assert len(calls) == 1


def test_review_audit_needs_real_prompt_draft_binding_and_approval(evaluation):
    request, prompt = sample_review()
    stage = {"model_input": {"messages": list(prompt.messages)}, "model_output": "An uncertain answer",
             "answer_review_calls": [{"state": "finished", "schema_valid": True, "request": asdict(request)}],
             "delivery": {"text": "An uncertain answer"}, "runtime": {"response_review": {
                 "status": "approved", "request_fingerprint": request.request_fingerprint,
                 "draft_sha256": request.draft_sha256, "policy_fingerprint": request.policy_fingerprint}}}
    assert all(evaluation.review_audit(stage).values())
    stage["runtime"]["response_review"]["status"] = "rejected"
    assert not evaluation.review_audit(stage)["review_approved_before_delivery"]
    stage["model_output"] = "A different draft with the same keywords"
    audited = evaluation.review_audit(stage)
    assert not audited["review_saw_actual_host_prompt_and_draft"]
    assert not audited["review_bound_to_persisted_draft"]


def test_delivery_is_durable_exact_and_never_duplicated(evaluation, tmp_path):
    trial = trial_fixture(evaluation, tmp_path)
    request = DeliveryRequest("turn", "delivery", "Actually delivered text")
    receipt = trial.deliver(request)
    assert receipt.state == "delivered"
    sink = trial.current["delivery_sink"]
    raw = (tmp_path / sink["path"]).read_bytes()
    assert json.loads(raw) == asdict(request)
    assert hashlib.sha256(raw).hexdigest() == sink["sha256"]
    with pytest.raises(RuntimeError, match="not be repeated"):
        trial.deliver(request)


def test_summary_update_reads_only_prior_summary_and_real_delivery_not_oracle(evaluation, tmp_path):
    requests = []
    trial = trial_fixture(evaluation, tmp_path, arm="rolling_summary", summary_host=lambda r: requests.append(r) or "New summary")
    trial.summary = "Old summary"
    trial.current.update(runtime={"stage": "completed", "turn_id": "turn"}, delivery_sink={"path": "sink"},
                         delivery={"text": "Actual answer"}, held_out_oracle={"secret": "Never send this"})
    trial.update_summary()
    sent = json.loads(requests[0].messages[-1]["content"])
    assert sent == {"previous_summary": "Old summary", "new_exchange": [
        {"role": "user", "content": "Original user message"}, {"role": "assistant", "content": "Actual answer"}]}
    assert "Never send this" not in json.dumps(requests[0].messages)
    assert trial.summary == "New summary"
    assert trial.current["summary_calls"][0]["response"] == "New summary"


def test_oversized_summary_is_recorded_but_not_silently_truncated_or_adopted(evaluation, tmp_path):
    trial = trial_fixture(evaluation, tmp_path, arm="rolling_summary", summary_host=lambda _: "x" * 12001)
    trial.summary = "Frozen old summary"
    trial.current.update(runtime={"stage": "completed", "turn_id": "turn"}, delivery_sink={"path": "sink"},
                         delivery={"text": "Actual answer"})
    with pytest.raises(ValueError, match="budget"):
        trial.update_summary()
    assert trial.summary == "Frozen old summary"
    assert len(trial.current["summary_calls"][0]["response"]) == 12001
    assert trial.current["summary_error"] == "summary_budget_exceeded"


def test_partial_status_remains_visible_but_quota_and_unknown_stop(evaluation):
    stage = {"runtime": {"stage": "completed"}, "orientation": {"result": {"status": "partial"}},
             "checks": {"orientation_completed_without_rejections": False}}
    meter = Meter()
    assert evaluation.stop_reason(stage, meter) is None
    assert stage["checks"]["orientation_completed_without_rejections"] is False
    stage["orientation"]["result"]["status"] = "budget_exhausted"
    assert evaluation.stop_reason(stage, meter) == "cognitive_processing_unavailable"
    meter.unknown = 1
    assert evaluation.stop_reason(stage, meter) == "unknown_provider_usage"


def test_transports_share_per_arm_ceiling_and_cannot_borrow_from_another_arm(evaluation, tmp_path):
    def reply(_):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "answer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    meters = [evaluation.MeteredTransport(tmp_path / "memory" / "calls.jsonl", max_calls=2,
                                         max_output_tokens=12288, capture_content=False,
                                         transport=httpx.MockTransport(reply)) for _ in range(2)]
    other = evaluation.MeteredTransport(tmp_path / "baseline" / "calls.jsonl", max_calls=2,
                                        max_output_tokens=12288, transport=httpx.MockTransport(reply))
    with httpx.Client(transport=meters[0]) as host, httpx.Client(transport=meters[1]) as reviewer:
        host.post("https://synthetic.invalid", json={"max_tokens": 4096})
        reviewer.post("https://synthetic.invalid", json={"max_tokens": 8192})
        assert other.summary()["reserved_calls"] == 0
        with pytest.raises(httpx.TransportError, match="call_limit"):
            host.post("https://synthetic.invalid", json={"max_tokens": 1})
    other.close()
    assert meters[0].summary()["usage_totals"]["total_tokens"] == 4


def test_main_preserves_failed_checks_and_stops_before_next_arm(evaluation, monkeypatch, tmp_path):
    output = tmp_path / "new"
    monkeypatch.setattr(evaluation.Settings, "from_env", lambda: SimpleNamespace(database_url="private", migration_database_url=None))
    monkeypatch.setattr(evaluation, "Database", lambda *args: object())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    seen = []

    def run_arm(directory, planned, arm, database, key):
        assert json.loads((output / "report.json").read_text())["plan_sha256"] == digest(planned)
        seen.append(arm)
        directory.mkdir()
        report = {"state": "stopped", "usage": {"reserved_calls": 6},
                  "stages": [{"name": "blocked", "checks": {"review_approved_before_delivery": False}}]}
        (directory / "report.json").write_text(json.dumps(report))
        return report

    monkeypatch.setattr(evaluation, "run_arm", run_arm)
    monkeypatch.setattr(sys, "argv", ["eval", "--live", "--profile", "long", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 1
    saved = json.loads((output / "report.json").read_text())
    assert saved["state"] == "stopped"
    assert saved["arms"]["memory"]["failed_pipeline_stages"] == ["blocked"]
    assert seen == ["memory"]
    assert "test-secret" not in (output / "report.json").read_text()
