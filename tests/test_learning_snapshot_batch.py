import copy
from collections import Counter
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb
from test_belief_store import linked
from test_belief_store import store as store
from test_consolidation import memory as memory
from test_semantic import episode, proposals, run

from membrain.helpers import DomainError, digest
from membrain.learning import LearningEngine, validate_snapshot

pytestmark = pytest.mark.integration
GROUPS = {
    "memory_snapshot": ("memory_assertions", "memory_id", "source_unavailable"),
    "episode_snapshot": ("memory_episodes", "episode_id", "context_stale"),
    "belief_relation_snapshot": ("memory_belief_relations", "relation_id", "context_stale"),
}


class Queries:
    def __init__(self, connection):
        self.connection, self.statements = connection, []

    def execute(self, statement, params=None):
        self.statements.append(statement)
        return self.connection.execute(statement, params)

    def document_reads(self):
        return Counter(table for table, _, _ in GROUPS.values()
                       for statement in self.statements if "FROM " + table + " WHERE" in statement)


@pytest.fixture
def snapshot(store):
    sources, *_ = linked(store)
    for source in sources:
        batch = store.batch(source)
        store.publish(batch, run(batch, proposals(episodes=[episode(source)])))
    service, principal = store.service, store.principal
    with store.database.transaction(principal) as conn:
        brain = service._brain(conn, principal, store.brain_id)
        target = {"conversation_ref": "test", "branch_ref": "main"}
        state = service._working(conn, principal, brain, target, None)
        result = {
            "scope": service._scope(principal, brain, target),
            "dependency_generation": brain["dependency_generation"],
            "source_snapshot": [{"event_id": event["event_id"], "revision": event["revision"],
                                 "content_sha256": event["content_sha256"]} for event in sources],
            "working_snapshot": state, "working_revision": state["revision"], "sampling": {},
        }
        documents = {}
        for field, (table, identifier, _) in GROUPS.items():
            rows = conn.execute("SELECT document FROM " + table + " WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                                "ORDER BY id DESC,revision DESC", service._params(principal, brain["id"])).fetchall()
            documents[field] = [row["document"] for row in rows]
            result[field] = [{identifier: document[identifier], "revision": document["revision"],
                              "content_sha256": digest(service._rebind(document, principal, brain)
                                                       if field == "belief_relation_snapshot" else document)}
                             for document in documents[field]]
        assert len(result["memory_snapshot"]) >= 2
        assert len(result["episode_snapshot"]) == 2
        assert len(result["belief_relation_snapshot"]) == 1
        result["belief_relations"] = [service._rebind(document, principal, brain)
                                      for document in documents["belief_relation_snapshot"]]
    return SimpleNamespace(store=store, brain=brain, result=result, documents=documents)


def test_snapshot_batches_each_document_type_and_preserves_public_result(snapshot):
    store = snapshot.store
    with store.database.transaction(store.principal) as raw:
        conn = Queries(raw)
        events = validate_snapshot(conn, store.service, store.principal, snapshot.brain, snapshot.result)
        assert {event["event_id"] for event in events} == {ref["event_id"] for ref in snapshot.result["source_snapshot"]}
        assert conn.document_reads() == dict.fromkeys((group[0] for group in GROUPS.values()), 1)
        validate_snapshot(conn, store.service, store.principal, snapshot.brain, snapshot.result)
        assert conn.document_reads() == dict.fromkeys((group[0] for group in GROUPS.values()), 2)


def test_resume_reuses_validated_batch_and_preserves_frozen_reference_order(snapshot):
    store = snapshot.store
    frozen = copy.deepcopy(snapshot.result)
    with store.database.transaction(store.principal) as raw:
        conn = Queries(raw)
        batch, resumed = LearningEngine(store.service)._resume_snapshot(
            conn, store.principal, snapshot.brain, {"kind": "consolidate", "id": "resume-job"}, frozen)
        assert resumed == snapshot.result
        assert [item["memory_id"] for item in batch.existing_assertions] == [
            item["memory_id"] for item in frozen["memory_snapshot"]]
        assert [item["episode_id"] for item in batch.episodes] == [item["episode_id"] for item in frozen["episode_snapshot"]]
        assert conn.document_reads() == dict.fromkeys((group[0] for group in GROUPS.values()), 1)


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("change", ["missing_version", "content", "scope", "deleted", "invalidated", "null_document"])
def test_batch_rejects_one_bad_reference_among_valid_documents(snapshot, group, change):
    store = snapshot.store
    result = copy.deepcopy(snapshot.result)
    table, identifier, code = GROUPS[group]
    ref = result[group][-1]
    params = (*store.service._params(store.principal, store.brain_id), ref[identifier], ref["revision"])
    with store.database.transaction(store.principal) as conn:
        if change == "missing_version":
            ref["revision"] += 99
        elif change in {"deleted", "invalidated"}:
            revoked = ",document=NULL" if group == "belief_relation_snapshot" else ""
            conn.execute("UPDATE " + table + " SET " + change + "=true" + revoked + " "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND revision=%s", params)
        elif change == "null_document":
            conn.execute("UPDATE " + table + " SET document=NULL "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND revision=%s", params)
        else:
            document = copy.deepcopy(snapshot.documents[group][-1])
            if change == "content":
                document["unrecorded_change"] = True
            else:
                document["scope"].update(visibility="conversation", conversation_id="another-conversation")
                ref["content_sha256"] = digest(store.service._rebind(document, store.principal, snapshot.brain)
                                                if group == "belief_relation_snapshot" else document)
            conn.execute("UPDATE " + table + " SET document=%s "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND revision=%s",
                         (Jsonb(document), *params))
        with pytest.raises(DomainError) as error:
            validate_snapshot(conn, store.service, store.principal, snapshot.brain, result)
        assert error.value.code == code


@pytest.mark.parametrize("change", ["superseded", "retracted", "suppressed"])
def test_historical_assertions_remain_allowed_but_retractions_and_suppression_do_not(snapshot, change):
    store, result = snapshot.store, copy.deepcopy(snapshot.result)
    ref = result["memory_snapshot"][0]
    document = copy.deepcopy(snapshot.documents["memory_snapshot"][0])
    with store.database.transaction(store.principal) as conn:
        if change != "suppressed":
            document["lifecycle"] = change
            ref["content_sha256"] = digest(document)
        conn.execute("UPDATE memory_assertions SET document=%s,is_current=false,suppressed=%s "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND revision=%s",
                     (Jsonb(document), change == "suppressed", *store.service._params(store.principal, store.brain_id),
                      ref["memory_id"], ref["revision"]))
        if change == "superseded":
            assert validate_snapshot(conn, store.service, store.principal, snapshot.brain, result)
        else:
            with pytest.raises(DomainError) as error:
                validate_snapshot(conn, store.service, store.principal, snapshot.brain, result)
            assert error.value.code == "source_unavailable"


def test_episode_must_remain_current_even_when_exact_old_content_matches(snapshot):
    store = snapshot.store
    ref = snapshot.result["episode_snapshot"][0]
    with store.database.transaction(store.principal) as conn:
        conn.execute("UPDATE memory_episodes SET is_current=false WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                     "AND id=%s AND revision=%s", (*store.service._params(store.principal, store.brain_id),
                                                  ref["episode_id"], ref["revision"]))
        with pytest.raises(DomainError) as error:
            validate_snapshot(conn, store.service, store.principal, snapshot.brain, snapshot.result)
        assert error.value.code == "context_stale"


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("change", [{"revision": True}, {"revision": 1.0}, {"revision": "1"},
                                    {"revision": 0}, {"content_sha256": "not-a-hash"}])
def test_malformed_document_references_are_rejected_before_batch_query(snapshot, group, change):
    store = snapshot.store
    result = copy.deepcopy(snapshot.result)
    result[group][0].update(change)
    with store.database.transaction(store.principal) as raw:
        conn = Queries(raw)
        with pytest.raises(DomainError) as error:
            validate_snapshot(conn, store.service, store.principal, snapshot.brain, result)
        assert error.value.code == "context_stale"
        assert conn.document_reads()[GROUPS[group][0]] == 0


def test_scope_rebinding_preserves_raw_parent_hashes_and_rebound_relation_hash(snapshot):
    store, result = snapshot.store, copy.deepcopy(snapshot.result)
    brain = {**snapshot.brain, "scope_generation": snapshot.brain["scope_generation"] + 1}
    result["scope"]["scope_generation"] = brain["scope_generation"]
    for ref, document in zip(result["belief_relation_snapshot"], snapshot.documents["belief_relation_snapshot"], strict=True):
        ref["content_sha256"] = digest(store.service._rebind(document, store.principal, brain))
    with store.database.transaction(store.principal) as conn:
        assert validate_snapshot(conn, store.service, store.principal, brain, result)
