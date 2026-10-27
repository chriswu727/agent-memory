import copy
import json

import httpx
import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_semantic import encoded, episode, proposals, run, verifier

from membrain.client import MemoryActionError, default_budget
from membrain.consolidation import publish_semantic
from membrain.helpers import DomainError, digest, uid
from membrain.jobs import JobStore
from membrain.learning import LearningEngine, validate_snapshot
from membrain.semantic import CognitiveBatch, SemanticProcessor

pytestmark = pytest.mark.integration


def organize(harness, receipt, previous=None):
    with harness.database.transaction(harness.principal) as conn:
        service = harness.service
        brain = service._brain(conn, harness.principal, harness.brain)
        source = service._events(conn, harness.principal, brain, event_ids=receipt["accepted_event_ids"])[0]
        episodes = (service._rebind(previous, harness.principal, brain),) if previous else ()
        batch = CognitiveBatch(uid(), source["scope"], (source,), episodes=episodes)
        action = episode(source)
        if previous:
            action.update(action="close", episode_id=previous["episode_id"], expected_revision=previous["revision"],
                          boundary_ref={"event_id": source["event_id"], "revision": source["revision"]})
        result = run(batch, proposals(episodes=[action]))
        assert result.status == "completed"
        metadata = publish_semantic(conn, service, harness.principal, brain, batch, result, job_id=uid())
        version = metadata["episode_versions"][0]
        return conn.execute(
            "SELECT document FROM memory_episodes WHERE tenant_id=%s AND brain_id=%s AND id=%s AND revision=%s",
            (harness.principal.tenant_id, harness.brain, version["episode_id"], version["revision"]),
        ).fetchone()["document"]


def test_production_neighborhood_supplies_complete_episode_sources_for_continuation(learning):
    old_receipt = learning.observe("开始讨论复习计划。")
    previous = organize(learning, old_receipt)
    receipt = learning.observe("接着讨论数学复习安排。")
    expected_ids = set(old_receipt["accepted_event_ids"] + receipt["accepted_event_ids"])
    calls = []
    originals = {}

    def propose(request):
        calls.append(request.phase)
        data = json.loads(request.messages[-1]["content"])
        assert expected_ids <= {source["event_id"] for source in data["sources"]}
        sources = {(source["event_id"], source["revision"]): source for source in data["sources"]}
        originals.update(sources)
        assert data["episodes"] == [{
            "view_version": "episode-source-view-v1",
            "use": "fallible_interaction_group_not_factual_evidence",
            "grouping_status": "inferred", "external_completion": "not_established",
            **{key: previous[key] for key in (
                "episode_id", "revision", "scope", "state", "member_refs", "boundary_ref",
            )},
            "member_source_refs": [
                {**ref, "content_sha256": sources[(ref["event_id"], ref["revision"])]["content_sha256"]}
                for ref in previous["member_refs"]
            ],
        }]
        current = next(source for source in data["sources"] if source["event_id"] == receipt["accepted_event_ids"][0])
        action = episode(current, action="continue", episode_id=previous["episode_id"], expected_revision=1,
                         organization_statement="后续数学复习讨论延续了已有的复习计划话题。")
        return encoded(proposals(episodes=[action]))

    def verify(request):
        calls.append(request.phase)
        body = json.loads(request.messages[-1]["content"])
        candidate = body["proposals"][0]
        sources = {(source["event_id"], source["revision"]): source for source in body["verification_sources"]}
        assert {key[0] for key in sources} == expected_ids
        assert all(source == originals[key] for key, source in sources.items())
        for item in candidate["evidence"]:
            ref = item["reference"]
            source = sources[(item["source_ref"]["event_id"], item["source_ref"]["revision"])]
            assert (source["event_id"], source["revision"]) == (ref["event_id"], ref["event_revision"])
            assert source["content_sha256"] == ref["source_content_sha256"]
            assert item["excerpt"] == source["observed"]["content"]["text"][ref["span"]["start"]:ref["span"]["end"]]
        assert candidate["episode_context"]["prior_member_refs"] == previous["member_refs"]
        return verifier(request)

    learning.service.semantic_processor = SemanticProcessor(propose, verify)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        job_id = learning.service._job(conn, learning.principal, brain, "consolidate", {
            "event_id": receipt["accepted_event_ids"][0], "revision": 1,
            "target": {"conversation_ref": "chat", "branch_ref": "main"},
            "through_sequence": receipt["durable_through_sequence"],
        })["job_id"]
    job = JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id)
    LearningEngine(learning.service).semantic(learning.principal, learning.brain, job)
    completed = learning.job(job_id)
    assert completed["state"] == "completed"
    assert completed["result"]["status"] == "completed", completed["result"]
    assert completed["result"]["publication"]["episode_versions"] == [{"episode_id": previous["episode_id"], "revision": 2}]
    assert expected_ids <= {ref["event_id"] for ref in completed["result"]["source_snapshot"]}
    assert calls == ["proposal", "verification"]


def test_reorganization_during_model_call_prevents_orientation_publication(learning):
    receipt = learning.observe("开始准备考试。")
    previous = organize(learning, receipt)
    journal, row, token = learning.capture()
    learning.during_proposal = lambda _: organize(learning, receipt, previous)
    with pytest.raises(httpx.HTTPStatusError) as error:
        journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    assert error.value.response.status_code == 409
    current = journal.get(row["turn_id"])
    job = learning.job(current["orientation_job_id"])
    assert job["result"]["episode_snapshot"] == [{"episode_id": previous["episode_id"], "revision": 1,
                                                "content_sha256": digest(previous)}]
    assert "situation_updates" not in job["result"]
    assert job["state"] != "completed"
    assert learning.calls == ["proposal", "verification"]
    assert learning.working() is None


@pytest.mark.parametrize("change", ["new_revision", "changed_content"])
def test_completed_orientation_cannot_replay_after_episode_changes(learning, change):
    receipt = learning.observe("开始准备考试。")
    previous = organize(learning, receipt)
    journal, row, token = learning.capture()
    oriented = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    if change == "new_revision":
        organize(learning, receipt, previous)
    else:
        changed = copy.deepcopy(previous)
        changed["state"] = "closed"
        with learning.database.transaction(learning.principal) as conn:
            conn.execute("UPDATE memory_episodes SET document=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(changed), learning.principal.tenant_id, learning.brain, previous["episode_id"]))
    with pytest.raises((MemoryActionError, httpx.HTTPStatusError)) as error:
        learning.memory.prepare(row["turn_id"], conversation_id="chat", branch_id="main",
                                latest_input_event_ids=[row["input_event_refs"][0]["event_id"]],
                                budget=default_budget(8000))
    if isinstance(error.value, httpx.HTTPStatusError):
        assert error.value.response.json()["error"]["code"] == "context_stale", error.value.response.json()
    else:
        assert error.value.code == "context_stale"
    assert learning.job(oriented["orientation_job_id"])["model_calls"] == 2
    assert learning.calls == ["proposal", "verification"]


def test_unrelated_scope_generation_change_rebinds_episodes_without_changing_snapshot_hash(learning):
    receipt = learning.observe("开始准备考试。")
    previous = organize(learning, receipt)
    unrelated = learning.observe("另一个会话中的内容。", conversation="unrelated")
    learning.memory.forget(unrelated["accepted_event_ids"], kind="events", mode="erase_content",
                           expected_scope_generation=1)
    journal, row, token = learning.capture()
    oriented = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    job = learning.job(oriented["orientation_job_id"])
    assert job["state"] == "completed"
    assert job["result"]["status"] == "completed"
    assert "episodes" not in learning.snapshots[0]
    assert job["result"]["scope"]["scope_generation"] == 2
    assert all(item["scope"]["scope_generation"] == 2 for item in learning.snapshots[0]["existing_assertions"])
    assert job["result"]["episode_snapshot"][0]["content_sha256"] == digest(previous)
    assert job["result"]["memory_snapshot"]
    with learning.database.transaction(learning.principal) as conn:
        ref = job["result"]["memory_snapshot"][0]
        raw = conn.execute("SELECT document FROM memory_assertions WHERE tenant_id=%s AND brain_id=%s "
                           "AND id=%s AND revision=%s", (learning.principal.tenant_id, learning.brain,
                                                         ref["memory_id"], ref["revision"])).fetchone()["document"]
        assert ref["content_sha256"] == digest(raw)
        assert raw["scope"]["scope_generation"] == 1
    assert organize(learning, receipt, previous)["scope"]["scope_generation"] == 2


@pytest.mark.parametrize("change", ["content", "retracted", "superseded"])
def test_parent_snapshot_checks_content_and_lifecycle_without_rejecting_valid_exact_historical_versions(learning, change):
    organize(learning, learning.observe("开始准备考试。"))
    journal, row, token = learning.capture()
    oriented = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    snapshot = copy.deepcopy(learning.job(oriented["orientation_job_id"])["result"])
    ref = snapshot["memory_snapshot"][0]
    with learning.database.transaction(learning.principal) as conn:
        params = (learning.principal.tenant_id, learning.brain, ref["memory_id"], ref["revision"])
        document = conn.execute("SELECT document FROM memory_assertions WHERE tenant_id=%s AND brain_id=%s "
                                "AND id=%s AND revision=%s", params).fetchone()["document"]
        assert ref["content_sha256"] == digest(document)
        if change == "content":
            document["statement"] = "Changed content at the same parent revision."
        else:
            document["lifecycle"] = change
            ref["content_sha256"] = digest(document)
        conn.execute("UPDATE memory_assertions SET document=%s,is_current=%s WHERE tenant_id=%s AND brain_id=%s "
                     "AND id=%s AND revision=%s", (Jsonb(document), change != "superseded", *params))
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        if change == "superseded":
            assert validate_snapshot(conn, learning.service, learning.principal, brain, snapshot)
        else:
            with pytest.raises(DomainError) as error:
                validate_snapshot(conn, learning.service, learning.principal, brain, snapshot)
            assert error.value.code == "source_unavailable"
