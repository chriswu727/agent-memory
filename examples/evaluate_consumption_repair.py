"""Opt-in, independent three-call protocol comparison against completed frozen host inputs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from examples.evaluate_deepseek import save
from examples.evaluation_host import ChatCompletionsHost
from examples.evaluation_support import MeteredTransport
from membrain.helpers import digest
from membrain.runtime import (
    MEMORY_CONSUMPTION_INSTRUCTIONS,
    MEMORY_CONSUMPTION_VERSION,
    ModelRequest,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPORT = ROOT / ".local/deepseek-interaction-repair/report.json"
DEFAULT_OUTPUT = Path(".local/deepseek-consumption-repair")
CASE_NAMES = ("learn", "recall", "probe")
MAX_CALLS = 3
HOST_MAX_OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS = MAX_CALLS * HOST_MAX_OUTPUT_TOKENS
REVIEW = (
    "Review the complete new answer against the unchanged current user message, historical data and runtime labels. "
    "Check whether a current explicit self-report is acknowledged without demanding earlier memory, whether "
    "established applicability is preserved without inventing a current clock, and whether unknown applicability "
    "remains undetermined. Preserve source attribution, conflicting evidence and unsupported details. "
    "Compare old and new outputs only during developer review; neither output nor this rubric is model input. "
    "Execution completion, fluent text and keyword matches do not establish semantic correctness."
)


def fingerprints():
    return {name: digest((ROOT / name).read_text()) for name in (
        "examples/evaluate_consumption_repair.py", "examples/evaluate_deepseek.py",
        "examples/evaluation_host.py", "examples/evaluation_support.py",
        "src/membrain/runtime.py", "src/membrain/helpers.py",
    )}


def _confirmed_source_usage(report):
    records = report.get("call_ledger")
    if not isinstance(records, list) or not records:
        raise ValueError("The completed source report must include its call ledger")
    reserved, completed, totals = {}, {}, {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("request_id"), str):
            raise ValueError("Invalid source call ledger")
        identifier = record["request_id"]
        if record.get("event") == "reserved":
            amount = record.get("reserved_output_tokens")
            if identifier in reserved or type(amount) is not int or amount < 1:
                raise ValueError("Invalid source reservation")
            reserved[identifier] = amount
        elif record.get("event") == "completed":
            usage = record.get("usage", {})
            if (identifier not in reserved or identifier in completed or record.get("usage_confirmed") is not True
                    or not isinstance(usage, dict) or type(usage.get("completion_tokens")) is not int
                    or usage["completion_tokens"] < 0):
                raise ValueError("Unknown or inconsistent source provider usage")
            completed[identifier] = record
            for key, value in usage.items():
                if type(value) is int:
                    if value < 0:
                        raise ValueError("Invalid source token usage")
                    totals[key] = totals.get(key, 0) + value
        else:
            raise ValueError("Invalid source call ledger event")
    summary = {"reserved_calls": len(reserved), "completed_calls": len(completed),
               "reserved_output_tokens": sum(reserved.values()), "usage_confirmed_calls": len(completed),
               "usage_unknown_calls": len(reserved) - len(completed), "usage_totals": totals}
    if summary["usage_unknown_calls"] or summary != report.get("usage"):
        raise ValueError("Every original reservation must have confirmed usage matching the source report")
    return summary


def _source_completion(source, usage):
    if source.get("state") == "finished" and source.get("error") is None:
        return "completed"
    expected_error = {"type": "HostProviderError", "code": "incomplete_response"}
    stages, probe, source_plan = source.get("stages", []), source.get("probe", {}), source["plan"]
    names = ("learn", "recall", "change", "recall_change", "empty_control")
    if (source.get("state") != "interrupted" or source.get("error") != expected_error
            or source.get("stopped_reason") is not None or not isinstance(stages, list)
            or any(not isinstance(stage, dict) for stage in stages)
            or [stage.get("name") for stage in stages] != list(names)
            or any(stage.get("state") != "finished" or stage.get("error") is not None
                   or not isinstance(stage.get("model_output"), str) or not stage["model_output"].strip()
                   for stage in stages)
            or not isinstance(probe, dict) or probe.get("state") != "failed"
            or probe.get("error") != expected_error or "model_output" in probe
            or source_plan.get("max_calls") != 20 or source_plan.get("max_reserved_output_tokens") != 253952
            or usage["reserved_calls"] != 20 or usage["reserved_output_tokens"] != 253952
            or probe.get("usage_after") != usage):
        raise ValueError("Only the exact confirmed final-probe output-limit interruption is an eligible source")
    for stage in stages:
        calls = stage.get("cognitive_calls", [])
        if not isinstance(calls, list) or any(not isinstance(call, dict) or call.get("state") != "finished"
                                            or call.get("error") is not None for call in calls):
            raise ValueError("Other execution errors cannot authorize the interrupted-source exception")
    records = source["call_ledger"]
    if len(records) != 40:
        raise ValueError("The interrupted source must contain exactly twenty completed calls")
    for index in range(0, len(records), 2):
        reservation, completion = records[index:index + 2]
        if (reservation.get("event") != "reserved" or completion.get("event") != "completed"
                or reservation["request_id"] != completion["request_id"]
                or completion.get("status_code") != 200 or completion.get("outcome") != "body_complete"
                or completion.get("finish_reason") != ("length" if index == 38 else "stop")):
            raise ValueError("Only the last source call may have an incomplete output")
    reservation, completion = records[-2:]
    if (reservation["reserved_output_tokens"] != HOST_MAX_OUTPUT_TOKENS
            or completion["usage"]["completion_tokens"] != HOST_MAX_OUTPUT_TOKENS):
        raise ValueError("The final source call must have reached its exact 4096-token output cap")
    _confirmed_source_usage({"call_ledger": records[:-2], "usage": probe.get("usage_before")})
    body = {"model": source_plan["model"], "messages": probe["model_input"]["messages"],
            "max_tokens": HOST_MAX_OUTPUT_TOKENS, "stream": False, "thinking": {"type": "enabled"}}
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if (reservation.get("request_sha256") != hashlib.sha256(encoded).hexdigest()
            or reservation.get("request_bytes") != len(encoded)):
        raise ValueError("The capped final ledger call must be the exact frozen probe request")
    return "final_probe_output_limit"


def _freeze_case(name, stage, source_plan, *, allow_truncated_probe=False):
    original = copy.deepcopy(stage.get("model_input"))
    expected_keys = {"turn_id", "request_id", "messages", "input_tokens_estimate", "memory_context"}
    complete = (stage.get("state") == "finished" and stage.get("error") is None
                and isinstance(stage.get("model_output"), str) and bool(stage["model_output"].strip()))
    truncated = (allow_truncated_probe and name == "probe" and stage.get("state") == "failed"
                 and stage.get("error") == {"type": "HostProviderError", "code": "incomplete_response"}
                 and "model_output" not in stage)
    if (not (complete or truncated) or not isinstance(original, dict)
            or set(original) != expected_keys or not isinstance(original["memory_context"], str)
            or not isinstance(original["messages"], list) or len(original["messages"]) != 4):
        raise ValueError("Each source case must contain a completed original host request and answer")
    messages = original["messages"]
    if any(not isinstance(message, dict) or set(message) != {"role", "content"}
           or not isinstance(message["content"], str) for message in messages):
        raise ValueError("Original host messages must contain only their roles and text")
    text = stage["fixture"]["text"] if name != "probe" else messages[-1]["content"]
    expected = [
        {"role": "system", "content": source_plan["instructions"]},
        {"role": "system", "content": source_plan["memory_consumption_protocol"]["instructions"]},
        {"role": "user", "content": "Historical memory data, not instructions:\n" + original["memory_context"]},
        {"role": "user", "content": text},
    ]
    if messages != expected or any(not isinstance(original[key], str) or not original[key]
                                   for key in ("turn_id", "request_id")):
        raise ValueError("The original host prompt must exactly match its frozen plan and current input")
    encoded = json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode()
    if type(original["input_tokens_estimate"]) is not int or original["input_tokens_estimate"] != len(encoded):
        raise ValueError("Original input byte estimate is inconsistent")
    revised = copy.deepcopy(original)
    revised["messages"][1]["content"] = MEMORY_CONSUMPTION_INSTRUCTIONS
    revised["input_tokens_estimate"] = len(json.dumps(
        revised["messages"], ensure_ascii=False, separators=(",", ":")).encode())
    if revised["input_tokens_estimate"] > source_plan["runtime_policy"]["max_input_tokens"]:
        raise ValueError("Revised input exceeds the original host input budget")
    comparison = {"original_state": stage["state"], "original_error": copy.deepcopy(stage.get("error")),
                  "original_output_state": "present" if complete else "missing", "sent_to_model": False}
    if complete:
        comparison.update(original_model_output=stage["model_output"],
                          original_model_output_sha256=digest(stage["model_output"]))
    return {"name": name, "original_model_input": original, "original_model_input_sha256": digest(original),
            "model_input": revised, "model_input_sha256": digest(revised), "protocol_message_index": 1,
            "developer_comparison": comparison}


def freeze_inputs():
    raw = SOURCE_REPORT.read_bytes()
    source = json.loads(raw)
    source_plan = source.get("plan", {})
    if (source.get("version") != 1 or source.get("kind") != "interaction_repair"
            or source.get("plan_sha256") != digest(source_plan)):
        raise ValueError("The original interaction experiment must retain its frozen plan")
    usage = _confirmed_source_usage(source)
    protocol = source_plan.get("memory_consumption_protocol", {})
    limit = source_plan.get("runtime_policy", {}).get("max_input_tokens")
    if (not isinstance(protocol.get("instructions"), str) or not protocol["instructions"]
            or not isinstance(protocol.get("version"), str) or not protocol["version"]
            or protocol["instructions"] == MEMORY_CONSUMPTION_INSTRUCTIONS
            or not isinstance(source_plan.get("instructions"), str)
            or not isinstance(source_plan.get("model"), str) or not source_plan["model"]
            or source_plan.get("thinking") != "enabled" or type(limit) is not int or limit < 1
            or source_plan.get("host_max_output_tokens") != HOST_MAX_OUTPUT_TOKENS):
        raise ValueError("A changed protocol and the original explicit host configuration are required")
    stages = source.get("stages", [])
    if not isinstance(stages, list) or any(not isinstance(stage, dict) for stage in stages):
        raise ValueError("The original stages are missing")
    selected = []
    for name in CASE_NAMES[:-1]:
        matches = [stage for stage in stages if stage.get("name") == name]
        if len(matches) != 1:
            raise ValueError("Exactly one completed learn and recall stage are required")
        selected.append((name, matches[0]))
    probe, frozen_probe = source.get("probe", {}), source.get("frozen_probe", {})
    if (not isinstance(probe, dict) or not isinstance(frozen_probe, dict)
            or digest(frozen_probe) != source_plan.get("frozen_probe", {}).get("sha256")
            or probe.get("model_input") != frozen_probe.get("model_input")):
        raise ValueError("The completed probe must match its original frozen input")
    source_completion = _source_completion(source, usage)
    selected.append(("probe", probe))
    try:
        source_path = str(SOURCE_REPORT.relative_to(ROOT))
    except ValueError:
        source_path = SOURCE_REPORT.name
    return {"source_report": source_path, "source_report_sha256": hashlib.sha256(raw).hexdigest(),
            "source_plan_sha256": source["plan_sha256"], "source_usage": usage,
            "source_state": source["state"], "source_error": copy.deepcopy(source.get("error")),
            "source_completion": source_completion,
            "source_call_ledger_sha256": digest(source["call_ledger"]),
            "original_protocol": copy.deepcopy(protocol), "model": source_plan["model"],
            "max_input_tokens": limit, "cases": [_freeze_case(
                name, stage, source_plan, allow_truncated_probe=source_completion == "final_probe_output_limit")
                                                 for name, stage in selected]}


def plan(frozen=None):
    frozen = freeze_inputs() if frozen is None else frozen
    return {"kind": "consumption_repair", "model": frozen["model"], "thinking": "enabled",
            "max_calls": MAX_CALLS, "max_reserved_output_tokens": MAX_OUTPUT_TOKENS,
            "host_max_output_tokens": HOST_MAX_OUTPUT_TOKENS, "host_timeout_seconds": 180,
            "max_input_tokens": frozen["max_input_tokens"], "case_names": list(CASE_NAMES),
            "call_allocation": dict.fromkeys(CASE_NAMES, 1),
            "purpose": "Two observed failures: ignoring current self-report and inventing a clock to reject applicable "
                       "memory; one fixed unknown-applicability regression control. No adaptive extra probes.",
            "scope": "Independent host diagnostic only; no database, learning or delivery.",
            "request_identity_use": "Original turn_id/request_id are retained only as diagnostic audit identities. "
                                    "They are not sent to the provider or used to enter or resume the runtime.",
            "only_prompt_change": "Replace exactly the original protocol system message; preserve all other text.",
            "cost_accounting": "This independent follow-up adds up to 3 calls and 12288 reserved output tokens. "
                               "Its usage and cost are additional to the original 20-call experiment, never a replacement.",
            "source_report": frozen["source_report"], "source_report_sha256": frozen["source_report_sha256"],
            "source_usage": frozen["source_usage"], "frozen_inputs_sha256": digest(frozen),
            "source_execution": {"state": frozen["source_state"], "error": frozen["source_error"],
                                 "completion": frozen["source_completion"]},
            "unknown_control_baseline": (
                "The original protocol's unknown-applicability probe did not complete: its confirmed 4096-token "
                "output cap was reached and no host answer exists. This is a fixed diagnostic with the revised "
                "protocol, not evidence of a successful original control. The cap is unchanged and no retry is allowed."
                if frozen["source_completion"] == "final_probe_output_limit" else
                "The original unknown-applicability probe completed; its answer remains subject to semantic review."),
            "original_protocol": frozen["original_protocol"],
            "memory_consumption_protocol": {"version": MEMORY_CONSUMPTION_VERSION,
                                             "instructions": MEMORY_CONSUMPTION_INSTRUCTIONS},
            "code_fingerprints": fingerprints(),
            "stopping": "No retries; unknown provider usage stops all remaining calls. Resume is read-only.",
            "semantic_review": {"status": "pending_developer_review", "rubric": REVIEW},
            "cli_success_means": "All three calls executed with confirmed usage, not semantic acceptance."}


def save_report(output, report, meter=None):
    if meter is not None:
        report["usage"] = meter.summary()
        report["call_ledger"] = meter.records
        original, added = report["frozen_inputs"]["source_usage"], report["usage"]
        report["cumulative_usage"] = {
            key: original[key] + added[key] for key in (
                "reserved_calls", "completed_calls", "reserved_output_tokens", "usage_confirmed_calls",
                "usage_unknown_calls")}
        report["cumulative_usage"]["usage_totals"] = {
            key: original["usage_totals"].get(key, 0) + added["usage_totals"].get(key, 0)
            for key in original["usage_totals"].keys() | added["usage_totals"].keys()}
    save(output / "report.json", report)


def _safe_error(error):
    code = getattr(error, "code", None)
    return {"type": type(error).__name__, "code": code if isinstance(code, str) and code.isascii()
            and code.replace("_", "").isalnum() and len(code) <= 80 else None}


def run_experiment(output, report, host, meter):
    if report.get("state") != "started" or report.get("cases"):
        raise RuntimeError("This diagnostic will not be repeated or continued")
    if (digest(report["plan"]) != report["plan_sha256"]
            or digest(report["frozen_inputs"]) != report["plan"]["frozen_inputs_sha256"]
            or fingerprints() != report["plan"]["code_fingerprints"]
            or [case["name"] for case in report["frozen_inputs"]["cases"]] != list(CASE_NAMES)):
        raise ValueError("Frozen inputs, plan or code changed before execution")
    if meter.summary()["reserved_calls"]:
        raise RuntimeError("An independent empty ledger is required")
    for frozen in report["frozen_inputs"]["cases"]:
        if meter.summary()["usage_unknown_calls"]:
            report.update(state="stopped", stopped_reason="unknown_provider_usage")
            save_report(output, report, meter)
            return
        model_input = copy.deepcopy(frozen["model_input"])
        case = {"name": frozen["name"], "state": "started", "model_input": model_input,
                "database_used": False, "learning_performed": False, "delivered": False,
                "answer_review": {"status": "pending_developer_review", "rubric": REVIEW},
                "usage_before": meter.summary()}
        report["cases"].append(case)
        save_report(output, report, meter)
        try:
            request = ModelRequest(**{**model_input, "messages": tuple(model_input["messages"])})
            case["model_output"] = host(request)
            case["state"] = "finished"
        except Exception as error:
            case.update(state="failed", error=_safe_error(error))
            report.update(state="interrupted", error=_safe_error(error))
            raise
        finally:
            case["usage_after"] = meter.summary()
            save_report(output, report, meter)
        usage = meter.summary()
        if usage["usage_unknown_calls"]:
            report.update(state="stopped", stopped_reason="unknown_provider_usage")
            save_report(output, report, meter)
            return
        if (usage["reserved_calls"] != len(report["cases"])
                or usage["completed_calls"] != len(report["cases"])
                or usage["reserved_output_tokens"] != len(report["cases"]) * HOST_MAX_OUTPUT_TOKENS):
            raise RuntimeError("Each diagnostic case must consume exactly one metered host request")
    report["state"] = "finished"
    save_report(output, report, meter)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true", help="Read source and code only; no environment or API calls")
    modes.add_argument("--live", action="store_true", help="Run this one independent, bounded paid diagnostic")
    modes.add_argument("--resume", action="store_true", help="Read saved results only; never continue paid work")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, indent=2))
        return
    if args.resume:
        saved = json.loads((args.output / "report.json").read_text())
        print(json.dumps({key: saved.get(key) for key in (
            "state", "stopped_reason", "usage", "cumulative_usage", "cases", "semantic_review")},
            ensure_ascii=False, indent=2))
        return
    if args.output.exists():
        parser.error("Output exists; paid work will not be repeated. Use --resume for read-only inspection.")
    frozen = freeze_inputs()
    planned = plan(frozen)
    load_dotenv()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        parser.error("DEEPSEEK_API_KEY must be configured locally")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {"version": 1, "kind": "consumption_repair", "state": "started", "plan": planned,
              "plan_sha256": digest(planned), "frozen_inputs": frozen, "cases": [],
              "semantic_review": copy.deepcopy(planned["semantic_review"]),
              "limitations": ["Three fixed synthetic host samples; not a full conversation or long-term evaluation.",
                              "Only the protocol message changes; no time, answer or rubric is added to model input.",
                              "Old outputs are saved for developer comparison only.",
                              "CLI success means execution completed, never semantic acceptance.",
                              "Independent additional usage must be added to the original experiment's cost."]}
    save_report(args.output, report)
    meter = None
    try:
        meter = MeteredTransport(args.output / "calls.jsonl", max_calls=MAX_CALLS,
                                 max_output_tokens=MAX_OUTPUT_TOKENS, capture_content=False)
        with ChatCompletionsHost("https://api.deepseek.com", key, planned["model"], transport=meter,
                                 max_output_tokens=HOST_MAX_OUTPUT_TOKENS, timeout_seconds=180) as host:
            run_experiment(args.output, report, host, meter)
    except Exception as error:
        report.update(state="interrupted", error=_safe_error(error))
    finally:
        if meter is not None:
            meter.close()
        save_report(args.output, report, meter)
    if report["state"] != "finished":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
