import copy
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_candidates import Candidates
from test_cognition import STAMP, event, prepare_payload, request, scope, state, working

from agent_memory import candidates as candidate_module
from agent_memory import cognition
from agent_memory.config import Settings
from agent_memory.contracts import CONTRACT_VERSION
from agent_memory.helpers import DomainError, uid
from agent_memory.service import MemoryService
from agent_memory.storage import Database


@pytest.fixture
def actual_time(monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: STAMP)


def rank(result, identifier):
    item = next(item for item in result["items"] if item["memory"]["memory_id"] == identifier)
    return sum(signal["score"] for signal in item["rank_signals"])


@pytest.mark.parametrize("tail", [False, True])
def test_access_time_changes_fallback_age_without_changing_evidence_or_audit_time(actual_time, tail):
    source = event("The keepsake is a quartz whistle.")
    memory, = cognition.extract_event(source)
    frozen = copy.deepcopy((source, memory))
    current = state(indexed_sequence=0 if tail else 1)
    supplied = [] if tail else [memory]
    query = request("quartz keepsake")
    fresh = cognition.recall(scope(), query, supplied, [source], current, access_at=STAMP)
    future = datetime.fromisoformat(STAMP) + timedelta(days=3650)
    old = cognition.recall(scope(), query, supplied, [source], current, access_at=future.isoformat())
    offset = cognition.recall(scope(), query, supplied, [source], current,
                              access_at=future.astimezone(timezone(timedelta(hours=-7))).isoformat())
    assert rank(fresh, memory["memory_id"]) > rank(old, memory["memory_id"]) > 0
    assert rank(old, memory["memory_id"]) == rank(offset, memory["memory_id"])
    assert old["items"][0]["retrieved_from"] == ("unindexed_tail" if tail else "index")
    assert old["items"][0]["memory"] == fresh["items"][0]["memory"]
    assert old["retrieval_snapshot"]["created_at"] == STAMP
    assert {trace["recorded_at"] for trace in old["recall_traces"]} == {STAMP}
    assert (source, memory) == frozen
    assert cognition.recall(scope(), query, supplied, [], current, access_at=future.isoformat())["items"] == []


@pytest.mark.parametrize("starts_tomorrow", [False, True])
def test_access_time_does_not_change_current_fact_validity(actual_time, starts_tomorrow):
    source = event("The keepsake is a quartz whistle.")
    memory, = cognition.extract_event(source)
    memory["memory_kind"] = "semantic"
    memory["valid_time"] = {
        "start": "2026-10-23T12:00:00Z" if starts_tomorrow else STAMP,
        "end": "2026-10-24T12:00:00Z", "precision": "exact", "basis": "source_explicit",
    }
    query = request("quartz keepsake", intent="current_fact")
    actual = cognition.recall(scope(), query, [memory], [source], state())
    aged = cognition.recall(scope(), query, [memory], [source], state(), access_at="2036-10-22T12:00:00Z")
    assert bool(actual["items"]) is not starts_tomorrow
    assert [item["memory"] for item in aged["items"]] == [item["memory"] for item in actual["items"]]
    if aged["items"]:
        assert aged["items"][0]["applicability"] == actual["items"][0]["applicability"]


def test_access_time_does_not_expire_working_context_or_change_packet_time(actual_time):
    source = event("The keepsake is a quartz whistle.")
    memory, = cognition.extract_event(source)
    recalled = cognition.recall(scope(), request("quartz"), [memory], [source], state(),
                                access_at="2036-10-22T12:00:00Z")
    current = working()
    current["items"] = [{
        "key": "keepsake", "value": memory["statement"], "knowledge_state": "grounded",
        "evidence_refs": memory["evidence_refs"], "expires_at": "2026-10-22T13:00:00Z",
        "updated_at": STAMP,
    }]
    packet = cognition.prepare(scope(), prepare_payload(), current, recalled, state())
    assert packet["working_state"]["items"] == current["items"]
    assert packet["created_at"] == STAMP
    assert {trace["recorded_at"] for trace in packet["recall_traces"]} == {STAMP}


@pytest.fixture
def stored():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    return Candidates(Database(settings.database_url))


def call(stored, action, payload):
    response = stored.service.handle(stored.principal, {
        "message_type": "request", "contract_version": CONTRACT_VERSION,
        "request_id": uid(), "action": action, "target": stored.target, "payload": payload,
    })
    assert response["outcome"] == "completed", response
    return response["result"]


def documents(stored):
    with stored.database.transaction(stored.principal) as conn:
        return [conn.execute(
            f"SELECT document FROM {table} WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "ORDER BY id,revision", stored.params,
        ).fetchall() for table in ("memory_events", "memory_assertions")]


def observe(stored, text):
    return call(stored, "observe", {
        "idempotency_key": uid(), "events": [{
            "producer_ref": "access-clock-test", "source_event_key": uid(), "source_revision": 1,
            "actor_ref": "user", "actor_role": "user", "event_kind": "message",
            "content": {"media_type": "text/plain", "text": text},
            "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
            "interpretation_hint": "real_world_report", "delivery_state": "received",
        }],
    })


@pytest.mark.integration
def test_service_freezes_one_access_time_for_index_and_tail_without_mutating_sources(stored, monkeypatch):
    memory = stored.add("The keepsake is a quartz whistle.")
    assert stored.service.work_once(stored.principal, stored.brain_id) == 1
    ribbon = observe(stored, "The quartz keepsake has a blue ribbon.")["accepted_event_ids"]
    cue = observe(stored, "What was my quartz keepsake?")["accepted_event_ids"]
    frozen = documents(stored)
    clock = SimpleNamespace(at=memory["system_time"]["recorded_at"], calls=0)

    def access_time():
        clock.calls += 1
        return clock.at

    stored.service = MemoryService(stored.database, access_time_provider=access_time)
    seen = []
    original_collect, original_recall = candidate_module.collect_candidates, cognition.recall

    def collect(*args, **kwargs):
        seen.append(("candidates", kwargs["access_at"]))
        return original_collect(*args, **kwargs)

    def recall(*args, **kwargs):
        seen.append(("cognition", kwargs["access_at"]))
        return original_recall(*args, **kwargs)

    monkeypatch.setattr(candidate_module, "collect_candidates", collect)
    monkeypatch.setattr(cognition, "recall", recall)
    before = datetime.now(UTC)
    fresh = call(stored, "recall", request("quartz keepsake", cue_event_ids=cue))
    fresh_cue = call(stored, "recall", request("quartz keepsake", intent="cue_recovery", cue_event_ids=cue))
    clock.at = (datetime.fromisoformat(clock.at) + timedelta(days=3650)).isoformat()
    aged = call(stored, "recall", request("quartz keepsake", cue_event_ids=cue))
    aged_cue = call(stored, "recall", request("quartz keepsake", intent="cue_recovery", cue_event_ids=cue))
    after = datetime.now(UTC)
    assert clock.calls == 4
    assert len(seen) == 8
    for index in range(0, len(seen), 2):
        assert seen[index][0] == "candidates" and seen[index + 1][0] == "cognition"
        assert seen[index][1] == seen[index + 1][1]
    assert rank(fresh, memory["memory_id"]) > rank(aged, memory["memory_id"]) > 0
    assert rank(fresh_cue, memory["memory_id"]) == rank(aged_cue, memory["memory_id"])
    assert any(item["memory"]["memory_id"] == memory["memory_id"] and item["retrieved_from"] == "index"
               for item in aged["items"])
    tail, = [item for item in aged["items"] if item["retrieved_from"] == "unindexed_tail"
             and any(ref["event_id"] in ribbon for ref in item["memory"]["evidence_refs"])]
    assert rank(fresh, tail["memory"]["memory_id"]) > rank(aged, tail["memory"]["memory_id"])
    assert documents(stored) == frozen
    for result in (fresh, fresh_cue, aged, aged_cue):
        timestamps = [result["retrieval_snapshot"]["created_at"],
                      *(trace["recorded_at"] for trace in result["recall_traces"])]
        assert all(before <= datetime.fromisoformat(value) <= after for value in timestamps)


@pytest.mark.integration
@pytest.mark.parametrize("invalid", [None, "2036-01-01T00:00:00", "invalid"])
def test_service_rejects_invalid_access_clock_even_without_candidates(stored, invalid):
    stored.service = MemoryService(stored.database, access_time_provider=lambda: invalid)
    with stored.database.transaction(stored.principal) as conn:
        brain = stored.service._brain(conn, stored.principal, stored.brain_id)
        with pytest.raises(DomainError, match="aware RFC3339"):
            stored.service._retrieval(conn, stored.principal, brain, stored.target, request())


def test_access_clock_must_be_a_callable_dependency():
    with pytest.raises(ValueError, match="callable"):
        MemoryService(None, access_time_provider=STAMP)
