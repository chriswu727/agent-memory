from __future__ import annotations

import copy
import unicodedata
from collections import Counter

from .cognition import _terms
from .helpers import digest

POLICY = "bounded-comparison-attention-v1"
LIMIT = 3
FOCUS_LIMIT = 2
MAX_CANDIDATES = 128
_SCALE = 1_000_000
_DISCOVERY_LIMITS = frozenset({"cue_limit", "candidate_scan_limit", "candidate_limit", "source_candidate_limit"})
_SUMMARY_FIELDS = (
    "version", "limit", "candidate_count", "admitted_count", "not_selected_count", "focus_count",
    "exploration_count", "source_origin_count", "retrieval_truncated", "search_exhaustive",
    "discovery_limitations",
)


def _features(text):
    words, pairs, _ = _terms(text)
    return {("word", value) for value in words} | {("bigram", value) for value in pairs}


def _support(memory):
    references = [{key: copy.deepcopy(reference[key]) for key in (
        "event_id", "event_revision", "source_content_sha256", "span",
    ) if key in reference} for reference in memory["evidence_refs"] if reference["relationship"] == "supports"]
    references.sort(key=digest)
    origins = {digest({key: reference[key] for key in (
        "event_id", "event_revision", "source_content_sha256",
    )}) for reference in references}
    return digest(references), origins


def _weighted_overlap(query, terms, frequencies):
    def weight(term):
        return (2 if term[0] == "word" else 1) * max(1, 1024 // (1 + frequencies[term]))

    shared = query & terms
    if not shared:
        return 0
    return max(1, _SCALE * sum(weight(term) for term in shared) // max(1, sum(weight(term) for term in query)))


def admit(manifest, memories, sources):
    objectives = manifest["objectives"]
    if len(objectives) > MAX_CANDIDATES:
        raise ValueError("Historical attention candidates exceed their bounded collection.")
    memory_lookup = {(memory["memory_id"], memory["revision"]): memory for memory in memories}
    sources = sorted(sources, key=lambda source: (source["sequence"], source["event_id"]))
    cue = "\n".join(source["observed"]["content"].get("text", "") for source in sources)[:8192]
    primary = [source for source in sources if source["observed"]["actor_role"] in {"user", "operator"}
               and source["observed"]["delivery_state"] == "received"] or sources
    primary_cue = "\n".join(source["observed"]["content"].get("text", "") for source in primary)[:8192]
    all_terms, primary_terms = _features(cue), _features(primary_cue)
    candidates, origin_terms = [], {}
    for objective in objectives:
        reference = objective["target_memory_version"]
        memory = memory_lookup[(reference["memory_id"], reference["revision"])]
        support, origins = _support(memory)
        terms = _features(memory["statement"])
        for origin in origins:
            origin_terms.setdefault(origin, set()).update(terms)
        candidates.append({"objective": objective, "support": support, "origins": origins, "terms": terms})
    frequencies = Counter(term for terms in origin_terms.values() for term in terms)
    seed = digest([POLICY, manifest["input_fingerprint"], manifest["through_sequence"]])
    for candidate in candidates:
        candidate["score"] = _weighted_overlap(primary_terms, candidate["terms"], frequencies)
        candidate["basis"] = "not_selected"
    selected, seen_supports, seen_origins = [], set(), set()

    def choose(candidate, basis):
        candidate["basis"] = basis
        selected.append(candidate)
        seen_supports.add(candidate["support"])
        seen_origins.update(candidate["origins"])

    for _ in range(FOCUS_LIMIT):
        available = [candidate for candidate in candidates
                     if candidate["basis"] == "not_selected" and candidate["terms"] & primary_terms]
        if not available:
            break
        choose(min(available, key=lambda candidate: (
            candidate["support"] in seen_supports, bool(candidate["origins"] & seen_origins),
            -candidate["score"], candidate["objective"]["objective_id"],
        )), "focus")
    available = [candidate for candidate in candidates if candidate["basis"] == "not_selected"]
    if available:
        choose(min(available, key=lambda candidate: (
            candidate["support"] in seen_supports, bool(candidate["origins"] & seen_origins),
            digest([seed, candidate["objective"]["target_memory_version"]]),
        )), "exploration")
    audit = [{**copy.deepcopy(candidate["objective"]), "support_fingerprint": candidate["support"],
              "origin_fingerprint": digest(sorted(candidate["origins"])), "score": candidate["score"],
              "shared_term_count": len(candidate["terms"] & all_terms),
              "primary_shared_term_count": len(candidate["terms"] & primary_terms),
              "basis": candidate["basis"]}
             for candidate in sorted(candidates, key=lambda candidate: candidate["objective"]["objective_id"])]
    limitations = sorted(set(manifest["discovery_limitations"]))
    attention = {
        "version": POLICY, "limit": LIMIT, "candidate_count": len(candidates),
        "admitted_count": len(selected), "not_selected_count": len(candidates) - len(selected),
        "focus_count": sum(candidate["basis"] == "focus" for candidate in selected),
        "exploration_count": sum(candidate["basis"] == "exploration" for candidate in selected),
        "source_origin_count": len(origin_terms), "retrieval_truncated": bool(manifest["unresolved_discovery"]),
        "search_exhaustive": False, "discovery_limitations": limitations, "selection_seed": seed,
        "candidates": audit,
    }
    return {**copy.deepcopy(manifest), "objectives": [copy.deepcopy(item["objective"]) for item in selected],
            "unresolved_discovery": False, "attention": attention}


def _integer(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def sanitize_attention_summary(value):
    if not isinstance(value, dict) or set(value) != set(_SUMMARY_FIELDS):
        return None
    if (value["version"] != POLICY or type(value["limit"]) is not int or value["limit"] != LIMIT
            or value["search_exhaustive"] is not False or type(value["retrieval_truncated"]) is not bool
            or not isinstance(value["discovery_limitations"], list) or len(value["discovery_limitations"]) > 4
            or any(not isinstance(item, str) or item not in _DISCOVERY_LIMITS
                   for item in value["discovery_limitations"])
            or value["discovery_limitations"] != sorted(set(value["discovery_limitations"]))
            or bool(value["discovery_limitations"]) and not value["retrieval_truncated"]):
        return None
    for key in ("candidate_count", "admitted_count", "not_selected_count", "focus_count", "exploration_count"):
        if not _integer(value[key], MAX_CANDIDATES):
            return None
    if (not _integer(value["source_origin_count"], MAX_CANDIDATES * 128)
            or bool(value["candidate_count"]) != bool(value["source_origin_count"])
            or bool(value["candidate_count"]) != bool(value["admitted_count"])
            or value["admitted_count"] > LIMIT or value["focus_count"] > FOCUS_LIMIT
            or value["exploration_count"] > 1
            or value["admitted_count"] != value["focus_count"] + value["exploration_count"]
            or value["candidate_count"] != value["admitted_count"] + value["not_selected_count"]):
        return None
    return copy.deepcopy(value)


def _validate_attention(manifest):
    if "attention" not in manifest:
        return
    attention = manifest["attention"]
    if (not isinstance(attention, dict) or set(attention) != {*_SUMMARY_FIELDS, "selection_seed", "candidates"}
            or sanitize_attention_summary({key: attention[key] for key in _SUMMARY_FIELDS}) is None
            or manifest["unresolved_discovery"] is not False
            or attention["selection_seed"] != digest([POLICY, manifest["input_fingerprint"], manifest["through_sequence"]])
            or attention["discovery_limitations"] != manifest["discovery_limitations"]
            or not isinstance(attention["discovery_limitations"], list)
            or attention["discovery_limitations"] != sorted(set(attention["discovery_limitations"]))
            or any(value not in _DISCOVERY_LIMITS for value in attention["discovery_limitations"])
            or bool(attention["discovery_limitations"]) and not attention["retrieval_truncated"]):
        raise ValueError("Invalid historical attention policy.")
    if (not isinstance(attention["candidates"], list)
            or len(attention["candidates"]) != attention["candidate_count"]):
        raise ValueError("Invalid historical attention bounds.")
    seen, admitted, bases = set(), {}, Counter()
    for item in attention["candidates"]:
        fields = {"objective_id", "target_memory_version", "support_fingerprint", "origin_fingerprint", "score",
                  "shared_term_count", "primary_shared_term_count", "basis"}
        if (not isinstance(item, dict) or set(item) != fields
                or not _hash(item["support_fingerprint"]) or not _hash(item["origin_fingerprint"])
                or not _integer(item["score"], _SCALE)
                or not _integer(item["shared_term_count"], 16384)
                or not _integer(item["primary_shared_term_count"], 16384)
                or item["basis"] not in {"focus", "exploration", "not_selected"}
                or item["basis"] == "focus" and (item["primary_shared_term_count"] == 0 or item["score"] == 0)):
            raise ValueError("Invalid historical attention candidate.")
        reference = item["target_memory_version"]
        if (not isinstance(reference, dict) or set(reference) != {"memory_id", "revision", "content_sha256"}
                or not isinstance(reference["memory_id"], str) or not 1 <= len(reference["memory_id"]) <= 256
                or any(character.isspace() or unicodedata.category(character).startswith("C")
                       for character in reference["memory_id"])
                or not _integer(reference["revision"], 2**63 - 1) or reference["revision"] == 0
                or not _hash(reference["content_sha256"])
                or item["objective_id"] != "comparison-" + digest([
                    manifest["version"], manifest["input_fingerprint"], reference])
                or item["objective_id"] in seen):
            raise ValueError("Invalid historical attention endpoint.")
        seen.add(item["objective_id"])
        bases[item["basis"]] += 1
        if item["basis"] != "not_selected":
            admitted[item["objective_id"]] = reference
    if (bases["focus"] != attention["focus_count"] or bases["exploration"] != attention["exploration_count"]
            or bases["not_selected"] != attention["not_selected_count"]
            or any(admitted.get(item["objective_id"]) != item["target_memory_version"]
                   for item in manifest["objectives"])):
        raise ValueError("Historical attention selection differs from its admitted endpoints.")


def validate_attention(manifest):
    try:
        _validate_attention(manifest)
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid historical attention audit.") from error


def attention_summary(manifest):
    if not isinstance(manifest, dict) or "attention" not in manifest:
        return None
    validate_attention(manifest)
    return {key: copy.deepcopy(manifest["attention"][key]) for key in _SUMMARY_FIELDS}


def model_manifest(manifest):
    result = copy.deepcopy(manifest)
    if isinstance(result, dict) and "attention" in result:
        result["attention"] = attention_summary(manifest)
    return result
