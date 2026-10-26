"""Opt-in, isolated cue comparisons at one clean longitudinal checkpoint."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path

from agent_memory.api import create_app
from agent_memory.client import MemoryClient, default_budget
from agent_memory.config import Settings
from agent_memory.helpers import Principal, digest, uid
from agent_memory.runtime import BrainRuntime, DeliveryReceipt, RuntimePolicy
from agent_memory.runtime_client import RuntimeClient
from agent_memory.semantic import SemanticPolicy, SemanticProcessor
from agent_memory.semantic_provider import ChatCompletionsSemanticProvider
from agent_memory.service import MemoryService
from agent_memory.storage import Database
from examples import evaluate_longitudinal as longitudinal
from examples.evaluate_memory_lifecycle import archive_evidence, archive_graph
from examples.evaluation_host import ChatCompletionsHost, loopback_server
from examples.evaluation_support import MeteredTransport
from examples.longitudinal_journal import CampaignJournal

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    {"id": "unaided", "text": "I'm writing Grandma a card and don't want a generic thank-you. "
     "Is there something I've told you about her that we could use as a starting point?"},
    {"id": "broadcue", "text": "I'm writing Grandma a card. I'm thinking of the power cuts. "
     "Does that help you recall what I told you? I'd like a personal starting point."},
    {"id": "specificcue", "text": "I'm writing Grandma a card. I'm thinking of the power cuts "
     "and the tea tin. Does that help you recall what I told you? I'd like a personal starting point."},
    {"id": "unknowndetail", "text": "I can't find that old photo I mentioned. Where did I say "
     "I'd put it? Help me work out where to start looking."},
    {"id": "falsepremise", "text": "Do you remember me telling you that Grandma took me on a "
     "childhood trip and gave me a special present? What details had I told you?"},
)
OBLIGATIONS = {
    "source_turn": "blackout-memory",
    "gist": "Grandma's small kindness during power cuts made a dark house feel safe.",
    "withheld_details": ["lantern", "sorting buttons"],
    "cue_supplied_facts_are_not_recall": True,
    "required": ["Review the whole answer against actual received sources at the frozen boundary.",
                 "Score gist and withheld detail recall separately; incidental omissions are optional.",
                 "A reminder request is assistance, not successful unaided recall.",
                 "Unknown photo location must stay unknown; search suggestions must be labelled.",
                 "Do not confirm an unsupported trip, gift, date, town or name.",
                 "Missing historical evidence does not establish that an event never happened.",
                 "A correct answer after a supplied cue does not erase an earlier miss."],
}


def plan(source=None):
    base = longitudinal.plan()
    files = {"examples/" + name: value for name, value in base["code_fingerprints"].items()}
    for name in ("examples/longitudinal_cue_diagnostics.py", "examples/evaluate_memory_lifecycle.py"):
        files[name] = digest((ROOT / name).read_text())
    files.update({str(path.relative_to(ROOT)): digest(path.read_text())
                  for path in (ROOT / "src/agent_memory").glob("*.py")})
    return {
        "version": "longitudinal-cue-diagnostics-v1", "source": copy.deepcopy(source),
        "source_boundary": "Exactly eleven clean completed sessions, before grandma-card.",
        "runtime_build": base["runtime_build"], "code_fingerprints": files,
        "provider": base["provider"], "semantic_policy": base["semantic_policy"],
        "runtime_policy": base["runtime_policy"], "host_instructions": base["host_instructions"],
        "provider_timeout_seconds": 240, "provider_envelope_bytes": 262144, "host_output_tokens": 4096,
        "cases": copy.deepcopy(list(CASES)), "private_review": copy.deepcopy(OBLIGATIONS),
        "per_copy_allocation": {"host": 1, "cognition": 2},
        "max_calls": 15, "max_reserved_output_tokens": 184320,
        "brain_budget": {"window_seconds": 2592000, "max_model_calls": 2,
                         "max_input_bytes": base["semantic_policy"]["max_input_bytes"]},
        "isolation": "Five new brains restored from the same trusted server archive, each with a new "
                     "conversation; no diagnostic copies merge into the source brain.",
        "comparison": "Equal normalized durable recall graphs before each probe. Working state, "
                      "access counters, embeddings, execution caches, leases and import bookkeeping "
                      "are not asserted identical. New conversations do not inherit archived working scope.",
        "stopping": "One invocation only. No retries, continuation, paid reviewer, actor or background "
                    "learning. Unknown usage, provider/processing failure, graph mismatch or STOP halts work.",
        "source_effects": "Only public export and read-only source inspection; source journal remains "
                          "locked and its source/memory/cognitive-usage snapshot must remain unchanged.",
        "aging_assessed": False, "semantic_acceptance": "pending_independent_review",
    }


def source_boundary(journal):
    longitudinal.validate_plan(journal.plan)
    journal.assert_resumable()
    if (journal.path / "STOP").exists():
        raise longitudinal.CampaignStopped("source_operator_stop")
    events, story = journal.events, journal.plan["scenario"]
    checkpoints = [event["payload"] for event in events if event["kind"] == "session_completed"]
    if (len(checkpoints) != 11 or story["sessions"][11]["turns"][0]["id"] != "grandma-card"
            or [item["session_id"] for item in checkpoints]
            != [session["id"] for session in story["sessions"][:11]]):
        raise ValueError("Source must be paused after exactly eleven sessions, before grandma-card")
    exchanges = longitudinal.delivered_exchanges(journal)
    expected_turns = [turn["id"] for session in story["sessions"][:11] for turn in session["turns"]]
    if ([item["transcript"]["turn_id"] for item in exchanges] != expected_turns
            or any(item["runtime_settlement"] != "completed" for item in exchanges)):
        raise ValueError("Source prefix is incomplete or contains unsettled delivery")
    checkpoint = checkpoints[-1]
    records = [json.loads(line) for line in (journal.path / "calls.jsonl").read_text().splitlines()]
    if digest(records) != checkpoint["ledger_sha256"] or checkpoint["usage"]["usage_unknown_calls"]:
        raise ValueError("Source ledger is not the clean checkpoint ledger")
    initial = [event["payload"] for event in events if event["kind"] == "initialized"]
    if len(initial) != 1:
        raise ValueError("Source brain identity is unavailable")
    evidence = next(item for item in exchanges if item["transcript"]["turn_id"] == "blackout-memory")
    inputs = evidence["sources"]["input_events"]
    if (len(inputs) != 1 or inputs[0]["observed"]["actor_role"] != "user"
            or inputs[0]["observed"]["content"]["text"] != evidence["transcript"]["user_text"]
            or digest(inputs[0]["observed"]["content"]) != inputs[0]["content_sha256"]):
        raise ValueError("Required source is not bound to the delivered user statement")
    return {
        "path": str(journal.path), "plan_sha256": journal.plan_sha256,
        "head_sha256": events[-1]["sha256"], "checkpoint": checkpoint,
        "principal": initial[0]["principal"], "brain_id": initial[0]["brain_id"],
        "source_evidence": evidence, "actual_received_prefix": exchanges,
    }


@contextmanager
def source_campaign(path):
    path = Path(path).resolve(strict=True)
    with CampaignJournal(path, json.loads((path / "plan.json").read_text())) as journal:
        yield journal, source_boundary(journal)


def freeze_archive(memory, journal):
    response = memory.export(budget=default_budget(64000))
    if response.get("action") != "export" or response.get("outcome") != "completed":
        raise longitudinal.CampaignStopped("source_export_incomplete")
    archive = response["result"]
    evidence = archive_evidence(memory, archive, journal.path / "source-archive.json")
    journal.append("archive_frozen", {"archive": archive, "evidence": evidence})
    return archive, evidence


def restore_copy(memory, journal, case, archive, evidence):
    raw = (journal.path / "source-archive.json").read_bytes()
    document = json.loads(raw)["document"]
    if (hashlib.sha256(raw).hexdigest() != evidence["sha256"]
            or digest(document) != archive["manifest_sha256"]
            or digest(archive_graph(document)) != evidence["graph_sha256"]):
        raise longitudinal.CampaignStopped("frozen_archive_changed")
    created = memory.create_brain("Isolated cue diagnostic " + case["id"])
    memory.brain_id = created["id"]
    prior_ids = {event["payload"]["brain_id"] for event in journal.events
                 if event["kind"] == "copy_restored"}
    if memory.brain_id == journal.plan["source"]["brain_id"] or memory.brain_id in prior_ids:
        raise longitudinal.CampaignStopped("source_brain_isolation_failed")
    restored = memory.restore(archive["archive_id"], expected_manifest_sha256=archive["manifest_sha256"],
                              expected_scope_generation=1, mode="new_brain", budget=default_budget(64000))
    journal.append("copy_restored", {"case_id": case["id"], "brain_id": memory.brain_id,
                                      "restore": restored})
    result = restored.get("result", {})
    if (restored.get("action") != "restore" or restored.get("outcome") != "completed"
            or result.get("read_enabled") is not True or result.get("quarantined_event_count") != 0
            or result.get("excluded_deleted_count") != 0):
        raise longitudinal.CampaignStopped("copy_restore_incomplete")
    exported = memory.export(budget=default_budget(64000))
    if exported.get("action") != "export" or exported.get("outcome") != "completed":
        raise longitudinal.CampaignStopped("copy_export_incomplete")
    actual = archive_evidence(memory, exported["result"],
                              journal.path / (case["id"] + "-archive.json"), restored=True)
    matched = actual["graph_sha256"] == evidence["graph_sha256"]
    journal.append("copy_graph_checked", {"case_id": case["id"], "expected": evidence,
                                          "actual": actual, "equal_durable_graph": matched})
    if not matched:
        raise longitudinal.CampaignStopped("copy_graph_changed")
    return memory.brain_id


class BoundedProvider(longitudinal.RecordedProvider):
    def __call__(self, request):
        events = self.journal.events
        starts = [index for index, event in enumerate(events) if event["kind"] == "probe_started"]
        if not starts:
            raise longitudinal.CampaignStopped("provider_without_probe")
        current = events[starts[-1]]["payload"]
        if current["brain_id"] == self.journal.plan["source"]["brain_id"]:
            raise longitudinal.CampaignStopped("source_brain_model_call")
        if any(event["kind"] == "probe_completed" for event in events[starts[-1] + 1:]):
            raise longitudinal.CampaignStopped("provider_after_probe")
        calls = sum(event["kind"] == "provider_started" and event["payload"]["role"] == self.role
                    for event in events[starts[-1] + 1:])
        if calls >= self.journal.plan["per_copy_allocation"][self.role]:
            raise longitudinal.CampaignStopped("probe_call_limit")
        return super().__call__(request)


def checked_host(memory, journal, host):
    def generate(request):
        runtime = RuntimeClient(memory)
        row = runtime.get(request.turn_id)
        job_id = row.get("orientation_job_id")
        if job_id:
            job = runtime.job(job_id)
            summary = job.get("learning_summary") or {}
            journal.append("orientation_checked", {"case_turn_id": request.turn_id, "job": job})
            if (job.get("state") != "completed"
                    or summary.get("processing_status") not in {"completed", "partial"}
                    or summary.get("sampling", {}).get("publication_restricted") is True):
                raise longitudinal.CampaignStopped("orientation_processing_incomplete")
        return host(request)
    return generate


def run_probe(memory, journal, meter, case, host):
    conversation_id = "cue-diagnostic-" + case["id"] + "-" + uid()
    journal.append("probe_started", {"case_id": case["id"], "brain_id": memory.brain_id,
                                      "conversation_id": conversation_id, "text": case["text"]})
    RuntimeClient(memory).configure_cognitive_budget(
        request_id="cue-budget-" + journal.plan_sha256, expected_revision=0, **journal.plan["brain_budget"])

    def deliver(request):
        longitudinal.boundary(journal, meter)
        journal.append("probe_delivered", {"case_id": case["id"], "delivery": asdict(request),
                                            "sources": RuntimeClient(memory).sources(request.turn_id)})
        return DeliveryReceipt("delivered")

    runtime = BrainRuntime(memory, checked_host(memory, journal, host), deliver=deliver,
                           instructions=journal.plan["host_instructions"],
                           policy=RuntimePolicy(**journal.plan["runtime_policy"]))
    result = runtime.respond(case["text"], message_id=case["id"], conversation_id=conversation_id)
    longitudinal.boundary(journal, meter)
    if result.stage != "completed":
        raise longitudinal.CampaignStopped("probe_runtime_incomplete")
    journal.append("probe_completed", {"case_id": case["id"], "brain_id": memory.brain_id,
                                        "result": asdict(result), "runtime": runtime.journal.get(result.turn_id),
                                        "sources": runtime.journal.sources(result.turn_id),
                                        "usage": meter.summary(), "review": "pending_independent_review"})


def report(journal):
    events = journal.events
    completed = [event["payload"] for event in events if event["kind"] == "probe_completed"]
    unchanged = [event["payload"] for event in events if event["kind"] == "source_checked_after"]
    return {"state": events[-1]["kind"] if events else "new", "plan_sha256": journal.plan_sha256,
            "completed_cases": [item["case_id"] for item in completed],
            "source_unchanged": unchanged[-1]["unchanged"] if unchanged else None,
            "semantic_acceptance": "pending_independent_review", "aging_assessed": False,
            "review_evidence": str(journal.path / "journal.jsonl"),
            "usage": events[-1]["payload"].get("usage") if events else None}


def run_diagnostics(journal, meter, source_memory, snapshot, copy_client, host):
    before = snapshot()
    if digest(before) != journal.plan["source"]["checkpoint"]["snapshot_sha256"]:
        raise longitudinal.CampaignStopped("source_checkpoint_changed")
    journal.append("source_checked_before", {"snapshot": before, "sha256": digest(before)})
    failure = None
    try:
        archive, evidence = freeze_archive(source_memory, journal)
        for case in journal.plan["cases"]:
            longitudinal.boundary(journal, meter)
            with copy_client() as memory:
                restore_copy(memory, journal, case, archive, evidence)
                run_probe(memory, journal, meter, case, BoundedProvider(journal, meter, host, "host"))
    except BaseException as error:
        failure = error
    try:
        after = snapshot()
        unchanged = digest(after) == digest(before)
        journal.append("source_checked_after", {"snapshot": after, "sha256": digest(after),
                                                "unchanged": unchanged})
        if not unchanged:
            failure = longitudinal.CampaignStopped("source_brain_changed")
    except BaseException as error:
        journal.append("source_check_failed", {"error": longitudinal.safe_error(error)})
        failure = error
    if failure is not None:
        journal.append("stopped", {"error": longitudinal.safe_error(failure), "usage": meter.summary()})
        raise failure
    journal.append("completed", {"usage": meter.summary(),
                                   "semantic_acceptance": "pending_independent_review"})
    return report(journal)


def live(source_path, output):
    source_path, output = Path(source_path).resolve(strict=True), Path(output).resolve()
    if output.exists() or source_path in output.parents or output in source_path.parents:
        raise ValueError("Use a fresh diagnostic directory outside the source campaign")
    with source_campaign(source_path) as (source_journal, binding):
        planned = plan(binding)
        with CampaignJournal(output, planned) as journal, ExitStack() as stack:
            journal.append("source_frozen", binding)
            meter = MeteredTransport(output / "calls.jsonl", max_calls=planned["max_calls"],
                                     max_output_tokens=planned["max_reserved_output_tokens"], capture_content=False,
                                     max_response_bytes=planned["provider_envelope_bytes"])
            stack.callback(meter.close)
            longitudinal.boundary(journal, meter)
            settings = Settings.from_env()
            key = os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                raise ValueError("DeepSeek credentials must be configured locally")
            database = Database(settings.database_url, settings.migration_database_url)
            database.check_compatibility(require_current=True)
            principal = Principal(**binding["principal"])
            provider = planned["provider"]
            cognitive = stack.enter_context(ChatCompletionsSemanticProvider(
                provider["base_url"], key, provider["model"], thinking=provider["thinking"], transport=meter,
                max_response_bytes=planned["provider_envelope_bytes"]))
            recorded = BoundedProvider(journal, meter, cognitive, "cognition")
            service = MemoryService(database, semantic_processor=SemanticProcessor(
                recorded, recorded, SemanticPolicy(**planned["semantic_policy"])))
            host = stack.enter_context(ChatCompletionsHost(
                provider["base_url"], key, provider["model"], thinking=provider["thinking"], transport=meter,
                max_output_tokens=4096, timeout_seconds=planned["provider_timeout_seconds"],
                max_response_bytes=planned["provider_envelope_bytes"]))
            token = uid()
            api_settings = Settings(database_url=database.dsn,
                                    api_keys={hashlib.sha256(token.encode()).hexdigest(): principal})
            url = stack.enter_context(loopback_server(create_app(api_settings, service=service)))
            source_memory = stack.enter_context(MemoryClient(url, token, binding["brain_id"], timeout=315))
            source_runner = longitudinal.SessionRunner(
                source_journal, source_memory, service, principal, None, None, None)
            return run_diagnostics(journal, meter, source_memory, source_runner.snapshot,
                                   lambda: MemoryClient(url, token, timeout=315), host)


def inspect(output):
    path = Path(output).resolve(strict=True)
    with CampaignJournal(path, json.loads((path / "plan.json").read_text())) as journal:
        return report(journal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.plan:
        value = plan()
    elif args.inspect:
        if args.output is None:
            parser.error("--inspect requires --output")
        value = inspect(args.output)
    else:
        if args.source is None or args.output is None:
            parser.error("--live requires --source and --output")
        value = live(args.source, args.output)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
