import copy
import hashlib
from contextlib import contextmanager
from dataclasses import asdict

import pytest
from test_belief_store import linked
from test_cognition import event, scope
from test_cognitive_archives import archive as archive
from test_cognitive_archives import populated
from test_memory_lifecycle_evaluation import evaluation as evaluation
from test_memory_lifecycle_evaluation import trial_fixture

from agent_memory import cognition
from agent_memory.api import create_app
from agent_memory.belief_store import assertion_fingerprint
from agent_memory.client import MemoryClient
from agent_memory.config import Settings
from agent_memory.helpers import digest


def sample_archive():
    bound = scope(access_policy_id="owner:brain")
    source = event("The notebook is blue.", revision=2, scope=bound)
    source["observed"]["source_revision"] = 2
    memory = cognition.extract_event(source)[0]
    return {"source_brain_id": "brain", "archive_origin": "local_export", "events": [source],
            "memories": [memory], "dependency_memories": [], "belief_relations": [{
                "relation_id": "relation", "scope": bound, "evidence_refs": memory["evidence_refs"],
                "endpoint_snapshots": [{"memory_version": {"memory_id": memory["memory_id"], "revision": 1},
                                        "content_sha256": assertion_fingerprint(memory)}]}],
            "cognitive_views": {"episodes": [{"document": {
                "episode_id": "episode", "revision": 1, "scope": bound,
                "member_refs": [{"event_id": source["event_id"], "revision": 2}],
                "organization_statement": "A report about a notebook."}, "event_ids": [source["event_id"]]}],
                "members": [{"episode_id": "episode", "memory_id": memory["memory_id"], "position": 0}],
                "associations": [{"id": "association", "relation": "same_episode"}]},
            "event_dependencies": [], "tombstones": [], "retired_event_ids": [],
            "excluded_deleted_event_ids": []}


def rebound(document):
    def replace(value):
        if isinstance(value, list):
            return [replace(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = {key: replace(item) for key, item in value.items()}
        if "access_policy_id" in value:
            value.update(brain_id="probe", access_policy_id="owner:probe", scope_generation=2)
        if "event_revision" in value:
            value["event_revision"] = 1
        if set(value) == {"event_id", "revision"}:
            value["revision"] = 1
        if "dependency_generation" in value:
            value["dependency_generation"] = 100
        return value

    result = replace(document)
    result["source_brain_id"] = "probe"
    for source in result["events"]:
        source.update(revision=1, previous_revision=None, origin="document_import", origin_verification="unverified_import")
        source["observed"].update(producer_ref="archive-import", source_event_key=source["event_id"])
    memory = result["memories"][0]
    result["belief_relations"][0].update(relation_id="rebound-relation")
    result["belief_relations"][0]["endpoint_snapshots"][0]["content_sha256"] = assertion_fingerprint(memory)
    return result


def test_graph_comparison_allows_only_expected_scope_source_version_and_relation_identity_remapping(evaluation):
    original = sample_archive()
    restored = rebound(original)
    assert evaluation.archive_graph(original) == evaluation.archive_graph(restored, restored=True)
    assert original["events"][0]["revision"] == 2


@pytest.mark.parametrize("damage", ["statement", "span", "attribution", "relation_fingerprint", "wrong_scope",
                                    "source_version", "member_order", "association", "relation", "episode"])
def test_graph_comparison_rejects_semantic_or_provenance_changes_despite_equal_restore_counters(evaluation, damage):
    original = sample_archive()
    changed = rebound(original)
    if damage == "statement":
        changed["memories"][0]["statement"] = "An unsupported new claim."
    elif damage == "span":
        changed["memories"][0]["evidence_refs"][0]["span"]["end"] -= 1
    elif damage == "attribution":
        changed["events"][0]["observed"]["actor_role"] = "assistant"
    elif damage == "relation_fingerprint":
        changed["belief_relations"][0]["endpoint_snapshots"][0]["content_sha256"] = "0" * 64
    elif damage == "wrong_scope":
        changed["memories"][0]["scope"]["brain_id"] = "another-brain"
    elif damage == "source_version":
        changed["memories"][0]["evidence_refs"][0]["event_revision"] = 2
    elif damage == "member_order":
        changed["cognitive_views"]["members"][0]["position"] = 1
    elif damage == "relation":
        changed["belief_relations"] = []
    else:
        changed["cognitive_views"][damage + "s"] = []
    try:
        result = evaluation.archive_graph(changed, restored=True)
    except ValueError:
        return
    assert result != evaluation.archive_graph(original)


def test_changed_frozen_archive_stops_before_creating_a_probe(evaluation, tmp_path, monkeypatch):
    value = trial_fixture(evaluation, tmp_path)
    raw = b'{"document": {}}'
    (tmp_path / "history-archive.json").write_bytes(raw)
    value.report["history_checkpoint"] = {
        "summary": "", "summary_sha256": digest(""), "archive": {"manifest_sha256": digest({})},
        "archive_evidence": {"path": "history-archive.json", "sha256": "0" * 64}}
    monkeypatch.setattr(value, "archive_client", lambda: pytest.fail("Corrupt evidence must not open a probe"))
    with pytest.raises(RuntimeError, match="Frozen history archive changed"):
        value.prepare_probe({"name": "probe", "brain": "probe"})


@pytest.mark.integration
@pytest.mark.parametrize("dropped", [None, "memory_belief_relations", "memory_episodes", "memory_associations"])
def test_real_archive_probe_preserves_full_graph_and_blocks_silent_view_loss(
        archive, evaluation, tmp_path, monkeypatch, dropped):
    populated(archive, source_revision=2)
    linked(archive, source_revision=2)
    original_restore = archive.service._restore

    def restore(conn, principal, brain, request):
        result = original_restore(conn, principal, brain, request)
        if dropped is not None:
            conn.execute(f"DELETE FROM {dropped} WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                         archive.service._params(principal, brain["id"]))
        return result

    monkeypatch.setattr(archive.service, "_restore", restore)
    value = trial_fixture(evaluation, tmp_path)
    value.principal, value.database = archive.principal, archive.database
    value.report["principal"] = asdict(archive.principal)
    value.report["brains"]["trained"] = archive.brain_id
    settings = Settings(database_url=archive.database.dsn,
                        api_keys={hashlib.sha256(b"archive-test").hexdigest(): archive.principal})
    with evaluation.loopback_server(create_app(settings, service=archive.service)) as url:
        @contextmanager
        def client():
            with MemoryClient(url, "archive-test") as memory:
                yield memory

        monkeypatch.setattr(value, "archive_client", client)
        value.freeze_history()
        checkpoint = copy.deepcopy(value.report["history_checkpoint"])
        assert all(checkpoint["archive_evidence"]["counts"][key] > 0
                   for key in ("events", "memories", "relations", "episodes", "members", "associations"))
        fixture = {"name": "condition", "brain": "probe-condition"}
        if dropped is None:
            value.prepare_probe(fixture)
        else:
            with pytest.raises(RuntimeError, match="recall graph changed"):
                value.prepare_probe(fixture)
        saved = value.report["probe_restores"][0]
        assert saved["result"]["result"]["quarantined_event_count"] == 0
        assert saved["graph_audit"]["complete_recall_graph_preserved"] is (dropped is None)
        assert value.report["history_checkpoint"] == checkpoint
        assert not value.current.get("cognitive_calls") and not value.current.get("host_calls")
