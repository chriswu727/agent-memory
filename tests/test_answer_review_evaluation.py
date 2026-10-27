import copy
import importlib
import json
from pathlib import Path

import httpx
import pytest
from test_answer_review import assessment

from membrain.answer_review import AnswerReviewer
from membrain.answer_review_provider import ChatCompletionsAnswerReviewProvider
from membrain.helpers import digest


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_answer_review")


def experiment(evaluation, tmp_path, transport):
    proposed = evaluation.plan()
    meter = evaluation.MeteredTransport(tmp_path / "calls.jsonl", max_calls=proposed["max_calls"],
                             max_output_tokens=proposed["max_reserved_output_tokens"], transport=transport)
    provider = ChatCompletionsAnswerReviewProvider("https://test.example", "test-key", evaluation.MODEL,
                                                  thinking="enabled", transport=meter)
    reviewer = AnswerReviewer(provider, reviewer_id=evaluation.REVIEWER_ID, policy=evaluation.POLICY)
    report = {"plan": proposed, "plan_sha256": digest(proposed), "state": "started", "cases": [],
              "review_policy_fingerprint": reviewer.fingerprint}
    return report, reviewer, meter


def success(request):
    return httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(assessment())}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    })


def test_frozen_bad_answers_and_legitimate_controls_are_distinct_from_judge_input(evaluation):
    frozen = evaluation.freeze_cases()
    assert len(frozen["cases"]) == 6
    assert [item["expected_status"] for item in frozen["cases"]] == ["rejected"] * 3 + ["approved"] * 3
    reviewer = AnswerReviewer(lambda _: pytest.fail("Freezing does not call a provider"),
                               reviewer_id=evaluation.REVIEWER_ID, policy=evaluation.POLICY)
    for case in frozen["cases"]:
        raw = case["model_input"]
        request = reviewer.prepare(evaluation.ModelRequest(**raw), case["draft"])
        content = json.loads(request.messages[-1]["content"])
        assert set(content) == {"host_messages", "draft"}
        assert content["host_messages"] == raw["messages"] and content["draft"] == case["draft"]


def test_completed_calls_do_not_hide_wrong_verdicts_and_cannot_be_repeated(evaluation, tmp_path):
    report, reviewer, meter = experiment(evaluation, tmp_path, httpx.MockTransport(success))
    result = evaluation.run(tmp_path, report, reviewer, meter)
    assert result["state"] == "finished"
    assert [item["matches_expected"] for item in result["cases"]] == [False] * 3 + [True] * 3
    assert result["usage"]["reserved_calls"] == 6
    assert result["usage"]["usage_totals"]["total_tokens"] == 180
    assert result["code_unchanged"]
    with pytest.raises(ValueError, match="cannot be changed"):
        evaluation.run(tmp_path, report, reviewer, meter)
    meter.close()


def test_unknown_usage_stops_experiment_without_retry(evaluation, tmp_path):
    def failure(request):
        raise httpx.ReadTimeout("private response not logged")

    report, reviewer, meter = experiment(evaluation, tmp_path, httpx.MockTransport(failure))
    result = evaluation.run(tmp_path, report, reviewer, meter)
    assert result["state"] == "interrupted" and len(result["cases"]) == 1
    assert result["cases"][0]["state"] == "failed"
    assert result["usage"]["reserved_calls"] == result["usage"]["usage_unknown_calls"] == 1
    assert "private response not logged" not in json.dumps(result)
    meter.close()


def test_modified_plan_is_rejected_before_any_spend(evaluation, tmp_path):
    report, reviewer, meter = experiment(evaluation, tmp_path, httpx.MockTransport(success))
    report["plan"] = copy.deepcopy(report["plan"])
    report["plan"]["frozen"]["cases"][0]["draft"] += " changed"
    with pytest.raises(ValueError, match="cannot be changed"):
        evaluation.run(tmp_path, report, reviewer, meter)
    assert meter.summary()["reserved_calls"] == 0
    meter.close()
