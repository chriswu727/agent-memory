import copy
import os
from dataclasses import replace

import pytest
from test_cognition import request
from test_semantic import claim, episode, proposals, reference, run, situation

from agent_memory.config import Settings
from agent_memory.consolidation import publish_semantic
from agent_memory.contracts import CONTRACT_VERSION, validate
from agent_memory.helpers import DomainError, Principal, uid
from agent_memory.semantic import CognitiveBatch
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


class Consolidation:
    def __init__(self, database):
        self.database = database
        self.service = MemoryService(database)
        self.principal = Principal(uid(), uid())
        self.brain_id = self.service.create_brain(self.principal, "Semantic publication tests")["id"]

    def action(self, action, payload):
        response = self.service.handle(self.principal, {
            "message_type": "request", "contract_version": CONTRACT_VERSION, "request_id": uid(),
            "action": action, "target": {"brain_ref": self.brain_id, "conversation_ref": "test", "branch_ref": "main"},
            "payload": payload,
        })
        assert response["outcome"] == "completed", response
        return response["result"]

    def observe(self, text, key=None, revision=1):
        result = self.action("observe", {"idempotency_key": uid(), "events": [{
            "source_event_key": key or uid(), "source_revision": revision, "producer_ref": "test",
            "actor_ref": "user", "actor_role": "user", "event_kind": "message",
            "content": {"media_type": "text/plain", "text": text},
            "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
            "interpretation_hint": "real_world_report", "delivery_state": "received",
        }]})
        event_id = result["accepted_event_ids"][0]
        with self.database.transaction(self.principal) as conn:
            brain = self.service._brain(conn, self.principal, self.brain_id)
            return self.service._events(conn, self.principal, brain, event_ids=[event_id])[0]

    def batch(self, *sources, **kwargs):
        return CognitiveBatch(uid(), sources[0]["scope"], tuple(sources), **kwargs)

    def publish(self, batch, result, job_id=None):
        with self.database.transaction(self.principal) as conn:
            brain = self.service._brain(conn, self.principal, self.brain_id)
            return publish_semantic(conn, self.service, self.principal, brain, batch, result, job_id=job_id or uid())

    def rows(self, table):
        assert table in {"memory_assertions", "memory_episodes", "memory_episode_members", "memory_working_states", "memory_associations"}
        with self.database.transaction(self.principal) as conn:
            return conn.execute(
                "SELECT * FROM " + table + " WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                self.service._params(self.principal, self.brain_id),
            ).fetchall()


@pytest.fixture
def memory():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    database = Database(os.environ.get("MEMORY_TEST_DATABASE_URL", settings.database_url), settings.migration_database_url)
    database.migrate()
    return Consolidation(database)


def test_published_claim_retains_inference_conditions_counterexample_and_all_input_roots(memory):
    first = memory.observe("工作时我希望直接获得建议。")
    counter = memory.observe("今天工作时我不想听建议。")
    context = memory.observe("另外一件没有选中的事情。")
    batch = memory.batch(first, counter, context)
    proposal = claim(first, counterexamples=[reference(counter, "contradicts")])
    result = run(batch, proposals(proposal, updates=[situation(first)]))
    metadata = memory.publish(batch, result)
    assert metadata["published_count"] == 1
    row = memory.rows("memory_assertions")[0]
    saved = row["document"]
    validate("MemoryAssertion", saved)
    assert saved["epistemic_status"] == "derived_inference"
    assert saved["conditions"] == proposal["conditions"]
    assert saved["verification"]["state"] == "contested"
    assert set(row["event_ids"]) == {first["event_id"], counter["event_id"], context["event_id"]}
    assert saved["counterexamples"][0]["relationship"] == "contradicts"
    assert saved["disclosure_basis"]["support_paths"][0]["evidence_refs"] == proposal["evidence_refs"]
    assert not memory.rows("memory_working_states")


def test_publication_rejects_changed_unselected_context_and_writes_nothing(memory):
    first = memory.observe("工作时我希望直接获得建议。")
    key = uid()
    context = memory.observe("未选中但被模型看到的旧上下文。", key=key)
    batch = memory.batch(first, context)
    result = run(batch, proposals(claim(first)))
    memory.observe("已改变的上下文。", key=key, revision=2)
    with pytest.raises(DomainError, match="snapshot"):
        memory.publish(batch, result)
    assert not memory.rows("memory_assertions")


def test_source_erasure_during_model_work_blocks_publication(memory):
    source = memory.observe("工作时我希望直接获得建议。")
    batch = memory.batch(source)
    result = run(batch, proposals(claim(source)))
    memory.action("forget", {"selector": {"kind": "events", "ids": [source["event_id"]]},
                             "mode": "erase_content", "expected_scope_generation": 1,
                             "idempotency_key": uid()})
    with pytest.raises(DomainError, match="snapshot"):
        memory.publish(batch, result)
    assert not memory.rows("memory_assertions")


def test_erasing_unselected_read_context_invalidates_a_published_inference(memory):
    first, other = memory.observe("工作时我希望直接获得建议。"), memory.observe("没有被选中的上下文。")
    batch = memory.batch(first, other)
    memory.publish(batch, run(batch, proposals(claim(first))))
    memory.action("forget", {"selector": {"kind": "events", "ids": [other["event_id"]]},
                             "mode": "suppress_recall", "expected_scope_generation": 1,
                             "idempotency_key": uid()})
    row = memory.rows("memory_assertions")[0]
    assert row["invalidated"] and row["suppressed"]


def test_same_semantic_claim_across_jobs_reuses_evidence_without_reinforcement(memory):
    source = memory.observe("工作时我希望直接获得建议。")
    batch = memory.batch(source)
    job_id = uid()
    result = run(batch, proposals(claim(source)))
    first = memory.publish(batch, result, job_id=job_id)
    second = memory.publish(batch, result, job_id=job_id)
    assert first == second and len(memory.rows("memory_assertions")) == 1
    original = copy.deepcopy(memory.rows("memory_assertions")[0]["document"])
    extra = memory.observe("另一个本轮上下文。")
    later = memory.batch(source, extra)
    repeated = memory.publish(later, run(later, proposals(claim(source, proposal_id="different-local-id"))))
    assert repeated["memory_versions"] == first["memory_versions"]
    assert len(memory.rows("memory_assertions")) == 1
    assert memory.rows("memory_assertions")[0]["document"] == original


def test_changed_conditions_do_not_collide_with_prior_semantic_identity(memory):
    source = memory.observe("工作时我希望直接获得建议。")
    batch = memory.batch(source)
    first = memory.publish(batch, run(batch, proposals(claim(source))))
    different = claim(source, conditions={"op": "eq", "key": "situation", "value": "home"})
    second = memory.publish(batch, run(batch, proposals(different)))
    assert first["memory_versions"] != second["memory_versions"]
    assert len(memory.rows("memory_assertions")) == 2


def test_repeated_claim_citing_itself_reuses_original_proof_without_a_reinforcement_chain(memory):
    source = memory.observe("工作时我希望直接获得建议。")
    batch = memory.batch(source)
    first = memory.publish(batch, run(batch, proposals(claim(source))))
    original = copy.deepcopy(memory.rows("memory_assertions")[0]["document"])
    batch = memory.batch(source, existing_assertions=(original,))
    repeated = claim(source, source_memory_versions=first["memory_versions"])
    second = memory.publish(batch, run(batch, proposals(repeated)))
    assert second["memory_versions"] == first["memory_versions"]
    assert len(memory.rows("memory_assertions")) == 1
    assert memory.rows("memory_assertions")[0]["document"] == original
    assert memory.rows("memory_associations") == []


def test_pending_or_forged_verification_cannot_publish(memory):
    source = memory.observe("工作时我希望直接获得建议。")
    batch = memory.batch(source)
    pending = run(batch, proposals(claim(source)), verify=None)
    assert memory.publish(batch, pending)["published_count"] == 0
    valid = run(batch, proposals(claim(source)))
    wrong = copy.deepcopy(valid.claims[0])
    wrong["epistemic_status"] = "observed_result"
    with pytest.raises(DomainError, match="remains inferred"):
        memory.publish(batch, replace(valid, claims=(wrong,)))
    assert not memory.rows("memory_assertions")


def test_exact_parent_versions_are_persisted_and_linked(memory):
    source = memory.observe("工作时我希望直接获得建议。")
    initial = memory.batch(source)
    memory.publish(initial, run(initial, proposals(claim(source))))
    parent = memory.rows("memory_assertions")[0]["document"]
    batch = memory.batch(source, existing_assertions=(parent,))
    proposal = claim(source, proposal_id="derived", claim_kind="inferred_pattern", representation="schema",
                     source_memory_versions=[{"memory_id": parent["memory_id"], "revision": 1}])
    metadata = memory.publish(batch, run(batch, proposals(proposal)))
    derived = next(row for row in memory.rows("memory_assertions") if row["id"] == metadata["memory_versions"][0]["memory_id"])
    assert derived["memory_ids"] == [parent["memory_id"]]
    assert derived["document"]["derivation"]["source_memory_versions"] == proposal["source_memory_versions"]
    association = memory.rows("memory_associations")[0]
    assert association["relation"] == "schema" and association["target_id"] == parent["memory_id"]


def test_episode_creation_references_actual_fragments_and_is_idempotent(memory):
    first, second = memory.observe("开始准备考试。"), memory.observe("然后制定复习计划。")
    batch = memory.batch(first, second)
    proposal = episode(first, member_refs=[{"event_id": first["event_id"], "revision": 1},
                                         {"event_id": second["event_id"], "revision": 1}])
    result = run(batch, proposals(episodes=[proposal]))
    job_id = uid()
    metadata = memory.publish(batch, result, job_id=job_id)
    assert memory.publish(batch, result, job_id=job_id) == metadata
    episodes = memory.rows("memory_episodes")
    assert len(episodes) == 1 and episodes[0]["state"] == "open"
    assert episodes[0]["document"]["member_refs"] == proposal["member_refs"]
    members = memory.rows("memory_episode_members")
    assert len(members) == 2 and {row["position"] for row in members} == {0, 1}
    assert all(row["role"] == "fragment" for row in members)


def test_episode_continue_close_and_reopen_use_exact_revisions(memory):
    first = memory.observe("开始准备考试。")
    initial = memory.batch(first)
    memory.publish(initial, run(initial, proposals(episodes=[episode(first)])))
    previous = memory.rows("memory_episodes")[0]["document"]
    second = memory.observe("复习计划已经定好了。")
    batch = memory.batch(second, episodes=(previous,))
    action = episode(second, action="close", episode_id=previous["episode_id"], expected_revision=1,
                     boundary_ref={"event_id": second["event_id"], "revision": 1})
    memory.publish(batch, run(batch, proposals(episodes=[action])))
    current = next(row["document"] for row in memory.rows("memory_episodes") if row["is_current"])
    assert current["revision"] == 2 and current["state"] == "closed"
    assert len(current["member_refs"]) == 2
    with pytest.raises(DomainError, match="snapshot"):
        memory.publish(batch, run(batch, proposals(episodes=[action])))
    third = memory.observe("再补充一下昨天的复习计划。")
    batch = memory.batch(third, episodes=(current,))
    action = episode(third, action="reopen", episode_id=current["episode_id"], expected_revision=2)
    memory.publish(batch, run(batch, proposals(episodes=[action])))
    current = next(row["document"] for row in memory.rows("memory_episodes") if row["is_current"])
    assert current["revision"] == 3 and current["state"] == "open" and len(current["member_refs"]) == 3


def test_episode_split_closes_prefix_and_opens_suffix(memory):
    first, second = memory.observe("正在讨论考试。"), memory.observe("现在转而讨论工作。")
    initial = memory.batch(first, second)
    member_refs = [{"event_id": source["event_id"], "revision": 1} for source in (first, second)]
    memory.publish(initial, run(initial, proposals(episodes=[episode(first, member_refs=member_refs)])))
    previous = memory.rows("memory_episodes")[0]["document"]
    batch = memory.batch(first, second, episodes=(previous,))
    action = episode(first, action="split", episode_id=previous["episode_id"], expected_revision=1,
                     member_refs=member_refs, boundary_ref=member_refs[1])
    metadata = memory.publish(batch, run(batch, proposals(episodes=[action])))
    assert len(metadata["episode_versions"]) == 2
    current = [row["document"] for row in memory.rows("memory_episodes") if row["is_current"]]
    assert {row["state"] for row in current} == {"open", "closed"}
    assert all(len(row["member_refs"]) == 1 for row in current)


def test_episode_conflict_rolls_back_claims_published_in_same_transaction(memory):
    source = memory.observe("开始准备考试。")
    initial = memory.batch(source)
    memory.publish(initial, run(initial, proposals(episodes=[episode(source)])))
    previous = memory.rows("memory_episodes")[0]["document"]
    batch = memory.batch(source, episodes=(previous,))
    close = episode(source, action="close", episode_id=previous["episode_id"], expected_revision=1,
                    boundary_ref={"event_id": source["event_id"], "revision": 1})
    ready = run(batch, proposals(claim(source), episodes=[close]))
    memory.publish(batch, run(batch, proposals(episodes=[close])))
    prior_count = len(memory.rows("memory_assertions"))
    with pytest.raises(DomainError, match="snapshot"):
        memory.publish(batch, ready)
    assert len(memory.rows("memory_assertions")) == prior_count


def test_changed_unselected_episode_snapshot_blocks_inference_publication(memory):
    source = memory.observe("开始准备考试。")
    initial = memory.batch(source)
    memory.publish(initial, run(initial, proposals(episodes=[episode(source)])))
    previous = memory.rows("memory_episodes")[0]["document"]
    batch = memory.batch(source, episodes=(previous,))
    ready = run(batch, proposals(claim(source)))
    close = episode(source, action="close", episode_id=previous["episode_id"], expected_revision=1,
                    boundary_ref={"event_id": source["event_id"], "revision": 1})
    memory.publish(batch, run(batch, proposals(episodes=[close])))
    with pytest.raises(DomainError, match="snapshot"):
        memory.publish(batch, ready)
    assert all(row["id"].startswith("memory-") for row in memory.rows("memory_assertions"))


def test_historical_retrieval_rejects_current_context_root_but_keeps_original_literal_evidence(memory):
    source = memory.observe("工作时我希望直接获得建议。")
    cue = memory.observe("还记得工作时我希望获得直接建议吗？")
    batch = memory.batch(source, cue)
    proposal = claim(source, conditions={"op": "always"})
    published = memory.publish(batch, run(batch, proposals(proposal)))
    derived_id = published["memory_versions"][0]["memory_id"]
    with memory.database.transaction(memory.principal) as conn:
        brain = memory.service._brain(conn, memory.principal, memory.brain_id)
        fragments = memory.service._current_captures(conn, memory.principal, brain, source)
        for fragment in fragments:
            memory.service._publish(conn, memory.principal, brain, fragment)
    contextual = memory.action("recall", request("工作"))
    assert derived_id in {item["memory"]["memory_id"] for item in contextual["items"]}
    historical = memory.action("recall", request(
        "工作", evidence_mode="prior_record_check", cue_event_ids=[cue["event_id"]],
        evidence_cutoff={"mode": "through_sequence", "through_sequence": source["sequence"]},
    ))
    found = {item["memory"]["memory_id"] for item in historical["items"]}
    assert derived_id not in found
    assert found == {fragment["memory_id"] for fragment in fragments}
