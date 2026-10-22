import copy
import json
import math
import re
import time
import unicodedata
from datetime import UTC, datetime
from typing import Any

from .contracts import validate
from .helpers import DomainError, digest, now, uid

RULE_VERSION = "literal-episodes-v1"
RETRIEVAL_VERSION = "unicode-lexical-v1"
_QUESTION = re.compile(
    r"[?？]|(?:吗|么|呢)\s*[。.!！]?$|是否|还记得|记不记得|有没有|"
    r"\b(?:do|did|does|can|could|would|will|is|are|was|were|have|has)\s+"
    r"(?:you|we|i|he|she|they|it)\b|\b(?:what|when|where|who|why|how)\b",
    re.IGNORECASE,
)
_HISTORICAL = re.compile(
    r"还记得|记不记得|以前.{0,12}(?:说|告诉|提)|曾经.{0,12}(?:说|告诉|提)|"
    r"\bremember\b|\b(?:told|mentioned|said).{0,20}(?:before|previously)\b",
    re.IGNORECASE,
)
_WORLD_MARKERS = (
    ("dream", re.compile(r"梦见|梦到|做梦|\b(?:dreamt|dreamed|dreaming)\b", re.I)),
    ("hypothetical", re.compile(r"假如|假设|如果|要是|\b(?:imagine|suppose|if)\b", re.I)),
    ("roleplay", re.compile(r"角色扮演|扮演|\brole[ -]?play\b", re.I)),
    ("quoted", re.compile(r"引用|原文如下|\b(?:quote|quotation)\b", re.I)),
)
_IGNORED_TERMS = frozenset(
    "a an the i you we they it is am are was were do does did have has had "
    "remember recall what when where who why how please about that this my your our "
    "before previously said tell told to of and or in on at for with".split()
)
_CJK_FILLERS = re.compile(
    "还记得|记不记得|有没有|能不能|请问|以前|之前|曾经|那次|这个|那个|什么|"
    "我们|你们|他们|她们|告诉|说过|记得|一下"
)


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else _serialized(value)
    return len(text.encode("utf-8"))


def _stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, AttributeError) as error:
        raise DomainError("validation_failed", "An aware RFC3339 time is required") from error


def _range(value: dict) -> tuple[datetime | None, datetime | None]:
    start = _stamp(value["start"]) if value["start"] else None
    end = _stamp(value["end"]) if value["end"] else None
    if start and end and end < start:
        raise DomainError("validation_failed", "Time range ends before its start")
    return start, end


def _content_text(content: dict) -> str:
    if content["media_type"] == "text/plain":
        return content["text"]
    if content["media_type"] == "application/json":
        return _serialized(content["structured"])
    return content["artifact_ref"]


def classify_segments(content: dict) -> list[dict]:
    validate("EventContent", content)
    if content["media_type"] != "text/plain":
        return [{"act": "not_applicable"}]
    text = content["text"]
    segments = []
    for match in re.finditer(r"[^。.!！?？\n]+[。.!！?？]*", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        sentence = text[start:end]
        act = "inquiry" if _QUESTION.search(sentence) else "direct_statement"
        if act != "inquiry" and re.match(r"(?:请|帮我|不要|记住|please\b)", sentence, re.I):
            act = "instruction"
        segments.append({
            "act": act,
            "span": {"start": start, "end": end, "unit": "unicode_code_point"},
        })
    return segments or [{"act": "unknown"}]


def _segments(event: dict) -> list[dict]:
    content = event["observed"]["content"]
    computed = classify_segments(content)
    text = _content_text(content)
    declared = event["utterance_segments"]
    for segment in computed:
        span = segment.get("span", {"start": 0, "end": len(text)})
        for original in declared:
            original_span = original.get("span", {"start": 0, "end": len(text)})
            overlap = max(span["start"], original_span["start"]) < min(
                span["end"], original_span["end"]
            )
            if overlap and original["act"] in {"inquiry", "presupposition", "instruction"}:
                segment["act"] = original["act"]
    return computed


def _world(observed: dict, text: str) -> str:
    hint = observed["interpretation_hint"]
    if hint in {"roleplay", "hypothetical", "dream"}:
        return hint
    if hint == "quotation":
        return "quoted"
    for name, pattern in _WORLD_MARKERS:
        if pattern.search(text):
            return name
    return "real_world"


def extract_event(event: dict) -> list[dict]:
    validate("Event", event)
    observed = event["observed"]
    _range(observed["occurred_time"])
    if digest(observed["content"]) != event["content_sha256"]:
        raise DomainError("invalid_evidence", "Event content hash does not match")
    if observed["delivery_state"] in {"draft", "cancelled", "unknown"}:
        return []
    if observed["actor_role"] in {"system"} or observed["event_kind"] in {"delivery_receipt", "source_correction"}:
        return []
    text = _content_text(observed["content"])
    memories = []
    for segment in _segments(event):
        span = segment.get("span", {"start": 0, "end": len(text),
                                    "unit": "unicode_code_point"})
        excerpt = text[span["start"]:span["end"]]
        if not excerpt.strip():
            continue
        evidence = {
            "event_id": event["event_id"],
            "event_revision": event["revision"],
            "source_content_sha256": event["content_sha256"],
            "relationship": "supports",
        }
        if observed["content"]["media_type"] == "text/plain":
            evidence["span"] = copy.deepcopy(span)
        memory_id = "memory-" + digest([event["event_id"], event["revision"], span])[:32]
        path_id = "path-" + memory_id
        is_tool = observed["actor_role"] == "tool" and event["origin_verification"] == (
            "verified_producer"
        )
        memory = {
            "memory_id": memory_id,
            "revision": 1,
            "scope": copy.deepcopy(event["scope"]),
            "memory_kind": "episodic",
            "statement": (f"{observed['actor_role']} {observed['actor_ref']} "
                          f"({segment['act']}): {excerpt}"),
            "epistemic_status": "observed_result" if is_tool else "supported_report",
            "world": _world(observed, excerpt),
            "evidence_refs": [copy.deepcopy(evidence)],
            "valid_time": copy.deepcopy(observed["occurred_time"]),
            "system_time": copy.deepcopy(event["system_time"]),
            "lifecycle": "active",
            "supersedes_memory_ids": [],
            "derivation": {
                "method": "direct_capture", "rule_version": RULE_VERSION,
                "model_version": None, "source_memory_versions": [],
            },
            "dynamics": {
                "activation": 0.5, "retention_priority": 0.5, "detail_level": "verbatim",
                "last_external_reinforcement_at": None, "last_recalled_at": None,
                "decay_policy_version": "external-use-only-v1", "user_pinned": False,
                "reasons": ["default_policy"],
            },
            "disclosure_basis": {
                "dependency_generation": 0, "validation_state": "current",
                "support_paths": [{
                    "path_id": path_id, "evidence_refs": [copy.deepcopy(evidence)],
                    "source_memory_versions": [], "permitted_scope": copy.deepcopy(event["scope"]),
                    "dependency_generation": 0, "state": "current",
                    "independently_supports_complete_statement": True,
                }],
                "selected_path_ids": [path_id], "release_mode": "supported_scope",
            },
        }
        validate("MemoryAssertion", memory)
        memories.append(memory)
    return memories


def _scope_allows(source: dict, scope: dict) -> bool:
    for field in ("tenant_id", "brain_id", "principal_binding_id", "access_policy_id",
                  "policy_revision", "scope_generation"):
        if source[field] != scope[field]:
            return False
    if source["visibility"] == "conversation":
        if source["conversation_id"] != scope["conversation_id"]:
            return False
    if source["visibility"] == "private" and scope["visibility"] != "private":
        return False
    if source["branch_id"] is not None and source["conversation_id"] == scope["conversation_id"]:
        if source["branch_id"] != scope["branch_id"]:
            return False
    return True


def _terms(text: str) -> tuple[set[str], set[str], set[str]]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    words = set(re.findall(r"[a-z0-9_]+", normalized)) - _IGNORED_TERMS
    runs = re.findall(r"[\u3400-\u9fff]+", _CJK_FILLERS.sub(" ", normalized))
    singles = {char for run in runs for char in run if char not in "的了是吗呢么在我你他她和有"}
    pairs = {run[index:index + 2] for run in runs for index in range(len(run) - 1)}
    return words, pairs, singles


def _lexical_score(query: str, text: str) -> float:
    q_words, q_pairs, q_chars = _terms(query)
    words, pairs, chars = _terms(text)
    word_score = len(q_words & words) / max(1, len(q_words))
    pair_score = len(q_pairs & pairs) / max(1, len(q_pairs))
    char_overlap = len(q_chars & chars)
    char_score = char_overlap / max(1, len(q_chars)) if char_overlap >= 2 else 0
    return word_score + pair_score + 0.15 * char_score


def _usage(budget: dict) -> dict:
    return {
        "context_tokens": 0, "token_counting": "conservative_estimate",
        "model_calls": 0, "embedding_calls": 0, "expansion_steps": 0,
        "elapsed_ms": 0, "actual_cost_microunits": 0,
        "reserved_background_cost_microunits": 0,
        "currency": budget["currency"], "stopped_by": "completed",
    }


def _measure(result: dict) -> int:
    for _ in range(6):
        count = estimate_tokens(result)
        if result["usage"]["context_tokens"] == count:
            return count
        result["usage"]["context_tokens"] = count
    raise DomainError("budget_exceeded", "Unable to stabilize serialized budget accounting")


def _check_budget(budget: dict) -> None:
    if budget["token_counting"] == "exact":
        raise DomainError("unsupported_capability", "The lexical provider has no exact tokenizer")


def _cutoff(request: dict, events: dict, durable: int) -> tuple[dict | None, int | None, set, str]:
    excluded = set(request["cue_event_ids"]) | set(request.get("query_event_ids", []))
    historical = request.get("evidence_mode") == "prior_record_check" or (
        request["intent"] == "cue_recovery" or _HISTORICAL.search(request["query"])
    )
    if not historical:
        return None, None, excluded, "not_requested"
    for event in events.values():
        if _content_text(event["observed"]["content"]).strip() == request["query"].strip():
            if _QUESTION.search(request["query"]):
                excluded.add(event["event_id"])
    boundary = copy.deepcopy(request.get("evidence_cutoff"))
    prior_sequences = [events[key]["sequence"] - 1 for key in excluded if key in events]
    if boundary is None:
        if not prior_sequences:
            return None, None, excluded, "unavailable"
        boundary = {"mode": "through_sequence", "through_sequence": min(prior_sequences)}
    if boundary["mode"] == "before_event":
        if boundary["event_id"] not in events:
            return boundary, None, excluded, "unavailable"
        excluded.add(boundary["event_id"])
        resolved = events[boundary["event_id"]]["sequence"] - 1
    else:
        resolved = boundary["through_sequence"]
    resolved = min([resolved, durable, *prior_sequences])
    if boundary["mode"] == "through_sequence":
        boundary["through_sequence"] = resolved
    return boundary, resolved, excluded, "enforced"


def _cue_descendants(excluded: set, events: dict) -> set:
    excluded = set(excluded)
    while True:
        keys = {event["observed"]["source_event_key"] for event in events.values()
                if event["event_id"] in excluded}
        additions = {event["event_id"] for event in events.values()
                     if keys.intersection(event["observed"].get("parent_source_event_keys", []))}
        if additions <= excluded:
            return excluded
        excluded.update(additions)


def _valid_ref(ref: dict, events: dict, scope: dict) -> bool:
    event = events.get(ref["event_id"])
    if not event or not _scope_allows(event["scope"], scope):
        return False
    if event["observed"]["event_kind"] in {"delivery_receipt", "source_correction"}:
        return False
    if event["revision"] != ref["event_revision"]:
        return False
    if event["system_time"]["superseded_at"] is not None:
        return False
    if event["observed"]["delivery_state"] in {"draft", "cancelled", "unknown"}:
        return False
    if event["content_sha256"] != ref["source_content_sha256"]:
        return False
    if digest(event["observed"]["content"]) != event["content_sha256"]:
        return False
    if "span" in ref:
        text = _content_text(event["observed"]["content"])
        if not 0 <= ref["span"]["start"] < ref["span"]["end"] <= len(text):
            return False
    return True


def evidence_overlaps(first: dict, second: dict) -> bool:
    if (first["event_id"], first["event_revision"], first["source_content_sha256"]) != (
        second["event_id"], second["event_revision"], second["source_content_sha256"]
    ):
        return False
    left, right = first.get("span"), second.get("span")
    return not left or not right or max(left["start"], right["start"]) < min(
        left["end"], right["end"]
    )


def _eligible_source(event: dict, ref: dict, historical: bool, intent: str) -> bool:
    if not historical or intent == "exact_quote":
        return True
    if event["observed"]["actor_role"] in {"assistant", "system"}:
        return False
    span = ref.get("span")
    acts = []
    for segment in _segments(event):
        bounds = segment.get("span")
        if span and bounds and max(span["start"], bounds["start"]) >= min(
            span["end"], bounds["end"]
        ):
            continue
        acts.append(segment["act"])
    return bool(acts) and all(act in {"direct_statement", "correction", "confirmation",
                                      "not_applicable"} for act in acts)


def _evidence_valid(memory: dict, all_memories: dict, events: dict, scope: dict,
                    generation: int, cutoff: int | None, excluded: set, historical: bool,
                    intent: str, visiting: frozenset = frozenset()) -> bool:
    key = (memory["memory_id"], memory["revision"])
    if key in visiting or not _scope_allows(memory["scope"], scope):
        return False
    basis = memory["disclosure_basis"]
    if basis["validation_state"] != "current" or basis["dependency_generation"] != generation:
        return False
    refs = memory["evidence_refs"]
    if not refs or not all(_valid_ref(ref, events, scope) for ref in refs):
        return False
    if historical:
        if cutoff is None:
            return False
        for ref in refs:
            event = events[ref["event_id"]]
            if event["sequence"] > cutoff or event["event_id"] in excluded:
                return False
            if not _eligible_source(event, ref, True, intent):
                return False
    paths = [path for path in basis["support_paths"]
             if path["path_id"] in basis["selected_path_ids"]]
    if not paths or len(paths) != len(basis["selected_path_ids"]):
        return False
    for path in paths:
        if path["state"] != "current" or path["dependency_generation"] != generation:
            return False
        if not _scope_allows(path["permitted_scope"], scope):
            return False
        if not all(ref in refs for ref in path["evidence_refs"]):
            return False
        parents = memory["derivation"]["source_memory_versions"] + path["source_memory_versions"]
        for parent in parents:
            source = all_memories.get((parent["memory_id"], parent["revision"]))
            if not source or source["lifecycle"] in {"retracted", "superseded"}:
                return False
            if not _evidence_valid(source, all_memories, events, scope, generation, cutoff,
                                   excluded, historical, intent, visiting | {key}):
                return False
    return True


def _coverage(state: dict, request: dict, candidate_count: int) -> dict:
    durable = state["durable_sequence"]
    indexed = min(state["indexed_sequence"], durable)
    fence = request["read_fence"]
    satisfied = durable >= fence["minimum_durable_sequence"] and (
        state["dependency_generation"] >= fence.get("minimum_dependency_generation", 0)
    ) and state["scope_generation"] >= fence.get("minimum_scope_generation", 1)
    snapshot = fence.get("snapshot_revision")
    if fence["consistency"] == "snapshot" and snapshot is not None and snapshot != durable:
        satisfied = False
    if state.get("source_truncated", False) and fence["minimum_durable_sequence"] > indexed:
        satisfied = False
    limitations = []
    if indexed < durable:
        limitations.append("index_lag")
    if state.get("candidate_total", candidate_count) > candidate_count or state.get(
        "source_truncated", False
    ):
        limitations.append("partial_scope")
    if state.get("rebuilding", False):
        limitations.append("rebuilding")
    if not satisfied:
        limitations.append("dependency_failure")
    return {
        "durable_through_sequence": durable, "indexed_through_sequence": indexed,
        "tail_covered_through_sequence": indexed, "snapshot_revision": durable,
        "read_fence_satisfied": satisfied, "search_exhaustive": False,
        "limitations": limitations, "dependency_generation": state["dependency_generation"],
        "projection_generation": state["dependency_generation"],
        "rebuilding": state.get("rebuilding", False),
    }


def recall(scope: dict, request: dict, memories: list[dict], events: list[dict],
           state: dict) -> dict:
    validate("BoundScope", scope)
    validate("RecallRequest", request)
    _check_budget(request["budget"])
    started = time.monotonic()
    timestamp = now()
    reference_time = _stamp(timestamp)
    event_map = {event["event_id"]: event for event in events
                 if event["sequence"] <= state["durable_sequence"]
                 and _scope_allows(event["scope"], scope)}
    boundary, cutoff, excluded, history_status = _cutoff(
        request, event_map, state["durable_sequence"]
    )
    excluded = _cue_descendants(excluded, event_map)
    coverage = _coverage(state, request, len(memories))
    if scope["scope_generation"] != state["scope_generation"]:
        raise DomainError("version_conflict", "Scope generation is no longer current")
    snapshot = {
        "snapshot_id": uid(), "created_at": timestamp,
        "observed_through_sequence": state["durable_sequence"],
        "dependency_generation": state["dependency_generation"],
        "scope_generation": state["scope_generation"],
        "query_event_ids": list(request.get("query_event_ids", [])),
        "historical_evidence_status": history_status, "evidence_cutoff": boundary,
        "resolved_evidence_through_sequence": cutoff,
        "excluded_cue_event_ids": sorted(excluded), "exclude_cue_descendants": True,
    }
    result = {
        "recall_id": uid(), "scope": copy.deepcopy(scope), "status": "no_match", "items": [],
        "unknowns": [], "ambiguities": [], "conflicts": [], "coverage": coverage,
        "usage": _usage(request["budget"]), "next_step": "expand_search",
        "retrieval_snapshot": snapshot, "recall_traces": [],
    }
    known = {(memory["memory_id"], memory["revision"]) for memory in memories}
    candidates = list(memories)
    if coverage["read_fence_satisfied"]:
        for event in sorted(event_map.values(), key=lambda item: item["sequence"]):
            if event["sequence"] <= state["indexed_sequence"]:
                continue
            if (time.monotonic() - started) * 1000 >= request["budget"]["max_wall_time_ms"]:
                break
            for memory in extract_event(event):
                key = (memory["memory_id"], memory["revision"])
                if key in known:
                    continue
                memory["disclosure_basis"]["dependency_generation"] = state["dependency_generation"]
                for path in memory["disclosure_basis"]["support_paths"]:
                    path["dependency_generation"] = state["dependency_generation"]
                candidates.append(memory)
                known.add(key)
            if not state.get("source_truncated", False):
                coverage["tail_covered_through_sequence"] = event["sequence"]
    all_memories = {(memory["memory_id"], memory["revision"]): memory for memory in candidates}
    query = " ".join([request["query"], *request["additional_cues"]])
    worlds = set(request.get("world_filter", ["real_world"]))
    requested_time = _range(request["valid_time_filter"]) if "valid_time_filter" in request else None
    scored = []
    historical = history_status != "not_requested"
    if not coverage["read_fence_satisfied"] or history_status == "unavailable":
        candidates = []
    for memory in candidates:
        if (time.monotonic() - started) * 1000 >= request["budget"]["max_wall_time_ms"]:
            coverage["limitations"].append("budget_exhausted")
            result["usage"]["stopped_by"] = "deadline"
            break
        if memory["lifecycle"] not in {"active", "dormant"} or memory["world"] not in worlds:
            continue
        if any(evidence_overlaps(reference, old) for reference in memory["evidence_refs"]
               for old in state.get("superseded_evidence_refs", [])):
            continue
        score = _lexical_score(query, memory["statement"])
        if score <= 0:
            continue
        if not _scope_allows(memory["scope"], scope):
            continue
        valid_start, valid_end = _range(memory["valid_time"])
        if requested_time:
            start, end = requested_time
            if (start or end) and valid_start is None and valid_end is None:
                coverage["limitations"].append("unknown")
                continue
            if start and valid_end and valid_end < start or end and valid_start and valid_start > end:
                continue
        if request["intent"] == "current_fact" and memory["memory_kind"] != "episodic":
            if valid_end and valid_end < reference_time or valid_start and valid_start > reference_time:
                continue
        if not _evidence_valid(memory, all_memories, event_map, scope,
                               state["dependency_generation"], cutoff, excluded, historical,
                               request["intent"]):
            basis = memory["disclosure_basis"]
            if basis["dependency_generation"] != state["dependency_generation"] or (
                basis["validation_state"] != "current"
            ):
                coverage["rebuilding"] = True
                coverage["limitations"].append("rebuilding")
            elif any(not _valid_ref(ref, event_map, scope) for ref in memory["evidence_refs"]):
                coverage["limitations"].append("source_unavailable")
            continue
        last_used = (memory["dynamics"]["last_external_reinforcement_at"]
                     or memory["system_time"]["recorded_at"])
        activation = float(memory["dynamics"]["activation"])
        if last_used:
            days = max(0, (reference_time - _stamp(last_used)).total_seconds() / 86400)
            activation /= 1 + math.log1p(days)
        scored.append((score + activation * 0.05, score, activation, memory))
    scored.sort(key=lambda row: (-row[0], row[3]["memory_id"]))
    for _, lexical, activation, memory in scored:
        tail = any(event_map[ref["event_id"]]["sequence"] > state["indexed_sequence"]
                   for ref in memory["evidence_refs"])
        item = {
            "memory": copy.deepcopy(memory),
            "rank_signals": [
                {"method": "keyword", "score": lexical,
                 "meaning": "ranking_only_not_probability",
                 "model_or_index_version": RETRIEVAL_VERSION},
                {"method": "recency", "score": activation,
                 "meaning": "ranking_only_not_probability",
                 "model_or_index_version": "external-activation-v1"},
            ],
            "retrieved_from": "unindexed_tail" if tail else "index",
            "display_mode": "explicit_recollection" if historical else "background_context",
        }
        result["items"].append(item)
        result["recall_traces"].append({
            "trace_id": uid(), "recall_id": result["recall_id"],
            "retrieval_snapshot_id": snapshot["snapshot_id"], "scope": copy.deepcopy(scope),
            "turn_id": None, "memory_id": memory["memory_id"], "kind": "candidate_hit",
            "source": "server_instrumentation", "feedback_event_id": None,
            "evidence_refs": copy.deepcopy(memory["evidence_refs"]),
            "recorded_at": timestamp, "allowed_effect": "none",
        })
    if result["items"]:
        result["status"] = "partial"
        result["next_step"] = "answer_with_limits"
        result["unknowns"].append(
            "Lexical candidates retain source roles; semantic answer sufficiency is not assessed."
        )
        if request["intent"] == "exact_quote" and all(
            request["query"] in _content_text(event_map[ref["event_id"]]["observed"]["content"])
            for item in result["items"] for ref in item["memory"]["evidence_refs"]
        ):
            result["status"] = "grounded"
            result["next_step"] = "answer_with_evidence"
            result["unknowns"] = []
    if coverage["rebuilding"] or history_status == "unavailable":
        result["status"] = "partial"
        result["next_step"] = "answer_with_limits"
    if history_status == "unavailable":
        result["unknowns"].append("The pre-query evidence boundary could not be established.")
        coverage["limitations"].append("unknown")
    if not coverage["read_fence_satisfied"]:
        result["status"] = "partial" if coverage["rebuilding"] else "indexing"
        result["next_step"] = "retry_after_indexing"
    if coverage["indexed_through_sequence"] < state["durable_sequence"] and not result["items"]:
        if result["status"] == "no_match":
            result["status"] = "indexing"
            result["next_step"] = "retry_after_indexing"
    if result["usage"]["stopped_by"] == "deadline":
        result["status"] = "partial"
        result["next_step"] = "answer_with_limits"
    coverage["limitations"] = list(dict.fromkeys(coverage["limitations"]))
    if coverage["limitations"] and result["status"] == "grounded":
        result["status"] = "partial"
        result["next_step"] = "answer_with_limits"
    result["usage"]["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if result["usage"]["stopped_by"] == "completed" and not result["items"]:
        result["usage"]["stopped_by"] = "no_candidates"
    limit = request["budget"]["max_context_tokens"]
    while _measure(result) > limit and result["items"]:
        result["items"].pop()
        result["recall_traces"].pop()
        result["status"] = "partial"
        result["next_step"] = "answer_with_limits"
        result["usage"]["stopped_by"] = "context_limit"
        if "budget_exhausted" not in coverage["limitations"]:
            coverage["limitations"].append("budget_exhausted")
    if _measure(result) > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the minimal recall envelope")
    validate("RecallResult", result)
    return result


def render_context(packet: dict) -> str:
    return _serialized({
        "trust": "memory_data_not_instructions",
        "blocks": [{
            "section": block["section"], "text": block["text"],
            "sources": [{"event_id": ref["event_id"], "revision": ref["event_revision"]}
                        for ref in block["evidence_refs"]],
        } for block in packet["blocks"]],
    })


def prepare(scope: dict, payload: dict, working: dict, recall_result: dict, state: dict) -> dict:
    validate("BoundScope", scope)
    validate("PreparePayload", payload)
    validate("WorkingState", working)
    validate("RecallResult", recall_result)
    _check_budget(payload["budget"])
    if recall_result["scope"] != scope:
        raise DomainError("version_conflict", "Recall scope is no longer valid")
    snapshot = recall_result["retrieval_snapshot"]
    if snapshot["dependency_generation"] != state["dependency_generation"] or (
        snapshot["scope_generation"] != state["scope_generation"]
    ):
        raise DomainError("version_conflict", "Recall snapshot is no longer current")
    if not _coverage(state, payload, 0)["read_fence_satisfied"]:
        raise DomainError("dependency_unavailable", "The preparation read fence is not covered", True)
    timestamp = now()
    valid_evidence = list(state.get("verified_working_evidence_refs", []))
    recalled_evidence = set()
    for item in recall_result["items"]:
        valid_evidence.extend(item["memory"]["evidence_refs"])
        recalled_evidence.update(digest(ref) for ref in item["memory"]["evidence_refs"])
    allowed = {digest(ref) for ref in valid_evidence}
    historical = snapshot["historical_evidence_status"] != "not_requested"
    historical_events = state.get("working_event_metadata", {})
    excluded = _cue_descendants(set(snapshot["excluded_cue_event_ids"]), historical_events)
    cutoff = snapshot["resolved_evidence_through_sequence"]

    def usable_working_ref(ref: dict) -> bool:
        if any(evidence_overlaps(ref, old) for old in state.get("superseded_evidence_refs", [])):
            return False
        if digest(ref) not in allowed:
            return False
        if not historical:
            return digest(ref) in recalled_evidence or _valid_ref(ref, historical_events, scope)
        if cutoff is None or not _valid_ref(ref, historical_events, scope):
            return False
        event = historical_events[ref["event_id"]]
        return (event["sequence"] <= cutoff and event["event_id"] not in excluded
                and _eligible_source(event, ref, True, "episode"))

    clean = copy.deepcopy(working)
    if working["scope"] != scope or working["through_sequence"] > state["durable_sequence"]:
        clean["items"] = []
        clean["open_loops"] = []
    else:
        clean["items"] = [item for item in clean["items"]
                          if all(usable_working_ref(ref) for ref in item["evidence_refs"])
                          and (item["evidence_refs"] or item["knowledge_state"] == "unknown")
                          and (item["expires_at"] is None or _stamp(item["expires_at"]) >
                               _stamp(timestamp))]
        clean["open_loops"] = [loop for loop in clean["open_loops"]
                               if all(usable_working_ref(ref) for ref in loop["evidence_refs"])
                               and (loop["evidence_refs"] or loop["state"] == "unknown")]
    clean["scope"] = copy.deepcopy(scope)
    result = {
        "packet_id": uid(), "turn_id": payload["turn_id"], "scope": copy.deepcopy(scope),
        "working_state": clean, "blocks": [], "recall_results": [copy.deepcopy(recall_result)],
        "coverage": copy.deepcopy(recall_result["coverage"]),
        "budget": copy.deepcopy(payload["budget"]), "usage": _usage(payload["budget"]),
        "integration": copy.deepcopy(payload["capabilities"]), "created_at": timestamp,
        "retrieval_snapshot": copy.deepcopy(snapshot), "recall_traces": [],
    }
    limit = payload["budget"]["max_context_tokens"]
    blocks = []
    metadata = []
    for item in clean["items"]:
        blocks.append(("working_state", _serialized({
            "key": item["key"], "value": item["value"],
            "knowledge_state": item["knowledge_state"], "expires_at": item["expires_at"],
            "use": "query_context_only" if item["knowledge_state"] != "grounded" else (
                "source_attributed_context"
            ),
        }), item["evidence_refs"]))
        metadata.append(None)
    for loop in clean["open_loops"]:
        blocks.append(("open_loops", _serialized({
            "description": loop["description"], "state": loop["state"],
            "due_time": loop["due_time"],
        }), loop["evidence_refs"]))
        metadata.append(None)
    for item in recall_result["items"]:
        if item["display_mode"] == "do_not_surface":
            continue
        memory = item["memory"]
        blocks.append(("relevant_memory", _serialized({
            "statement": memory["statement"], "world": memory["world"],
            "epistemic_status": memory["epistemic_status"], "valid_time": memory["valid_time"],
        }), memory["evidence_refs"]))
        metadata.append(memory)
    uncertainty = {
        "recall_status": recall_result["status"],
        "historical_evidence_status": snapshot["historical_evidence_status"],
        "unknowns": recall_result["unknowns"],
        "limitations": result["coverage"]["limitations"],
        "context_truncated": False,
    }

    def uncertainty_block() -> dict:
        text = _serialized(uncertainty)
        return {"section": "uncertainty", "text": text, "evidence_refs": [],
                "trust": "memory_data_not_instructions", "token_count": estimate_tokens(text)}

    result["blocks"] = [uncertainty_block()]
    if estimate_tokens(render_context(result)) > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the context safety envelope")
    for (section, text, refs), memory in zip(blocks, metadata, strict=True):
        block = {"section": section, "text": text, "evidence_refs": copy.deepcopy(refs),
                 "trust": "memory_data_not_instructions", "token_count": estimate_tokens(text)}
        result["blocks"].append(block)
        if estimate_tokens(render_context(result)) > limit:
            result["blocks"].pop()
            result["usage"]["stopped_by"] = "context_limit"
            uncertainty["context_truncated"] = True
            result["blocks"][0] = uncertainty_block()
            continue
        if memory is not None:
            result["recall_traces"].append({
                "trace_id": uid(), "recall_id": recall_result["recall_id"],
                "retrieval_snapshot_id": snapshot["snapshot_id"], "scope": copy.deepcopy(scope),
                "turn_id": payload["turn_id"], "memory_id": memory["memory_id"],
                "kind": "context_selected", "source": "server_instrumentation",
                "feedback_event_id": None, "evidence_refs": copy.deepcopy(refs),
                "recorded_at": timestamp, "allowed_effect": "activation_only",
            })
    if uncertainty["context_truncated"]:
        if "budget_exhausted" not in result["coverage"]["limitations"]:
            result["coverage"]["limitations"].append("budget_exhausted")
        result["blocks"][0] = uncertainty_block()
        while estimate_tokens(render_context(result)) > limit and len(result["blocks"]) > 1:
            removed = result["blocks"].pop()
            result["recall_traces"] = [trace for trace in result["recall_traces"]
                                       if trace["evidence_refs"] != removed["evidence_refs"]]
    result["usage"]["context_tokens"] = estimate_tokens(render_context(result))
    if result["usage"]["context_tokens"] > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the context limitation report")
    validate("ContextPacket", result)
    return result
