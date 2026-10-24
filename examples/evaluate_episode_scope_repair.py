"""One independent, bounded processor diagnostic; no database publication or host turn."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from agent_memory.contracts import validate
from agent_memory.episode_semantics import episode_assessment
from agent_memory.helpers import digest
from agent_memory.semantic import (
    _PROPOSAL_VALIDATOR,
    _VERIFICATION_VALIDATOR,
    EPISODE_SCOPE_INPUT_VERSION,
    GENERAL_PROPOSAL_MODE,
    PROPOSAL_SCHEMA,
    CognitiveBatch,
    SemanticPolicy,
    SemanticProcessor,
    _parse,
    proposal_data,
    request_payload,
)
from agent_memory.semantic_provider import ChatCompletionsSemanticProvider
from examples.evaluate_consumption_repair import _confirmed_source_usage, _safe_error, save_report
from examples.evaluation_support import MeteredTransport

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPORT = ROOT / ".local/deepseek-orientation-on-demand-v1/memory/report.json"
DEFAULT_OUTPUT = Path(".local/deepseek-episode-scope-repair")
MODEL = "deepseek-v4-pro"
BASE_CALLS, BASE_TOKENS, BASE_OUTPUT = 14, 118460, 126976
MAX_CALLS, OUTPUT_PER_CALL = 2, 16384
MAX_OUTPUT = MAX_CALLS * OUTPUT_PER_CALL
OLD_REJECTION = {"reason": "invalid_proposal", "proposal_id": "episode-create-learn-20261020"}
METADATA_KEYS = {"episode_operation_scope", "episode_eligible_member_refs", "learning_input_refs"}
REVIEW = (
    "Review the complete new proposals and independent verification against every unchanged original source. "
    "Does the sole episode organize only the current conversation's learning-window interaction, without "
    "recreating the older conversation? Does the accepted reported preference retain the user's name, order, "
    "replacement and temporal qualifiers, and does the supersedes relation preserve its exact prior target? "
    "Machine acceptance and model verification do not establish semantic correctness. Original rejected "
    "proposals and this rubric are developer comparison data only, never model input."
)


def fingerprints():
    names = ["examples/evaluate_episode_scope_repair.py", "examples/evaluate_consumption_repair.py",
             "examples/evaluate_deepseek.py", "examples/evaluation_support.py"]
    names += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "src/agent_memory").glob("*.py"))]
    names += ["architecture/memory-contract.schema.json"]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def _versions(items, identifier):
    return [{identifier: item[identifier], "revision": item["revision"],
             "content_sha256": item["content_sha256"] if identifier == "event_id" else digest(item)}
            for item in items]


def _identities(items, identifier):
    return [(item[identifier], item["revision"]) for item in items]


def freeze_inputs():
    raw = SOURCE_REPORT.read_bytes()
    source = json.loads(raw)
    usage = _confirmed_source_usage(source)
    ledger_raw = SOURCE_REPORT.with_name("calls.jsonl").read_bytes()
    if (not ledger_raw.endswith(b"\n")
            or [json.loads(line) for line in ledger_raw.splitlines()] != source["call_ledger"]):
        raise ValueError("The original raw call ledger must exactly match the saved report")
    baseline_plan = source.get("plan", {})
    if (source.get("version") != 1 or source.get("state") != "finished" or source.get("arm") != "memory"
            or source.get("plan_sha256") != digest(baseline_plan)
            or baseline_plan.get("profile") != "smoke" or baseline_plan.get("model") != MODEL
            or baseline_plan.get("thinking") != "enabled"
            or baseline_plan.get("runtime_policy", {}).get("orientation_policy") != "on_demand"
            or usage["reserved_calls"] != BASE_CALLS or usage["reserved_output_tokens"] != BASE_OUTPUT
            or usage["usage_totals"].get("total_tokens") != BASE_TOKENS):
        raise ValueError("The original finished on-demand smoke and its fourteen confirmed calls are required")
    stages = source.get("stages", [])
    if ([stage.get("name") for stage in stages] != ["learn", "recall", "change", "recall_change", "empty_control"]
            or any(stage.get("state") != "finished" for stage in stages)):
        raise ValueError("All five original stages must be preserved")
    stage = stages[2]
    jobs = stage.get("learning_jobs", [])
    if (len(jobs) != 1 or jobs[0].get("state") != "completed" or jobs[0].get("execution_error")
            or jobs[0].get("job_id") != stage.get("learning_job_id")):
        raise ValueError("The original completed change learning job is required")
    original = jobs[0]["result"]
    if (original.get("status") != "partial" or original.get("rejected") != [OLD_REJECTION]
            or original.get("proposal_mode") != GENERAL_PROPOSAL_MODE):
        raise ValueError("The unique original cross-scope episode failure must remain visible")
    calls = stage.get("cognitive_calls", [])
    if (len(calls) != 2 or [call.get("request", {}).get("phase") for call in calls] != ["proposal", "verification"]
            or any(call.get("state") != "finished" or call.get("role") != "consolidate"
                   or call.get("schema_valid") is not True for call in calls)):
        raise ValueError("Both original schema-valid processor calls are required")
    request, response = copy.deepcopy(calls[0]["request"]), copy.deepcopy(calls[0]["response"])
    data = json.loads(request["messages"][-1]["content"])
    if request.get("max_output_tokens") != OUTPUT_PER_CALL or data.get("current_input_refs") != []:
        raise ValueError("The original general batch must not authorize current-request inference")
    for event in data["sources"]:
        validate("Event", event)
        if event["content_sha256"] != digest(event["observed"]["content"]):
            raise ValueError("Original source content changed")
    for item in data["existing_assertions"]:
        validate("MemoryAssertion", item)
    scope = copy.deepcopy(original["scope"])
    validate("BoundScope", scope)
    if (_versions(data["sources"], "event_id") != original["source_snapshot"]
            or _identities(data["existing_assertions"], "memory_id")
            != _identities(original["memory_snapshot"], "memory_id")
            or _versions(data["episodes"], "episode_id") != original["episode_snapshot"]
            or data["working_state"] != original["working_snapshot"]
            or data["sampling"] != original["sampling"]
            or data["belief_relations"] != original["belief_relations"]
            or data["consolidation_plan"] != original["consolidation_plan"]):
        raise ValueError("Original proposal sources, version bindings and job context must match the frozen snapshot")
    refs = copy.deepcopy(original["learning_input_refs"])
    events = {(event["event_id"], event["revision"]): event for event in data["sources"]}
    if (not refs or any(set(ref) != {"event_id", "revision"} or (ref["event_id"], ref["revision"]) not in events
                        for ref in refs) or len({(ref["event_id"], ref["revision"]) for ref in refs}) != len(refs)
            or any(events[(ref["event_id"], ref["revision"])]["scope"] != scope for ref in refs)):
        raise ValueError("Learning inputs must be exact current-scope source versions")
    window = original["learning_window"]
    if (window.get("status") != "processed" or window.get("processing_succeeded") is not True
            or window.get("input_count") != len(refs)
            or any(not window["from_sequence"] <= events[(ref["event_id"], ref["revision"])]["sequence"]
                   <= window["through_sequence"] for ref in refs)):
        raise ValueError("Learning input refs must remain bound to the original processed window")
    rejected = [item for item in response["episode_actions"] if item["proposal_id"] == OLD_REJECTION["proposal_id"]]
    if len(rejected) != 1:
        raise ValueError("The original rejected episode proposal is missing")
    targets = [item["target_memory_versions"] for item in original["belief_links"] if item["kind"] == "supersedes"]
    if len(targets) != 1:
        raise ValueError("The original accepted supersedes target must be preserved")
    batch = {"batch_id": "episode-scope-diagnostic-" + digest([scope, data, refs]), "scope": scope,
             **{key: copy.deepcopy(data[key]) for key in (
                 "sources", "existing_assertions", "working_state", "episodes", "sampling", "belief_relations",
                 "consolidation_plan", "current_input_refs")},
             "proposal_mode": GENERAL_PROPOSAL_MODE, "learning_input_refs": refs,
             "input_contract_version": EPISODE_SCOPE_INPUT_VERSION}
    try:
        source_name = str(SOURCE_REPORT.relative_to(ROOT))
    except ValueError:
        source_name = SOURCE_REPORT.name
    return {"source_report": source_name, "source_report_sha256": hashlib.sha256(raw).hexdigest(),
            "source_plan": baseline_plan, "source_plan_sha256": source["plan_sha256"],
            "source_usage": usage, "source_call_ledger": copy.deepcopy(source["call_ledger"]),
            "source_call_ledger_sha256": digest(source["call_ledger"]),
            "source_ledger_file_sha256": hashlib.sha256(ledger_raw).hexdigest(),
            "batch": batch, "batch_sha256": digest(batch), "original_proposal_data": data,
            "original_memory_snapshot": copy.deepcopy(original["memory_snapshot"]),
            "developer_comparison": {"sent_to_model": False, "original_status": original["status"],
                                     "original_rejected": copy.deepcopy(original["rejected"]),
                                     "original_proposal_response": response,
                                     "rejected_episode": rejected[0], "supersedes_targets": targets[0]}}


def batch_from_saved(frozen):
    data = copy.deepcopy(frozen["batch"])
    if digest(data) != frozen["batch_sha256"] or data["current_input_refs"]:
        raise ValueError("Frozen diagnostic batch changed")
    for key in ("sources", "existing_assertions", "episodes", "belief_relations", "current_input_refs", "learning_input_refs"):
        data[key] = tuple(data[key])
    return CognitiveBatch(**data)


def old_episode_rejected(batch, frozen):
    try:
        episode_assessment(frozen["developer_comparison"]["rejected_episode"], batch.scope,
                           {(item["event_id"], item["revision"]): item for item in batch.sources},
                           {item["episode_id"]: item for item in batch.episodes})
    except ValueError:
        return True
    return False


def plan(frozen=None):
    frozen = freeze_inputs() if frozen is None else frozen
    batch = batch_from_saved(frozen)
    policy = SemanticPolicy(**frozen["source_plan"]["semantic_policy"])
    if (policy.max_model_calls != MAX_CALLS or policy.max_output_tokens != OUTPUT_PER_CALL
            or policy.max_input_bytes < 2 * policy.max_request_bytes):
        raise ValueError("The unchanged policy must reserve both bounded processor phases")
    data = proposal_data(batch.sources, batch.existing_assertions, batch.working_state, batch.episodes,
                         batch.sampling, batch.belief_relations, batch.consolidation_plan,
                         current_input_refs=batch.current_input_refs, proposal_mode=batch.proposal_mode,
                         scope=batch.scope, learning_input_refs=batch.learning_input_refs,
                         input_contract_version=batch.input_contract_version)
    expected_refs = list(batch.learning_input_refs)
    if (data.get("episode_operation_scope") != batch.scope
            or data.get("learning_input_refs") != expected_refs
            or data.get("episode_eligible_member_refs") != expected_refs
            or {key: value for key, value in data.items() if key not in METADATA_KEYS}
            != frozen["original_proposal_data"] or not old_episode_rejected(batch, frozen)):
        raise ValueError("Only explicit scope and eligibility metadata may change the original proposal data")
    messages, size = request_payload("proposal", data, PROPOSAL_SCHEMA, OUTPUT_PER_CALL,
                                    input_contract_version=batch.input_contract_version)
    if size > policy.max_request_bytes:
        raise ValueError("The frozen proposal exceeds the original input budget")
    return {"kind": "episode_scope_processor_diagnostic", "model": MODEL, "thinking": "enabled",
            "policy": asdict(policy), "max_calls": MAX_CALLS, "max_reserved_output_tokens": MAX_OUTPUT,
            "maximum_attempts": 1, "source_report_sha256": frozen["source_report_sha256"],
            "frozen_inputs_sha256": digest(frozen), "code_fingerprints": fingerprints(),
            "proposal_input": {"messages": list(messages), "response_schema": PROPOSAL_SCHEMA,
                               "max_output_tokens": OUTPUT_PER_CALL, "input_bytes": size},
            "scope": "Independent semantic processor only; no database, publication, host answer or delivery.",
            "only_data_change": "Add explicit episode operation scope, eligible source refs and learning-window refs.",
            "input_contract_version": batch.input_contract_version,
            "assertion_snapshot_binding": "Preserve exact provider-visible assertions and original storage snapshot "
                                          "hashes separately; disclosure generation rebinding changes their full hashes.",
            "cost_accounting": "Add at most two calls and 32768 reserved output tokens to the original fourteen calls "
                               "and 118460 confirmed tokens. The original partial result is not overwritten.",
            "stopping": "No retries. Unknown usage blocks the next phase. Resume only reads saved output.",
            "semantic_review": {"state": "pending_developer_review", "rubric": REVIEW},
            "cli_success_means": "Execution finished with confirmed usage; semantic acceptance remains pending."}


def _verified(item, group, responses, verification_input):
    proposed = [entry for entry in responses.get("proposal", {}).get(group, [])
                if entry.get("proposal_id") == item["proposal_id"]]
    assessed = [entry for entry in responses.get("verification", {}).get("assessments", [])
                if entry.get("proposal_id") == item["proposal_id"]]
    entries = [entry for entry in verification_input.get("proposals", [])
               if entry.get("group") == group and entry.get("proposal", {}).get("proposal_id") == item["proposal_id"]]
    if len(proposed) != 1 or len(assessed) != 1 or len(entries) != 1:
        return False
    proposal, verdict, entry = proposed[0], assessed[0], entries[0]
    relations, evidence = verdict.get("relations", []), entry.get("evidence", [])
    return (all(item.get(key) == value for key, value in proposal.items())
            and entry["proposal"] == proposal and verdict.get("proposal_verdict") == "supports"
            and verdict.get("link_verdict") == ("supports" if group == "belief_links" else None)
            and len(relations) == len(evidence) > 0
            and {relation.get("evidence_index") for relation in relations} == set(range(len(evidence)))
            and all(relation.get("verdict") == "supports" for relation in relations)
            and item.get("verification") == {"state": "supported_inference", "method": "semantic_model",
                                              "not_a_truth_certificate": True})


def review_result(batch, frozen, result, responses, verification_input):
    episodes = list(result.episode_actions)
    expected = {(ref["event_id"], ref["revision"]) for ref in batch.learning_input_refs}
    users = {(event["event_id"], event["revision"]) for event in batch.sources
             if event["observed"]["actor_role"] == "user" and (event["event_id"], event["revision"]) in expected}
    preferences = [item for item in result.claims if item.get("claim_kind") == "reported_preference"
                   and item.get("representation") == "semantic_anchor"
                   and {(ref["event_id"], ref["event_revision"]) for ref in item.get("evidence_refs", [])} == users]
    links = [item for item in result.belief_links if item.get("kind") == "supersedes"]
    valid_episode = False
    if len(episodes) == 1:
        try:
            episode_assessment(episodes[0], batch.scope,
                               {(event["event_id"], event["revision"]): event for event in batch.sources},
                               {episode["episode_id"]: episode for episode in batch.episodes})
            valid_episode = (episodes[0]["action"] == "create"
                             and {(ref["event_id"], ref["revision"]) for ref in episodes[0]["member_refs"]} == expected)
        except (ValueError, KeyError, TypeError):
            pass
    return {"checks": {
        "original_cross_scope_episode_still_rejected": old_episode_rejected(batch, frozen),
        "only_current_scope_episode_proposed_and_accepted": valid_episode
        and len(responses.get("proposal", {}).get("episode_actions", [])) == 1,
        "episode_independently_verified": valid_episode and _verified(episodes[0], "episode_actions", responses, verification_input),
        "current_reported_preference_retained_and_verified": bool(preferences)
        and all(_verified(item, "claims", responses, verification_input) for item in preferences),
        "supersedes_retains_original_target_and_is_verified": len(links) == 1
        and links[0]["target_memory_versions"] == frozen["developer_comparison"]["supersedes_targets"]
        and links[0]["claim_proposal_id"] in {item["proposal_id"] for item in preferences}
        and _verified(links[0], "belief_links", responses, verification_input),
        "processor_completed_without_rejections": result.status == "completed" and not result.rejected,
    }, "processor_status": result.status, "rejected": list(result.rejected),
        "semantic_review": {"state": "pending_developer_review", "rubric": REVIEW}}


def _check_frozen(report):
    if (digest(report["plan"]) != report["plan_sha256"]
            or digest(report["frozen_inputs"]) != report["plan"]["frozen_inputs_sha256"]
            or fingerprints() != report["plan"]["code_fingerprints"]
            or hashlib.sha256(SOURCE_REPORT.read_bytes()).hexdigest() != report["plan"]["source_report_sha256"]
            or hashlib.sha256(SOURCE_REPORT.with_name("calls.jsonl").read_bytes()).hexdigest()
            != report["frozen_inputs"]["source_ledger_file_sha256"]):
        raise ValueError("Frozen code, inputs or original report changed")


def run_experiment(output, report, provider, meter):
    if report["state"] != "started" or report["calls"] or meter.summary()["reserved_calls"]:
        raise ValueError("An independent empty attempt is required; paid work cannot be resumed")
    batch = batch_from_saved(report["frozen_inputs"])
    policy = SemanticPolicy(**report["plan"]["policy"])

    def check():
        _check_frozen(report)
        if meter.summary()["usage_unknown_calls"]:
            raise ValueError("Unknown provider usage prevents another processor phase")

    def reserve(**entry):
        check()
        usage = meter.summary()
        if (usage["reserved_calls"] != len(report["reservations"])
                or usage["completed_calls"] != usage["reserved_calls"]
                or usage["reserved_calls"] >= MAX_CALLS
                or usage["reserved_output_tokens"] + OUTPUT_PER_CALL > MAX_OUTPUT):
            raise ValueError("The independent processor budget is exhausted or inconsistent")
        report["reservations"].append(entry)
        save_report(output, report, meter)

    def record(request):
        check()
        if request.phase == "proposal" and (
                list(request.messages) != report["plan"]["proposal_input"]["messages"]
                or request.response_schema != report["plan"]["proposal_input"]["response_schema"]
                or request.max_output_tokens != OUTPUT_PER_CALL):
            raise ValueError("Actual proposal differs from the frozen new input")
        call = {"request": asdict(request), "state": "started"}
        report["calls"].append(call)
        if request.phase == "verification":
            report["verification_input"] = json.loads(request.messages[-1]["content"])
        save_report(output, report, meter)
        try:
            raw = provider(request)
            validator = _PROPOSAL_VALIDATOR if request.phase == "proposal" else _VERIFICATION_VALIDATOR
            try:
                parsed = _parse(raw, validator, policy.max_response_bytes)
                report["responses"][request.phase] = call["response"] = parsed
                call["schema_valid"] = True
            except (ValueError, TypeError, UnicodeError, RecursionError):
                call["schema_valid"] = False
            call["state"] = "finished"
            return raw
        except Exception as error:
            call.update(state="failed", error=_safe_error(error))
            raise
        finally:
            save_report(output, report, meter)

    check()
    result = SemanticProcessor(record, record, policy).process(batch, reserve=reserve, check=check)
    report["result"] = asdict(result)
    report["review"] = review_result(batch, report["frozen_inputs"], result,
                                     report["responses"], report["verification_input"])
    check()
    report["state"] = "finished"
    save_report(output, report, meter)


def run_live(output):
    if output.exists():
        raise ValueError("Output already exists; this independent attempt cannot be repeated")
    frozen = freeze_inputs()
    planned = plan(frozen)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {"version": 1, "kind": planned["kind"], "state": "started", "plan": planned,
              "plan_sha256": digest(planned), "frozen_inputs": frozen, "calls": [], "reservations": [],
              "responses": {}, "verification_input": {}, "database_used": False,
              "published": False, "host_generated": False, "delivered": False,
              "usage": {"reserved_calls": 0, "completed_calls": 0, "reserved_output_tokens": 0,
                        "usage_confirmed_calls": 0, "usage_unknown_calls": 0, "usage_totals": {}},
              "call_ledger": [], "cumulative_usage": copy.deepcopy(frozen["source_usage"]),
              "semantic_review": copy.deepcopy(planned["semantic_review"])}
    save_report(output, report)
    meter = None
    try:
        load_dotenv()
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY must be configured locally")
        meter = MeteredTransport(output / "calls.jsonl", max_calls=MAX_CALLS,
                                 max_output_tokens=MAX_OUTPUT, capture_content=False)
        with ChatCompletionsSemanticProvider("https://api.deepseek.com", key, MODEL,
                                             thinking="enabled", transport=meter) as provider:
            run_experiment(output, report, provider, meter)
    except BaseException as error:
        report.update(state="interrupted", error=_safe_error(error))
        if not isinstance(error, Exception):
            raise
    finally:
        if meter is not None:
            meter.close()
        report["integrity_checks"] = {
            "original_report_unchanged": hashlib.sha256(SOURCE_REPORT.read_bytes()).hexdigest()
            == frozen["source_report_sha256"],
            "original_ledger_unchanged": hashlib.sha256(SOURCE_REPORT.with_name("calls.jsonl").read_bytes()).hexdigest()
            == frozen["source_ledger_file_sha256"],
            "code_unchanged": fingerprints() == planned["code_fingerprints"],
        }
        save_report(output, report, meter)
    return report


def resume_report(output):
    return json.loads((output / "report.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true", help="Read frozen sources and code only")
    modes.add_argument("--live", action="store_true", help="Allow the sole two-call processor diagnostic")
    modes.add_argument("--resume", action="store_true", help="Read a saved attempt; never continue calls")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, indent=2))
        return
    report = resume_report(args.output) if args.resume else run_live(args.output)
    print(json.dumps({key: report.get(key) for key in (
        "state", "usage", "cumulative_usage", "review", "semantic_review", "integrity_checks",
    )}, ensure_ascii=False, indent=2))
    if (report["state"] != "finished" or report.get("usage", {}).get("usage_unknown_calls", 1)
            or not all(report.get("integrity_checks", {}).values())):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
