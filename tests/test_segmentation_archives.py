import copy

import pytest
from test_cognition import request
from test_cognitive_archives import archive as archive
from test_sentence_segmentation import legacy_source

from agent_memory import cognition
from agent_memory.archive_views import restore_memories
from agent_memory.helpers import Principal, uid
from agent_memory.service import MemoryService


class EmptyMemoryStore:
    def execute(self, query, params):
        assert query.startswith("SELECT ")
        return self

    def fetchone(self):
        return None


def restore_literal(monkeypatch, source, memories, *, origin="untrusted_upload", superseded=()):
    service = MemoryService(None)
    published = []
    monkeypatch.setattr(service, "_superseded_evidence", lambda *_: list(superseded))
    monkeypatch.setattr(service, "_publish", lambda *args: published.append(copy.deepcopy(args[-1])))
    available = copy.deepcopy(source)
    available["utterance_segments"] = cognition.classify_segments(available["observed"]["content"])
    data = {"events": [source], "memories": memories, "archive_origin": origin}
    restored, rejected, _ = restore_memories(
        EmptyMemoryStore(), service, Principal("tenant", "owner"),
        {"id": "brain", "scope_generation": 1, "dependency_generation": 0},
        data, [available], set(), set())
    return restored, rejected, published


@pytest.mark.parametrize("origin", ["local_export", "untrusted_upload"])
@pytest.mark.parametrize("text", [
    "Meet at 7:00 p.m. on Wednesday.",
    "1. The letter remains unopened.",
    "She decides to open it “later.”",
])
def test_archive_restores_exact_old_literal_spans_and_ids(monkeypatch, origin, text):
    source = legacy_source(text)
    original = cognition.extract_event(source)
    restored, rejected, published = restore_literal(monkeypatch, source, original, origin=origin)
    assert rejected == 0
    assert restored == {(memory["memory_id"], memory["revision"]) for memory in original}
    for before, after in zip(original, published, strict=True):
        for key in ("memory_id", "revision", "statement", "evidence_refs", "derivation"):
            assert after[key] == before[key]


@pytest.mark.parametrize("damage", ["statement", "span", "hash", "version"])
def test_old_rule_upload_requires_exact_supported_literal(monkeypatch, damage):
    source = legacy_source("The letter remains unopened “for now.”")
    memory = cognition.extract_event(source)[0]
    if damage == "statement":
        memory["statement"] = "The letter has already been opened."
    elif damage == "span":
        memory["evidence_refs"][0]["span"]["end"] -= 1
    elif damage == "hash":
        memory["evidence_refs"][0]["source_content_sha256"] = "0" * 64
    else:
        memory["derivation"]["rule_version"] = "literal-episodes-v999"
    restored, rejected, published = restore_literal(monkeypatch, source, [memory])
    assert not restored and rejected == 1 and not published


def test_old_rule_upload_still_excludes_superseded_evidence(monkeypatch):
    source = legacy_source("The letter remains unopened “for now.”")
    memory = cognition.extract_event(source)[0]
    restored, rejected, published = restore_literal(
        monkeypatch, source, [memory], superseded=memory["evidence_refs"])
    assert not restored and rejected == 1 and not published


def test_upload_validation_cache_keeps_extraction_versions_separate(monkeypatch):
    source = legacy_source("Meet at 7 p.m. on Wednesday.")
    old = cognition.extract_event(source)
    modern = cognition.extract_event(source, rule_version="literal-episodes-v2")
    memories = old + modern
    restored, rejected, published = restore_literal(monkeypatch, source, memories)
    assert rejected == 0
    assert len(restored) == len(published) == len(memories)
    assert {memory["derivation"]["rule_version"] for memory in published} == {
        "literal-episodes-v1", "literal-episodes-v2"}


@pytest.mark.integration
@pytest.mark.parametrize("upload", [False, True])
def test_database_restore_preserves_legacy_source_units_and_capture_identity(archive, monkeypatch, upload):
    original_classifier = cognition.classify_segments
    with monkeypatch.context() as patch:
        patch.setattr(cognition, "classify_segments", lambda content, **_: original_classifier(
            content, rule_version="literal-episodes-v1"))
        source = archive.observe("1. The letter remains unopened until 7 p.m. “Not yet.”")
    captures = cognition.extract_event(source)
    assert all(memory["derivation"]["rule_version"] == "literal-episodes-v1" for memory in captures)
    with archive.database.transaction(archive.principal) as conn:
        brain = archive.service._brain(conn, archive.principal, archive.brain_id)
        for memory in captures:
            archive.service._publish(conn, archive.principal, brain, memory)
    exported = archive.export()
    result = archive.restore(exported, upload=upload)
    assert result["quarantined_event_count"] == 0
    stored = {row["id"]: row["document"] for row in archive.rows("memory_assertions")}
    assert set(stored) == {memory["memory_id"] for memory in captures}
    with archive.database.transaction(archive.principal) as conn:
        brain = archive.service._brain(conn, archive.principal, archive.brain_id)
        restored_source = archive.service._events(conn, archive.principal, brain,
                                                   event_ids=[source["event_id"]])[0]
        recaptured = archive.service._current_captures(conn, archive.principal, brain, restored_source)
    assert restored_source["utterance_segments"] == source["utterance_segments"]
    assert {memory["memory_id"] for memory in recaptured} == set(stored)
    for memory in captures:
        after = stored[memory["memory_id"]]
        assert after["evidence_refs"] == memory["evidence_refs"]
        assert after["derivation"] == memory["derivation"]


@pytest.mark.integration
def test_local_restore_and_recall_preserve_old_semantic_span_after_list_prefix(archive, monkeypatch):
    original_classifier = cognition.classify_segments
    with monkeypatch.context() as patch:
        patch.setattr(cognition, "classify_segments", lambda content, **_: original_classifier(
            content, rule_version="literal-episodes-v1"))
        source = archive.observe("1. The letter remains unopened. She will decide later.")
    literal = next(memory for memory in cognition.extract_event(source)
                   if "letter remains unopened" in memory["statement"])
    semantic = copy.deepcopy(literal)
    semantic.update(memory_id="semantic-" + uid(), statement="The user reported that the letter remains unopened.",
                    claim_kind="reported_fact", representation="semantic_anchor",
                    epistemic_status="derived_inference", conditions={"op": "always"},
                    valid_during={"kind": "unknown"},
                    verification={"state": "supported_inference", "method": "semantic_model",
                                  "not_a_truth_certificate": True})
    semantic["derivation"].update(method="aggregation", rule_version="bounded-semantic-v1")
    with archive.database.transaction(archive.principal) as conn:
        brain = archive.service._brain(conn, archive.principal, archive.brain_id)
        archive.service._publish(conn, archive.principal, brain, literal)
        archive.service._publish(conn, archive.principal, brain, semantic)
    result = archive.restore(archive.export())
    assert result["quarantined_event_count"] == 0
    stored = {row["id"]: row["document"] for row in archive.rows("memory_assertions")}
    assert stored[semantic["memory_id"]]["evidence_refs"] == semantic["evidence_refs"]
    assert stored[semantic["memory_id"]]["evidence_refs"][0]["span"]["start"] == 3
    recalled = archive.action("recall", request("letter remains unopened", intent="exact_quote"))
    recalled_ids = {item["memory"]["memory_id"] for item in recalled["items"]}
    assert {literal["memory_id"], semantic["memory_id"]}.issubset(recalled_ids)
