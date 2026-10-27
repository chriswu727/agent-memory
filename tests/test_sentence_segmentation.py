import copy

import pytest
from test_cognition import event, request, scope, state

from membrain import cognition
from membrain.helpers import DomainError


def captured(text, **changes):
    source = event(text, **changes)
    memories = cognition.extract_event(source)
    excerpts = []
    for memory in memories:
        reference = memory["evidence_refs"][0]
        span = reference["span"]
        excerpt = text[span["start"]:span["end"]]
        assert span["unit"] == "unicode_code_point"
        assert reference["source_content_sha256"] == source["content_sha256"]
        assert memory["statement"].endswith(": " + excerpt)
        assert memory["derivation"]["rule_version"] == "literal-episodes-v2"
        excerpts.append(excerpt)
    return memories, excerpts


def test_writing_schedule_keeps_complete_time_headers_without_formatting_memories():
    text = (
        "- **Slot 1 — Wednesday, 7:00–7:45 p.m.**  \n"
        "  Turn your rough outline into clear story beats, decide what the letter contains, "
        "and draft the opening: Lea’s night routine, the observatory, the unopened letter. "
        "Target about 700–800 words.\n\n"
        "- **Slot 2 — Saturday, 10:00–10:45 a.m.**  \n"
        "  Draft the middle and ending: Lea finally opens the letter, the emotional turn, "
        "closing image. Target about 1,000–1,100 words, then a quick polish."
    )
    _, excerpts = captured(text, role="assistant")
    assert excerpts == [
        "- **Slot 1 — Wednesday, 7:00–7:45 p.m.**",
        "Turn your rough outline into clear story beats, decide what the letter contains, "
        "and draft the opening: Lea’s night routine, the observatory, the unopened letter.",
        "Target about 700–800 words.",
        "- **Slot 2 — Saturday, 10:00–10:45 a.m.**",
        "Draft the middle and ending: Lea finally opens the letter, the emotional turn, "
        "closing image.",
        "Target about 1,000–1,100 words, then a quick polish.",
    ]


def test_numbered_writing_steps_and_closing_quote_stay_with_their_content():
    text = (
        "1. **One opening image:** Lea alone at night—red-lit control room, telescope dome "
        "open, cold air, tea going cold.\n"
        "2. **A small routine:** checking instruments, logging something, walking the "
        "catwalk, locking a door.\n"
        "3. **The letter:** show where it is—drawer, coat pocket, dashboard—and one "
        "physical detail: handwriting, postmark, a crease.\n"
        "4. **One avoidance move:** she touches it, sets it down, puts it away, but doesn’t "
        "open it.\n"
        "5. **A forward-pull ending line:** a sound, a memory of her sister, or her deciding "
        "to open it “later.”"
    )
    _, excerpts = captured(text, role="assistant")
    assert excerpts == text.splitlines()


@pytest.mark.parametrize("text,expected", [
    ("Meet at 7 p.m. on Wednesday. I have not booked anything.",
     ["Meet at 7 p.m. on Wednesday.", "I have not booked anything."]),
    ("Meet at 7 p.m. I have not booked anything.",
     ["Meet at 7 p.m.", "I have not booked anything."]),
    ("Meet at 7P.M. I have not booked anything.",
     ["Meet at 7P.M.", "I have not booked anything."]),
    ("The cost is 3.50. The rate is 0.25, not 2.5.",
     ["The cost is 3.50.", "The rate is 0.25, not 2.5."]),
    ("版本是3.14。不是3.15！确认了吗？", ["版本是3.14。", "不是3.15！", "确认了吗？"]),
    ('She said “not yet.” Then left. **Nothing was sent.**',
     ['She said “not yet.”', "Then left.", "**Nothing was sent.**"]),
    ("你好。Ready? Yes!\r\nNo.", ["你好。", "Ready?", "Yes!", "No."]),
    ("1. Please do not send this.\n2. 不要删除原文。",
     ["1. Please do not send this.", "2. 不要删除原文。"]),
    ("42", ["42"]),
    ("1.", ["1."]),
    ("1.\n2.", ["1.", "2."]),
    ("✅", ["✅"]),
])
def test_sentence_boundaries_preserve_complete_unicode_excerpts(text, expected):
    _, excerpts = captured(text)
    assert excerpts == expected


@pytest.mark.parametrize("text", ["**", "”", "-\n**\n”", "  \n"])
def test_formatting_only_text_remains_source_data_without_literal_assertions(text):
    source = event(text)
    assert source["utterance_segments"] == [{"act": "unknown"}]
    assert cognition.extract_event(source) == []
    assert source["observed"]["content"]["text"] == text


def test_numbered_negation_and_world_qualifications_remain_separate():
    text = "1. I did not send it.\n2. If I send it, I will say so.\n3. 不要删除原文。"
    memories, excerpts = captured(text)
    assert excerpts == text.splitlines()
    assert [memory["world"] for memory in memories] == ["real_world", "hypothetical", "real_world"]
    assert "(instruction)" in memories[2]["statement"]
    assert "did not send" in memories[0]["statement"]


def test_prefix_emoji_does_not_shift_unicode_spans_into_utf8_offsets():
    text = "🌙 夜班。\n  **7:00 p.m.**\n  没有开信。"
    memories, excerpts = captured(text)
    assert excerpts == ["🌙 夜班。", "**7:00 p.m.**", "没有开信。"]
    assert memories[1]["evidence_refs"][0]["span"]["start"] == text.index("**")


def legacy_source(text, **changes):
    source = event(text, **changes)
    source["utterance_segments"] = cognition.classify_segments(
        source["observed"]["content"], rule_version="literal-episodes-v1")
    return source


@pytest.mark.parametrize("text", [
    "Meet at 7:00 p.m. on Wednesday.",
    "1. The letter remains unopened.",
    "She decides to open it “later.”",
])
def test_legacy_sources_keep_literal_identities_without_silent_recapture(text):
    source = legacy_source(text, role="assistant")
    before = copy.deepcopy(source)
    original = cognition.extract_event(source, rule_version="literal-episodes-v1")
    assert cognition.extract_event(source) == original
    assert source == before
    modern = cognition.extract_event(event(text, role="assistant"))
    assert all(memory["derivation"]["rule_version"] == "literal-episodes-v2" for memory in modern)
    assert {memory["memory_id"] for memory in modern} != {memory["memory_id"] for memory in original}


@pytest.mark.parametrize("text,query", [
    ("Meet at 7:00 p.m. on Wednesday.", "Wednesday"),
    ("1. The letter remains unopened.", "1"),
    ("She decides to open it “later.”", "later"),
])
def test_old_literal_and_semantic_citations_remain_recallable_after_boundary_repair(text, query):
    source = legacy_source(text, role="assistant")
    originals = cognition.extract_event(source, rule_version="literal-episodes-v1")
    selected = next(memory for memory in originals if query in memory["statement"])
    semantic = copy.deepcopy(selected)
    semantic.update(memory_id="semantic-old", claim_kind="agent_said",
                    statement="The assistant said: " + selected["statement"].split(": ", 1)[1],
                    epistemic_status="derived_inference")
    semantic["derivation"].update(method="aggregation", rule_version="bounded-semantic-v1")
    old = [selected, semantic]
    before = copy.deepcopy(old)
    result = cognition.recall(scope(), request(query, intent="exact_quote"), old, [source],
                              state(candidate_total=2))
    assert {item["memory"]["memory_id"] for item in result["items"]} == {
        selected["memory_id"], semantic["memory_id"]}
    assert old == before
    assert all(cognition._valid_ref(memory["evidence_refs"][0], {source["event_id"]: source}, scope())
               for memory in old)


def test_frozen_old_semantic_units_remain_valid_but_new_sources_require_repaired_units():
    from test_semantic import claim

    from membrain.semantic import _check_claim_sources

    source = legacy_source("1. The letter remains unopened.", role="assistant")
    old = cognition.extract_event(source)[0]
    proposed = claim(source, statement="The assistant offered a writing step.", claim_kind="agent_said",
                     evidence_refs=old["evidence_refs"])
    _check_claim_sources(proposed, {(source["event_id"], source["revision"]): source}, scope())
    modern = event("1. The letter remains unopened.", role="assistant")
    with pytest.raises(ValueError, match="partial_semantic_unit"):
        _check_claim_sources(proposed, {(modern["event_id"], modern["revision"]): modern}, scope())


def test_unknown_segmentation_versions_are_never_silently_downgraded():
    source = event("A complete statement.")
    with pytest.raises(DomainError, match="Unknown literal segmentation"):
        cognition.extract_event(source, rule_version="literal-episodes-v999")
    with pytest.raises(DomainError, match="Unknown literal segmentation"):
        cognition.classify_segments(source["observed"]["content"], rule_version="literal-episodes-v999")


def test_same_spans_with_updated_list_instruction_classification_keep_the_correct_version():
    text = "- Please keep the original."
    old = cognition.extract_event(legacy_source(text))[0]
    modern = cognition.extract_event(event(text))[0]
    assert old["derivation"]["rule_version"] == "literal-episodes-v1"
    assert modern["derivation"]["rule_version"] == "literal-episodes-v2"
    assert "(direct_statement)" in old["statement"]
    assert "(instruction)" in modern["statement"]
