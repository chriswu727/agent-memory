import copy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from agent_memory import cognition
from agent_memory.candidates import collect_candidates
from agent_memory.client import default_budget, default_read_fence
from agent_memory.config import Settings
from agent_memory.contracts import CONTRACT_VERSION
from agent_memory.embeddings import EmbeddingResult, EmbeddingSpace
from agent_memory.helpers import DomainError, Principal, digest, now, uid
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration
SPACE = EmbeddingSpace("test-embedding", "v1", 3)


class Candidates:
    def __init__(self, database):
        self.database = database
        self.service = MemoryService(database)
        self.principal = Principal(uid(), uid())
        self.brain_id = self.service.create_brain(self.principal, "Candidate collection tests")["id"]
        self.target = {"brain_ref": self.brain_id, "conversation_ref": "current", "branch_ref": "main"}

    @property
    def params(self):
        return self.principal.tenant_id, self.principal.principal_id, self.brain_id

    def add(self, text, *, branch="main", conversation="current", visibility="private", **changes):
        result = self.service.handle(self.principal, {
            "contract_version": CONTRACT_VERSION, "message_type": "request", "request_id": uid(),
            "action": "observe", "target": {**self.target, "conversation_ref": conversation, "branch_ref": branch},
            "payload": {"idempotency_key": uid(), "events": [{
                "producer_ref": "candidates-test", "source_event_key": uid(), "source_revision": 1,
                "actor_ref": "user", "actor_role": "user", "event_kind": "message",
                "content": {"media_type": "text/plain", "text": text},
                "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
                "interpretation_hint": "real_world_report", "delivery_state": "received",
            }]},
        })
        assert result["outcome"] == "completed", result
        event_id = result["result"]["accepted_event_ids"][0]
        with self.database.transaction(self.principal) as conn:
            brain = self.service._brain(conn, self.principal, self.brain_id)
            event = self.service._events(conn, self.principal, brain, event_ids=[event_id])[0]
            memory = cognition.extract_event(event)[0]
            memory.update(changes)
            self.service._publish(conn, self.principal, brain, memory)
            memory["scope"]["visibility"] = visibility
            for path in memory["disclosure_basis"]["support_paths"]:
                path["permitted_scope"]["visibility"] = visibility
            conn.execute("UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND owner_id=%s "
                         "AND brain_id=%s AND id=%s AND revision=%s", (Jsonb(memory), *self.params, memory["memory_id"], memory["revision"]))
        return memory

    def query(self, text, *, budget=64000, intent="explore", recall_plan=None, query_embedding=None):
        query = {"query": text, "intent": intent, "budget": default_budget(budget),
                 "read_fence": default_read_fence(), "cue_event_ids": [], "additional_cues": []}
        with self.database.transaction(self.principal) as conn:
            brain = self.service._brain(conn, self.principal, self.brain_id)
            return collect_candidates(conn, self.service, self.principal, brain, self.target, query,
                                      recall_plan=recall_plan, query_embedding=query_embedding)

    def edge(self, first, second, *, invalidated=False):
        with self.database.transaction(self.principal) as conn:
            conn.execute(
                "INSERT INTO memory_associations(tenant_id,owner_id,brain_id,id,source_id,source_revision,"
                "target_id,target_revision,relation,invalidated) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'context',%s)",
                (*self.params, uid(), first["memory_id"], first["revision"], second["memory_id"], second["revision"], invalidated),
            )

    def embed(self, memory, vector, *, space=SPACE, content_hash=None):
        with self.database.transaction(self.principal) as conn:
            conn.execute(
                "INSERT INTO memory_embeddings(tenant_id,owner_id,brain_id,memory_id,memory_revision,model,model_version,"
                "content_hash,dimensions,embedding) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                (*self.params, memory["memory_id"], memory["revision"], space.model, space.model_version,
                 content_hash or digest(memory["statement"]), space.dimensions, str(vector)),
            )


@pytest.fixture
def candidates():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    return Candidates(Database(settings.database_url))


def key(memory):
    return memory["memory_id"], memory["revision"]


def query_vector(vector=(1.0, 0.0, 0.0), *, space=SPACE):
    return EmbeddingResult(space, (vector,), None, digest("query"), 100)


def test_lexical_feature_hash_neighbours_without_actual_overlap_are_not_positive(candidates):
    matching = candidates.add("The turquoise lighthouse was our meeting place")
    candidates.add("The winter garden holds orange trees")
    memories, state = candidates.query("turquoise lighthouse")
    assert state["candidate_versions"] == [key(matching)]
    assert [key(memory) for memory in memories] == [key(matching)]
    assert {signal["method"] for signal in state["rank_signals"][key(matching)]} == {"keyword"}
    memories, state = candidates.query("zygomatic")
    assert memories == []
    assert state["rank_signals"] == {}


def test_branch_and_conversation_scope_are_filtered_before_top_k(candidates):
    visible = candidates.add("turquoise")
    for _ in range(20):
        candidates.add("turquoise lighthouse", branch="other")
    for _ in range(20):
        candidates.add("turquoise lighthouse", conversation="other", visibility="conversation")
    memories, state = candidates.query("turquoise lighthouse", budget=2048)
    assert state["candidate_versions"] == [key(visible)]
    assert [key(memory) for memory in memories] == [key(visible)]
    assert not state["source_truncated"]


def test_private_cross_conversation_history_and_scoped_visibility_are_preserved(candidates):
    old = candidates.add("turquoise lighthouse", conversation="previous")
    scoped = candidates.add("turquoise lighthouse", visibility="conversation")
    memories, state = candidates.query("turquoise")
    assert set(state["candidate_versions"]) == {key(old), key(scoped)}
    returned = next(memory for memory in memories if key(memory) == key(scoped))
    assert returned["scope"]["visibility"] == "conversation"
    assert returned["disclosure_basis"]["support_paths"][0]["permitted_scope"]["visibility"] == "conversation"


def test_semantic_vector_route_requires_exact_space_revision_content_and_dimension(candidates):
    matching = candidates.add("coffee preference")
    wrong_version = candidates.add("tea preference")
    stale_content = candidates.add("milk preference")
    opposite = candidates.add("juice preference")
    candidates.embed(matching, [1, 0, 0])
    candidates.embed(wrong_version, [1, 0, 0], space=EmbeddingSpace(SPACE.model, "v2", 3))
    candidates.embed(stale_content, [1, 0, 0], content_hash=digest("old text"))
    candidates.embed(opposite, [-1, 0, 0])
    memories, state = candidates.query("zygomatic", query_embedding=query_vector())
    assert state["candidate_versions"] == [key(matching)]
    assert [key(memory) for memory in memories] == [key(matching)]
    assert {signal["method"] for signal in state["rank_signals"][key(matching)]} == {"vector"}
    with candidates.database.transaction(candidates.principal) as conn:
        conn.execute("UPDATE memory_assertions SET is_current=false WHERE id=%s", (matching["memory_id"],))
    assert candidates.query("zygomatic", query_embedding=query_vector())[1]["candidate_versions"] == []


def test_configured_space_mismatch_and_malformed_query_vectors_are_rejected(candidates):
    candidates.service.embedding_provider = SimpleNamespace(space=SPACE)
    for invalid in (query_vector(space=EmbeddingSpace("another", "v1", 3)), query_vector((1, 0)),
                    query_vector((float("nan"), 0, 1)), query_vector((True, 0, 1)), query_vector((0, 0, 0))):
        with pytest.raises(DomainError, match="embedding"):
            candidates.query("query", query_embedding=invalid)


def test_association_expansion_is_one_hop_and_respects_current_scope(candidates):
    first = candidates.add("quartz keynote")
    second = candidates.add("indigo lantern")
    third = candidates.add("bronze canyon")
    forbidden = candidates.add("cobalt bridge", branch="other")
    invalidated = candidates.add("copper island")
    candidates.edge(first, second)
    candidates.edge(second, third)
    candidates.edge(first, forbidden)
    candidates.edge(first, invalidated, invalidated=True)
    _, state = candidates.query("quartz")
    assert set(state["candidate_versions"]) == {key(first), key(second)}
    assert {signal["method"] for signal in state["rank_signals"][key(second)]} == {"association"}


def test_episode_membership_expands_detail_without_keyword_overlap(candidates):
    outline = candidates.add("exam preparation")
    fragment = candidates.add("ten minutes of conversation")
    with candidates.database.transaction(candidates.principal) as conn:
        episode_id = uid()
        conn.execute("INSERT INTO memory_episodes(tenant_id,owner_id,brain_id,id,revision,conversation_id,branch_id,state,document) "
                     "VALUES(%s,%s,%s,%s,1,'current','main','closed','{}')", (*candidates.params, episode_id))
        for position, memory in enumerate((outline, fragment)):
            conn.execute("INSERT INTO memory_episode_members(tenant_id,owner_id,brain_id,episode_id,episode_revision,"
                         "memory_id,memory_revision,role,position) VALUES(%s,%s,%s,%s,1,%s,%s,'fragment',%s)",
                         (*candidates.params, episode_id, *key(memory), position))
    _, state = candidates.query("exam")
    assert set(state["candidate_versions"]) == {key(outline), key(fragment)}


def test_exact_dependency_revision_is_loaded_without_becoming_an_answer_candidate(candidates):
    parent = candidates.add("indigo lantern")
    child = candidates.add("exam routine")
    child["derivation"]["source_memory_versions"] = [{"memory_id": parent["memory_id"], "revision": 1}]
    child["disclosure_basis"]["support_paths"][0]["source_memory_versions"] = copy.deepcopy(child["derivation"]["source_memory_versions"])
    with candidates.database.transaction(candidates.principal) as conn:
        conn.execute("UPDATE memory_assertions SET document=%s WHERE id=%s", (Jsonb(child), child["memory_id"]))
        conn.execute("UPDATE memory_assertions SET is_current=false WHERE id=%s", (parent["memory_id"],))
    memories, state = candidates.query("exam")
    assert state["candidate_versions"] == [key(child)]
    assert state["valid_dependency_versions"] == {key(parent)}
    assert set(map(key, memories)) == {key(child), key(parent)}
    loaded = next(memory for memory in memories if key(memory) == key(parent))
    assert loaded["lifecycle"] == "superseded"
    assert key(parent) not in state["rank_signals"]


def test_unavailable_dependency_is_not_substituted_by_another_version(candidates):
    parent = candidates.add("indigo lantern")
    child = candidates.add("exam routine")
    child["derivation"]["source_memory_versions"] = [{"memory_id": parent["memory_id"], "revision": 2}]
    with candidates.database.transaction(candidates.principal) as conn:
        conn.execute("UPDATE memory_assertions SET document=%s WHERE id=%s", (Jsonb(child), child["memory_id"]))
    memories, state = candidates.query("exam")
    assert list(map(key, memories)) == [key(child)]
    assert state["valid_dependency_versions"] == set()
    assert state["source_truncated"]


def test_recall_plan_selects_only_exact_versions_without_association_expansion(candidates):
    chosen = candidates.add("indigo lantern")
    other = candidates.add("bronze canyon")
    candidates.edge(chosen, other)
    plan = {"memory_versions": [{"memory_id": chosen["memory_id"], "revision": 1}]}
    memories, state = candidates.query("unrelated", recall_plan=plan)
    assert list(map(key, memories)) == state["candidate_versions"] == [key(chosen)]
    assert state["rank_signals"] == {}
    plan["memory_versions"][0]["revision"] = 2
    assert candidates.query("unrelated", recall_plan=plan)[1]["candidate_versions"] == []


def test_old_details_remain_retrievable_and_cue_recovery_bypasses_age_weight(candidates):
    item = candidates.add("quartz keynote", system_time={"recorded_at": datetime(1990, 1, 1, tzinfo=UTC).isoformat(), "superseded_at": None})
    _, ordinary = candidates.query("quartz")
    _, direct = candidates.query("quartz", intent="cue_recovery")
    assert ordinary["candidate_versions"] == direct["candidate_versions"] == [key(item)]
    ordinary_score = sum(signal["score"] for signal in ordinary["rank_signals"][key(item)])
    direct_score = sum(signal["score"] for signal in direct["rank_signals"][key(item)])
    assert 0 < ordinary_score < direct_score


def test_actual_external_usage_changes_candidate_priority_without_mutating_assertions(candidates):
    old = {"recorded_at": datetime(1990, 1, 1, tzinfo=UTC).isoformat(), "superseded_at": None}
    first = candidates.add("quartz keynote", system_time=old)
    second = candidates.add("quartz keynote", system_time=old)
    unused = candidates.query("quartz")[1]
    promoted = second if unused["candidate_versions"][0] == key(first) else first
    with candidates.database.transaction(candidates.principal) as conn:
        conn.execute("INSERT INTO memory_access_state(tenant_id,owner_id,brain_id,memory_id,memory_revision,"
                     "external_uses,successes,last_external_use_at) VALUES(%s,%s,%s,%s,%s,10,10,%s)",
                     (*candidates.params, *key(promoted), now()))
    memories, state = candidates.query("quartz")
    assert state["candidate_versions"][0] == key(promoted)
    returned = next(memory for memory in memories if key(memory) == key(promoted))
    assert returned["revision"] == promoted["revision"]
    assert returned["epistemic_status"] == promoted["epistemic_status"]


def test_weak_matches_cannot_gain_strong_relevance_from_unrelated_neighborhoods(candidates):
    anchor = candidates.add("Revise the pottery workshop timetable")
    relevant = candidates.add("Twenty minutes is manageable")
    candidates.edge(anchor, relevant)
    distractors = []
    for text in ("Revise the hiking route", "Revise the garden layout", "Revise the grocery list"):
        seed = candidates.add(text)
        neighbor = candidates.add("An unrelated detail")
        candidates.edge(seed, neighbor)
        distractors.extend([seed, neighbor])
    _, state = candidates.query("Revise the pottery workshop timetable")
    ordering = state["candidate_versions"]
    assert ordering[0] == key(anchor)
    assert all(ordering.index(key(relevant)) < ordering.index(key(item)) for item in distractors)
    assert state["rank_signals"][key(relevant)][0]["method"] == "association"


def test_duplicate_paths_do_not_multiply_neighbor_relevance(candidates, monkeypatch):
    anchor = candidates.add("pottery workshop timetable")
    neighbor = candidates.add("Twenty minutes is manageable")
    timestamp = now()
    monkeypatch.setattr("agent_memory.candidates.now", lambda: timestamp)
    candidates.edge(anchor, neighbor)
    first = candidates.query("pottery workshop timetable")[1]
    candidates.edge(anchor, neighbor)
    second = candidates.query("pottery workshop timetable")[1]
    assert first["rank_signals"][key(neighbor)] == second["rank_signals"][key(neighbor)]


def test_episode_seeds_neither_boost_themselves_nor_exhaust_neighbor_slots(candidates):
    anchor = candidates.add("pottery workshop timetable", memory_id="a-anchor")
    others = [candidates.add("pottery equipment", memory_id=f"b-seed-{index}") for index in range(3)]
    detail = candidates.add("Twenty minutes is manageable", memory_id="z-detail")
    with candidates.database.transaction(candidates.principal) as conn:
        episode_id = uid()
        conn.execute("INSERT INTO memory_episodes(tenant_id,owner_id,brain_id,id,revision,conversation_id,branch_id,state,document) "
                     "VALUES(%s,%s,%s,%s,1,'current','main','closed','{}')", (*candidates.params, episode_id))
        for position, memory in enumerate((anchor, *others, detail)):
            conn.execute("INSERT INTO memory_episode_members(tenant_id,owner_id,brain_id,episode_id,episode_revision,"
                         "memory_id,memory_revision,role,position) VALUES(%s,%s,%s,%s,1,%s,%s,'fragment',%s)",
                         (*candidates.params, episode_id, *key(memory), position))
    _, state = candidates.query("pottery workshop timetable", budget=2048)
    assert state["candidate_versions"][:2] == [key(anchor), key(detail)]
    assert len(state["candidate_versions"]) == 4
    assert [signal["method"] for signal in state["rank_signals"][key(anchor)]] == ["keyword"]
