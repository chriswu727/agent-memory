"""Replay saved synthetic evidence, then optionally spend at most two DeepSeek calls."""

from __future__ import annotations

import argparse
import copy
import json
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from jsonschema import Draft202012Validator

from examples.evaluate_deepseek import SCENARIOS, Trial, code_fingerprints, save
from examples.evaluation_support import MeteredTransport
from membrain import cognition
from membrain.config import Settings
from membrain.consolidation_planning import plan_consolidation, validate_consolidation_plan
from membrain.helpers import Principal, digest
from membrain.semantic import (
    PROPOSAL_SCHEMA,
    VERIFICATION_SCHEMA,
    CognitiveBatch,
    SemanticPolicy,
    SemanticProcessor,
)
from membrain.semantic_provider import ChatCompletionsSemanticProvider
from membrain.service import MemoryService
from membrain.source_candidates import SOURCE_ROUTE_VERSION
from membrain.storage import Database

MODEL = "deepseek-v4-pro"
POLICY = SemanticPolicy(max_model_calls=2, max_output_tokens=16384,
                        max_response_bytes=65536, max_wall_time_ms=240000)
QUERY = "备考 同时 互相冲突 更正"
AT = "2026-10-24T12:00:00Z"
DEFAULT_CONDITIONAL = Path(".local/deepseek-evaluation-compact-planning/report.json")
DEFAULT_ABSTRACTION = Path(".local/deepseek-evaluation-final/report.json")


def fingerprints():
    values = code_fingerprints()
    root = Path(__file__).resolve().parents[1]
    for path in ("examples/evaluate_memory_repairs.py", "src/membrain/candidates.py",
                 "src/membrain/source_candidates.py"):
        values[path] = digest((root / path).read_text())
    return values


def saved_scenario(path, name, index):
    report = json.loads(path.read_text())
    scenario = report.get("scenarios", {}).get(name, {})
    if report.get("synthetic_only") is not True or scenario.get("fixture") != SCENARIOS[name]:
        raise ValueError("Only the unchanged, saved synthetic fixture is allowed")
    stages = [stage for stage in scenario.get("stages", []) if stage["index"] == index]
    if len(stages) != 1:
        raise ValueError("Expected exactly one saved stage")
    return report, scenario, stages[0]


def version(value):
    return value["memory_id"], value["revision"]


def evidence_key(reference):
    return digest({key: value for key, value in reference.items() if key != "relationship"})


def read_exact_sources(service, principal, brain_id, references):
    with service.database.transaction(principal) as conn:
        brain = service._brain(conn, principal, brain_id)
        events = service._events(conn, principal, brain,
                                 event_ids=[item["event_id"] for item in references], limit=128)
    expected = {(item["event_id"], item["revision"]): item["content_sha256"] for item in references}
    actual = {(item["event_id"], item["revision"]): item["content_sha256"] for item in events}
    if actual != expected:
        raise ValueError("Saved synthetic source versions have changed")
    return tuple(events)


def confirm_documents(saved, current):
    expected = {version(item): digest(item) for item in saved}
    actual = {version(item): digest(item) for item in current}
    if actual != expected:
        raise ValueError("Saved synthetic memory snapshot has changed; refusing a misleading replay")


def freeze_batch(service, baseline_path):
    report, scenario, stage = saved_scenario(baseline_path, "abstraction", 0)
    principal = Principal(**report["principal"])
    trial = Trial(service, principal, scenario["brain_id"])
    current = {version(item): item for item in trial.documents()["memories"]}
    assertions = tuple(item for item in stage["documents"]["memories"]
                       if item.get("epistemic_status") == "derived_inference"
                       and item.get("claim_kind") == "reported_fact")
    if len(assertions) != 2 or any(current.get(version(item)) != item for item in assertions):
        raise ValueError("Expected the exact two previously published semantic instances")
    events = read_exact_sources(service, principal, scenario["brain_id"],
                                stage["job"]["result"]["source_snapshot"])
    if sorted(event["observed"]["content"].get("text") for event in events) != sorted(SCENARIOS["abstraction"][0]):
        raise ValueError("Abstraction sources differ from the recorded synthetic fixture")
    sampling = {"strategy": "saved-independent-instances-diagnostic", "search_exhaustive": False,
                "publication_restricted": False, "source_count": len(events), "assertion_count": 2}
    plan = plan_consolidation(events, assertions, sampling=sampling)
    targets = {version(item) for item in assertions}
    if len(plan["objectives"]) != 1 or {version(item) for item in
            plan["objectives"][0]["instance_memory_versions"]} != targets:
        raise ValueError("Planner did not select the two saved independent instances")
    data = {"scope": stage["job"]["result"]["scope"], "sources": list(events),
            "existing_assertions": list(assertions), "working_state": {}, "episodes": [],
            "sampling": sampling, "belief_relations": [], "consolidation_plan": plan}
    return {"synthetic_only": True, "kind": "frozen_processor_input_without_publication",
            "baseline_report": str(baseline_path), "baseline_report_sha256": digest(report),
            "batch": {"batch_id": "repair-evaluation-" + digest(data), **data},
            "batch_sha256": digest(data), "code_fingerprints": fingerprints()}


def prepared_versions(observation):
    result = set()
    for block in observation.get("blocks", []):
        if block.get("section") not in {"relevant_memory", "conditional_memory"}:
            continue
        try:
            item = json.loads(block["text"]).get("memory_version")
        except (KeyError, TypeError, ValueError):
            continue
        if item is not None:
            result.add(version(item))
    return result


def replay_checks(target, observation):
    expected = version(target)
    recalled = [item for item in observation["items"] if version(item["memory"]) == expected]
    prepared = prepared_versions(observation)
    return {
        "english_assertion_has_no_direct_chinese_lexical_match": cognition._lexical_score(QUERY, target["statement"]) == 0,
        "exact_english_assertion_recalled": len(recalled) == 1,
        "exact_english_assertion_prepared": expected in prepared,
        "source_bridge_ranking_used": len(recalled) == 1 and any(
            signal.get("method") == "keyword" and signal.get("model_or_index_version") == SOURCE_ROUTE_VERSION
            for signal in recalled[0].get("rank_signals", [])),
        "recalled_assertion_unchanged": len(recalled) == 1 and recalled[0]["memory"] == target,
        "recall_within_budget": observation["context_usage"]["recall"] <= observation["context_budget"],
        "prepare_within_budget": observation["context_usage"]["prepare"] <= observation["context_budget"],
    }


def replay(service, baseline_path):
    report, scenario, stage = saved_scenario(baseline_path, "conditional", 2)
    trial = Trial(service, Principal(**report["principal"]), scenario["brain_id"])
    saved = stage["documents"]["memories"]
    confirm_documents(saved, trial.documents()["memories"])
    source = stage["input_event_ids"][0]
    targets = [item for item in saved if item.get("epistemic_status") == "derived_inference"
               and item.get("claim_kind") == "reported_fact"
               and {ref["event_id"] for ref in item["evidence_refs"]} == {source}]
    if len(targets) != 1:
        raise ValueError("Expected the original English conflict assertion, not a literal source")
    observation = trial.inspect_time(AT, QUERY, stage["input_event_ids"], context_budget=16000)
    confirm_documents(saved, trial.documents()["memories"])
    return {"kind": "same_synthetic_brain_recall_prepare_replay", "model_calls": 0,
            "baseline_report": str(baseline_path), "baseline_report_sha256": digest(report),
            "query": QUERY, "target": targets[0], "observation": observation,
            "checks": replay_checks(targets[0], observation), "code_fingerprints": fingerprints(),
            "limitations": ["Recall/prepare only; no new semantic processing or publication.",
                            "This source-language bridge does not demonstrate arbitrary multilingual retrieval."]}


def batch_from_saved(frozen):
    data = copy.deepcopy(frozen["batch"])
    batch_id = data.pop("batch_id")
    if frozen.get("synthetic_only") is not True or digest(data) != frozen["batch_sha256"]:
        raise ValueError("Frozen diagnostic input was modified")
    validate_consolidation_plan(data["consolidation_plan"], data["sources"], data["existing_assertions"],
                                data["belief_relations"], data["sampling"])
    for field in ("sources", "existing_assertions", "episodes", "belief_relations"):
        data[field] = tuple(data[field])
    return CognitiveBatch(batch_id=batch_id, **data)


def exact_supporting_basis(assessment, references):
    relations = assessment.get("relations", [])
    actual = [item["evidence_index"] for item in relations]
    basis = assessment.get("supporting_evidence_indices", [])
    supported = {item["evidence_index"] for item in relations if item["verdict"] == "supports"}
    return (len(actual) == len(references) and set(actual) == set(range(len(references)))
            and bool(basis) and len(basis) == len(set(basis)) and set(basis) == supported
            and all(item["verdict"] in {"supports", "irrelevant"} for item in relations))


def abstraction_review(batch, result, responses, instance_support=None):
    instance_support = instance_support or {}
    parent_versions = {version(item) for item in batch.existing_assertions}
    parents = {version(item): item for item in batch.existing_assertions}
    claims = {item["proposal_id"]: item for item in result.claims}
    proposed = responses.get("proposal", {})
    original_claims = {item["proposal_id"]: item for item in proposed.get("claims", [])}
    assessments = {item["proposal_id"]: item for item in responses.get("verification", {}).get("assessments", [])}
    accepted = []
    for link in result.belief_links:
        if link["kind"] != "abstracts":
            continue
        claim = claims.get(link["claim_proposal_id"])
        if claim is None:
            continue
        original_claim = original_claims.get(claim["proposal_id"], {})
        original_refs = original_claim.get("evidence_refs", []) + original_claim.get("counterexamples", [])
        refs = list(original_refs)
        for parent in parents.values():
            refs += parent["evidence_refs"] + parent.get("counterexamples", [])
        exact_refs = {evidence_key(item) for item in refs}
        assessment = assessments.get(claim["proposal_id"], {})
        link_assessment = assessments.get(link["proposal_id"], {})
        verdicts = link_assessment.get("instance_verdicts", [])
        verdict_versions = [version(item["memory_version"]) for item in verdicts]
        support_map = instance_support.get(link["proposal_id"], [])
        support_versions = [version(item["memory_version"]) for item in support_map]
        indices = [item.get("evidence_indices", []) for item in support_map]
        claim_basis = assessment.get("supporting_evidence_indices", [])
        checks = {
            "claim_supporting_basis_exact": exact_supporting_basis(assessment, original_refs),
            "link_supporting_basis_exact": exact_supporting_basis(link_assessment, link["evidence_refs"]),
            "claim_keeps_exactly_supported_citations": claim["evidence_refs"] == [
                ref for index, ref in enumerate(original_refs) if index in claim_basis],
            "whole_claim_text_unchanged": bool(original_claim)
            and claim["statement"] == original_claim["statement"],
            "complete_supported_instance_verdicts": len(verdict_versions) == len(parent_versions)
            and set(verdict_versions) == parent_versions
            and all(item["verdict"] == "supports" for item in verdicts),
            "complete_instance_support_map": len(support_versions) == len(parent_versions)
            and set(support_versions) == parent_versions
            and all(items and len(items) == len(set(items))
                    and all(type(index) is int and 0 <= index < len(link["evidence_refs"])
                            for index in items) for items in indices),
            "all_original_parents": {version(item) for item in claim["source_memory_versions"]} == parent_versions,
            "all_original_endpoints": {version(item) for item in link["target_memory_versions"]} == parent_versions,
            "exact_endpoint_evidence_union": {evidence_key(item) for item in link["evidence_refs"]} == exact_refs,
            "schema_inferred_pattern": claim["representation"] == "schema" and claim["claim_kind"] == "inferred_pattern",
            "world_preserved": all(claim["world"] == link["world"] == parent["world"] for parent in parents.values()),
            "conditions_preserved": all(claim["conditions"] == link["conditions"] == parent["conditions"] for parent in parents.values()),
            "joint_claim_supported": assessment.get("proposal_verdict") == "supports",
            "joint_link_supported": link_assessment.get("proposal_verdict") == "supports" and link_assessment.get("link_verdict") == "supports",
            "independent_supporting_sources": len({item["event_id"] for item in claim["evidence_refs"]
                                                   if item["relationship"] == "supports"}) >= 2,
        }
        accepted.append({"claim": claim, "link": link, "checks": checks,
                         "instance_support": support_map, "instance_verdicts": verdicts,
                         "supporting_evidence_indices": {"claim": claim_basis,
                             "link": link_assessment.get("supporting_evidence_indices", [])}})
    rejected = {item["proposal_id"]: item["reason"] for item in result.rejected}
    rejected_schemas = [item for item in proposed.get("claims", [])
                        if item.get("representation") == "schema" and item["proposal_id"] in rejected]
    checks = {
        "exact_instance_comparison_planned": len(batch.consolidation_plan["objectives"]) == 1
        and {version(item) for item in batch.consolidation_plan["objectives"][0]["instance_memory_versions"]} == parent_versions,
        "verified_abstraction_accepted": bool(accepted) and all(all(item["checks"].values()) for item in accepted),
        "rejected_schemas_not_accepted": not ({item["proposal_id"] for item in rejected_schemas} & claims.keys()),
        "model_calls_within_fixed_cap": result.model_calls <= 2,
    }
    return {"checks": checks, "accepted_abstractions": accepted, "rejected_schemas": rejected_schemas,
            "proposed_abstractions": [item for item in proposed.get("claims", []) if item.get("representation") == "schema"],
            "semantic_quality": "requires_human_review_of_whole_claim_and_evidence",
            "review_questions": ["Is each accepted schema one supported commonality, without invented details?",
                                 "Is its scope limited to the observed instances rather than a universal habit?"],
            "source_texts": [item["observed"]["content"]["text"] for item in batch.sources]}


def followup_metadata(batch, path, meter, reason):
    if path.name != "followup.json" or not 1 <= len(reason.strip()) <= 1000:
        raise ValueError("A diagnosed followup requires a separate followup.json and a concise reason")
    original_path = path.with_name("live.json")
    original = json.loads(original_path.read_text())
    checks = original.get("review", {}).get("checks", {})
    usage = meter.summary()
    if (original.get("state") != "finished" or original.get("synthetic_only") is not True
            or original.get("batch_id") != batch.batch_id or not checks or all(checks.values())
            or original.get("result", {}).get("model_calls") != 2
            or original.get("usage_before", {}).get("reserved_calls") != 0
            or original.get("usage_after") != usage
            or usage["reserved_calls"] != 2 or usage["completed_calls"] != 2):
        raise ValueError("Followup requires the original finished failure and exactly two completed ledger calls")
    return {"reason": reason.strip(), "prior_report": str(original_path),
            "prior_report_sha256": digest(original), "attempt": 2, "maximum_attempts": 2}


def run_live(batch, path, ledger_path, *, followup_reason=None):
    if path.exists():
        raise ValueError("A paid diagnostic is already recorded; use --resume, never automatically repeat")
    meter = MeteredTransport(ledger_path, max_calls=4 if followup_reason is not None else 2,
                             max_output_tokens=65536 if followup_reason is not None else 32768,
                             capture_content=False)
    try:
        followup = (followup_metadata(batch, path, meter, followup_reason)
                    if followup_reason is not None else None)
        if followup is None and meter.summary()["reserved_calls"]:
            raise ValueError("The shared ledger already contains calls; refusing to restart paid processing")
    except Exception:
        meter.close()
        raise
    report = {"kind": "standalone_semantic_processor_without_publication", "state": "started",
              "synthetic_only": True, "model": MODEL, "thinking": "enabled", "policy": asdict(POLICY),
              "batch_id": batch.batch_id, "code_fingerprints": fingerprints(), "responses": {},
              "reservations": [], "verification_instance_support": {},
              "verification_evidence_selection": {}, "usage_before": meter.summary(),
              "limitations": ["Exact old instances, new standalone processing; no database publication.",
                              "Structural checks and model assessments do not establish semantic correctness.",
                              "No keyword oracle or expected conclusion is supplied to the model."]}
    if followup is not None:
        report["followup"] = followup
    save(path, report)
    try:
        with ChatCompletionsSemanticProvider("https://api.deepseek.com", os.environ["DEEPSEEK_API_KEY"],
                                             MODEL, thinking="enabled", transport=meter) as provider:
            def record(request):
                if request.phase == "verification":
                    data = json.loads(request.messages[-1]["content"])
                    report["verification_evidence_selection"] = {
                        entry["proposal"]["proposal_id"]: entry["evidence_selection"]
                        for entry in data["proposals"] if "evidence_selection" in entry}
                    report["verification_instance_support"] = {
                        entry["proposal"]["proposal_id"]: entry["instance_support"]
                        for entry in data["proposals"] if "instance_support" in entry}
                    save(path, report)
                raw = provider(request)
                try:
                    value = json.loads(raw)
                    schema = PROPOSAL_SCHEMA if request.phase == "proposal" else VERIFICATION_SCHEMA
                    if Draft202012Validator(schema).is_valid(value):
                        report["responses"][request.phase] = value
                    else:
                        report.setdefault("invalid_responses", {})[request.phase] = {"sha256": digest(raw)}
                except (ValueError, TypeError, RecursionError):
                    report.setdefault("invalid_responses", {})[request.phase] = {"sha256": digest(raw)}
                save(path, report)
                return raw

            def reserve(**entry):
                report["reservations"].append(entry)
                save(path, report)

            result = SemanticProcessor(record, record, POLICY).process(batch, reserve=reserve)
            report["result"] = asdict(result)
            report["review"] = abstraction_review(batch, result, report["responses"],
                                                    report["verification_instance_support"])
            report["state"] = "finished"
    except Exception as error:
        report["state"] = "interrupted"
        report["execution_error"] = {"type": type(error).__name__, "code": getattr(error, "code", None)}
    finally:
        meter.close()
        report["usage_after"] = meter.summary()
        save(path, report)
    return report


def resume_report(path):
    report = json.loads(path.read_text())
    if report.get("state") != "finished":
        raise ValueError("Interrupted paid diagnostic requires inspection and will not be repeated")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--replay", action="store_true", help="Zero API calls: replay and freeze exact saved synthetic inputs")
    modes.add_argument("--live", action="store_true", help="Opt in to at most two paid calls against the frozen batch")
    modes.add_argument("--resume", action="store_true", help="Read a finished paid report without database or API calls")
    parser.add_argument("--followup", metavar="REASON",
                        help="With --live, allow one diagnosed followup on the same ledger; with --resume, select that saved followup")
    parser.add_argument("--output", type=Path, default=Path(".local/deepseek-memory-repairs"))
    parser.add_argument("--conditional-baseline", type=Path, default=DEFAULT_CONDITIONAL)
    parser.add_argument("--abstraction-baseline", type=Path, default=DEFAULT_ABSTRACTION)
    args = parser.parse_args()
    if args.followup is not None and (args.replay or not 1 <= len(args.followup.strip()) <= 1000):
        parser.error("--followup needs --live or --resume and a reason of 1–1000 characters")
    live_path = args.output / ("followup.json" if args.followup is not None else "live.json")
    frozen_path = args.output / "batch.json"
    if args.resume:
        report = resume_report(live_path)
    elif args.replay:
        if any((args.output / name).exists() for name in ("replay.json", "batch.json", "live.json", "calls.jsonl")):
            parser.error("This diagnostic directory already exists; choose a new directory for a free replay")
        settings = Settings.from_env()
        service = MemoryService(Database(settings.database_url, settings.migration_database_url))
        report = replay(service, args.conditional_baseline)
        frozen = freeze_batch(service, args.abstraction_baseline)
        args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        save(args.output / "replay.json", report)
        save(frozen_path, frozen)
        print(json.dumps({"replay_checks": report["checks"], "model_calls": 0,
                          "frozen_objectives": frozen["batch"]["consolidation_plan"]["objectives"]}, ensure_ascii=False))
        if not all(report["checks"].values()):
            raise SystemExit(1)
        return
    else:
        if live_path.exists():
            parser.error("A paid diagnostic is already recorded; use --resume")
        batch = batch_from_saved(json.loads(frozen_path.read_text()))
        load_dotenv()
        if not os.environ.get("DEEPSEEK_API_KEY"):
            parser.error("DEEPSEEK_API_KEY is required in the local environment")
        report = run_live(batch, live_path, args.output / "calls.jsonl", followup_reason=args.followup)
    print(json.dumps({"state": report["state"], "checks": report.get("review", {}).get("checks", {}),
                      "usage": report.get("usage_after", {}),
                      "semantic_quality": "requires_human_review"}, ensure_ascii=False))
    if report["state"] != "finished" or not all(report["review"]["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
