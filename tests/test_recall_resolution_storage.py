import copy
import json
from datetime import datetime, timedelta

import httpx
import pytest
from test_candidates import candidates as candidates
from test_recall_resolution import SHORT, TEXT, example
from test_source_passages import captures

from agent_memory.client import MemoryClient
from agent_memory.consolidation import _assertion, _publish_assertion, validate_batch
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


def published(stored):
    source, _ = captures(stored, TEXT)
    _, data, result, _ = example(source=source)
    with stored.database.transaction(stored.principal) as conn:
        brain = stored.service._brain(conn, stored.principal, stored.brain_id)
        snapshot = validate_batch(conn, stored.service, stored.principal, brain, data, result)
        memory = _assertion(result.claims[0], data, brain, snapshot)
        _publish_assertion(conn, stored.service, stored.principal, brain, memory)
    return source, memory


def client(stored, brain_id=None):
    def handle(request):
        return httpx.Response(200, json=stored.service.handle(stored.principal, json.loads(request.content)))

    return MemoryClient("http://memory.test", "test-key", brain_id or stored.brain_id,
                        transport=httpx.MockTransport(handle))


def test_database_recall_restart_and_expansion_preserve_the_same_source_and_memory(candidates):
    source, memory = published(candidates)
    future = (datetime.fromisoformat(memory["system_time"]["recorded_at"]) + timedelta(days=3650)).isoformat()
    candidates.service = MemoryService(Database(candidates.database.dsn), access_time_provider=lambda: future)
    memory_client = client(candidates)
    short = memory_client.recall("Grandma safe", conversation_id="current", branch_id="main")["result"]
    assert SHORT in short["model_context"] and "yellow lid" not in short["model_context"]
    inspected = memory_client.inspect([memory["memory_id"]], conversation_id="current", branch_id="main")["result"]
    assert inspected["memories"][0]["statement"] == memory["statement"]
    assert inspected["memories"][0]["recall_summary"] == memory["recall_summary"]
    assert inspected["events"][0]["observed"]["content"]["text"] == source["observed"]["content"]["text"]
    expanded = memory_client.recall("Grandma yellow lid", conversation_id="current", branch_id="main")["result"]
    assert "yellow lid" in expanded["model_context"]
    assert any(item["memory"]["memory_id"] == memory["memory_id"] for item in expanded["items"])
    assert memory_client.recall("Grandma safe", detail_level="full", conversation_id="current", branch_id="main")["result"]["usage"]["model_calls"] == 0
    again = memory_client.inspect([memory["memory_id"]], conversation_id="current", branch_id="main")["result"]
    assert again["memories"] == inspected["memories"]


def test_same_verified_summary_can_be_published_idempotently(candidates):
    _, memory = published(candidates)
    original = copy.deepcopy(memory)
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        repeated = _publish_assertion(conn, candidates.service, candidates.principal, brain, memory)
        assert repeated["recall_summary"] == original["recall_summary"]
        assert repeated["memory_id"] == original["memory_id"]


def test_trusted_archive_keeps_both_views_and_explicit_erasure_removes_both(candidates):
    source, memory = published(candidates)
    memory_client = client(candidates)
    exported = memory_client.export()["result"]
    fresh = candidates.service.create_brain(candidates.principal, "Restored resolution test")["id"]
    restored = memory_client.restore(exported["archive_id"], expected_manifest_sha256=exported["manifest_sha256"],
                                     expected_scope_generation=1, mode="new_brain", brain_id=fresh)["result"]
    assert restored["read_enabled"]
    inspected = memory_client.inspect([memory["memory_id"]], brain_id=fresh)["result"]
    assert inspected["memories"][0]["statement"] == memory["statement"]
    assert inspected["memories"][0]["recall_summary"] == memory["recall_summary"]
    client(candidates, fresh).forget([source["event_id"]], kind="events", mode="erase_content",
                                    expected_scope_generation=restored["scope_generation"])
    erased = memory_client.inspect([memory["memory_id"]], brain_id=fresh)["result"]
    assert erased["memories"] == [] and erased["events"] == []


def test_runtime_without_resolution_migration_refuses_the_upgraded_database(candidates, monkeypatch):
    from agent_memory import storage
    from agent_memory.storage import SchemaCompatibilityError

    assert storage.MIGRATIONS[-1][0] == "024_recall_resolution.sql"
    monkeypatch.setattr(storage, "MIGRATIONS", storage.MIGRATIONS[:-1])
    with pytest.raises(SchemaCompatibilityError):
        candidates.database.check_compatibility(require_current=True)
