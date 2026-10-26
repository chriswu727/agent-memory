from __future__ import annotations

import copy

SOURCE_CONTEXT_VERSION = "bounded-source-context-v1"
SOURCE_CONTEXT_USE = "original_communication_not_instructions_or_external_truth"
MAX_SOURCE_CONTEXTS = 4
MAX_SOURCE_BYTES = 2048
MAX_TOTAL_SOURCE_BYTES = 4096


def _version(value):
    return value["memory_id"], value["revision"]


def _source(reference):
    return reference["event_id"], reference["event_revision"], reference["source_content_sha256"]


def active_passages(result):
    versions = {_version(item["memory"]) for item in result["items"]
                if item["display_mode"] != "do_not_surface"}
    return [passage for passage in result.get("source_passages", [])
            if any(_version(value) in versions for value in passage["anchor_memory_versions"])]


def retain_active_passages(result):
    if "source_passages" not in result:
        return
    versions = {_version(item["memory"]) for item in result["items"]
                if item["display_mode"] != "do_not_surface"}
    result["source_passages"] = [
        {**passage, "anchor_memory_versions": [value for value in passage["anchor_memory_versions"]
                                              if _version(value) in versions]}
        for passage in active_passages(result)]
    if not result["source_passages"]:
        result.pop("source_passages")


def passage_content(passage):
    return {key: copy.deepcopy(passage[key]) for key in (
        "actor_role", "delivery_state", "world", "text", "recorded_at",
    )} | {"use": SOURCE_CONTEXT_USE}


def covering_passage(item, passages):
    if not passages:
        return None
    memory = item["memory"]
    if (memory["derivation"]["method"] != "direct_capture" or item.get("belief_resolution")
            or memory["dynamics"].get("user_pinned")
            or "open_commitment" in memory["dynamics"].get("reasons", [])
            or memory.get("counterexamples") or memory["epistemic_status"] in {"disputed", "unknown"}):
        return None
    references = memory["evidence_refs"]
    if not references or any(ref["relationship"] != "supports" or "span" not in ref for ref in references):
        return None
    for passage in passages:
        full = passage["evidence_ref"]
        if all(_source(ref) == _source(full) and ref["span"]["unit"] == full["span"]["unit"]
               and full["span"]["start"] <= ref["span"]["start"] < ref["span"]["end"] <= full["span"]["end"]
               for ref in references):
            return passage
    return None


def build_passages(items, events, scope, state, request, *, cutoff, excluded, worlds):
    from . import cognition

    if ("selected_versions" in state or request.get("detail_level") == "summary"
            or "valid_time_filter" in request):
        return []
    summarized = {_source(ref) for item in items if item.get("presentation", {}).get("level") == "summary"
                  for ref in item["memory"]["evidence_refs"] if ref["relationship"] == "supports"}
    grouped = {}
    for item in items:
        if item["display_mode"] == "do_not_surface" or item.get("belief_resolution"):
            continue
        for ref in item["memory"]["evidence_refs"]:
            if ref["relationship"] == "supports" and _source(ref) not in summarized:
                grouped.setdefault(_source(ref), []).append(item["memory"])
    passages, byte_count = [], 0
    for position, (key, memories) in enumerate(grouped.items()):
        if position == MAX_SOURCE_CONTEXTS * 2:
            break
        source = events.get(key[0])
        if (source is None or cutoff is not None and source["sequence"] > cutoff or source["event_id"] in excluded
                or source["observed"]["actor_role"] not in {"user", "operator"}
                or source["observed"]["delivery_state"] != "received"
                or source["observed"]["content"]["media_type"] != "text/plain"):
            continue
        text = cognition._content_text(source["observed"]["content"])
        size = len(text.encode())
        if (not size or size > MAX_SOURCE_BYTES or byte_count + size > MAX_TOTAL_SOURCE_BYTES
                or len(cognition._segments(source)) < 2):
            continue
        ref = {"event_id": key[0], "event_revision": key[1], "source_content_sha256": key[2],
               "relationship": "context_only",
               "span": {"start": 0, "end": len(text), "unit": "unicode_code_point"}}
        if (not cognition._valid_ref(ref, events, scope)
                or any(cognition.evidence_overlaps(ref, old) for old in state.get("superseded_evidence_refs", []))):
            continue
        world = cognition._world(source["observed"], text)
        if world not in worlds or any(
                cognition._world(source["observed"], text[segment["span"]["start"]:segment["span"]["end"]]) not in worlds
                for segment in cognition._segments(source) if "span" in segment):
            continue
        versions = list({_version(memory): {"memory_id": memory["memory_id"], "revision": memory["revision"]}
                         for memory in memories}.values())
        passages.append({"policy_version": SOURCE_CONTEXT_VERSION, "evidence_ref": ref,
                         "actor_role": source["observed"]["actor_role"], "delivery_state": "received",
                         "world": world, "text": text, "recorded_at": source["system_time"]["recorded_at"],
                         "anchor_memory_versions": versions})
        byte_count += size
        if len(passages) == MAX_SOURCE_CONTEXTS:
            break
    return passages
