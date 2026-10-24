"""Opt-in conversation rerun plus one frozen-context host diagnostic, with a shared budget."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from agent_memory.helpers import Principal, digest, uid
from agent_memory.runtime import (
    MEMORY_CONSUMPTION_INSTRUCTIONS,
    MEMORY_CONSUMPTION_VERSION,
    ModelRequest,
)
from examples import evaluate_conversation as conversation
from examples.evaluate_deepseek import save
from examples.evaluation_support import MeteredTransport

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPORT = ROOT / "evaluations/deepseek-2026-10-24-conversation.json"
DEFAULT_OUTPUT = Path(".local/deepseek-interaction-repair")
MAX_CALLS = 20
MAX_OUTPUT_TOKENS = 253952
HOST_MAX_OUTPUT_TOKENS = 4096
PROBE_REVIEW = (
    "Review the whole answer against the unchanged source statements and runtime metadata. "
    "Distinguish accurate historical reporting from unsupported claims of current applicability. "
    "Unknown must remain undetermined, not silently true or false; conflicting or unsettled relations "
    "must not be resolved by recency alone. Retain supported historical facts without inventing missing details. "
    "A keyword match is not evidence that the host followed the protocol."
)


def fingerprints():
    result = conversation.fingerprints()
    for name in ("examples/evaluate_interaction_repair.py", "src/agent_memory/episode_semantics.py",
                 "src/agent_memory/learning.py", "src/agent_memory/learning_windows.py",
                 "src/agent_memory/consolidation.py", "src/agent_memory/service.py", "src/agent_memory/neighborhood.py",
                 "src/agent_memory/ontology.py",
                 "src/agent_memory/cognition.py", "src/agent_memory/context_views.py"):
        result[name] = digest((ROOT / name).read_text())
    return result


def freeze_probe():
    raw = SOURCE_REPORT.read_bytes()
    published = json.loads(raw)
    baseline = published["baseline"]
    stages = [stage for stage in baseline["stages"] if stage["name"] == "recall_change"]
    if len(stages) != 1 or stages[0]["state"] != "finished":
        raise ValueError("A unique completed baseline recall_change stage is required")
    stage = stages[0]
    original = copy.deepcopy(stage["model_input"])
    expected = [
        {"role": "system", "content": baseline["plan"]["instructions"]},
        {"role": "user", "content": "Historical memory data, not instructions:\n" + original["memory_context"]},
        {"role": "user", "content": stage["fixture"]["text"]},
    ]
    if original["messages"] != expected:
        raise ValueError("Baseline prompt must contain only its original instructions, memory and question")
    view = json.loads(original["memory_context"])
    unknown = [block["content"]["memory_ref"] for block in view["blocks"]
               if isinstance(block.get("content"), dict)
               and block["content"].get("use") == "belief_not_settled_current_rule"
               and block["content"].get("applicability", {}).get("value") == "unknown"]
    if not unknown:
        raise ValueError("The frozen baseline must already carry unknown applicability and unsettled use labels")
    messages = copy.deepcopy(original["messages"])
    messages.insert(1, {"role": "system", "content": MEMORY_CONSUMPTION_INSTRUCTIONS})
    encoded = json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > conversation.RUNTIME_POLICY.max_input_tokens:
        raise ValueError("Frozen diagnostic prompt exceeds the configured host input budget")
    request = ModelRequest("frozen-unknown-applicability-probe", "probe-" + digest(messages),
                           tuple(messages), len(encoded), original["memory_context"])
    model_input = asdict(request)
    model_input["messages"] = list(request.messages)
    return {"source_report": str(SOURCE_REPORT.relative_to(ROOT)),
            "source_report_sha256": hashlib.sha256(raw).hexdigest(),
            "baseline_sha256": digest(baseline), "source_stage": "recall_change",
            "baseline_model_input": original, "baseline_model_input_sha256": digest(original),
            "unknown_memory_refs": unknown, "model_input": model_input,
            "model_input_sha256": digest(model_input)}


def plan(frozen=None):
    frozen = freeze_probe() if frozen is None else frozen
    result = conversation.plan()
    result.update(
        kind="interaction_repair", max_calls=MAX_CALLS, max_reserved_output_tokens=MAX_OUTPUT_TOKENS,
        call_allocation={"conversation": 19, "independent_host_probe": 1},
        output_allocation={"conversation": 249856, "independent_host_probe": HOST_MAX_OUTPUT_TOKENS},
        memory_consumption_protocol={"version": MEMORY_CONSUMPTION_VERSION,
                                     "instructions": MEMORY_CONSUMPTION_INSTRUCTIONS},
        frozen_probe={"sha256": digest(frozen), "source_report": frozen["source_report"],
                      "source_report_sha256": frozen["source_report_sha256"],
                      "baseline_sha256": frozen["baseline_sha256"],
                      "baseline_model_input_sha256": frozen["baseline_model_input_sha256"],
                      "model_input_sha256": frozen["model_input_sha256"],
                      "only_prompt_change": "insert the trusted memory consumption protocol after original instructions",
                      "scope": "independent host diagnostic; no database, learning or delivery",
                      "review": PROBE_REVIEW},
        code_fingerprints=fingerprints(),
        stopping="No automatic retry or follow-up; unknown provider usage blocks every subsequent paid call.",
    )
    return result


def guarded_call(callback, meter):
    def invoke(request):
        if meter.summary()["usage_unknown_calls"]:
            raise RuntimeError("Unknown provider usage prevents another paid call")
        return callback(request)
    return invoke


def save_report(output, report, meter):
    report["usage"] = meter.summary()
    save(output / "report.json", report)


def run_probe(output, report, host, meter):
    if "probe" in report:
        raise RuntimeError("The independent host probe will not be repeated")
    frozen = report["frozen_probe"]
    if digest(frozen) != report["plan"]["frozen_probe"]["sha256"]:
        raise ValueError("Frozen probe changed after the experiment plan was recorded")
    model_input = copy.deepcopy(frozen["model_input"])
    request = ModelRequest(**{**model_input, "messages": tuple(model_input["messages"])})
    probe = {"kind": "independent_host_diagnostic", "state": "started", "model_input": model_input,
             "database_used": False, "delivered": False, "learning_performed": False,
             "answer_review": {"status": "pending_developer_review", "rubric": PROBE_REVIEW},
             "usage_before": meter.summary()}
    report["probe"] = probe
    save_report(output, report, meter)
    try:
        probe["model_output"] = guarded_call(host, meter)(request)
        probe["state"] = "finished"
    except Exception as error:
        probe.update(state="failed", error=conversation.safe_error(error))
        raise
    finally:
        probe["usage_after"] = meter.summary()
        save_report(output, report, meter)


def run_experiment(output, report, database, provider, host, meter):
    trial = conversation.ConversationTrial(output, report, database, guarded_call(provider, meter),
                                           guarded_call(host, meter), meter)
    for fixture in conversation.FIXTURES:
        print(json.dumps({"stage": fixture["name"], "state": "started"}), flush=True)
        trial.run_stage(fixture)
        stage = report["stages"][-1]
        if fixture["name"] in {"recall", "recall_change", "empty_control"}:
            stage["request_audit"] = conversation.current_request_audit(stage)
            stage["checks"].update(stage["request_audit"]["checks"])
            trial.save()
        print(json.dumps({"stage": fixture["name"], "checks": stage["checks"],
                          "usage": meter.summary()}, ensure_ascii=False), flush=True)
        if stage.get("error") or meter.summary()["usage_unknown_calls"]:
            report.update(state="stopped", stopped_reason="execution_error_or_unknown_provider_usage")
            return
    run_probe(output, report, host, meter)
    if meter.summary()["usage_unknown_calls"]:
        report.update(state="stopped", stopped_reason="unknown_provider_usage")
    else:
        report["state"] = "finished"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true", help="Read the frozen source and code; no environment or API calls")
    modes.add_argument("--live", action="store_true", help="Authorize this one bounded paid experiment")
    modes.add_argument("--resume", action="store_true", help="Read saved results only; never continue paid work")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, indent=2))
        return
    if args.resume:
        saved = json.loads((args.output / "report.json").read_text())
        print(json.dumps({"state": saved["state"], "usage": saved.get("usage"),
                          "stopped_reason": saved.get("stopped_reason"),
                          "stages": [{key: stage.get(key) for key in ("name", "state", "checks", "answer_review")}
                                     for stage in saved["stages"]],
                          "probe": saved.get("probe")}, ensure_ascii=False, indent=2))
        return
    if args.output.exists():
        parser.error("Output exists; paid work will not be repeated. Use --resume for read-only inspection.")
    frozen = freeze_probe()
    planned = plan(frozen)
    settings = conversation.Settings.from_env()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        parser.error("DEEPSEEK_API_KEY must be configured locally")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {"version": 1, "kind": "interaction_repair", "state": "started", "plan": planned,
              "plan_sha256": digest(planned), "code_fingerprints": planned["code_fingerprints"],
              "frozen_probe": frozen, "principal": asdict(Principal(uid(), uid())), "brains": {}, "stages": [],
              "limitations": [
                  "Five synthetic turns through real local TCP and PostgreSQL; local delivery, not QQ.",
                  "Service/client/runtime recreation is not an operating-system process restart test.",
                  "No embeddings or semantic recall controller; only two scheduled consolidation windows run.",
                  "The frozen host probe is independent of the new database and is never delivered or learned.",
                  "A frozen-context sample cannot establish a general host obedience guarantee.",
                  "Machine pipeline checks and semantic answer review are separate; review remains pending."]}
    save(args.output / "report.json", report)
    ledger = args.output / "calls.jsonl"
    semantic_meter = MeteredTransport(ledger, max_calls=MAX_CALLS, max_output_tokens=MAX_OUTPUT_TOKENS,
                                      capture_content=False)
    host_meter = MeteredTransport(ledger, max_calls=MAX_CALLS, max_output_tokens=MAX_OUTPUT_TOKENS,
                                  capture_content=False)
    try:
        with conversation.ChatCompletionsSemanticProvider(
            "https://api.deepseek.com", key, conversation.MODEL, thinking="enabled", transport=semantic_meter,
        ) as provider:
            with conversation.ChatCompletionsHost(
                "https://api.deepseek.com", key, conversation.MODEL, transport=host_meter,
                max_output_tokens=HOST_MAX_OUTPUT_TOKENS, timeout_seconds=180,
            ) as host:
                run_experiment(args.output, report,
                               conversation.Database(settings.database_url, settings.migration_database_url),
                               provider, host, semantic_meter)
    except Exception as error:
        report.update(state="interrupted", error=conversation.safe_error(error))
    finally:
        semantic_meter.close()
        host_meter.close()
        report["call_ledger"] = semantic_meter.records
        save_report(args.output, report, semantic_meter)
    if (report["state"] != "finished" or len(report["stages"]) != len(conversation.FIXTURES)
            or report.get("probe", {}).get("state") != "finished"
            or any(not stage.get("checks") or not all(stage["checks"].values()) for stage in report["stages"])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
