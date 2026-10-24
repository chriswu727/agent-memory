"""Opt-in live evaluation using synthetic conversations and isolated database identities."""

import argparse
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agent_memory.client import default_budget, default_read_fence, integration_capabilities
from agent_memory.cognition import render_context
from agent_memory.config import Settings
from agent_memory.context_views import render_recall
from agent_memory.contracts import CONTRACT_VERSION
from agent_memory.helpers import Principal, digest, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.semantic import SemanticPolicy, SemanticProcessor
from agent_memory.semantic_provider import ChatCompletionsSemanticProvider
from agent_memory.service import MemoryService
from agent_memory.storage import Database
from examples.evaluation_support import MeteredTransport

SCENARIOS = {
    "temporal": [
        ["从2026年10月20日00:00 UTC起，我的一般回复偏好是简短回答。这项偏好没有预定结束日期，也没有其他情境条件。"],
        ["仅在2026年10月24日00:00 UTC到2026年10月25日00:00 UTC期间，我的一般回复偏好是详细解释。这只是一天的临时例外；结束后恢复简短回答。"],
        ["从2026年10月26日00:00 UTC起，我的一般回复偏好长期改为先给结论，再解释理由。这次替代以前的一般简短回答偏好，没有预定结束日期。"],
    ],
    "conditional": [
        ["从2026年10月20日00:00 UTC起，我平常喜欢直接得到建议，没有预定结束日期，也没有其他情境条件。"],
        ["从2026年10月21日00:00 UTC起，只有我正在备考时，我才希望先被倾听、不主动得到建议。这是备考情境下的例外；非备考时仍按平常偏好处理。我没有在这条消息中说明自己现在是否备考。"],
        ["关于2026年10月24日12:00 UTC的备考状态，两份同时记录的自述分别是“我正在备考”和“我没有在备考”。我无法确定哪份准确，这两份记录目前互相冲突，后一句不是对前一句的更正。",
         "只有处于我称为“银色状态”的时候，我才需要诗歌式回答；这个状态的含义我还没有解释。"],
    ],
    "abstraction": [
        ["2026年10月20日，我在开始复习前绕公园走了十分钟，然后打开教材。那次穿的是蓝色外套。",
         "2026年10月22日，我先在河边散步，回来以后才开始看复习资料。那次我没有穿外套。"],
        ["我又想起前面提到的那两次复习前的经历。"],
    ],
}


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Trial:
    def __init__(self, service, principal, brain_id):
        self.service, self.principal, self.brain_id = service, principal, brain_id

    def action(self, action, payload):
        response = self.service.handle(self.principal, {
            "message_type": "request", "contract_version": CONTRACT_VERSION, "request_id": uid(),
            "action": action, "target": {"brain_ref": self.brain_id, "conversation_ref": "synthetic-evaluation",
                                         "branch_ref": "main"}, "payload": payload,
        })
        if response["outcome"] not in {"completed", "partial"}:
            raise RuntimeError("Evaluation action failed: " + response.get("error", {}).get("code", "unknown"))
        return response["result"]

    def observe(self, text, key):
        return self.action("observe", {"idempotency_key": key, "events": [{
            "source_event_key": key, "source_revision": 1, "producer_ref": "synthetic-live-evaluation",
            "actor_ref": "user", "actor_role": "user", "event_kind": "message",
            "content": {"media_type": "text/plain", "text": text},
            "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
            "interpretation_hint": "real_world_report", "delivery_state": "received",
        }]})

    def learn(self, checkpoint, job_id=None):
        if job_id is not None:
            result = self.job_result(job_id)
            if result["state"] not in {"completed", "failed", "cancelled"}:
                raise RuntimeError("Interrupted paid job requires inspection before resuming")
            return result
        jobs, engine = JobStore(self.service.database), LearningEngine(self.service)
        while job := jobs.claim(self.principal, self.brain_id, worker_id=uid(), kinds=["extract", "rebuild"]):
            engine._execute(self.principal, self.brain_id, job)
        job = jobs.claim(self.principal, self.brain_id, worker_id=uid(), kinds=["consolidate"], lease_seconds=120)
        if job is None:
            raise RuntimeError("No new consolidation job; refusing to repeat a completed paid stage")
        checkpoint(job["id"])
        error = None
        try:
            engine._execute(self.principal, self.brain_id, job)
        except Exception as exc:
            error = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
        return self.job_result(job["id"], error)

    def job_result(self, job_id, error=None):
        with self.service.database.transaction(self.principal) as conn:
            params = self.service._params(self.principal, self.brain_id)
            row = conn.execute("SELECT state,result,last_error,model_calls,input_bytes FROM memory_jobs "
                               "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                               (*params, job_id)).fetchone()
        return {**dict(row), "job_id": job_id, "execution_error": error}

    def documents(self):
        with self.service.database.transaction(self.principal) as conn:
            brain = self.service._brain(conn, self.principal, self.brain_id)
            params = self.service._params(self.principal, self.brain_id)
            memories = [self.service._rebind(row["document"], self.principal, brain) for row in conn.execute(
                "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND is_current AND NOT invalidated AND NOT deleted AND NOT suppressed", params)]
            relations = [self.service._rebind(row["document"], self.principal, brain) for row in conn.execute(
                "SELECT document FROM memory_belief_relations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND NOT invalidated AND NOT deleted AND document IS NOT NULL", params)]
        return {"memories": memories, "relations": relations}

    def inspect_time(self, at, query, input_event_ids, context_budget=48000):
        with patch("agent_memory.cognition.now", return_value=at):
            recall = self.action("recall", {"query": query, "intent": "explore", "cue_event_ids": [],
                                            "additional_cues": [], "read_fence": default_read_fence(),
                                            "budget": default_budget(context_budget)})
            packet = self.action("prepare", {"turn_id": "evaluation-" + uid(), "latest_input_event_ids": input_event_ids,
                                              "read_fence": default_read_fence(), "budget": default_budget(context_budget),
                                              "capabilities": integration_capabilities()})
        return {"at": at, "clock": "injected_domain_clock_not_host_time_metadata",
                "items": recall["items"], "blocks": packet["blocks"], "coverage": recall["coverage"],
                "belief_relations": recall.get("belief_relations", []),
                "context_budget": context_budget,
                "model_context": {"recall": json.loads(render_recall(recall)),
                                  "prepare": json.loads(render_context(packet))},
                "context_usage": {"recall": recall["usage"]["context_tokens"],
                                  "prepare": packet["usage"]["context_tokens"]}}


EVALUATION_TIMES = ("2026-10-24T12:00:00Z", "2026-10-25T12:00:00Z", "2026-10-27T12:00:00Z")
EVALUATION_QUERY = "回复偏好 简短 详细解释 建议 备考 复习 散步 公园 河边 外套 衣着 提醒 回忆 诗歌 银色 状态"


def _version(value):
    return value.get("memory_id"), value.get("revision")


def _support_events(memory):
    return {ref["event_id"] for ref in memory.get("evidence_refs", []) if ref.get("relationship") == "supports"}


def _semantic(memory):
    return (memory.get("derivation", {}).get("rule_version") == "bounded-semantic-v1"
            and memory.get("derivation", {}).get("method") == "aggregation"
            and memory.get("verification", {}).get("method") == "semantic_model"
            and memory.get("epistemic_status") == "derived_inference")


def _interval(start, end=None):
    return {"kind": "interval", "start": {"kind": "instant", "at": start},
            "end": {"kind": "instant", "at": end} if end else {"kind": "unbounded"}}


def _same_interval(actual, expected):
    if not isinstance(actual, dict) or actual.get("kind") != "interval":
        return False
    for boundary in ("start", "end"):
        left, right = actual.get(boundary, {}), expected[boundary]
        if left.get("kind") != right["kind"]:
            return False
        if right["kind"] == "instant":
            try:
                if datetime.fromisoformat(left["at"]) != datetime.fromisoformat(right["at"]):
                    return False
            except (KeyError, TypeError, ValueError):
                return False
    return True


def _evidence_key(reference):
    return json.dumps({key: value for key, value in reference.items() if key != "relationship"}, sort_keys=True)


def _condition_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _condition_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _condition_nodes(child)


def _positive_condition_nodes(value):
    for node in _condition_nodes(value):
        if node.get("op") == "holds" and node.get("polarity", "positive") == "positive":
            yield {key: item for key, item in node.items() if key != "polarity"}


def _fixture_preference(memory, name, index):
    text = memory.get("statement", "").lower()
    groups = (("简短", "简洁", "精简", "简明", "concise", "brief", "short"),) if name == "temporal" and index == 0 else (
        (("详细", "详尽", "细致", "detailed", "elaborat"),) if name == "temporal" and index == 1 else
        (("结论", "答案", "conclusion"), ("理由", "原因", "解释", "reason")) if name == "temporal" else
        (("直接", "direct"), ("建议", "advice")) if index == 0 else
        (("倾听", "聆听", "听完", "听我", "listen"),))
    return memory.get("claim_kind") == "reported_preference" and all(any(word in text for word in group) for group in groups)


def _prepared_memory(observation, memory):
    found = []
    for block in observation.get("blocks", []):
        if block.get("section") not in {"relevant_memory", "conditional_memory"}:
            continue
        try:
            content = json.loads(block["text"])
        except (TypeError, ValueError, KeyError):
            continue
        if content.get("statement") == memory["statement"]:
            found.append((block["section"], content))
        meanings = {item["slot_id"]: item["meaning"]
                    for item in content.get("definition", {}).get("shared_claims", [])}
        for instance in content.get("instances", []):
            for binding in instance.get("shared_bindings", []):
                if _version(binding.get("memory_version", {})) == _version(memory):
                    found.append((block["section"], {**meanings.get(binding["slot_id"], {}),
                                                      **binding.get("attributes", {})}))
            for residual in instance.get("residuals", []):
                if _version(residual.get("memory_version", {})) == _version(memory):
                    found.append((block["section"], residual["fact"]))
    return found


def _view_matches(observation, memory, *, applicability, resolution):
    matching = [item for item in observation.get("items", []) if _version(item.get("memory", {})) == _version(memory)]
    prepared = _prepared_memory(observation, memory)
    if len(matching) != 1 or not prepared:
        return False
    current = applicability == "true" and resolution == "unchanged"
    expected_section = "relevant_memory" if current else "conditional_memory"
    for value in [matching[0], *[content for _, content in prepared]]:
        assessment = value.get("applicability", {})
        belief = value.get("belief_resolution", {})
        if (assessment.get("value") != applicability or assessment.get("conflict") is not False
                or belief.get("state", "unchanged") != resolution
                or belief and belief.get("complete") is not True):
            return False
    return all(section == expected_section for section, _ in prepared)


def stage_checks(name, index, stage, previous):
    result = stage["job"].get("result") or {}
    publication = result.get("publication", {})
    documents = stage.get("documents", {})
    memories = {_version(memory): memory for memory in documents.get("memories", [])}
    published_refs = {_version(ref) for ref in publication.get("memory_versions", [])}
    published = [memories[key] for key in published_refs if key in memories and _semantic(memories[key])]
    inputs = set(stage.get("input_event_ids", []))
    observations = {value["at"]: value for value in stage.get("recall", [])}
    checks = {"job_completed": stage["job"]["state"] == "completed" and result.get("status") == "completed",
              "semantic_claim_published": bool(published) and len(published) == len(published_refs),
              "new_input_supports_semantic_claim": any(_support_events(memory) & inputs for memory in published),
              "input_processed": bool(inputs) and result.get("learning_window", {}).get("processing_succeeded") is True
                  and inputs <= {ref["event_id"] for ref in result.get("learning_input_refs", [])},
              "recall_observed_at_all_fixture_times": set(EVALUATION_TIMES) <= observations.keys()}
    checks["published_memories_recalled_and_prepared"] = bool(published) and all(any(
        any(_version(item.get("memory", {})) == _version(memory) for item in observation.get("items", []))
        and bool(_prepared_memory(observation, memory)) for observation in observations.values()) for memory in published)
    previous_memories = {_version(memory): memory for memory in (previous or {}).get("documents", {}).get("memories", [])}
    if name == "abstraction" and index == 1:
        checks.pop("new_input_supports_semantic_claim")
    if index:
        immutable = ("statement", "world", "memory_kind", "representation", "claim_kind", "conditions",
                     "valid_time", "valid_during", "evidence_refs", "derivation", "counterexamples")
        checks["prior_semantics_preserved"] = bool(previous_memories) and all(
            key in memories and all(memories[key].get(field) == old.get(field) for field in immutable)
            for key, old in previous_memories.items())
    desired = ("exception" if index == 1 and name in {"temporal", "conditional"} else
               "supersedes" if name == "temporal" and index == 2 else
               "abstracts" if name == "abstraction" and index == 1 else None)
    relation_refs = {(ref["relation_id"], ref["revision"]) for ref in publication.get("belief_relation_versions", [])}
    relations = [relation for relation in documents.get("relations", [])
                 if (relation["relation_id"], relation["revision"]) in relation_refs and relation["kind"] == desired]
    valid_links = []
    if desired:
        for relation in relations:
            source = _version(relation["source_memory_version"])
            targets = {_version(ref) for ref in relation["target_memory_versions"]}
            endpoint_refs = {_evidence_key(ref) for key in {source, *targets} if key in memories
                             for ref in memories[key].get("evidence_refs", [])}
            relation_evidence = {_evidence_key(ref) for ref in relation["evidence_refs"]}
            if (source in published_refs and targets and targets <= previous_memories.keys()
                    and source not in targets and source in memories
                    and endpoint_refs <= relation_evidence
                    and {ref["event_id"] for ref in relation["evidence_refs"]} <= set(relation["root_event_ids"])):
                valid_links.append(relation)
        checks[desired + "_published_with_correct_direction_and_sources"] = bool(valid_links)
    if name in {"temporal", "conditional"}:
        if index == 0:
            candidates = [memory for memory in published if memory.get("claim_kind") == "reported_preference"]
        else:
            candidates = [memories[_version(link["source_memory_version"])] for link in valid_links]
        if index < 2 or name == "temporal":
            checks["fixture_preference_content_present"] = any(_fixture_preference(memory, name, index) for memory in candidates)
        if name == "temporal":
            validity = (_interval("2026-10-20T00:00:00Z") if index == 0 else
                        _interval("2026-10-24T00:00:00Z", "2026-10-25T00:00:00Z") if index == 1 else
                        _interval("2026-10-26T00:00:00Z"))
            checks["fixture_validity_preserved"] = any(_same_interval(memory.get("valid_during"), validity) for memory in candidates)
            if index:
                checks["relation_fixture_validity_preserved"] = any(_same_interval(link["valid_during"], validity) for link in valid_links)
            expected = ("true", "true", "true") if index == 0 else ("true", "false", "false") if index == 1 else ("false", "false", "true")
            checks["new_preference_temporal_applicability"] = bool(candidates) and any(all(
                at in observations and _view_matches(observations[at], memory, applicability=value, resolution="unchanged")
                for at, value in zip(EVALUATION_TIMES, expected, strict=True)) for memory in candidates)
            if index:
                target_states = ("exception_applies", "unchanged", "unchanged") if index == 1 else ("exception_applies", "unchanged", "superseded")
                checks["old_preference_temporal_resolution"] = any(all(
                    at in observations and _view_matches(observations[at], memories[_version(target)],
                                                        applicability="true", resolution=resolution)
                    for target in link["target_memory_versions"]
                    for at, resolution in zip(EVALUATION_TIMES, target_states, strict=True)) for link in valid_links)
        elif index == 0:
            checks["baseline_preference_applicable"] = bool(candidates) and any(all(
                at in observations and _view_matches(observations[at], memory, applicability="true", resolution="unchanged")
                for at in EVALUATION_TIMES) for memory in candidates)
        elif index == 1:
            expected_condition = {"op": "holds", "subject_ref": "user", "predicate_ref": {"id": "core.activity", "version": 1},
                                  "value": {"type": "concept", "id": "activity:exam_preparation"}}
            checks["registered_condition_preserved"] = any(
                expected_condition in list(_positive_condition_nodes(link["conditions"]))
                and expected_condition in list(_positive_condition_nodes(memories[_version(link["source_memory_version"])].get("conditions")))
                for link in valid_links)
            checks["unknown_condition_not_assumed_true"] = bool(valid_links) and any(all(
                at in observations and _view_matches(observations[at], memories[_version(target)],
                                                    applicability="true", resolution="uncertain")
                for target in link["target_memory_versions"] for at in EVALUATION_TIMES) for link in valid_links)
        else:
            unfamiliar = set(stage.get("input_event_ids", [])[1:])
            poetry = [memory for memory in published if _support_events(memory) & unfamiliar
                      and memory.get("claim_kind") == "reported_preference"]
            checks["unfamiliar_condition_not_coerced"] = bool(poetry) and all(
                any(node.get("op") == "unresolved" for node in _condition_nodes(memory.get("conditions")))
                and not any(node.get("op") in {"holds", "always"} for node in _condition_nodes(memory.get("conditions")))
                for memory in poetry)
            prior_targets = {_version(target) for link in documents.get("relations", []) if link["kind"] == "exception"
                             for target in link["target_memory_versions"]}
            checks["conflicting_state_not_settled"] = bool(prior_targets) and all(any(
                _version(item["memory"]) == key and item.get("belief_resolution", {}).get("state") in {"uncertain", "conflicted"}
                for item in observations.get(EVALUATION_TIMES[0], {}).get("items", [])) for key in prior_targets)
    elif name == "abstraction":
        if index == 0:
            checks["two_independent_semantic_instances"] = len(inputs) == 2 and all(any(
                _support_events(memory) == {identifier} for memory in published) for identifier in inputs)
        else:
            checks["abstraction_retains_independent_instance_parents"] = any(
                len(link["target_memory_versions"]) >= 2
                and {_version(ref) for ref in link["target_memory_versions"]}
                    <= {_version(ref) for ref in memories[_version(link["source_memory_version"])]["derivation"]["source_memory_versions"]}
                and memories[_version(link["source_memory_version"])].get("representation") == "schema"
                and memories[_version(link["source_memory_version"])].get("claim_kind") == "inferred_pattern"
                and all(_support_events(memories[_version(target)]) for target in link["target_memory_versions"])
                and all(not _support_events(memories[_version(left)]) & _support_events(memories[_version(right)])
                        for position, left in enumerate(link["target_memory_versions"])
                        for right in link["target_memory_versions"][position + 1:])
                and _support_events(memories[_version(link["source_memory_version"])])
                    == set().union(*(_support_events(memories[_version(target)]) for target in link["target_memory_versions"]))
                for link in valid_links)
            checks["abstraction_is_bounded_to_observed_instances"] = any(
                any(word in memories[_version(link["source_memory_version"])]["statement"].lower()
                    for word in ("两次", "两条", "这两", "两个", "记录", "实例", "样本", "two", "observed", "recorded"))
                and not any(word in memories[_version(link["source_memory_version"])]["statement"].lower()
                            for word in ("总是", "每次", "永远", "必定", "always", "every time"))
                for link in valid_links)
            checks["abstraction_shared_walking_and_study_content_present"] = any(
                any(word in memories[_version(link["source_memory_version"])]["statement"].lower()
                    for word in ("复习", "学习", "study", "studying", "revision"))
                and any(word in memories[_version(link["source_memory_version"])]["statement"].lower()
                        for word in ("散步", "走", "步行", "walk"))
                for link in valid_links)
    return checks


def code_fingerprints():
    paths = ["examples/evaluate_deepseek.py", "examples/evaluation_support.py",
             "src/agent_memory/semantic.py", "src/agent_memory/semantic_provider.py",
             "src/agent_memory/recall_provider.py", "src/agent_memory/learning.py",
             "src/agent_memory/consolidation_planning.py", "src/agent_memory/context_views.py",
             "src/agent_memory/cognition.py", "src/agent_memory/applicability.py"]
    root = Path(__file__).resolve().parents[1]
    return {path: digest((root / path).read_text()) for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly permit billable DeepSeek calls")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-after-failure", metavar="REASON",
                        help="Diagnosed continuation of a failed scenario; preserves all earlier failures")
    parser.add_argument("--recheck", action="store_true", help="Recheck saved evidence without API or database calls")
    parser.add_argument("--scenario", choices=SCENARIOS, default="temporal")
    parser.add_argument("--stage-limit", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=16)
    parser.add_argument("--max-wall-time-ms", type=int, default=120000,
                        help="Total time for one proposal/verification stage, at most 300000 ms")
    parser.add_argument("--output", type=Path, default=Path(".local/deepseek-evaluation"))
    args = parser.parse_args()
    if args.continue_after_failure and (not args.live or not args.resume or args.recheck):
        parser.error("A diagnosed continuation requires --live and --resume, without --recheck")
    if args.continue_after_failure and not 1 <= len(args.continue_after_failure.strip()) <= 1000:
        parser.error("Continuation reason must contain 1–1000 characters")
    if not 1 <= args.max_wall_time_ms <= 300000:
        parser.error("Stage time limit must be within 1–300000 ms")
    path = args.output / "report.json"
    if args.recheck:
        if args.live or args.resume or not path.is_file():
            parser.error("Recheck requires an existing report, without --live or --resume")
        report = json.loads(path.read_text())
        for name, scenario in report["scenarios"].items():
            previous = None
            for stage in scenario["stages"]:
                stage["checks"] = stage_checks(name, stage["index"], stage, previous)
                previous = stage
        save(path, report)
        print(json.dumps({name: [stage["checks"] for stage in scenario["stages"]]
                          for name, scenario in report["scenarios"].items()}))
        if any(not all(stage["checks"].values()) for scenario in report["scenarios"].values()
               for stage in scenario["stages"]):
            raise SystemExit(1)
        return
    if not args.live:
        parser.error("Use --live only when billable testing is authorized")
    if not 1 <= args.stage_limit <= len(SCENARIOS[args.scenario]) or not 1 <= args.max_calls <= 32:
        parser.error("Invalid bounded stage or call limit")
    settings = Settings.from_env()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        parser.error("DEEPSEEK_API_KEY must be configured locally")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.resume and not path.exists():
        parser.error("Cannot resume without an existing report and its job identities")
    if not path.exists() and (args.output / "calls.jsonl").exists():
        parser.error("Ledger exists without its report; inspect before starting another paid run")
    if path.exists() and not args.resume:
        parser.error("Report exists; use --resume to preserve cumulative cost and earlier results")
    report = json.loads(path.read_text()) if path.exists() else {
        "version": 1, "model": "deepseek-v4-pro", "thinking": "enabled", "synthetic_only": True,
        "code_fingerprints": code_fingerprints(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "principal": asdict(Principal(uid(), uid())), "scenarios": {},
        "limitations": ["Small diagnostic sample, not a long-term benchmark",
                        "Temporal checks inject the domain clock; no host historical-context API is claimed",
                        "No end-user answer model is used as a judge"],
    }
    if (report["version"] != 1 or report["model"] != "deepseek-v4-pro"
            or report["thinking"] != "enabled" or report["synthetic_only"] is not True):
        parser.error("Saved run configuration does not match this evaluator")
    for name, saved_scenario in report["scenarios"].items():
        if saved_scenario.get("fixture") != SCENARIOS.get(name):
            parser.error("Saved fixture differs; preserve this report and use a new evaluation directory")
        if saved_scenario.get("pending", {}).get("index", len(saved_scenario["stages"])) != len(saved_scenario["stages"]):
            parser.error("Pending stage does not match saved progress")
    principal = Principal(**report["principal"])
    meter = MeteredTransport(args.output / "calls.jsonl", max_calls=args.max_calls, max_output_tokens=262144)
    policy = SemanticPolicy(max_output_tokens=16384, max_response_bytes=65536,
                            max_wall_time_ms=args.max_wall_time_ms)
    with ChatCompletionsSemanticProvider("https://api.deepseek.com", key, report["model"],
                                         thinking="enabled", transport=meter) as provider:
        service = MemoryService(Database(settings.database_url, settings.migration_database_url),
                                semantic_processor=SemanticProcessor(provider, provider, policy))
        scenario = report["scenarios"].setdefault(args.scenario, {})
        if not scenario:
            scenario.update(brain_id=service.create_brain(principal, "Synthetic live evaluation " + args.scenario)["id"],
                            stages=[], fixture=SCENARIOS[args.scenario])
        trial = Trial(service, principal, scenario["brain_id"])
        save(path, report)
        failed = [stage["index"] for stage in scenario["stages"] if not all(stage["checks"].values())]
        if failed:
            if not args.continue_after_failure:
                parser.error("An earlier stage failed; diagnose from the saved report before spending more")
            scenario.setdefault("diagnosed_continuations", []).append({
                "after_stages": failed, "reason": args.continue_after_failure,
                "next_stage": len(scenario["stages"]), "code_fingerprints": code_fingerprints(),
            })
            save(path, report)
        for index in range(len(scenario["stages"]), args.stage_limit):
            print(json.dumps({"scenario": args.scenario, "stage": index, "status": "started"}), flush=True)
            pending = scenario.setdefault("pending", {"index": index})
            pending.setdefault("code_fingerprints", code_fingerprints())
            pending.setdefault("execution_policy", asdict(policy))
            if "input_event_ids" not in pending:
                receipts = [trial.observe(text, f"{args.scenario}:{index}:{item}")
                            for item, text in enumerate(SCENARIOS[args.scenario][index])]
                pending["input_event_ids"] = [identifier for receipt in receipts
                                              for identifier in receipt["accepted_event_ids"]]
                save(path, report)

            def checkpoint(job_id):
                pending["job_id"] = job_id
                save(path, report)

            job = trial.learn(checkpoint, pending.get("job_id"))
            stage = {"index": index, "input_event_ids": pending["input_event_ids"],
                     "job": job, "documents": trial.documents()}
            stage["code_fingerprints"] = pending["code_fingerprints"]
            stage["execution_policy"] = pending["execution_policy"]
            pending["result"] = stage
            report["usage"] = meter.summary()
            save(path, report)
            if job["state"] == "completed":
                try:
                    stage["recall"] = [trial.inspect_time(at, EVALUATION_QUERY, stage["input_event_ids"]) for at in (
                        "2026-10-24T12:00:00Z", "2026-10-25T12:00:00Z", "2026-10-27T12:00:00Z")]
                except Exception as exc:
                    stage["inspection_error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
            stage["checks"] = stage_checks(args.scenario, index, stage,
                                            scenario["stages"][-1] if scenario["stages"] else None)
            scenario["stages"].append(stage)
            del scenario["pending"]
            save(path, report)
            print(json.dumps({"scenario": args.scenario, "stage": index, "checks": stage["checks"],
                              "processing_status": (stage["job"].get("result") or {}).get("status"),
                              "usage": report["usage"]}), flush=True)
            if not all(stage["checks"].values()):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
