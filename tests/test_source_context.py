import copy

import pytest
from test_cognition import budget, event, prepare_payload, request, scope, state, working

from membrain import cognition
from membrain.contracts import validate
from membrain.source_context import MAX_SOURCE_CONTEXTS, MAX_TOTAL_SOURCE_BYTES

TEXT = ("Aunt Mara spread the blue map. I marked the route while Leo folded the blanket. "
        "Being trusted to choose made me feel capable.")


def plain_report(source):
    memory = cognition.extract_event(source)[0]
    memory.update(memory_id="map-report", statement="The user reported that Mara spread the blue map.",
                  representation="episode_fragment", claim_kind="reported_fact", epistemic_status="derived_inference",
                  conditions={"op": "always"}, valid_during={"kind": "unknown"}, procedure=None)
    memory["derivation"].update(method="aggregation", rule_version="source-composition-v2")
    memory["verification"] = {"state": "supported_inference", "method": "semantic_model",
                               "not_a_truth_certificate": True}
    return memory


def report_recall():
    source = event(TEXT)
    report = plain_report(source)
    result = cognition.recall(scope(), request("blue map"), [report, *cognition.extract_event(source)],
                               [source], state())
    return source, report, result


def test_complete_original_replaces_duplicate_plain_episode_wrappers_without_removing_records():
    from membrain.context_views import recall_context_view

    source, report, result = report_recall()
    assert any(item["memory"]["memory_id"] == report["memory_id"] for item in result["items"])
    view = recall_context_view(result)
    assert not view["memories"] and view["source_passages"][0]["text"] == TEXT
    packet = cognition.prepare(scope(), prepare_payload(), working(), result, state())
    assert not any(block["section"] == "relevant_memory" for block in packet["blocks"])
    assert source["observed"]["content"]["text"] == TEXT
    legacy = copy.deepcopy(result)
    legacy["source_passages"][0]["policy_version"] = "bounded-source-context-v1"
    old = recall_context_view(legacy)
    assert len(old["memories"]) == 1 and old["memories"][0]["statement"] == report["statement"]


@pytest.mark.parametrize("changes", [
    {"conditions": {"op": "eq", "key": "activity", "value": "walking"}},
    {"valid_during": {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"}}},
    {"valid_time": {"start": "2026-10-22T12:00:00Z", "end": None,
                    "precision": "exact", "basis": "source_explicit"}},
    {"memory_kind": "semantic", "representation": "semantic_anchor"},
    {"claim_kind": "inferred_pattern"},
    {"derivation": {"method": "aggregation", "source_memory_versions": [{"memory_id": "parent", "revision": 1}]}},
    {"dynamics": {"user_pinned": True}},
])
def test_full_source_never_suppresses_rules_validity_inference_dependencies_or_pins(changes):
    from membrain.source_context import covering_passage

    _, report, result = report_recall()
    report.update(changes)
    assert covering_passage({"memory": report}, result["source_passages"]) is None


def test_literal_occurrence_time_stays_visible_alongside_original_communication():
    from membrain.context_views import recall_context_view

    source = event(TEXT)
    memories = cognition.extract_event(source)
    memories[0]["valid_time"] = {"start": "2026-10-22T12:00:00Z", "end": None,
                                "precision": "exact", "basis": "source_explicit"}
    result = cognition.recall(scope(), request("blue map"), memories, [source], state())
    view = recall_context_view(result)
    assert view["source_passages"][0]["text"] == TEXT
    assert view["memories"][0]["valid_time"] == memories[0]["valid_time"]


def test_source_unit_admission_retains_distinct_coverage_before_duplicate_versions():
    from membrain.source_context import source_unit_candidates

    documents, ranked = {}, []
    for number, text in enumerate(["The shift is temporary. I am free later.",
                                   "I have sketched two pages. Captions are pending.",
                                   "Mara spread a map. I chose a path.",
                                   "Mondays are occupied. Thursdays are occupied."], 1):
        source = event(text, number)
        literal = cognition.extract_event(source)[0]
        report = plain_report(source)
        report["memory_id"] = f"report-{number}"
        for memory in (report, literal):
            key = memory["memory_id"], memory["revision"]
            documents[key] = memory
            ranked.append({"memory_id": key[0], "revision": key[1], "score": 1 / (len(ranked) + 1)})
    original = copy.deepcopy((ranked, documents))
    naive_sources = {documents[(v["memory_id"], v["revision"])]["evidence_refs"][0]["event_id"]
                     for v in ranked[:4]}
    selected = source_unit_candidates(ranked, documents, limit=4, expand=True)
    selected_sources = {documents[(v["memory_id"], v["revision"])]["evidence_refs"][0]["event_id"]
                        for v in selected}
    assert len(naive_sources) == 2 and len(selected_sources) == 4 and len(selected) == 8
    assert len(source_unit_candidates(ranked, documents, limit=4)) == 4
    assert (ranked, documents) == original


def test_duplicate_family_cannot_exceed_the_version_cap_or_starve_other_selected_units():
    from membrain.source_context import source_unit_candidates

    source = event(TEXT)
    literal = cognition.extract_event(source)[0]
    values = [literal]
    for number in range(140):
        report = plain_report(source)
        report["memory_id"] = f"duplicate-{number}"
        values.append(report)
    for number in range(2, 5):
        values.append(cognition.extract_event(event(f"Independent source {number}.", number))[0])
    documents = {(m["memory_id"], m["revision"]): m for m in values}
    ranked = [{"memory_id": m["memory_id"], "revision": m["revision"], "score": 1 / (i + 1)}
              for i, m in enumerate(values)]
    selected = source_unit_candidates(ranked, documents, limit=4, expand=True)
    assert len(selected) == 128
    assert {documents[(v["memory_id"], v["revision"])]["evidence_refs"][0]["event_id"]
            for v in selected} == {"event-1", "event-2", "event-3", "event-4"}


@pytest.mark.parametrize("difference", ["scope", "world"])
def test_identical_citations_do_not_join_candidate_families_across_scope_or_world(difference):
    from membrain.source_context import source_unit_candidates

    source = event(TEXT)
    report, literal = plain_report(source), cognition.extract_event(source)[0]
    if difference == "scope":
        report["scope"]["visibility"] = "conversation"
    else:
        report["world"] = "quoted"
    documents = {(m["memory_id"], m["revision"]): m for m in (report, literal)}
    ranked = [{"memory_id": m["memory_id"], "revision": m["revision"], "score": 1.0}
              for m in (report, literal)]
    assert source_unit_candidates(ranked, documents, limit=1, expand=True) == ranked[:1]


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
