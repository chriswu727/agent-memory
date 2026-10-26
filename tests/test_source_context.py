import copy

import pytest
from test_cognition import budget, event, prepare_payload, request, scope, state, working

from agent_memory import cognition
from agent_memory.contracts import validate
from agent_memory.source_context import MAX_SOURCE_CONTEXTS, MAX_TOTAL_SOURCE_BYTES

TEXT = ("Aunt Mara spread the blue map. I marked the route while Leo folded the blanket. "
        "Being trusted to choose made me feel capable.")


def recall(source=None, *, query="blue map", tokens=6000, current=None, **options):
    source = event(TEXT) if source is None else source
    memories = cognition.extract_event(source)
    result = cognition.recall(scope(), request(query, budget=budget(tokens), **options), memories, [source],
                               current or state())
    return source, memories, result


def test_a_single_scene_cue_exposes_its_complete_bounded_original_including_distant_meaning():
    source, memories, result = recall()
    saved = copy.deepcopy((source, memories))
    passage, = result["source_passages"]
    assert passage["text"] == TEXT and passage["evidence_ref"]["relationship"] == "context_only"
    assert "capable" in result["model_context"]
    assert result["usage"]["context_tokens"] == len(result["model_context"].encode()) <= 6000
    packet = cognition.prepare(scope(), prepare_payload(6000), working(), result, state())
    assert "capable" in packet["model_context"]
    quote, = [block for block in packet["blocks"] if block["section"] == "verbatim_evidence"]
    assert quote["evidence_refs"] == [passage["evidence_ref"]]
    assert packet["recall_traces"] and (source, memories) == saved
    validate("RecallResult", result)
    validate("ContextPacket", packet)


def test_superseded_sibling_span_cannot_be_resurrected_by_reading_the_whole_message():
    source = event("My old door code was 884477. I like blue cups.")
    memories = cognition.extract_event(source)
    blocked = copy.deepcopy(memories[0]["evidence_refs"][0])
    result = cognition.recall(scope(), request("blue cups"), memories, [source],
                               state(superseded_evidence_refs=[blocked]))
    assert result["items"] and "884477" not in result["model_context"]
    assert not result.get("source_passages")


@pytest.mark.parametrize("mutation", ["hash", "revision", "scope", "erased", "assistant"])
def test_whole_source_context_cannot_bypass_source_integrity_scope_or_erasure(mutation):
    source = event(TEXT)
    memories = cognition.extract_event(source)
    if mutation == "hash":
        source["content_sha256"] = "0" * 64
    elif mutation == "revision":
        source["revision"] += 1
    elif mutation == "scope":
        source["scope"]["brain_id"] = "another-brain"
    elif mutation == "assistant":
        source["observed"].update(actor_role="assistant", delivery_state="delivered")
    sources = [] if mutation == "erased" else [source]
    result = cognition.recall(scope(), request("blue map"), memories, sources, state())
    assert not result.get("source_passages")


@pytest.mark.parametrize("options", [
    {"detail_level": "summary"},
    {"evidence_cutoff": {"mode": "through_sequence", "through_sequence": 0}, "evidence_mode": "prior_record_check"},
    {"valid_time_filter": {"start": "2026-01-01T00:00:00Z", "end": None,
                           "precision": "exact", "basis": "source_explicit"}},
    {"world_filter": ["hypothetical"]}, {"cue_event_ids": ["event-1"], "evidence_mode": "prior_record_check"},
])
def test_source_reading_never_overrides_explicit_resolution_cutoff_time_world_or_cue_exclusion(options):
    _, _, result = recall(**options)
    assert not result.get("source_passages")


def test_mixed_world_source_does_not_expand_beyond_real_world_item():
    source = event("I like blue cups. If I were a pirate, I would hide a treasure chest.")
    _, _, result = recall(source, query="blue cups", world_filter=["real_world"])
    assert "blue cups" in result["model_context"] and "treasure chest" not in result["model_context"]
    assert not result.get("source_passages")


def test_large_quote_falls_back_to_an_individual_capture_within_the_same_small_budget():
    source = event("Unrelated background " + "x" * 1300 + ". I like blue cups.")
    _, _, result = recall(source, query="blue cups", tokens=1800)
    assert "blue cups" in result["model_context"]
    assert result["usage"]["context_tokens"] <= 1800 and not result.get("source_passages")
    packet = cognition.prepare(scope(), prepare_payload(1800), working(), result, state())
    assert "blue cups" in packet["model_context"] and packet["usage"]["context_tokens"] <= 1800

    _, _, full_recall = recall(source, query="blue cups")
    assert full_recall.get("source_passages")
    small_packet = cognition.prepare(scope(), prepare_payload(1800), working(), full_recall, state())
    assert "blue cups" in small_packet["model_context"]
    assert not any(block["section"] == "verbatim_evidence" for block in small_packet["blocks"])
    assert small_packet["usage"]["context_tokens"] <= 1800


def test_explicit_memory_selection_does_not_expose_unselected_source_units():
    source = event(TEXT)
    memories = cognition.extract_event(source)
    selected = {key: memories[0][key] for key in ("memory_id", "revision")}
    _, _, result = recall(source, current=state(selected_versions=[selected]))
    assert not result.get("source_passages") and "capable" not in result["model_context"]


def test_expansion_caps_complete_source_bytes_and_count_without_clipping_qualifiers():
    sources = [event(f"Map number {i} has a blue border. " + "x" * 700 + ". This is only a reported scene.", i + 1)
               for i in range(8)]
    memories = [memory for source in sources for memory in cognition.extract_event(source)]
    result = cognition.recall(scope(), request("blue map", budget=budget(64000)), memories, sources, state(8))
    assert 0 < len(result["source_passages"]) <= MAX_SOURCE_CONTEXTS
    assert sum(len(passage["text"].encode()) for passage in result["source_passages"]) <= MAX_TOTAL_SOURCE_BYTES
    assert all(passage["text"] in [s["observed"]["content"]["text"] for s in sources]
               for passage in result["source_passages"])
