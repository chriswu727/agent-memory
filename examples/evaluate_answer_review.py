"""Bounded opt-in review of frozen bad answers and legitimate uncertainty controls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from examples.evaluate_conversation import safe_error
from examples.evaluate_deepseek import save
from examples.evaluation_support import MeteredTransport
from membrain.answer_review import AnswerReviewer, AnswerReviewPolicy
from membrain.answer_review_provider import ChatCompletionsAnswerReviewProvider
from membrain.helpers import digest
from membrain.runtime import ModelRequest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evaluations/deepseek-2026-10-24-interaction-repair.json"
MODEL = "deepseek-v4-pro"
POLICY = AnswerReviewPolicy(max_output_tokens=8192, max_response_bytes=32768, timeout_seconds=240)
REVIEWER_ID = MODEL + ":thinking-enabled:answer-review-v1"


def fingerprints():
    return {name: digest((ROOT / name).read_text()) for name in (
        "examples/evaluate_answer_review.py", "examples/evaluation_support.py",
        "src/membrain/answer_review.py", "src/membrain/answer_review_provider.py",
        "src/membrain/recall_provider.py",
    )}


def freeze_cases():
    raw = SOURCE.read_bytes()
    source = json.loads(raw)
    original = {stage["name"]: stage for stage in source["baseline"]["stages"]}
    followup = {stage["name"]: stage for stage in source["consumption_followup"]["cases"]}
    selections = (
        ("current_input_misread", original["learn"], "rejected"),
        ("invented_clock", original["recall"], "rejected"),
        ("unknown_upgraded_to_current", followup["probe"], "rejected"),
        ("explicit_current_report", original["change"], "approved"),
        ("attributed_recall", followup["recall"], "approved"),
    )
    cases = [{"name": name, "model_input": copy.deepcopy(stage["model_input"]),
              "draft": stage["model_output"], "expected_status": expected,
              "origin": "unchanged_previous_model_answer"} for name, stage, expected in selections]
    cases.append({
        "name": "legitimate_unknown", "model_input": copy.deepcopy(followup["probe"]["model_input"]),
        "draft": "记录中，‘折纸灯’最初是先散步再看教材，后来改为先看教材再散步。"
                 "但现有资料无法确定现在适用哪一版，不能仅凭记录较新就认定新顺序当前有效。"
                 "至于海边复习，我没有找到相关约定。",
        "expected_status": "approved", "origin": "developer_written_uncertainty_control",
    })
    for case in cases:
        if not isinstance(case["draft"], str) or not case["draft"].strip():
            raise ValueError("Frozen cases require complete draft answers")
        case["draft_sha256"] = hashlib.sha256(case["draft"].encode()).hexdigest()
        case["model_input_sha256"] = digest(case["model_input"])
    return {"source_sha256": hashlib.sha256(raw).hexdigest(), "cases": cases}


def plan():
    frozen = freeze_cases()
    return {"model": MODEL, "thinking": "enabled", "reviewer_id": REVIEWER_ID,
            "policy": asdict(POLICY), "max_calls": len(frozen["cases"]),
            "max_reserved_output_tokens": len(frozen["cases"]) * POLICY.max_output_tokens,
            "frozen": frozen, "code_fingerprints": fingerprints(),
            "scope": "Independent review only; no new host answers, database writes, learning or delivery.",
            "limits": "Known regression examples plus one authored control; not a held-out or general quality benchmark.",
            "stopping": "No retries. Stop on invalid review, provider failure or unknown usage. Resume is read-only.",
            "expected_labels": "Not sent to the reviewer; runtime verdicts still need separate developer inspection."}


def run(output, report, reviewer, meter):
    frozen = report["plan"]
    if (report["state"] != "started" or report["cases"] or meter.summary()["reserved_calls"]
            or digest(frozen) != report["plan_sha256"] or fingerprints() != frozen["code_fingerprints"]
            or reviewer.fingerprint != report["review_policy_fingerprint"]):
        raise ValueError("A frozen experiment cannot be changed, repeated or continued")

    def persist():
        report["usage"], report["call_ledger"] = meter.summary(), meter.records
        save(output / "report.json", report)

    try:
        for case in frozen["frozen"]["cases"]:
            item = {"name": case["name"], "state": "started", "usage_before": meter.summary()}
            report["cases"].append(item)
            original = case["model_input"]
            if (digest(original) != case["model_input_sha256"]
                    or hashlib.sha256(case["draft"].encode()).hexdigest() != case["draft_sha256"]):
                raise ValueError("Frozen answer or host input changed")
            request = reviewer.prepare(ModelRequest(**(original | {"messages": tuple(original["messages"])})), case["draft"])
            item["review_input"] = asdict(request)
            persist()
            result = reviewer.evaluate(request)
            item.update(state="finished", result=asdict(result), expected_status=case["expected_status"],
                        matches_expected=result.status == case["expected_status"], usage_after=meter.summary())
            persist()
            if meter.summary()["usage_unknown_calls"]:
                raise RuntimeError("Unknown provider usage stops the remaining experiment")
        report["state"] = "finished"
    except Exception as error:
        report.update(state="interrupted", error=safe_error(error))
        if report["cases"] and report["cases"][-1]["state"] == "started":
            report["cases"][-1].update(state="failed", error=safe_error(error))
    finally:
        report["code_unchanged"] = fingerprints() == frozen["code_fingerprints"]
        persist()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".local/deepseek-answer-review"))
    args = parser.parse_args()
    if args.resume:
        report = json.loads((args.output / "report.json").read_text())
    elif args.plan:
        proposed = plan()
        print(json.dumps({key: value for key, value in proposed.items() if key != "frozen"}, ensure_ascii=False, indent=2))
        return 0
    else:
        load_dotenv()
        base_url = "https://api.deepseek.com"
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("Configure DEEPSEEK_API_KEY locally before live evaluation")
        proposed = plan()
        args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        with MeteredTransport(args.output / "calls.jsonl", max_calls=proposed["max_calls"],
                              max_output_tokens=proposed["max_reserved_output_tokens"]) as meter:
            with ChatCompletionsAnswerReviewProvider(base_url, api_key, MODEL, thinking="enabled", transport=meter) as provider:
                reviewer = AnswerReviewer(provider, reviewer_id=REVIEWER_ID, policy=POLICY)
                report = {"version": 1, "kind": "answer_review_regressions", "state": "started",
                          "plan": proposed, "plan_sha256": digest(proposed), "cases": [],
                          "review_policy_fingerprint": reviewer.fingerprint,
                          "developer_review": "pending", "error": None}
                report = run(args.output, report, reviewer, meter)
    print(json.dumps({"state": report["state"], "usage": report.get("usage"),
                      "results": [{"name": item["name"], "state": item["state"],
                                   "matches_expected": item.get("matches_expected")} for item in report["cases"]]},
                     ensure_ascii=False, indent=2))
    return 0 if report["state"] == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
