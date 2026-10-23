import copy
import json

import pytest
from psycopg.types.json import Jsonb
from test_consolidation import Consolidation
from test_semantic import claim, episode, proposals, run

from agent_memory.archive_views import REBUILD_POLICY
from agent_memory.client import default_budget
from agent_memory.config import Settings
from agent_memory.helpers import DomainError, uid
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


class Archives(Consolidation):
    def export(self, **changes):
        exported = self.action("export", {
            "include_events": True, "include_assertions": True, "include_working_states": True,
            "include_provenance": True, "include_tombstones": True, "budget": default_budget(64000), **changes,
        })
        return self.service.download_archive(self.principal, exported["archive_id"])

    def restore(self, archive, *, new_brain=True, upload=False):
        if new_brain:
            self.brain_id = self.service.create_brain(self.principal, "Restored cognitive archive")["id"]
        if upload:
            archive = self.service.upload_archive(self.principal, self.brain_id, archive["document"])
        return self.action("restore", {
            "uploaded_archive_ref": archive["archive_id"], "expected_manifest_sha256": archive["manifest_sha256"],
            "mode": "new_brain" if new_brain else "merge_authorized_brain", "apply_current_tombstones": True,
            "expected_scope_generation": self.service.dashboard(self.principal, self.brain_id)["brain"]["scope_generation"],
            "budget": default_budget(64000),
        })


@pytest.fixture
def archive():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    return Archives(Database(settings.database_url))


def populated(archive, *, source_revision=1):
    source = archive.observe("工作时我希望直接获得建议。", key="versioned-source")
    if source_revision == 2:
        source = archive.observe("工作时我希望获得直接、具体的建议。", key="versioned-source", revision=2)
    context = archive.observe("这段上下文决定本次记忆的适用情境。")
    first_batch = archive.batch(source, context)
    first = archive.publish(first_batch, run(first_batch, proposals(claim(source), episodes=[episode(source)])))
    parent = next(row["document"] for row in archive.rows("memory_assertions") if row["id"] == first["memory_versions"][0]["memory_id"])
    batch = archive.batch(source, context, existing_assertions=(parent,))
    derived = claim(source, proposal_id="pattern", representation="schema", claim_kind="inferred_pattern",
                    statement="工作情境中的反馈模式。",
                    source_memory_versions=[{"memory_id": parent["memory_id"], "revision": parent["revision"]}])
    archive.publish(batch, run(batch, proposals(derived)))
    return source, context


def test_local_cognitive_archive_restores_exact_views_and_remaps_source_revisions(archive):
    source, _ = populated(archive, source_revision=2)
    exported = archive.export()
    data = exported["document"]
    assert data["archive_origin"] == "local_export"
    assert data["cognitive_views"]["episodes"]
    assert data["cognitive_views"]["members"]
    assert data["cognitive_views"]["associations"]
    assert data["rebuild_policy"] == REBUILD_POLICY
    result = archive.restore(exported)
    assert result["quarantined_event_count"] == 0
    restored_memories = {row["id"]: row["document"] for row in archive.rows("memory_assertions")}
    for memory in data["memories"]:
        restored = restored_memories[memory["memory_id"]]
        assert restored["revision"] == memory["revision"]
        assert restored["derivation"]["source_memory_versions"] == memory["derivation"]["source_memory_versions"]
        for ref in restored["evidence_refs"]:
            if ref["event_id"] == source["event_id"]:
                assert ref["event_revision"] == 1
    restored_episode = archive.rows("memory_episodes")[0]
    assert restored_episode["revision"] == data["cognitive_views"]["episodes"][0]["document"]["revision"]
    assert restored_episode["document"]["member_refs"] == [{"event_id": source["event_id"], "revision": 1}]
    assert len(archive.rows("memory_episode_members")) == len(data["cognitive_views"]["members"])
    assert len(archive.rows("memory_associations")) == len(data["cognitive_views"]["associations"])
    with archive.database.transaction(archive.principal) as conn:
        for table in ("memory_access_state", "memory_embeddings", "memory_cognitive_operations"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table} WHERE brain_id=%s", (archive.brain_id,)).fetchone()["n"] == 0


def test_export_includes_exact_noncurrent_parent_version_without_rewriting_to_latest(archive):
    source = archive.observe("工作时我希望直接获得建议。")
    batch = archive.batch(source)
    archive.publish(batch, run(batch, proposals(claim(source))))
    parent = archive.rows("memory_assertions")[0]["document"]
    parent["revision"] = 7
    with archive.database.transaction(archive.principal) as conn:
        conn.execute("UPDATE memory_assertions SET revision=7,document=%s WHERE brain_id=%s AND id=%s",
                     (Jsonb(parent), archive.brain_id, parent["memory_id"]))
    batch = archive.batch(source, existing_assertions=(parent,))
    child = claim(source, proposal_id="child", representation="schema", claim_kind="inferred_pattern",
                  source_memory_versions=[{"memory_id": parent["memory_id"], "revision": 7}])
    archive.publish(batch, run(batch, proposals(child)))
    with archive.database.transaction(archive.principal) as conn:
        conn.execute("UPDATE memory_assertions SET is_current=false WHERE brain_id=%s AND id=%s", (archive.brain_id, parent["memory_id"]))
    exported = archive.export()
    assert [(m["memory_id"], m["revision"]) for m in exported["document"]["dependency_memories"]] == [(parent["memory_id"], 7)]
    result = archive.restore(exported)
    assert result["quarantined_event_count"] == 0
    rows = archive.rows("memory_assertions")
    restored_parent = next(row for row in rows if row["id"] == parent["memory_id"])
    assert restored_parent["revision"] == 7 and not restored_parent["is_current"]
    restored_child = next(row["document"] for row in rows if row["id"] != parent["memory_id"])
    assert restored_child["derivation"]["source_memory_versions"] == [{"memory_id": parent["memory_id"], "revision": 7}]


def test_erasure_removes_archived_semantics_episodes_and_unselected_root_context(archive):
    _, context = populated(archive)
    exported = archive.export()
    semantic_ids = {m["memory_id"] for m in exported["document"]["memories"] if m.get("verification")}
    offline = copy.deepcopy(exported["document"])
    offline["events"] = [event for event in offline["events"] if event["event_id"] != context["event_id"]]
    for memory in offline["memories"]:
        if memory["memory_id"] in semantic_ids:
            memory["memory_id"] = "offline-renamed-" + memory["memory_id"]
    archive.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [context["event_id"]]},
                              "mode": "erase_content", "expected_scope_generation": 1})
    sanitized = archive.service.download_archive(archive.principal, exported["archive_id"])["document"]
    assert not any(m.get("verification") for m in sanitized["memories"])
    assert not sanitized["cognitive_views"]["episodes"]
    assert not sanitized["cognitive_views"]["members"]
    assert not sanitized["cognitive_views"]["associations"]
    assert "工作情境中的反馈模式" not in json.dumps(sanitized, ensure_ascii=False)
    uploaded = archive.service.upload_archive(archive.principal, archive.brain_id, offline)
    sanitized_upload = archive.service.download_archive(archive.principal, uploaded["archive_id"])["document"]
    assert not any(m.get("verification") for m in sanitized_upload["memories"])
    assert not sanitized_upload["cognitive_views"]["episodes"]


def test_upload_cannot_assert_local_semantic_verification_or_restore_execution_state(archive):
    populated(archive)
    exported = archive.export()
    result = archive.restore(exported, upload=True)
    assert result["quarantined_event_count"] >= 2
    assert all(row["document"].get("verification") is None for row in archive.rows("memory_assertions"))
    assert not archive.rows("memory_episodes")
    assert not archive.rows("memory_episode_members")
    assert not archive.rows("memory_associations")
    assert any("reverification" in detail for detail in result["job"]["details"])


def test_upload_literal_label_cannot_hide_an_unsupported_statement(archive):
    populated(archive)
    exported = archive.export()
    literal = next(m for m in exported["document"]["memories"] if m["derivation"]["method"] == "direct_capture")
    literal["statement"] = "Unsupported private claim that was never in the source."
    result = archive.restore(exported, upload=True)
    assert result["quarantined_event_count"] >= 1
    assert all("Unsupported private claim" not in row["document"]["statement"] for row in archive.rows("memory_assertions"))


def test_export_without_assertions_does_not_include_cognitive_interpretations(archive):
    populated(archive)
    data = archive.export(include_assertions=False)["document"]
    assert data["memories"] == data["dependency_memories"] == []
    assert all(data["cognitive_views"][field] == [] for field in ("episodes", "members", "associations"))
    assert "工作情境中的反馈模式" not in json.dumps(data, ensure_ascii=False)


def test_archive_without_sources_quarantines_interpretations_instead_of_assuming_latest(archive):
    populated(archive)
    exported = archive.export(include_events=False)
    result = archive.restore(exported)
    assert result["imported_event_count"] == 0
    assert result["quarantined_event_count"] >= 2
    assert not archive.rows("memory_assertions")
    assert not archive.rows("memory_episodes")


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(untracked_summary="hidden private content"),
    lambda data: data["cognitive_views"].update(unknown_private_field="hidden content"),
    lambda data: data["cognitive_views"]["episodes"][0]["document"].update(hidden_text="untracked private summary"),
    lambda data: data["cognitive_views"]["episodes"][0]["document"].update(root_event_ids=["x"] * 257),
    lambda data: data["cognitive_views"]["members"][0].update(memory_revision=0),
    lambda data: data["cognitive_views"]["associations"][0].update(event_ids=["x"] * 513),
])
def test_cognitive_archive_extension_fields_are_strict_and_bounded(archive, mutate):
    populated(archive)
    data = archive.export()["document"]
    mutate(data)
    with pytest.raises(DomainError):
        archive.service.upload_archive(archive.principal, archive.brain_id, data)


def test_same_local_archive_merge_is_idempotent_for_episode_versions(archive):
    populated(archive)
    exported = archive.export()
    before = len(archive.rows("memory_episodes"))
    result = archive.restore(exported, new_brain=False)
    assert len(archive.rows("memory_episodes")) == before
    assert result["quarantined_event_count"] == 0
    assert '"quarantined_views": 0' in result["job"]["details"][1]


def test_uploaded_working_state_does_not_bypass_semantic_reverification(archive):
    source, _ = populated(archive)
    exported = archive.export()
    with archive.database.transaction(archive.principal) as conn:
        brain = archive.service._brain(conn, archive.principal, archive.brain_id)
        working = archive.service._working(conn, archive.principal, brain,
                                          {"conversation_ref": "test", "branch_ref": "main"}, None)
    working["revision"] = 1
    working["items"] = [{"key": "fabricated", "value": "Never established by the source.",
                         "knowledge_state": "grounded", "evidence_refs": [exported["document"]["memories"][0]["evidence_refs"][0]],
                         "expires_at": None, "updated_at": source["system_time"]["recorded_at"]}]
    exported["document"]["working_states"] = [working]
    archive.restore(exported, upload=True)
    assert not archive.rows("memory_working_states")


def test_restore_reports_conflicting_association_instead_of_overwriting_it(archive):
    populated(archive)
    exported = archive.export()
    association_id = exported["document"]["cognitive_views"]["associations"][0]["id"]
    with archive.database.transaction(archive.principal) as conn:
        conn.execute("UPDATE memory_associations SET relation='exception' WHERE brain_id=%s AND id=%s", (archive.brain_id, association_id))
    result = archive.restore(exported, new_brain=False)
    assert archive.rows("memory_associations")[0]["relation"] == "exception"
    assert '"quarantined_views": 1' in result["job"]["details"][1]


def test_invalidated_episode_does_not_reappear_or_abort_entire_restore(archive):
    populated(archive)
    exported = archive.export()
    with archive.database.transaction(archive.principal) as conn:
        conn.execute("UPDATE memory_episodes SET invalidated=true,document=NULL WHERE brain_id=%s", (archive.brain_id,))
    result = archive.restore(exported, new_brain=False)
    assert archive.rows("memory_episodes")[0]["document"] is None
    assert result["read_enabled"]
    assert '"quarantined_views": 0' not in result["job"]["details"][1]
