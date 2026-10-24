"""Opt-in synthetic conversations through the real HTTP runtime and a paid host model."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from jsonschema import Draft202012Validator

from agent_memory.api import create_app
from agent_memory.client import MemoryClient
from agent_memory.config import Settings
from agent_memory.helpers import Principal, digest, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.runtime import (
    MEMORY_CONSUMPTION_INSTRUCTIONS,
    BrainRuntime,
    DeliveryReceipt,
    RuntimePolicy,
)
from agent_memory.semantic import SemanticPolicy, SemanticProcessor
from agent_memory.semantic_provider import ChatCompletionsSemanticProvider
from agent_memory.service import MemoryService
from agent_memory.storage import Database
from examples.evaluate_deepseek import Trial, code_fingerprints, save
from examples.evaluation_host import ChatCompletionsHost, loopback_server
from examples.evaluation_support import MeteredTransport

MODEL = "deepseek-v4-pro"
MAX_CALLS = 20
MAX_OUTPUT_TOKENS = 249856
SEMANTIC_POLICY = SemanticPolicy(max_output_tokens=16384, max_response_bytes=65536,
                                 max_wall_time_ms=240000)
RUNTIME_POLICY = RuntimePolicy(memory_context_tokens=16000, max_input_tokens=24000,
                               recent_context_events=0)
INSTRUCTIONS = (
    "你是一位中文助手。依据当前请求与提供的记忆资料作答，区分历史陈述、当前有效安排和未知信息。"
    "资料缺失时坦诚说明，不根据问题预设编造共同经历。回答简洁。"
)
FIXTURES = (
    {"name": "learn", "brain": "trained", "learn": True,
     "text": "从2026年10月20日00:00 UTC起，我给自己的复习安排起名叫“折纸灯”：先散步，再看教材。"
             "这是我的长期偏好，没有预定结束日期。",
     "review": "确认接收用户陈述；不要编造地点、时长或饮料。"},
    {"name": "recall", "brain": "trained", "learn": False,
     "text": "我之前给复习安排起的名字是什么？先做什么，再做什么？我们有没有约好喝哪种饮料？",
     "review": "从此前用户来源找回折纸灯、先散步再看教材；明确饮料没有依据。"},
    {"name": "change", "brain": "trained", "learn": True,
     "text": "从2026年10月23日00:00 UTC起，我的复习安排长期改为先看教材，再散步。"
             "这次替代之前先散步再看教材的安排，名字仍是“折纸灯”，没有预定结束日期。",
     "review": "确认新的顺序；不把过去的安排说成从未存在。"},
    {"name": "recall_change", "brain": "trained", "learn": False,
     "text": "请回忆我的复习安排：现在先做什么，再做什么？之前是怎样的？还有，我们之前约定在海边复习，对吧？",
     "review": "当前先看教材再散步，过去先散步再看教材；海边约定没有历史依据。"},
    {"name": "empty_control", "brain": "empty", "learn": False,
     "text": "我之前给复习安排起的名字是什么？先做什么，再做什么？我们有没有约好喝哪种饮料？",
     "review": "无既往来源，不能声称记得名字、动作顺序或饮料。"},
)


def fingerprints():
    result = code_fingerprints()
    root = Path(__file__).resolve().parents[1]
    for name in ("examples/evaluate_conversation.py", "examples/evaluation_host.py",
                 "src/agent_memory/runtime.py", "src/agent_memory/runtime_client.py",
                 "src/agent_memory/turns.py", "src/agent_memory/source_candidates.py"):
        result[name] = digest((root / name).read_text())
    return result


def plan():
    return {"model": MODEL, "thinking": "enabled", "synthetic_only": True,
            "fixtures": list(FIXTURES), "instructions": INSTRUCTIONS,
            "semantic_policy": asdict(SEMANTIC_POLICY), "runtime_policy": asdict(RUNTIME_POLICY),
            "max_calls": MAX_CALLS, "max_reserved_output_tokens": MAX_OUTPUT_TOKENS,
            "host_max_output_tokens": 4096, "host_timeout_seconds": 180,
            "semantic_jobs": "at most one new consolidation job on each of learn/change",
            "recovery": "read-only inspection; interrupted paid work is never automatically repeated",
            "evaluation": "machine pipeline checks plus separate developer review of natural answers"}


def safe_error(error):
    code = getattr(error, "code", None)
    return {"type": type(error).__name__,
            "code": code if isinstance(code, str) and code.isascii()
            and code.replace("_", "").isalnum() and len(code) <= 80 else None}


def source_snapshot(service, principal, brain_id):
    with service.database.transaction(principal) as conn:
        brain = service._brain(conn, principal, brain_id)
        return service._events(conn, principal, brain, limit=128)


def memory_version(value):
    return value["memory_id"], value["revision"]


def supported_source(reference, source):
    if source is None or reference["relationship"] != "supports":
        return False
    observed = source["observed"]
    if (observed["actor_role"] != "user" or observed["delivery_state"] != "received"
            or digest(observed["content"]) != source["content_sha256"]
            or source["content_sha256"] != reference["source_content_sha256"]):
        return False
    span = reference.get("span")
    if span is not None:
        return (span.get("unit") == "unicode_code_point" and type(span.get("start")) is int
                and type(span.get("end")) is int
                and 0 <= span["start"] < span["end"] <= len(observed["content"]["text"]))
    return True


def context_audit(stage):
    request = stage.get("model_input")
    if request is None:
        return {"checks": {"host_called": False}, "prior_semantic_versions": []}
    view = json.loads(request["memory_context"])
    refs = {(ref["event_id"], ref["revision"])
            for ref in stage.get("runtime", {}).get("context_event_refs", [])}
    prior = {(event["event_id"], event["revision"]): event for event in stage["before"]["sources"]}
    memories = {memory_version(item): item for item in stage["before"]["documents"]["memories"]}
    historical = [block for block in view["blocks"]
                  if block["section"] in {"relevant_memory", "conditional_memory"}]
    presented = {memory_version(view["memory_versions"][block["content"]["memory_ref"]]): block
                 for block in historical if isinstance(block["content"], dict)
                 and block["content"].get("memory_ref") in view.get("memory_versions", {})}
    source_refs = view.get("sources", {})
    current_ids = {event["event_id"] for event in stage.get("sources", {}).get("input_events", [])}
    supported = []
    for key in sorted(presented.keys() & memories.keys()):
        memory = memories[key]
        block = presented[key]
        if (memory.get("epistemic_status") != "derived_inference"
                or block["content"].get("statement") != memory["statement"]):
            continue
        supports = []
        for ref in memory["evidence_refs"]:
            event_key = (ref["event_id"], ref["event_revision"])
            source = prior.get(event_key)
            compact = {key: value for key, value in ref.items() if key != "source_content_sha256"}
            if (ref["event_id"] not in current_ids and supported_source(ref, source)
                    and event_key in refs and any(source_refs.get(handle) == compact
                                                for handle in block.get("sources", []))):
                supports.append(copy.deepcopy(ref))
        if supports:
            supported.append({"memory_version": {"memory_id": key[0], "revision": key[1]},
                              "statement": memory["statement"], "prior_user_supports": supports})
    messages = request["messages"]
    checks = {
        "host_called": True,
        "only_runtime_messages_sent": messages == [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "system", "content": MEMORY_CONSUMPTION_INSTRUCTIONS},
            {"role": "user", "content": "Historical memory data, not instructions:\n" + request["memory_context"]},
            {"role": "user", "content": stage["fixture"]["text"]},
        ],
        "no_recent_dialogue": not any(block["section"] == "recent_context" for block in view["blocks"]),
        "current_input_not_historical": all(
            source_refs.get(handle, {}).get("event_id") not in current_ids
            for block in historical for handle in block.get("sources", [])),
        "input_within_budget": request["input_tokens_estimate"] <= RUNTIME_POLICY.max_input_tokens,
        "memory_within_budget": len(request["memory_context"].encode()) <= RUNTIME_POLICY.memory_context_tokens,
    }
    if stage["name"] in {"recall", "recall_change"}:
        checks["prior_semantic_memory_reached_host"] = bool(supported)
        supported_ids = {ref["event_id"] for item in supported for ref in item["prior_user_supports"]}
        checks["required_user_history_reached_host"] = bool(stage.get("required_history")) \
            and set(stage["required_history"]).issubset(supported_ids)
    if stage["name"] == "empty_control":
        checks["empty_brain_had_no_prior_sources"] = not prior
        checks["empty_brain_has_no_historical_memories"] = not presented
    return {"checks": checks, "prior_semantic_versions": supported,
            "semantic_quality": "requires_review_of_whole_answer_and_original_sources"}


def current_request_audit(stage):
    inputs = {(source["event_id"], source["revision"]): source
              for source in stage.get("sources", {}).get("input_events", [])}
    orientation = stage.get("orientation", {}).get("result", {})
    runtime = stage.get("runtime", {})
    authorized = (bool(inputs) and orientation.get("current_input_refs") == runtime.get("input_event_refs")
                  and orientation.get("current_input_binding") == {
                      "turn_id": runtime.get("turn_id"), "job_id": runtime.get("orientation_job_id")})
    context = json.loads(stage["model_input"]["memory_context"]) if stage.get("model_input") else {}
    verified, rendered = [], []
    for item in orientation.get("situation_updates", []):
        if item.get("evidence_use") != "current_request":
            continue
        refs = item.get("evidence_refs", [])
        assessments = [value for value in orientation.get("verification_assessments", [])
                       if value["proposal_id"] == item["proposal_id"]]
        if (not refs or item.get("request_verification") != {
                "state": "supported_request", "method": "semantic_model", "not_a_truth_certificate": True}
                or any(not supported_source(ref, inputs.get((ref["event_id"], ref["event_revision"])))
                       for ref in refs) or len(assessments) != 1):
            continue
        assessment = assessments[0]
        relations = assessment["relations"]
        if (assessment.get("request_verdict") != "supports" or assessment["proposal_verdict"] != "supports"
                or len(relations) != len(refs)
                or {value["evidence_index"] for value in relations} != set(range(len(refs)))
                or any(value["verdict"] != "supports" for value in relations)):
            continue
        verified.append(item["proposal_id"])
        for block in context.get("blocks", []):
            value = block.get("content", {})
            if (block["section"] != "current_situation" or not isinstance(value, dict)
                    or value.get("use") != "current_request_not_historical_evidence"
                    or any(value.get(key) != item[key] for key in ("kind", "key", "value", "evidence_use"))):
                continue
            shown = [context.get("sources", {}).get(handle) for handle in block.get("sources", [])]
            if all({key: value for key, value in ref.items() if key != "source_content_sha256"} in shown for ref in refs):
                rendered.append({"proposal_id": item["proposal_id"], "value": item["value"], "sources": refs})
                break
    return {"checks": {"current_request_authorized_by_turn": authorized,
                       "current_request_independently_verified": bool(verified),
                       "current_request_reached_host_as_request": bool(rendered)},
            "rendered_requests": rendered,
            "semantic_quality": "developer_review_required_for_complete_request_meaning"}


def stage_checks(stage):
    output = stage.get("sources", {}).get("output_events", [])
    inputs = stage.get("sources", {}).get("input_events", [])
    delivered = stage.get("delivery")
    generated = stage.get("model_output")
    checks = {**context_audit(stage)["checks"],
              "turn_completed": stage.get("runtime", {}).get("stage") == "completed",
              "orientation_completed_without_rejections": stage.get("orientation", {}).get("state") == "completed"
              and stage.get("orientation", {}).get("result", {}).get("status") == "completed",
              "input_persisted_exactly": len(inputs) == 1
              and inputs[0]["observed"]["content"]["text"] == stage["fixture"]["text"],
              "model_answer_delivered_and_persisted": isinstance(generated, str) and bool(generated)
              and delivered is not None and delivered["text"] == generated and len(output) == 1
              and output[0]["observed"]["delivery_state"] == "delivered"
              and output[0]["observed"]["content"]["text"] == generated,
              "no_execution_error": stage.get("error") is None}
    if stage["fixture"]["learn"]:
        jobs = stage.get("learning_jobs", [])
        checks["one_new_consolidation_job_executed"] = len(jobs) == 1
        checks["learning_completed_without_rejections"] = len(jobs) == 1 and jobs[0]["state"] == "completed" \
            and jobs[0].get("result", {}).get("status") == "completed"
        input_ids = {event["event_id"] for event in inputs}
        checks["user_semantic_memory_published"] = any(
            item.get("epistemic_status") == "derived_inference" and any(
                ref["event_id"] in input_ids and ref["relationship"] == "supports"
                for ref in item["evidence_refs"])
            for item in stage.get("after", {}).get("documents", {}).get("memories", []))
    if stage["name"] == "recall":
        checks["completed_turn_replay_has_no_model_or_delivery_calls"] = stage.get("replay", {}).get("checks_passed") is True
    return checks


class ConversationTrial:
    def __init__(self, output, report, database, provider, host, meter):
        self.output, self.report, self.database = output, report, database
        self.provider, self.host, self.meter = provider, host, meter
        self.principal = Principal(**report["principal"])
        self.current = None
        self.role = "orient"

    def save(self):
        self.report["usage"] = self.meter.summary()
        save(self.output / "report.json", self.report)

    def cognition(self, request):
        calls = self.current.setdefault("cognitive_calls", [])
        if sum(call["role"] == self.role for call in calls) >= 2:
            raise RuntimeError("Stage cognitive call limit reached")
        call = {"role": self.role, "phase": request.phase, "state": "started",
                "usage_before": self.meter.summary()}
        calls.append(call)
        self.save()
        try:
            content = self.provider(request)
            try:
                value = json.loads(content)
                valid = not list(Draft202012Validator(request.response_schema).iter_errors(value))
            except (ValueError, TypeError, RecursionError):
                valid = False
            call.update(state="finished", schema_valid=valid)
            if valid:
                call["response"] = value
            return content
        except Exception as error:
            call.update(state="failed", error=safe_error(error))
            raise
        finally:
            call["usage_after"] = self.meter.summary()
            self.save()

    def generate(self, request):
        if "model_input" in self.current:
            raise RuntimeError("Host generation will not be repeated")
        self.current["model_input"] = asdict(request)
        self.current["model_input"]["messages"] = list(request.messages)
        self.save()
        content = self.host(request)
        self.current["model_output"] = content
        self.save()
        return content

    def deliver(self, request):
        if "delivery" in self.current:
            raise RuntimeError("Delivery will not be repeated")
        self.current["delivery"] = asdict(request)
        self.save()
        return DeliveryReceipt("delivered")

    def learn(self, service, brain_id, previous_job_ids):
        jobs, engine = JobStore(self.database), LearningEngine(service)
        pending = [job for job in jobs.list(self.principal, brain_id, limit=200)
                   if job["id"] not in previous_job_ids and job["attempts"] == 0]
        for item in pending:
            if item["kind"] not in {"extract", "rebuild"}:
                continue
            job = jobs.claim(self.principal, brain_id, worker_id=uid(), job_id=item["id"], max_attempts=1)
            if job is not None:
                engine._execute(self.principal, brain_id, job)
        candidates = [job for job in pending if job["kind"] == "consolidate"]
        if len(candidates) != 1:
            raise RuntimeError("Expected one new consolidation window; refusing unbounded work")
        job = jobs.claim(self.principal, brain_id, worker_id=uid(), job_id=candidates[0]["id"], max_attempts=1)
        if job is None:
            raise RuntimeError("Scheduled consolidation is unavailable")
        self.current["learning_job_id"] = job["id"]
        self.save()
        self.role = "consolidate"
        try:
            engine._execute(self.principal, brain_id, job)
        finally:
            self.current["learning_jobs"] = [Trial(service, self.principal, brain_id).job_result(job["id"])]
            self.save()

    def run_stage(self, fixture):
        stage = {"name": fixture["name"], "fixture": copy.deepcopy(fixture), "state": "started"}
        required = {"recall": {"learn"}, "recall_change": {"learn", "change"}}.get(fixture["name"], set())
        stage["required_history"] = [event["event_id"] for previous in self.report["stages"]
                                     if previous["name"] in required
                                     for event in previous.get("sources", {}).get("input_events", [])]
        self.report["stages"].append(stage)
        self.current, self.role = stage, "orient"
        self.save()
        service = MemoryService(self.database, semantic_processor=SemanticProcessor(
            self.cognition, self.cognition, SEMANTIC_POLICY))
        token = uid()
        settings = Settings(database_url=self.database.dsn,
                            api_keys={hashlib.sha256(token.encode()).hexdigest(): self.principal})
        with loopback_server(create_app(settings, service=service)) as url:
            with MemoryClient(url, token, timeout=260) as memory:
                brain_id = self.report["brains"].get(fixture["brain"])
                if brain_id is None:
                    brain_id = memory.create_brain("Synthetic conversation " + fixture["brain"])["id"]
                    self.report["brains"][fixture["brain"]] = brain_id
                memory.brain_id = brain_id
                stage["brain_id"] = brain_id
                trial = Trial(service, self.principal, brain_id)
                stage["before"] = {"documents": trial.documents(),
                                   "sources": source_snapshot(service, self.principal, brain_id)}
                previous_jobs = {job["id"] for job in JobStore(self.database).list(self.principal, brain_id, limit=200)}
                runtime = BrainRuntime(memory, self.generate, deliver=self.deliver,
                                       instructions=INSTRUCTIONS, policy=RUNTIME_POLICY)
                stage["capabilities"] = runtime.capabilities()
                self.save()
                try:
                    result = runtime.respond(fixture["text"], message_id=fixture["name"],
                                             conversation_id="conversation-" + fixture["name"])
                    stage["turn"] = asdict(result)
                    stage["runtime"] = runtime.journal.get(result.turn_id)
                    stage["sources"] = runtime.journal.sources(result.turn_id)
                    if fixture["name"] == "recall":
                        before = self.meter.summary()
                        fresh = BrainRuntime(memory, self.generate, deliver=self.deliver,
                                             instructions=INSTRUCTIONS, policy=RUNTIME_POLICY)
                        replay = fresh.resume(result.turn_id)
                        stage["replay"] = {"result": asdict(replay), "checks_passed": replay.replayed
                                           and replay.text == result.text and self.meter.summary() == before}
                    if fixture["learn"]:
                        self.learn(service, brain_id, previous_jobs)
                except Exception as error:
                    stage["error"] = safe_error(error)
                    rows = runtime.journal.list()
                    row = next((row for row in rows if row["conversation_id"] == "conversation-" + fixture["name"]), None)
                    if row is not None:
                        stage["runtime"] = row
                        stage["sources"] = runtime.journal.sources(row["turn_id"])
                stage["after"] = {"documents": trial.documents(),
                                  "sources": source_snapshot(service, self.principal, brain_id),
                                  "jobs": JobStore(self.database).list(self.principal, brain_id, limit=200)}
                if stage.get("runtime", {}).get("orientation_job_id"):
                    stage["orientation"] = trial.job_result(stage["runtime"]["orientation_job_id"])
        stage["context_audit"] = context_audit(stage)
        stage["checks"] = stage_checks(stage)
        stage["answer_review"] = {"status": "pending_developer_review", "rubric": fixture["review"]}
        stage["state"] = "finished"
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true", help="Show the frozen experiment without I/O or API calls")
    modes.add_argument("--live", action="store_true", help="Authorize this bounded paid experiment")
    modes.add_argument("--resume", action="store_true", help="Read the saved report only; never spend again")
    parser.add_argument("--output", type=Path, default=Path(".local/deepseek-conversation"))
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, indent=2))
        return
    if args.resume:
        saved = json.loads((args.output / "report.json").read_text())
        print(json.dumps({"state": saved["state"], "usage": saved.get("usage"),
                          "stages": [{"name": stage["name"], "state": stage["state"],
                                      "checks": stage.get("checks"), "answer_review": stage.get("answer_review")}
                                     for stage in saved["stages"]]}, ensure_ascii=False, indent=2))
        return
    if args.output.exists():
        parser.error("Output exists; paid work will not be repeated. Use --resume for read-only inspection.")
    settings = Settings.from_env()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        parser.error("DEEPSEEK_API_KEY must be configured locally")
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {"version": 1, "state": "started", "plan": plan(), "plan_sha256": digest(plan()),
              "code_fingerprints": fingerprints(), "principal": asdict(Principal(uid(), uid())),
              "brains": {}, "stages": [], "limitations": [
                  "Synthetic small sample, not a long-term benchmark or human-neuron simulation.",
                  "Real loopback TCP API and PostgreSQL; local durable delivery sink, not a QQ channel.",
                  "Service/client/runtime recreated between turns; not OS process-crash recovery.",
                  "Fixed lexical recall path; no semantic recall controller or embedding calls.",
                  "Only two scheduled consolidation windows executed; probe learning remains pending.",
                  "Pipeline checks do not certify final answer semantics; developer review is separate."]}
    save(args.output / "report.json", report)
    ledger = args.output / "calls.jsonl"

    def meter():
        return MeteredTransport(ledger, max_calls=MAX_CALLS,
                                max_output_tokens=MAX_OUTPUT_TOKENS, capture_content=False)

    semantic_meter, host_meter = meter(), meter()
    try:
        with ChatCompletionsSemanticProvider("https://api.deepseek.com", key, MODEL,
                                             thinking="enabled", transport=semantic_meter) as provider:
            with ChatCompletionsHost("https://api.deepseek.com", key, MODEL,
                                     transport=host_meter, max_output_tokens=4096,
                                     timeout_seconds=180) as host:
                trial = ConversationTrial(args.output, report,
                                          Database(settings.database_url, settings.migration_database_url),
                                          provider, host, semantic_meter)
                for fixture in FIXTURES:
                    print(json.dumps({"stage": fixture["name"], "state": "started"}), flush=True)
                    trial.run_stage(fixture)
                    print(json.dumps({"stage": fixture["name"], "checks": report["stages"][-1]["checks"],
                                      "usage": semantic_meter.summary()}, ensure_ascii=False), flush=True)
                    if report["stages"][-1].get("error") or semantic_meter.summary()["usage_unknown_calls"]:
                        report["stopped_reason"] = "execution_error_or_unknown_provider_usage"
                        break
                report["state"] = "finished"
    except Exception as error:
        report.update(state="interrupted", error=safe_error(error))
    finally:
        report["usage"] = semantic_meter.summary()
        report["call_ledger"] = semantic_meter.records
        semantic_meter.close()
        host_meter.close()
        save(args.output / "report.json", report)
    if report["state"] != "finished" or len(report["stages"]) != len(FIXTURES) \
            or any(not all(stage.get("checks", {}).values()) for stage in report["stages"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
