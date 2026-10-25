"""Fixed-proposal episode verification diagnostic; no learning, publication, host or delivery."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tarfile
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from jsonschema import Draft202012Validator

from agent_memory import cognition
from agent_memory.contracts import validate
from agent_memory.helpers import digest
from agent_memory.semantic import (
    COMMUNICATION_INTENT_INPUT_VERSION,
    EPISODE_FIDELITY_INPUT_VERSION,
    CognitiveBatch,
    CognitiveRequest,
    SemanticPolicy,
    SemanticProcessor,
    _parse,
)
from agent_memory.semantic_provider import ChatCompletionsSemanticProvider
from examples.evaluate_deepseek import save
from examples.evaluation_support import MeteredTransport

ROOT = Path(__file__).resolve().parents[1]
LONG_ARCHIVE = ROOT / "evaluations/deepseek-2026-10-25-long-history-stopped.evidence.tar.gz"
SMOKE_REPORT = ROOT / "evaluations/deepseek-2026-10-25-lifecycle-smoke.json"
LONG_MEMBER = "run/memory/report.json"
LONG_REPORT_SHA256 = "a3054fb6778b398caeda15dceed5749043cd8fb6cb718acc89bcbbf25bc5e4db"
DEFAULT_OUTPUT = Path(".local/deepseek-episode-fidelity")
MODEL, BASE_URL, THINKING = "deepseek-v4-pro", "https://api.deepseek.com", "enabled"
VERSIONS = (COMMUNICATION_INTENT_INPUT_VERSION, EPISODE_FIDELITY_INPUT_VERSION)
MAX_CALLS, OUTPUT_PER_CALL, TIMEOUT_SECONDS = 16, 8192, 240
MAX_OUTPUT = MAX_CALLS * OUTPUT_PER_CALL
POLICY = SemanticPolicy(max_output_tokens=OUTPUT_PER_CALL, max_response_bytes=65536,
                        max_wall_time_ms=TIMEOUT_SECONDS * 1000)
FACETS = ("attribution", "modality", "specificity", "boundary")
CASE_IDS = ("recorded_uncertain_save", "faithful_uncertainty", "recorded_study_narrowing",
            "supported_exam_preparation", "quoted_speaker", "unsupported_causality",
            "explicit_negative", "unperformed_action")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprints():
    files = ["examples/evaluate_episode_fidelity.py", "examples/evaluation_support.py",
             "examples/evaluate_deepseek.py", "architecture/memory-contract.schema.json"]
    files += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "src/agent_memory").glob("*.py"))]
    return {name: sha((ROOT / name).read_bytes()) for name in files}


def _recorded_case(report, stage_name, proposal_id, provenance):
    stage, = [stage for stage in report["stages"] if stage["name"] == stage_name]
    call, = [call for call in stage["cognitive_calls"] if call["request"]["phase"] == "proposal"
             and call.get("role") == "consolidate"]
    proposal, = [item for item in call["response"]["episode_actions"] if item["proposal_id"] == proposal_id]
    data = json.loads(call["request"]["messages"][-1]["content"])
    refs = {(ref["event_id"], ref["revision"]) for ref in proposal["member_refs"]}
    sources = [source for source in data["sources"] if (source["event_id"], source["revision"]) in refs]
    if len(sources) != len(refs) or proposal["action"] != "create":
        raise ValueError("Recorded episode members are incomplete")
    return {"sources": sources, "proposal": proposal,
            "provenance": provenance | {"kind": "original_recorded_sources_and_proposal",
                "stage": stage_name, "proposal_id": proposal_id,
                "original_proposal_request_sha256": digest(call["request"]),
                "source_events_sha256": digest(sources), "proposal_sha256": digest(proposal),
                "context_boundary": "Complete original episode member events; unrelated proposals and historical "
                                    "neighborhood are excluded. This is not a replay of the whole old batch."}}


def _synthetic(text, role, sequence):
    content = {"media_type": "text/plain", "text": text}
    scope = {"tenant_id": "diagnostic", "brain_id": "diagnostic", "principal_binding_id": "owner",
             "conversation_id": "conversation", "branch_id": "main", "visibility": "private",
             "access_policy_id": "private-owner", "policy_revision": 1, "scope_generation": 1}
    return {"event_id": f"source-{sequence}", "revision": 1, "sequence": sequence, "scope": scope,
            "observed": {"source_event_key": f"source-{sequence}", "source_revision": 1,
                         "producer_ref": "synthetic-adapter", "actor_ref": role, "actor_role": role,
                         "event_kind": "message", "content": content,
                         "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
                         "interpretation_hint": "unknown",
                         "delivery_state": "delivered" if role == "assistant" else "received",
                         "parent_source_event_keys": []},
            "system_time": {"recorded_at": "2026-10-25T12:00:00Z", "superseded_at": None},
            "content_sha256": digest(content), "origin_verification": "verified_producer",
            "origin": "assistant_output" if role == "assistant" else "user_statement",
            "previous_revision": None, "utterance_segments": cognition.classify_segments(content)}


def _controlled(inputs, statement):
    sources = [_synthetic(text, role, index + 1) for index, (role, text) in enumerate(inputs)]
    return {"sources": sources,
            "proposal": {"proposal_id": "episode-action", "action": "create", "episode_id": None,
                         "expected_revision": None, "boundary_ref": None, "organization_statement": statement,
                         "member_refs": [{"event_id": e["event_id"], "revision": e["revision"]} for e in sources]},
            "provenance": {"kind": "controlled_synthetic_sources_and_proposal"}}


def source_cases():
    archive_raw, smoke_raw = LONG_ARCHIVE.read_bytes(), SMOKE_REPORT.read_bytes()
    with tarfile.open(LONG_ARCHIVE, "r:gz") as archive:
        member = archive.getmember(LONG_MEMBER)
        if not member.isfile() or member.size > 16 * 1024 * 1024:
            raise ValueError("Unexpected original report member")
        raw = archive.extractfile(member).read()
    if sha(raw) != LONG_REPORT_SHA256:
        raise ValueError("The original stopped report changed")
    smoke = json.loads(smoke_raw)
    sources = {str(LONG_ARCHIVE.relative_to(ROOT)): sha(archive_raw),
               str(SMOKE_REPORT.relative_to(ROOT)): sha(smoke_raw)}
    cases = [
        _recorded_case(json.loads(raw), "history-000", "ep-1", {
            "file": str(LONG_ARCHIVE.relative_to(ROOT)), "file_sha256": sha(archive_raw),
            "member": LONG_MEMBER, "member_sha256": sha(raw)}),
        _controlled([("assistant", "I cannot confirm whether the arrangement was saved permanently.")],
                    "The assistant states that it cannot confirm whether the arrangement was saved permanently."),
        _recorded_case(smoke["experiment"], "learn", "episode-action-1", {
            "file": str(SMOKE_REPORT.relative_to(ROOT)), "file_sha256": sha(smoke_raw),
            "original_report_sha256": smoke["original_report_sha256"]}),
        _controlled([("user", "I call my exam-preparation routine Paper Lantern: walk first, then read the textbook.")],
                    "The user reports an exam-preparation routine named Paper Lantern, walking before reading."),
        _controlled([("user", 'In my novel, Ada says, "I saved the arrangement permanently." This is fictional dialogue.')],
                    "The user reports having saved the arrangement permanently in real life."),
        _controlled([("user", "I studied at the library and later had a headache. I do not know what caused it.")],
                    "The user reports that studying at the library caused their later headache."),
        _controlled([("user", "I did not save the arrangement permanently.")],
                    "The user explicitly reports not saving the arrangement permanently."),
        _controlled([("user", "Please discuss a possible reminder; do not schedule anything yet."),
                     ("assistant", "We can consider a morning reminder. I have not scheduled it.")],
                    "This exchange completes scheduling a morning reminder for the user."),
    ]
    expected = ("reject", "accept", "reject", "accept", "reject", "reject", "accept", "reject")
    rubrics = (
        "Original Chinese: lack of confirmation does not establish non-storage. Preserve the assistant's uncertainty.",
        "Preserve uncertainty about storage and assistant attribution without deciding whether storage occurred.",
        "Original Chinese 复习 does not establish an exam. Reject the added exam-preparation domain.",
        "The source explicitly names exam preparation; the faithful attributed report should remain admissible.",
        "Fictional Ada's quoted speech does not establish an action by the real user.",
        "Chronology and an explicitly unknown cause do not establish causality.",
        "Explicit negative user testimony can support an attributed negative report, not independently verified truth.",
        "Discussion with explicit non-execution does not establish external completion or a completed action boundary.",
    )
    for case, identifier, outcome, rubric in zip(cases, CASE_IDS, expected, rubrics, strict=True):
        case.update(case_id=identifier, developer_expectation={"sent_to_model": False, "rubric": rubric,
                    "by_contract": dict.fromkeys(VERSIONS, outcome)})
    return cases, sources


def _batch(case, version):
    return CognitiveBatch("fidelity-" + digest([case["sources"], case["proposal"], version]),
                          copy.deepcopy(case["sources"][0]["scope"]), tuple(copy.deepcopy(case["sources"])),
                          learning_input_refs=tuple(copy.deepcopy(case["proposal"]["member_refs"])),
                          input_contract_version=version)


def _request_dict(request):
    value = asdict(request)
    value["timeout_seconds"] = TIMEOUT_SECONDS
    return json.loads(json.dumps(value))


def replay(case, version, response=None):
    captured = []

    def verify(request):
        captured.append(_request_dict(request))
        return '{"assessments":[]}' if response is None else response

    proposal = {"claims": [], "situation_updates": [], "episode_actions": [case["proposal"]]}
    result = SemanticProcessor(lambda _: json.dumps(proposal, ensure_ascii=False), verify, POLICY).process(
        _batch(case, version), reserve=lambda **_: None)
    if len(captured) != 1:
        raise ValueError("The fixed episode did not reach exactly one production verifier request")
    return captured[0], result


def freeze_inputs():
    cases, source_files = source_cases()
    for case in cases:
        for source in case["sources"]:
            validate("Event", source)
            if source["content_sha256"] != digest(source["observed"]["content"]):
                raise ValueError("Source content hash changed")
        case["requests"] = {version: replay(case, version)[0] for version in VERSIONS}
    return {"cases": cases, "source_files": source_files}


def plan(frozen=None):
    frozen = freeze_inputs() if frozen is None else frozen
    return {"version": 1, "kind": "fixed_episode_verifier_diagnostic", "model": MODEL,
            "base_url": BASE_URL, "thinking": THINKING, "versions": list(VERSIONS),
            "max_calls": MAX_CALLS, "output_tokens_per_call": OUTPUT_PER_CALL,
            "max_reserved_output_tokens": MAX_OUTPUT, "timeout_seconds_per_call": TIMEOUT_SECONDS,
            "max_response_bytes": POLICY.max_response_bytes, "maximum_attempts_per_request": 1,
            "case_order": list(CASE_IDS), "frozen_inputs_sha256": digest(frozen),
            "code_fingerprints": fingerprints(),
            "scope": "Eight fixed proposals, two verification contracts. No proposal generation, database, "
                     "publication, host answer or delivery. No evidence of end-to-end quality or forgetting calibration.",
            "stopping": "No retries or paid continuation. Stop on uncertain/incomplete usage, transport failure, "
                        "invalid response, frozen-input/code drift or exhausted ledger. Resume is read-only.",
            "accounting": "Only the sixteen possible HTTP verification calls count as model calls. Local processor "
                          "construction/replay uses injected fixed responses and incurs zero provider calls.",
            "cli_success_means": "All planned calls completed with known usage and valid frozen bindings; "
                                 "not that their verdicts agree with the expected semantic outcomes.",
            "semantic_review": {"state": "pending_developer_review", "sent_to_model": False,
                                "rubric": "Review each complete original source and fixed proposal independently. "
                                          "Schema validity and gate acceptance do not establish truth. Both contracts "
                                          "are expected to reject unsupported meaning; improvement is not presumed."}}


def _save(output, report, meter=None):
    if meter is not None:
        report.update(usage=meter.summary(), call_ledger=meter.records)
    save(output / "report.json", report)


def _check(output, report):
    planned, frozen = report["plan"], report["frozen_inputs"]
    if (digest(planned) != report["plan_sha256"] or digest(frozen) != planned["frozen_inputs_sha256"]
            or json.loads((output / "plan.json").read_bytes()) != planned
            or json.loads((output / "inputs.json").read_bytes()) != frozen
            or fingerprints() != planned["code_fingerprints"]
            or any(sha((ROOT / name).read_bytes()) != value for name, value in frozen["source_files"].items())):
        raise ValueError("Frozen plan, inputs, sources or implementation changed")


def _known_usage(meter, count):
    summary, records = meter.summary(), meter.records
    completed = [item for item in records if item["event"] == "completed"]
    return (summary["reserved_calls"] == summary["completed_calls"] == summary["usage_confirmed_calls"] == count
            and summary["usage_unknown_calls"] == 0
            and all(all(type(item.get("usage", {}).get(key)) is int for key in
                        ("prompt_tokens", "completion_tokens", "total_tokens")) for item in completed))


def run_experiment(output, report, provider, meter):
    if report["state"] != "started" or report["calls"] or meter.summary()["reserved_calls"]:
        raise ValueError("An attempt cannot be repeated or continued")
    for case in report["frozen_inputs"]["cases"]:
        for version in VERSIONS:
            _check(output, report)
            if not _known_usage(meter, len(report["calls"])):
                raise ValueError("Provider usage is not fully known")
            saved = copy.deepcopy(case["requests"][version])
            rebuilt, _ = replay(case, version)
            if saved != rebuilt:
                raise ValueError("Frozen verifier request differs from the production processor")
            call = {"case_id": case["case_id"], "contract": version, "state": "reserved",
                    "request": copy.deepcopy(saved), "request_sha256": digest(saved)}
            report["calls"].append(call)
            _save(output, report, meter)
            request = CognitiveRequest(**(saved | {"messages": tuple(saved["messages"])}))
            raw = provider(request)
            call.update(state="received", response_text=raw, response_sha256=sha(raw.encode("utf-8")))
            _save(output, report, meter)
            if not _known_usage(meter, len(report["calls"])):
                raise ValueError("Provider usage is not fully known")
            parsed = _parse(raw, Draft202012Validator(saved["response_schema"]), POLICY.max_response_bytes)
            rebuilt, result = replay(case, version, raw)
            if rebuilt != saved or result.status not in {"completed", "partial"}:
                raise ValueError("Incomplete verification or replay binding failure")
            outcome = "accept" if result.episode_actions else "reject"
            expected = case["developer_expectation"]["by_contract"][version]
            call.update(state="finished", response=parsed, processor_status=result.status, gate_outcome=outcome,
                        expectation_comparison={"expected": expected, "matches": outcome == expected,
                                                "independent_semantic_review_required": True},
                        accepted_episodes=list(result.episode_actions), rejected=list(result.rejected),
                        semantic_review="pending_developer_review")
            _check(output, report)
            _save(output, report, meter)
    report["state"] = "finished"
    _save(output, report, meter)


def run_live(output):
    if output.exists():
        raise ValueError("Output already exists; an attempt cannot be repeated")
    frozen = freeze_inputs()
    planned = plan(frozen)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    save(output / "plan.json", planned)
    save(output / "inputs.json", frozen)
    report = {"version": 1, "kind": planned["kind"], "state": "started", "plan": planned,
              "plan_sha256": digest(planned), "frozen_inputs": frozen, "calls": [],
              "database_used": False, "published": False, "host_generated": False, "delivered": False,
              "semantic_review": copy.deepcopy(planned["semantic_review"])}
    _save(output, report)
    meter = None
    try:
        load_dotenv()
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        meter = MeteredTransport(output / "calls.jsonl", max_calls=MAX_CALLS,
                                 max_output_tokens=MAX_OUTPUT, capture_content=False)
        with ChatCompletionsSemanticProvider(BASE_URL, key, MODEL, thinking=THINKING, transport=meter) as provider:
            run_experiment(output, report, provider, meter)
    except BaseException as error:
        report.update(state="interrupted", error={"type": type(error).__name__})
        if not isinstance(error, Exception):
            raise
    finally:
        if meter is not None:
            meter.close()
        try:
            _check(output, report)
            report["frozen_integrity_verified"] = True
        except (ValueError, OSError):
            report["frozen_integrity_verified"] = False
        _save(output, report, meter)
    return report


def resume_report(output):
    return json.loads((output / "report.json").read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true", help="Read sources and freeze inputs without credentials or calls")
    modes.add_argument("--live", action="store_true", help="Run the sole bounded sixteen-verifier-call attempt")
    modes.add_argument("--resume", action="store_true", help="Read an existing report; never continue paid work")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.plan:
        frozen = freeze_inputs()
        print(json.dumps({"plan": plan(frozen), "frozen_inputs": frozen}, ensure_ascii=False, indent=2))
        return
    report = resume_report(args.output) if args.resume else run_live(args.output)
    print(json.dumps({key: report.get(key) for key in
                      ("state", "usage", "frozen_integrity_verified", "semantic_review")}, indent=2))
    if report["state"] != "finished" or not report.get("frozen_integrity_verified"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
