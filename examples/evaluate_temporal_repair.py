"""One explicit two-call diagnostic using an existing conversation evaluation's ledger."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from examples.evaluate_conversation import FIXTURES, MODEL, plan, safe_error
from examples.evaluate_deepseek import save
from examples.evaluation_support import MeteredTransport
from membrain import cognition
from membrain.contracts import validate
from membrain.helpers import digest
from membrain.semantic import (
    _PROPOSAL_VALIDATOR,
    _VERIFICATION_VALIDATOR,
    PROPOSAL_SCHEMA,
    CognitiveBatch,
    SemanticPolicy,
    SemanticProcessor,
    _parse,
)
from membrain.semantic_provider import ChatCompletionsSemanticProvider
from membrain.temporal import evaluate_valid_during

BASE_CALLS = 18
BASE_RESERVED_OUTPUT = 233472
MAX_CALLS = 20
MAX_RESERVED_OUTPUT = 249856
POLICY = SemanticPolicy(max_model_calls=2, max_output_tokens=8192,
                        max_response_bytes=65536, max_wall_time_ms=240000)
DEFAULT_OUTPUT = Path(".local/deepseek-conversation")
FOLLOWUP_NAME = "temporal-followup.json"
EXPECTED_START = "2026-10-20T00:00:00Z"
PROBE_TIMES = ("2026-10-19T23:59:59Z", EXPECTED_START, "2026-10-21T00:00:00Z")


def fingerprints():
    root = Path(__file__).resolve().parents[1]
    return {name: digest((root / name).read_text()) for name in (
        "examples/evaluate_temporal_repair.py", "examples/evaluation_support.py",
        "src/membrain/semantic.py", "src/membrain/semantic_provider.py",
        "src/membrain/temporal.py",
    )}


def load_baseline(directory):
    path = directory / "report.json"
    raw = path.read_bytes()
    baseline = json.loads(raw)
    stages = baseline.get("stages", [])
    if (baseline.get("version") != 1 or baseline.get("state") != "finished"
            or baseline.get("plan") != plan() or baseline.get("plan_sha256") != digest(plan())
            or len(stages) != len(FIXTURES)
            or any(stage.get("fixture") != fixture or stage.get("name") != fixture["name"]
                   or stage.get("state") != "finished" for stage, fixture in zip(stages, FIXTURES))):
        raise ValueError("The original finished five-stage synthetic plan must be unchanged")
    sources = stages[0].get("sources", {}).get("input_events", [])
    if len(sources) != 1:
        raise ValueError("Exactly one original learn-stage user source is required")
    source = copy.deepcopy(sources[0])
    validate("Event", source)
    observed = source["observed"]
    if (source["revision"] != 1 or observed["source_revision"] != 1
            or observed["actor_role"] != "user" or observed["delivery_state"] != "received"
            or observed["content"] != {"media_type": "text/plain", "text": FIXTURES[0]["text"]}
            or source["content_sha256"] != digest(observed["content"])
            or stages[0].get("runtime", {}).get("input_event_refs") != [
                {"event_id": source["event_id"], "revision": source["revision"]}]
            or source["scope"]["brain_id"] != stages[0]["brain_id"]):
        raise ValueError("The source must be the exact original received user event and version")
    calls = stages[0].get("cognitive_calls", [])
    proposals = [call["response"] for call in calls
                 if call.get("role") == "consolidate" and call.get("phase") == "proposal"
                 and call.get("schema_valid") is True]
    if len(proposals) != 1:
        raise ValueError("The original consolidation proposal is required")
    candidates = [claim for claim in proposals[0].get("claims", [])
                  if claim.get("claim_kind") == "reported_preference"
                  and "valid_during" not in claim and claim.get("evidence_refs")
                  and all(ref.get("event_id") == source["event_id"]
                          and ref.get("event_revision") == source["revision"]
                          and ref.get("source_content_sha256") == source["content_sha256"]
                          for ref in claim["evidence_refs"])]
    if len(candidates) != 1:
        raise ValueError("Expected the original preference proposal missing typed validity")
    original = candidates[0]
    stored = [item for item in stages[0].get("after", {}).get("documents", {}).get("memories", [])
              if item.get("statement") == original["statement"]
              and item.get("evidence_refs") == original["evidence_refs"]
              and item.get("epistemic_status") == "derived_inference"
              and item.get("valid_during") == {"kind": "unknown"}]
    if len(stored) != 1:
        raise ValueError("The original stored unknown-validity assertion must be preserved")
    data = {"scope": copy.deepcopy(source["scope"]), "sources": [source]}
    frozen = {"batch_id": "temporal-followup-" + digest(data), **data}
    return baseline, {
        "baseline_report": str(path), "baseline_report_sha256": hashlib.sha256(raw).hexdigest(),
        "baseline_plan_sha256": baseline["plan_sha256"],
        "baseline_target": {"proposal": copy.deepcopy(original), "stored_memory": copy.deepcopy(stored[0])},
        "batch": frozen, "batch_sha256": digest(frozen),
    }


def batch_from_saved(frozen):
    data = copy.deepcopy(frozen["batch"])
    if digest(data) != frozen["batch_sha256"] or len(data["sources"]) != 1:
        raise ValueError("The frozen diagnostic source was modified")
    source = data["sources"][0]
    validate("Event", source)
    if (source["observed"]["content"] != {"media_type": "text/plain", "text": FIXTURES[0]["text"]}
            or source["content_sha256"] != digest(source["observed"]["content"])
            or data["scope"] != source["scope"]):
        raise ValueError("The frozen diagnostic source is inconsistent")
    return CognitiveBatch(data["batch_id"], data["scope"], tuple(data["sources"]))


def validate_ledger(meter, baseline):
    usage = meter.summary()
    if (usage != baseline.get("usage") or usage["reserved_calls"] != BASE_CALLS
            or usage["completed_calls"] != BASE_CALLS
            or usage["usage_confirmed_calls"] != BASE_CALLS or usage["usage_unknown_calls"] != 0
            or usage["reserved_output_tokens"] != BASE_RESERVED_OUTPUT
            or usage["reserved_calls"] + POLICY.max_model_calls > MAX_CALLS
            or usage["reserved_output_tokens"] + POLICY.max_model_calls * POLICY.max_output_tokens > MAX_RESERVED_OUTPUT):
        raise ValueError("The shared ledger must exactly match the original eighteen confirmed calls")
    return usage, meter.records


def _exclusive_save(path, value):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_supports(refs, source, scope):
    if not refs or any(ref.get("relationship") != "supports"
                       or ref.get("event_id") != source["event_id"]
                       or ref.get("event_revision") != source["revision"]
                       or ref.get("source_content_sha256") != source["content_sha256"]
                       or not cognition._valid_ref(ref, {source["event_id"]: source}, scope) for ref in refs):
        return False
    spans = [ref.get("span", {}) for ref in refs]
    segments = cognition._segments(source)
    return all(any(span.get("start", -1) <= segment["span"]["start"]
                   and span.get("end", -1) >= segment["span"]["end"] for span in spans)
               for segment in segments)


def temporal_review(batch, result, responses, verification_input):
    source = batch.sources[0]
    originals = {item["proposal_id"]: item for item in responses.get("proposal", {}).get("claims", [])}
    assessments = {item["proposal_id"]: item for item in responses.get("verification", {}).get("assessments", [])}
    verified = {entry["proposal"]["proposal_id"]: entry["proposal"]
                for entry in verification_input.get("proposals", []) if entry.get("group") == "claims"}
    targets = []
    for claim in result.claims:
        if claim.get("claim_kind") != "reported_preference":
            continue
        original = originals.get(claim["proposal_id"], {})
        assessment = assessments.get(claim["proposal_id"], {})
        interval = claim.get("valid_during", {})
        boundary, probes = False, []
        try:
            boundary = (interval.get("kind") == "interval"
                        and interval["start"].get("kind") == "instant"
                        and datetime.fromisoformat(interval["start"]["at"].replace("Z", "+00:00"))
                        == datetime.fromisoformat(EXPECTED_START.replace("Z", "+00:00"))
                        and interval["end"] == {"kind": "unbounded"} and "anchor" not in interval)
            probes = [{"at": at, **evaluate_valid_during(interval, at=at).to_dict()} for at in PROBE_TIMES]
        except (ValueError, TypeError, KeyError):
            pass
        refs = claim.get("evidence_refs", [])
        relations = assessment.get("relations", [])
        checks = {
            "exact_original_source_support": _source_supports(refs, source, batch.scope),
            "typed_interval_matches_explicit_source": boundary,
            "not_yet_valid_before_boundary": len(probes) == 3 and probes[0]["value"] == "false",
            "valid_at_and_after_boundary": len(probes) == 3 and all(probe["value"] == "true" for probe in probes[1:]),
            "whole_predicate_independently_supported": assessment.get("proposal_verdict") == "supports"
            and assessment.get("link_verdict") is None,
            "every_cited_premise_supported": len(relations) == len(refs)
            and {item.get("evidence_index") for item in relations} == set(range(len(refs)))
            and all(item.get("verdict") == "supports" for item in relations),
            "accepted_text_and_time_unchanged": bool(original)
            and claim.get("statement") == original.get("statement")
            and interval == original.get("valid_during")
            and refs == original.get("evidence_refs"),
            "verifier_received_same_complete_proposal": original == verified.get(claim["proposal_id"]),
            "scope_and_qualifiers_preserved": claim.get("world") == "real_world"
            and claim.get("conditions") == {"op": "always"}
            and not claim.get("counterexamples") and not claim.get("source_memory_versions"),
            "accepted_as_verified_inference": claim.get("epistemic_status") == "derived_inference"
            and claim.get("verification") == {"state": "supported_inference", "method": "semantic_model",
                                              "not_a_truth_certificate": True},
        }
        targets.append({"claim": copy.deepcopy(claim), "checks": checks, "temporal_probes": probes})
    return {
        "checks": {"verified_typed_preference_accepted": bool(targets)
                   and all(all(target["checks"].values()) for target in targets),
                   "model_calls_within_two_call_cap": 0 <= result.model_calls <= 2},
        "processor_status": result.status, "rejected": list(result.rejected),
        "accepted_temporal_claims": targets,
        "semantic_quality": "requires_developer_review_of_entire_claim_against_original_source",
        "developer_review": {"state": "pending"},
        "review_questions": ["Does the complete accepted preference preserve the user's actual arrangement, name and order?",
                             "Are its temporal bounds and all qualifiers grounded, without invented details?"],
    }


def run_live(directory):
    path, ledger_path = directory / FOLLOWUP_NAME, directory / "calls.jsonl"
    if path.exists() or not ledger_path.is_file():
        raise ValueError("A diagnostic already exists or its original ledger is missing; do not repeat")
    baseline, frozen = load_baseline(directory)
    if "valid_during" not in PROPOSAL_SCHEMA["properties"]["claims"]["items"]["required"]:
        raise ValueError("Typed validity must be required before spending diagnostic calls")
    batch = batch_from_saved(frozen)
    meter = MeteredTransport(ledger_path, max_calls=MAX_CALLS, max_output_tokens=MAX_RESERVED_OUTPUT,
                             capture_content=False)
    try:
        usage, ledger = validate_ledger(meter, baseline)
        report = {"version": 1, "kind": "standalone_temporal_processor_without_publication", "state": "started",
                  "synthetic_only": True, "model": MODEL, "thinking": "enabled", "policy": asdict(POLICY),
                  **frozen, "code_fingerprints": fingerprints(), "usage_before": usage,
                  "responses": {}, "reservations": [], "verification_input": {},
                  "maximum_attempts": 1,
                  "limitations": ["A single original source is reprocessed without database publication or a host answer.",
                                  "The original conversation failures and stored unknown validity remain unchanged.",
                                  "Machine checks and semantic-model verification still require developer review.",
                                  "No desired conclusion or expected interval is supplied to the processor."]}
        _exclusive_save(path, report)
    except BaseException:
        meter.close()
        raise
    try:
        load_dotenv()
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY must be configured locally")
        with ChatCompletionsSemanticProvider("https://api.deepseek.com", key, MODEL,
                                             thinking="enabled", transport=meter) as provider:
            def record(request):
                if request.phase == "verification":
                    report["verification_input"] = json.loads(request.messages[-1]["content"])
                    save(path, report)
                raw = provider(request)
                validator = _PROPOSAL_VALIDATOR if request.phase == "proposal" else _VERIFICATION_VALIDATOR
                try:
                    report["responses"][request.phase] = _parse(raw, validator, POLICY.max_response_bytes)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    report.setdefault("invalid_response_phases", []).append(request.phase)
                save(path, report)
                return raw

            def reserve(**entry):
                current = meter.summary()
                expected_calls = BASE_CALLS + len(report["reservations"])
                if (current["reserved_calls"] != expected_calls or current["completed_calls"] != expected_calls
                        or current["usage_unknown_calls"] != 0
                        or meter.records[:len(ledger)] != ledger):
                    raise ValueError("The shared ledger changed or has unconfirmed usage; refusing another call")
                report["reservations"].append(entry)
                save(path, report)

            result = SemanticProcessor(record, record, POLICY).process(batch, reserve=reserve)
            report["result"] = asdict(result)
            report["review"] = temporal_review(batch, result, report["responses"], report["verification_input"])
            report["state"] = "finished"
    except BaseException as error:
        report["state"] = "interrupted"
        report["execution_error"] = safe_error(error)
        if not isinstance(error, Exception):
            raise
    finally:
        meter.close()
        report["usage_after"] = meter.summary()
        report["call_ledger"] = meter.records
        report["integrity_checks"] = {
            "original_report_unchanged": hashlib.sha256((directory / "report.json").read_bytes()).hexdigest()
            == frozen["baseline_report_sha256"],
            "original_ledger_records_unchanged": report["call_ledger"][:len(ledger)] == ledger,
            "within_cumulative_call_cap": report["usage_after"]["reserved_calls"] <= MAX_CALLS,
            "within_cumulative_output_cap": report["usage_after"]["reserved_output_tokens"] <= MAX_RESERVED_OUTPUT,
            "no_unknown_usage": report["usage_after"]["usage_unknown_calls"] == 0,
            "paid_calls_within_followup_cap": report["usage_after"]["reserved_calls"] - usage["reserved_calls"] <= 2,
        }
        save(path, report)
    return report


def resume_report(directory):
    return json.loads((directory / FOLLOWUP_NAME).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="Permit the sole additional two-call diagnostic")
    mode.add_argument("--resume", action="store_true", help="Read the saved diagnostic without environment, database or API access")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = resume_report(args.output) if args.resume else run_live(args.output)
    print(json.dumps({"state": report["state"], "processor_status": report.get("result", {}).get("status"),
                      "checks": report.get("review", {}).get("checks", {}),
                      "developer_review": report.get("review", {}).get("developer_review", {"state": "pending"}),
                      "usage": report.get("usage_after", report.get("usage_before"))}, ensure_ascii=False))
    if (report["state"] != "finished" or not all(report.get("integrity_checks", {}).values())
            or not report.get("review", {}).get("checks", {}).get("verified_typed_preference_accepted")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
