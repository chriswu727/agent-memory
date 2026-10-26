import copy
import json
import math
import re
import time
import unicodedata
from datetime import UTC, datetime
from typing import Any

from .applicability import ApplicabilityResult, evaluate_applicability, validate_observation
from .context_views import FORMAT, context_view, memory_content, render_recall
from .contracts import validate
from .helpers import DomainError, digest, now, uid
from .ontology import CORE_REGISTRY
from .retrieval import RANK_METHODS
from .temporal import evaluate_valid_during

RULE_VERSION = "literal-episodes-v2"
LEGACY_RULE_VERSION = "literal-episodes-v1"
RETRIEVAL_VERSION = "unicode-lexical-v1"
_LIST_PREFIX = re.compile(r"^[ \t]*(?:>[ \t]*)*(?:(?:[-+*]|\d+[.)])(?:[ \t]+|$))")
_MERIDIEM = re.compile(r"(?<![a-z])[ap]\.m\.", re.I)
_SENTENCE_END = re.compile(r"[。.!！?？]+[\"'”’»」』）)\]}*_~`]*")
_FORMATTING_CHARACTERS = frozenset("*_~`#>+-|\"'“”‘’«»「」『』（）()[]{}.!?。！？…")
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


def _sentence_spans(text: str):
    for line_match in re.finditer(r"[^\r\n]+", text):
        line = line_match.group()
        protected = {match.start() for match in re.finditer(r"(?<=\d)\.(?=\d)", line)}
        prefix = _LIST_PREFIX.match(line)
        if prefix:
            protected.update(index for index in range(prefix.end()) if line[index] == ".")
        for match in _MERIDIEM.finditer(line):
            protected.update(index for index in range(match.start(), match.end())
                             if line[index] == ".")
            following = line[match.end():].lstrip(" \t\"'”’»」』）)]}*_~`")
            if following and following[0].isupper():
                protected.discard(match.end() - 1)
        start = 0
        for boundary in _SENTENCE_END.finditer(line):
            if boundary.start() in protected:
                continue
            yield line_match.start() + start, line_match.start() + boundary.end()
            start = boundary.end()
        if start < len(line):
            yield line_match.start() + start, line_match.end()


def classify_segments(content: dict, *, rule_version: str = RULE_VERSION) -> list[dict]:
    validate("EventContent", content)
    if rule_version not in {LEGACY_RULE_VERSION, RULE_VERSION}:
        raise DomainError("validation_failed", "Unknown literal segmentation rule version.")
    if content["media_type"] != "text/plain":
        return [{"act": "not_applicable"}]
    text = content["text"]
    segments = []
    spans = (match.span() for match in re.finditer(r"[^。.!！?？\n]+[。.!！?？]*", text)) if (
        rule_version == LEGACY_RULE_VERSION) else _sentence_spans(text)
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        sentence = text[start:end]
        prefix = _LIST_PREFIX.match(sentence) if rule_version == RULE_VERSION else None
        body = sentence[prefix.end():] if prefix else sentence
        if prefix and not body and re.fullmatch(r"\d+[.)]", sentence):
            body = sentence
        if rule_version == RULE_VERSION and not any(
            not char.isspace() and char not in _FORMATTING_CHARACTERS for char in body
        ):
            continue
        act = "inquiry" if _QUESTION.search(body) else "direct_statement"
        if act != "inquiry" and re.match(r"(?:请|帮我|不要|记住|please\b)", body, re.I):
            act = "instruction"
        segments.append({
            "act": act,
            "span": {"start": start, "end": end, "unit": "unicode_code_point"},
        })
    return segments or [{"act": "unknown"}]


def _literal_rule_version(event: dict) -> str:
    content = event["observed"]["content"]
    legacy = classify_segments(content, rule_version=LEGACY_RULE_VERSION)
    current = classify_segments(content)
    if event["utterance_segments"] == current:
        return RULE_VERSION
    declared_spans = [segment.get("span") for segment in event["utterance_segments"]]
    if legacy != current and declared_spans == [segment.get("span") for segment in legacy]:
        return LEGACY_RULE_VERSION
    return RULE_VERSION


def _segments(event: dict, *, rule_version: str | None = None) -> list[dict]:
    content = event["observed"]["content"]
    rule_version = _literal_rule_version(event) if rule_version is None else rule_version
    computed = classify_segments(content, rule_version=rule_version)
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


def extract_event(event: dict, *, rule_version: str | None = None) -> list[dict]:
    validate("Event", event)
    rule_version = _literal_rule_version(event) if rule_version is None else rule_version
    if rule_version not in {LEGACY_RULE_VERSION, RULE_VERSION}:
        raise DomainError("validation_failed", "Unknown literal segmentation rule version.")
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
    for segment in _segments(event, rule_version=rule_version):
        if observed["content"]["media_type"] == "text/plain" and "span" not in segment:
            continue
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
                "method": "direct_capture", "rule_version": rule_version,
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
        count = estimate_tokens(render_recall(result))
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


def _eligible_source(event: dict, ref: dict, historical: bool, intent: str,
                     claim_kind: str | None = None, *, verified_preference: bool = False) -> bool:
    role = event["observed"]["actor_role"]
    if role == "assistant" and claim_kind is not None:
        return (claim_kind in {"agent_said", "proposal_offered", "commitment_expressed"}
                and event["observed"]["delivery_state"] in {"delivered", "partially_delivered"})
    if role == "system" and claim_kind is not None:
        return False
    if not historical or intent == "exact_quote":
        return True
    if role in {"assistant", "system"}:
        return False
    span = ref.get("span")
    acts = []
    for segment in _segments(event):
        bounds = segment.get("span")
        if span and bounds and max(span["start"], bounds["start"]) >= min(
            span["end"], bounds["end"]
        ):
            continue
        act = segment["act"]
        if (act == "instruction" and verified_preference and role == "user"
                and event["observed"]["delivery_state"] == "received"
                and span and bounds and span["start"] <= bounds["start"] and span["end"] >= bounds["end"]):
            act = "direct_statement"
        acts.append(act)
    return bool(acts) and all(act in {"direct_statement", "correction", "confirmation",
                                      "not_applicable"} for act in acts)


def _evidence_valid(memory: dict, all_memories: dict, events: dict, scope: dict,
                    generation: int, cutoff: int | None, excluded: set, historical: bool,
                    intent: str, visiting: frozenset = frozenset(),
                    valid_dependency_versions: frozenset = frozenset(),
                    superseded_evidence_refs: tuple = (), validation_cache: dict | None = None) -> bool:
    validation_cache = {} if validation_cache is None else validation_cache
    key = (memory["memory_id"], memory["revision"])
    if key in visiting or not _scope_allows(memory["scope"], scope):
        return False
    if key in validation_cache:
        return validation_cache[key]
    basis = memory["disclosure_basis"]
    if basis["validation_state"] != "current" or basis["dependency_generation"] != generation:
        return False
    refs = memory["evidence_refs"]
    if not refs or not all(_valid_ref(ref, events, scope) for ref in refs):
        return False
    if any(evidence_overlaps(ref, old) for ref in refs for old in superseded_evidence_refs):
        return False
    for event_id in memory.get("root_event_ids", []):
        source = events.get(event_id)
        if source is None or not _scope_allows(source["scope"], scope):
            return False
        if any(ref["event_id"] == event_id for ref in superseded_evidence_refs):
            return False
        if historical and (cutoff is None or source["sequence"] > cutoff or event_id in excluded):
            return False
    claim_kind = memory.get("claim_kind")
    verification = memory.get("verification", {})
    verified_preference = (
        claim_kind == "reported_preference" and memory["epistemic_status"] == "derived_inference"
        and verification.get("state") in {"supported_inference", "contested"}
        and verification.get("method") == "semantic_model"
        and verification.get("not_a_truth_certificate") is True
    )
    if claim_kind is not None and any(
        not _eligible_source(events[ref["event_id"]], ref, historical, intent, claim_kind,
                             verified_preference=verified_preference)
        for ref in refs
    ):
        return False
    if historical:
        if cutoff is None:
            return False
        for ref in refs:
            event = events[ref["event_id"]]
            if event["sequence"] > cutoff or event["event_id"] in excluded:
                return False
            if not _eligible_source(event, ref, True, intent, claim_kind,
                                    verified_preference=verified_preference):
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
            parent_key = (parent["memory_id"], parent["revision"])
            source = all_memories.get(parent_key)
            if not source or source["lifecycle"] == "retracted" or (
                source["lifecycle"] == "superseded" and parent_key not in valid_dependency_versions
            ):
                return False
            valid = _evidence_valid(source, all_memories, events, scope, generation, cutoff,
                                    excluded, historical, intent, visiting | {key},
                                    valid_dependency_versions, superseded_evidence_refs, validation_cache)
            validation_cache[parent_key] = valid
            if not valid:
                return False
    validation_cache[key] = True
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
    if state.get("candidate_truncated", False) or state.get("candidate_total", candidate_count) > candidate_count or state.get(
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


def _candidate_signals(state: dict, key: tuple[str, int]) -> list[dict]:
    signals = state.get("rank_signals", {}).get(key, [])
    if not isinstance(signals, list):
        raise DomainError("validation_failed", "Candidate rank signals must be a list.")
    for signal in signals:
        validate("RankSignal", signal)
        if not math.isfinite(signal["score"]):
            raise DomainError("validation_failed", "Candidate rank scores must be finite.")
    return copy.deepcopy(signals)


def recall(scope: dict, request: dict, memories: list[dict], events: list[dict],
           state: dict, *, access_at: str | None = None) -> dict:
    validate("BoundScope", scope)
    validate("RecallRequest", request)
    _check_budget(request["budget"])
    started = time.monotonic()
    timestamp = now()
    reference_time = _stamp(timestamp)
    access_reference_time = _stamp(access_at) if access_at is not None else reference_time
    event_map = {event["event_id"]: event for event in events
                 if event["sequence"] <= state["durable_sequence"]
                 and _scope_allows(event["scope"], scope)}
    boundary, cutoff, excluded, history_status = _cutoff(
        request, event_map, state["durable_sequence"]
    )
    excluded = _cue_descendants(excluded, event_map)
    candidate_versions = state.get("candidate_versions")
    candidate_versions = set(candidate_versions) if candidate_versions is not None else None
    coverage = _coverage(state, request, len(candidate_versions) if candidate_versions is not None else len(memories))
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
        "retrieval_snapshot": snapshot, "recall_traces": [], "context_format": FORMAT,
    }
    known = {(memory["memory_id"], memory["revision"]) for memory in memories}
    candidates = list(memories)
    tail_versions = set()
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
                tail_versions.add(key)
            if not state.get("source_truncated", False):
                coverage["tail_covered_through_sequence"] = event["sequence"]
    all_memories = {(memory["memory_id"], memory["revision"]): memory for memory in candidates}
    query = " ".join([request["query"], *request["additional_cues"]])
    worlds = set(request.get("world_filter", ["real_world"]))
    requested_time = _range(request["valid_time_filter"]) if "valid_time_filter" in request else None
    scored = []
    selected_order = {(item["memory_id"], item["revision"]): index
                      for index, item in enumerate(state.get("selected_versions", []))}
    selecting = "selected_versions" in state
    historical = history_status != "not_requested"
    validation_cache = {}
    if not coverage["read_fence_satisfied"] or history_status == "unavailable":
        candidates = []
    for memory in candidates:
        if (time.monotonic() - started) * 1000 >= request["budget"]["max_wall_time_ms"]:
            coverage["limitations"].append("budget_exhausted")
            result["usage"]["stopped_by"] = "deadline"
            break
        if memory["lifecycle"] not in {"active", "dormant"} or memory["world"] not in worlds:
            continue
        if (memory.get("claim_kind") in {"agent_said", "proposal_offered", "commitment_expressed"}
                and request["intent"] not in {"episode", "open_commitment", "exact_quote"}
                and not historical
                and not re.search(r"(你说|你答应|you said|you promised|your suggestion)", query, re.I)):
            continue
        key = (memory["memory_id"], memory["revision"])
        if candidate_versions is not None and key not in candidate_versions and key not in tail_versions:
            continue
        if selecting and key not in selected_order:
            continue
        if any(evidence_overlaps(reference, old) for reference in memory["evidence_refs"]
               for old in state.get("superseded_evidence_refs", [])):
            continue
        signals = [] if selecting else _candidate_signals(state, key)
        ranked_score = sum(max(0.0, signal["score"]) for signal in signals if signal["method"] in RANK_METHODS)
        lexical_score = _lexical_score(query, memory["statement"])
        score = (1 / (1 + selected_order[key])) if selecting else ranked_score or lexical_score
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
                               request["intent"],
                               valid_dependency_versions=frozenset(state.get("valid_dependency_versions", ())),
                               superseded_evidence_refs=tuple(state.get("superseded_evidence_refs", ())),
                               validation_cache=validation_cache):
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
            days = max(0, (access_reference_time - _stamp(last_used)).total_seconds() / 86400)
            activation /= 1 + math.log1p(days)
        if signals or selecting:
            activation = 0.0
        if not signals:
            signals = [{"method": "reranker" if selecting else "keyword", "score": score,
                        "meaning": "ranking_only_not_probability",
                        "model_or_index_version": "evidence-recall-v1" if selecting else RETRIEVAL_VERSION}]
        if not selecting and activation:
            signals.append({"method": "recency", "score": activation,
                            "meaning": "ranking_only_not_probability",
                            "model_or_index_version": "external-activation-v1"})
        scored.append((score + activation * 0.05, score, activation, memory, signals))
    scored.sort(key=lambda row: (-row[0], row[3]["memory_id"]))
    if selecting:
        scored.sort(key=lambda row: selected_order[(row[3]["memory_id"], row[3]["revision"])])
    for _, _, _, memory, signals in scored:
        tail = any(event_map[ref["event_id"]]["sequence"] > state["indexed_sequence"]
                   for ref in memory["evidence_refs"])
        item = {
            "memory": copy.deepcopy(memory),
            "rank_signals": signals,
            "retrieved_from": "unindexed_tail" if tail else "index",
            "display_mode": "explicit_recollection" if historical else "background_context",
        }
        if _has_applicability(memory):
            item["applicability"] = _memory_applicability(memory, state, scope, event_map, timestamp)
        result["items"].append(item)
        result["recall_traces"].append({
            "trace_id": uid(), "recall_id": result["recall_id"],
            "retrieval_snapshot_id": snapshot["snapshot_id"], "scope": copy.deepcopy(scope),
            "turn_id": None, "memory_id": memory["memory_id"], "memory_revision": memory["revision"], "kind": "candidate_hit",
            "source": "server_instrumentation", "feedback_event_id": None,
            "evidence_refs": copy.deepcopy(memory["evidence_refs"]),
            "recorded_at": timestamp, "allowed_effect": "none",
        })
    if result["items"]:
        result["status"] = "partial"
        result["next_step"] = "answer_with_limits"
        result["unknowns"].append(
            "Model-selected sources do not independently verify claims or exhaustive recall." if selecting else
            ("Candidate rank signals do not establish semantic answer sufficiency." if state.get("rank_signals") else
             "Lexical candidates retain source roles; semantic answer sufficiency is not assessed.")
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
    from .belief_resolution import connected_item_groups, factor_relations, resolve

    refresh_beliefs = resolve(result["items"], candidates, event_map, scope, state, snapshot, timestamp)
    groups = connected_item_groups(result["items"])
    limit = request["budget"]["max_context_tokens"]
    if _measure(result) > limit and result["items"]:
        original_items, original_traces = result["items"], result["recall_traces"]
        result["items"], result["recall_traces"] = [], []
        result["status"] = "partial"
        result["next_step"] = "answer_with_limits"
        result["usage"]["stopped_by"] = "context_limit"
        if "budget_exhausted" not in coverage["limitations"]:
            coverage["limitations"].append("budget_exhausted")
        admitted = set()
        for group in groups:
            candidates = admitted | {item["memory"]["memory_id"] for item in group}
            result["items"] = [item for item in original_items if item["memory"]["memory_id"] in candidates]
            refresh_beliefs(result["items"])
            if _measure(result) <= limit:
                admitted = candidates
        result["items"] = [item for item in original_items if item["memory"]["memory_id"] in admitted]
        result["recall_traces"] = [trace for trace in original_traces if trace["memory_id"] in admitted]
        refresh_beliefs(result["items"])
    if _measure(result) > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the minimal recall envelope")
    factor_relations(result)
    if _measure(result) > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the final recall envelope")
    result["model_context"] = render_recall(result)
    validate("RecallResult", result)
    return result


def render_context(packet: dict) -> str:
    return _serialized(context_view(packet))


def _has_applicability(memory):
    return ("conditions" in memory or "valid_during" in memory
            or memory["memory_kind"] != "episodic" and any((
                memory["valid_time"]["start"], memory["valid_time"]["end"])))


def _memory_applicability(memory, state, scope, events, timestamp):
    observations = {}
    complete = state.get("context_observations_complete", True) is True
    supplied = state.get("context_observations", [])
    if not isinstance(supplied, (list, tuple)):
        supplied, complete = [], False
    for observation in supplied:
        try:
            validate_observation(observation)
        except (ValueError, TypeError):
            continue
        if all(_valid_ref(ref, events, scope)
               and events[ref["event_id"]]["observed"]["actor_role"] in {"user", "operator", "tool"}
               and not any(evidence_overlaps(ref, old) for old in state.get("superseded_evidence_refs", []))
               for ref in observation["evidence_refs"]):
            canonical = {key: value for key, value in observation.items() if key != "observation_id"}
            canonical["evidence_refs"] = sorted(observation["evidence_refs"], key=digest)
            observations.setdefault(digest(canonical), observation)
    complete = complete and len(observations) <= 128
    at = state.get("applicability_at", timestamp)
    try:
        if memory.get("condition_registry_fingerprint", CORE_REGISTRY.fingerprint) != CORE_REGISTRY.fingerprint:
            raise ValueError("Unknown registry definition")
        condition = memory.get("conditions", {"op": "always"})
        assessment = evaluate_applicability(condition, list(observations.values())[:128], at=at)
        result = assessment.to_dict()
        if (memory.get("memory_id") and memory.get("conditions") == {"op": "always"}
                and "condition_registry_fingerprint" not in memory):
            result.update(value="unknown", status="unknown")
            result["reasons"] = sorted(set(result["reasons"] + ["legacy_unqualified_rule"]))
        if not complete and condition != {"op": "always"}:
            result.update(value="unknown", status="conflict" if result["conflict"] else "unknown")
            result["reasons"] = sorted(set(result["reasons"] + ["context_incomplete"]))
        if "valid_during" in memory:
            temporal = evaluate_valid_during(memory["valid_during"], at=at)
            result["reasons"] = sorted(set(result["reasons"] + list(temporal.reasons)))
            if temporal.value == "false":
                result.update(value="false", status="false", conflict=False)
            elif temporal.value == "unknown" and result["value"] == "true":
                result.update(value="unknown", status="unknown")
        elif memory.get("memory_id") and "conditions" in memory:
            result["reasons"] = sorted(set(result["reasons"] + ["validity_unstated"]))
            if result["value"] == "true":
                result.update(value="unknown", status="unknown")
        if "valid_during" not in memory and memory.get("memory_kind") != "episodic" and "valid_time" in memory:
            start, end = _range(memory["valid_time"])
            instant = _stamp(at)
            if start and start > instant or end and end < instant:
                result.update(value="false", status="false", conflict=False)
                result["reasons"] = sorted(set(result["reasons"] + ["outside_recorded_valid_time"]))
    except (ValueError, TypeError):
        result = ApplicabilityResult("unknown", reasons=("invalid_or_unresolved_context",),
                                     registry_fingerprint=CORE_REGISTRY.fingerprint).to_dict()
    return {**result, "evaluated_at": at}


def _pattern_context_units(rows, views, scope, events, snapshot, excluded, superseded):
    from .patterns import validate_pattern

    def key(version):
        return version["memory_id"], version["revision"]

    def ordinary(row, *, episodes=None, roots=()):
        content = copy.deepcopy(row["content"])
        refs = copy.deepcopy(row["references"])
        if episodes:
            content["episode_refs"] = copy.deepcopy(episodes)
            content["coverage"] = "selected_assertions_only"
            content["memory_version"] = {"memory_id": row["memory"]["memory_id"],
                                         "revision": row["memory"]["revision"]}
        for ref in roots:
            if ref not in refs:
                refs.append(ref)
        return (row["section"], _serialized(content), refs), [(row["memory"], row["references"])]

    def size(unit):
        return estimate_tokens(render_context({"blocks": [
            {"section": block[0], "text": block[1], "evidence_refs": block[2]}
            for block, _ in unit
        ]}))

    memories = [row["memory"] for row in rows]
    lookup = {key(row["memory"]): row for row in rows}
    groups, used = {}, set()
    for view in views[:16]:
        if not validate_pattern(view, memories) or not _scope_allows(view["scope"], scope):
            continue
        keys = {key(version) for version in view["source_memory_versions"]}
        if keys & used:
            continue
        roots = []
        for identifier in view["root_event_ids"]:
            event = events.get(identifier)
            if event is None:
                break
            ref = {"event_id": identifier, "event_revision": event["revision"],
                   "source_content_sha256": event["content_sha256"], "relationship": "context_only"}
            if (not _valid_ref(ref, events, scope)
                    or any(old["event_id"] == identifier for old in superseded)
                    or snapshot["historical_evidence_status"] != "not_requested" and (
                        snapshot["resolved_evidence_through_sequence"] is None
                        or event["sequence"] > snapshot["resolved_evidence_through_sequence"]
                        or identifier in excluded)):
                break
            roots.append(ref)
        else:
            instances, memberships = [], {}
            meanings = {slot["slot_id"]: slot["meaning"] for slot in view["definition"]["shared_claims"]}
            for instance in view["instances"]:
                bindings, residuals = [], []
                for version in instance["observed_memory_versions"]:
                    memberships.setdefault(key(version), []).append(instance["episode_ref"])
                for binding in instance["shared_bindings"]:
                    row = lookup[key(binding["memory_version"])]
                    attributes = {name: value for name, value in row["content"].items()
                                  if name not in meanings[binding["slot_id"]]
                                  or value != meanings[binding["slot_id"]][name]}
                    bindings.append({**copy.deepcopy(binding), "attributes": copy.deepcopy(attributes),
                                     "evidence_refs": copy.deepcopy(row["references"])})
                for version in instance["residual_memory_versions"]:
                    row = lookup[key(version)]
                    residuals.append({"memory_version": copy.deepcopy(version),
                                      "fact": copy.deepcopy(row["content"]),
                                      "evidence_refs": copy.deepcopy(row["references"])})
                instances.append({"episode_ref": copy.deepcopy(instance["episode_ref"]),
                                  "observed_memory_versions": copy.deepcopy(instance["observed_memory_versions"]),
                                  "shared_bindings": bindings, "residuals": residuals})
            selected = [row for row in rows if key(row["memory"]) in keys]
            references = {digest(ref): ref for row in selected for ref in row["references"]}
            cited = {ref["event_id"] for ref in references.values()}
            extra_roots = [ref for ref in roots if ref["event_id"] not in cited]
            references.update({digest(ref): ref for ref in extra_roots})
            section = ("conditional_memory" if any(row["section"] == "conditional_memory" for row in selected)
                       else "relevant_memory")
            content = {
                "format": view["format_version"], "coverage": view["coverage"],
                "use": "apply_each_instance_with_its_own_evidence_and_applicability; "
                       "shared_meaning_does_not_fill_unobserved_details",
                "definition": copy.deepcopy(view["definition"]), "instances": instances,
            }
            compressed = [((section, _serialized(content), list(references.values())),
                           [(row["memory"], row["references"]) for row in selected])]
            expanded = [ordinary(row, episodes=memberships[key(row["memory"])], roots=extra_roots)
                        for row in selected]
            unit = compressed if size(compressed) < size([ordinary(row) for row in selected]) else expanded
            first = key(selected[0]["memory"])
            groups[first] = unit
            used.update(keys)
    return [groups[key(row["memory"])] if key(row["memory"]) in groups else [ordinary(row)]
            for row in rows if key(row["memory"]) in groups or key(row["memory"]) not in used]


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
    from .belief_resolution import (
        atomic_units,
        connected_item_groups,
        factor_relations,
        resolution_evidence,
        resolve,
        version,
    )

    recall_result = copy.deepcopy(recall_result)
    resolve([item for item in recall_result["items"] if item["display_mode"] != "do_not_surface"],
            state.get("belief_memories", [item["memory"] for item in recall_result["items"]]),
            state.get("working_event_metadata", {}), scope, state, snapshot, timestamp)
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

    def contextual_value(value, *, current=False):
        if isinstance(value, dict) and value.get("evidence_use") == "current_request":
            return value, ("current_request_not_historical_evidence" if current
                           else "prior_request_context_not_historical_evidence")
        if not isinstance(value, dict) or "observation" not in value:
            return value, None
        observation = value["observation"]
        try:
            validate_observation(observation)
        except (ValueError, TypeError):
            return value, "unresolved_or_expired_context_not_current_state"
        condition = {"op": "holds", **{key: observation[key] for key in (
            "subject_ref", "predicate_ref", "value", "polarity") if key in observation}}
        applicability = _memory_applicability(
            {"conditions": condition, "valid_during": observation.get("valid_during", {"kind": "unknown"})},
            state, scope, historical_events, timestamp,
        )
        return {**value, "applicability": applicability}, (
            "fallible_current_context_not_historical_evidence"
            if applicability["value"] == "true" and not applicability["conflict"]
            else "unresolved_or_expired_context_not_current_state"
        )

    def context_references(references, value):
        result = copy.deepcopy(references)
        if isinstance(value, dict):
            for ref in value.get("applicability", {}).get("evidence_refs", []):
                context_ref = {**ref, "relationship": "context_only"}
                if context_ref not in result:
                    result.append(context_ref)
        return result

    for item in state.get("current_situation", []):
        value, use = contextual_value({
            key: item[key] for key in ("kind", "key", "value", "state", "world", "conditions", "verification")
        } | {key: item[key] for key in ("observation", "evidence_use", "request_verification") if key in item},
                                     current=True)
        blocks.append(("current_situation", _serialized(
            value | {"use": use or "fallible_current_context_not_historical_evidence"}),
            context_references(item["evidence_refs"], value)))
    for item in clean["items"]:
        value, use = contextual_value(item["value"])
        blocks.append(("working_state", _serialized({
            "key": item["key"], "value": value,
            "knowledge_state": item["knowledge_state"], "expires_at": item["expires_at"],
            "use": use or ("query_context_only" if item["knowledge_state"] != "grounded" else (
                "source_attributed_context"
            )),
        }), context_references(item["evidence_refs"], value)))
    for loop in clean["open_loops"]:
        blocks.append(("open_loops", _serialized({
            "description": loop["description"], "state": loop["state"],
            "due_time": loop["due_time"],
        }), loop["evidence_refs"]))
    for event in state.get("recent_events", []):
        observed = event["observed"]
        blocks.append(("recent_context", _serialized({
            "actor_role": observed["actor_role"], "actor_ref": observed["actor_ref"],
            "content": observed["content"], "delivery_state": observed["delivery_state"],
            "sequence": event["sequence"], "occurred_time": observed["occurred_time"],
            "world": _world(observed, _content_text(observed["content"])),
            "utterance_segments": event["utterance_segments"],
            "use": "conversation_context_only_not_independent_historical_evidence",
        }), [{"event_id": event["event_id"], "event_revision": event["revision"],
              "source_content_sha256": event["content_sha256"], "relationship": "context_only"}]))
    memory_rows = []
    belief_membership = {version(item["memory"]): {f"component-{index}"}
                         for index, group in enumerate(connected_item_groups(result["recall_results"][0]["items"]))
                         for item in group}
    for item in result["recall_results"][0]["items"]:
        if item["display_mode"] == "do_not_surface":
            continue
        memory = item["memory"]
        references = copy.deepcopy(memory["evidence_refs"])
        for ref in memory.get("counterexamples", []):
            if ref not in references:
                references.append(copy.deepcopy(ref))
        cited = {ref["event_id"] for ref in references}
        for event_id in memory.get("root_event_ids", []):
            event = historical_events.get(event_id)
            if event and event_id not in cited:
                references.append({"event_id": event_id, "event_revision": event["revision"],
                                   "source_content_sha256": event["content_sha256"], "relationship": "context_only"})
        applicability = (_memory_applicability(memory, state, scope, historical_events, timestamp)
                         if _has_applicability(memory) else None)
        if applicability is not None:
            item["applicability"] = applicability
        applicable = applicability is None or applicability["value"] == "true" and not applicability["conflict"]
        resolution = item.get("belief_resolution")
        if resolution:
            applicable = applicable and resolution["complete"] and resolution["state"] == "unchanged"
            for ref in resolution_evidence(resolution):
                if ref not in references:
                    references.append(copy.deepcopy(ref))
        if applicability is not None:
            for ref in applicability["evidence_refs"]:
                context_ref = {**ref, "relationship": "context_only"}
                if context_ref not in references:
                    references.append(context_ref)
        content = memory_content(item)
        if applicability is not None:
            content["applicability"] = applicability
        if resolution:
            content["belief_resolution"] = copy.deepcopy(resolution)
        if content["use"] != "historical_report_not_current_rule":
            applicable = content["use"] == "source_attributed_applicable_memory"
        memory_rows.append({"section": "relevant_memory" if applicable else "conditional_memory",
                            "content": content, "references": references, "memory": memory})
    units = [[(block, [])] for block in blocks]
    units.extend(atomic_units(_pattern_context_units(
        memory_rows, [] if "recall_plan" in payload else state.get("pattern_views", []),
        scope, historical_events, snapshot, excluded, state.get("superseded_evidence_refs", []),
    ), belief_membership))
    uncertainty = {
        "recall_status": recall_result["status"],
        "historical_evidence_status": snapshot["historical_evidence_status"],
        "unknowns": recall_result["unknowns"],
        "limitations": result["coverage"]["limitations"],
        "context_truncated": False,
    }
    if "recall_plan" in payload:
        plan = payload["recall_plan"]
        uncertainty.update(
            recall_status="partial" if plan["memory_versions"] else "no_match",
            recall_decision=plan["outcome"],
            unknowns=["Model relevance judgments do not verify source claims or exhaustive recall."],
            limitations=copy.deepcopy(plan["coverage_limitations"]),
        )

    def uncertainty_block() -> dict:
        text = _serialized(uncertainty)
        return {"section": "uncertainty", "text": text, "evidence_refs": [],
                "trust": "memory_data_not_instructions", "token_count": estimate_tokens(text)}

    result["blocks"] = [uncertainty_block()]
    if estimate_tokens(render_context(result)) > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the context safety envelope")
    emitted = []
    for unit in units:
        for (section, text, refs), _ in unit:
            result["blocks"].append({
                "section": section, "text": text, "evidence_refs": copy.deepcopy(refs),
                "trust": "memory_data_not_instructions", "token_count": estimate_tokens(text),
            })
        if estimate_tokens(render_context(result)) > limit:
            del result["blocks"][-len(unit):]
            result["usage"]["stopped_by"] = "context_limit"
            uncertainty["context_truncated"] = True
            result["blocks"][0] = uncertainty_block()
            continue
        trace_ids = set()
        for _, provenance in unit:
            for memory, refs in provenance:
                trace_id = uid()
                trace_ids.add(trace_id)
                result["recall_traces"].append({
                    "trace_id": trace_id, "recall_id": recall_result["recall_id"],
                    "retrieval_snapshot_id": snapshot["snapshot_id"], "scope": copy.deepcopy(scope),
                    "turn_id": payload["turn_id"], "memory_id": memory["memory_id"], "memory_revision": memory["revision"],
                    "kind": "context_selected", "source": "server_instrumentation",
                    "feedback_event_id": None, "evidence_refs": copy.deepcopy(refs),
                    "recorded_at": timestamp, "allowed_effect": "activation_only",
                })
        emitted.append((len(unit), trace_ids))
    if uncertainty["context_truncated"]:
        if "budget_exhausted" not in result["coverage"]["limitations"]:
            result["coverage"]["limitations"].append("budget_exhausted")
        result["blocks"][0] = uncertainty_block()
        while estimate_tokens(render_context(result)) > limit and len(result["blocks"]) > 1:
            count, trace_ids = emitted.pop()
            del result["blocks"][-count:]
            result["recall_traces"] = [trace for trace in result["recall_traces"]
                                       if trace["trace_id"] not in trace_ids]
    result["usage"]["context_tokens"] = estimate_tokens(render_context(result))
    if result["usage"]["context_tokens"] > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the context limitation report")
    factor_relations(result["recall_results"][0])
    _measure(result["recall_results"][0])
    result["recall_results"][0]["model_context"] = render_recall(result["recall_results"][0])
    for block in result["blocks"]:
        content = json.loads(block["text"])
        if isinstance(content, dict) and "belief_resolution" in content:
            resolution = content["belief_resolution"]
            from .belief_store import relation_ref

            resolution["relation_refs"] = [relation_ref(value["relation"])
                                            for value in resolution.pop("relations", [])]
            resolution.pop("evidence_refs", None)
            block["text"] = _serialized(content)
            block["token_count"] = estimate_tokens(block["text"])
    result["model_context"] = render_context(result)
    result["usage"]["context_tokens"] = estimate_tokens(result["model_context"])
    if result["usage"]["context_tokens"] > limit:
        raise DomainError("budget_exceeded", "Budget cannot hold the final context envelope")
    validate("ContextPacket", result)
    return result
