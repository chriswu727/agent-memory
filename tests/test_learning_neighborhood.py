import copy
from dataclasses import replace

import httpx
import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_semantic import claim, proposals, reference, run

from agent_memory import cognition
from agent_memory.helpers import DomainError, digest, uid
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.neighborhood import select_neighborhood
from agent_memory.semantic import CognitiveBatch, SemanticPolicy

pytestmark = pytest.mark.integration


def source(learning, text, **scope):
    receipt = learning.observe(text, **scope)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        return learning.service._events(conn, learning.principal, brain,
                                        event_ids=receipt["accepted_event_ids"])[0]


def publish(learning, event, *, parents=(), counterexamples=(), **changes):
    memory = cognition.extract_event(event)[0]
    memory["memory_id"] = uid()
    versions = [{"memory_id": parent["memory_id"], "revision": parent["revision"]} for parent in parents]
    memory["derivation"]["source_memory_versions"] = copy.deepcopy(versions)
    memory["disclosure_basis"]["support_paths"][0]["source_memory_versions"] = copy.deepcopy(versions)
    for parent in parents:
        memory["evidence_refs"].extend(copy.deepcopy(parent["evidence_refs"]))
    if counterexamples:
        memory["counterexamples"] = [reference(event, "contradicts") for event in counterexamples]
        memory["evidence_refs"].extend(copy.deepcopy(memory["counterexamples"]))
    memory.update(changes)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        return learning.service._publish(conn, learning.principal, brain, memory)


def select(learning, current, *, policy=None, through=None, conversation="chat", branch="main"):
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        return select_neighborhood(
            conn, learning.service, learning.principal, brain,
            {"conversation_ref": conversation, "branch_ref": branch},
            through_sequence=current["sequence"] if through is None else through,
            required_event_refs=[{"event_id": current["event_id"], "revision": current["revision"]}],
            policy=policy or SemanticPolicy(),
        )


def link_exception(learning, first, second, *, event_ids=()):
    with learning.database.transaction(learning.principal) as conn:
        conn.execute(
            "INSERT INTO memory_associations(tenant_id,owner_id,brain_id,id,source_id,source_revision,"
            "target_id,target_revision,relation,event_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'exception',%s)",
            (learning.principal.tenant_id, learning.principal.principal_id, learning.brain, uid(),
             first["memory_id"], first["revision"], second["memory_id"], second["revision"], list(event_ids)),
        )


def versions(result):
    return {(memory["memory_id"], memory["revision"]) for memory in result.assertions}


def test_related_preference_and_counterexample_survive_long_distance_without_unrelated_history(learning):
    first = source(learning, "During study I prefer conversation before planning.", conversation="previous")
    exception = source(learning, "A deadline requires silence and immediate action.", conversation="previous")
    memory = publish(learning, first, counterexamples=[exception], representation="semantic_anchor")
    unrelated = publish(learning, source(learning, "An amber otter sleeps beside a lighthouse.", conversation="unrelated"))
    for _ in range(40):
        source(learning, "Unrelated weather observations.")
    current = source(learning, "What study conversation preference applies?")
    result = select(learning, current)
    assert (memory["memory_id"], memory["revision"]) in versions(result)
    assert (unrelated["memory_id"], unrelated["revision"]) not in versions(result)
    ids = {event["event_id"] for event in result.sources}
    assert {first["event_id"], exception["event_id"], current["event_id"]} <= ids
    assert next(event for event in result.sources if event["event_id"] == first["event_id"])["scope"]["conversation_id"] == "previous"
    assert result.sampling["search_exhaustive"] is False
    assert not result.sampling["publication_restricted"]
    assert "recent_window_limit" in result.sampling["limitations"]
    assert len(result.sources) <= 32 and len(result.assertions) <= 32 and len(result.episodes) <= 16


def test_exception_neighbor_is_loaded_even_without_lexical_overlap(learning):
    positive = publish(learning, source(learning, "Study conversation comes before planning.", conversation="old"))
    counter = publish(learning, source(learning, "Deadline mode requires silence.", conversation="old"))
    link_exception(learning, positive, counter)
    result = select(learning, source(learning, "Study conversation?"))
    assert {(positive["memory_id"], 1), (counter["memory_id"], 1)} <= versions(result)
    assert not result.sampling["publication_restricted"]


def test_precise_superseded_dependency_is_loaded_without_substituting_newer_content(learning):
    parent = publish(learning, source(learning, "Previous recorded preference.", conversation="old"))
    child = publish(learning, source(learning, "Study conversation pattern.", conversation="old"), parents=[parent])
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_assertions SET is_current=false WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, parent["memory_id"]))
        newer = copy.deepcopy(parent)
        newer.update(revision=2, statement="Different later preference.")
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        learning.service._publish(conn, learning.principal, brain, newer)
    result = select(learning, source(learning, "Study conversation?"))
    assert {(parent["memory_id"], 1), (child["memory_id"], 1)} <= versions(result)
    assert (parent["memory_id"], 2) not in versions(result)


def test_missing_dependency_omits_whole_related_package_and_restricts_learning(learning):
    parent = publish(learning, source(learning, "Older recorded habit.", conversation="old"))
    child = publish(learning, source(learning, "Study conversation pattern.", conversation="old"), parents=[parent])
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_assertions SET invalidated=true WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, parent["memory_id"]))
    result = select(learning, source(learning, "Study conversation?"))
    assert (child["memory_id"], 1) not in versions(result)
    assert result.sampling["publication_restricted"]
    assert "dependency_incomplete" in result.sampling["limitations"]


def test_source_budget_never_keeps_positive_half_of_known_exception_package(learning):
    positive = publish(learning, source(learning, "Study conversation preference.", conversation="old"))
    counter = publish(learning, source(learning, "Deadline requires silence.", conversation="old"))
    link_exception(learning, positive, counter)
    for _ in range(4):
        source(learning, "Weather log.")
    current = source(learning, "Study conversation?")
    result = select(learning, current, policy=replace(SemanticPolicy(), max_sources=2))
    assert not versions(result)
    assert result.sampling["publication_restricted"]
    assert "source_budget" in result.sampling["limitations"]
    assert len(result.sources) == 2
    batch = CognitiveBatch(uid(), current["scope"], result.sources, sampling=result.sampling)
    proposed = claim(current, statement="A new unconditional preference.")
    outcome = run(batch, proposals(proposed))
    assert not outcome.claims and not outcome.episode_actions
    assert outcome.rejected == ({"proposal_id": proposed["proposal_id"], "reason": "incomplete_history_package"},)


def test_related_support_and_exception_take_priority_over_incidental_recent_context(learning):
    positive = publish(learning, source(learning, "Study conversation preference.", conversation="old"))
    counter = publish(learning, source(learning, "Deadline requires silence.", conversation="old"))
    link_exception(learning, positive, counter)
    for _ in range(5):
        source(learning, "Weather log.")
    current = source(learning, "Study conversation?")
    result = select(learning, current, policy=replace(SemanticPolicy(), max_sources=3))
    assert versions(result) == {(positive["memory_id"], 1), (counter["memory_id"], 1)}
    assert len(result.sources) == 3
    assert not result.sampling["publication_restricted"]
    assert "source_budget" in result.sampling["limitations"]


def test_related_episode_with_incomplete_members_is_omitted_and_restricts_reorganization(learning):
    first = source(learning, "Study conversation began.")
    current = source(learning, "Study conversation continues.")
    document = {"episode_id": uid(), "revision": 1, "scope": current["scope"], "state": "open",
                "member_refs": [{"event_id": event["event_id"], "revision": event["revision"]}
                                for event in (first, current)], "boundary_ref": None,
                "organization_status": "inferred", "job_id": uid(), "proposal_id": uid()}
    with learning.database.transaction(learning.principal) as conn:
        conn.execute(
            "INSERT INTO memory_episodes(tenant_id,owner_id,brain_id,id,revision,conversation_id,branch_id,state,document,event_ids) "
            "VALUES(%s,%s,%s,%s,1,'chat','main','open',%s,%s)",
            (learning.principal.tenant_id, learning.principal.principal_id, learning.brain, document["episode_id"],
             Jsonb(document), [first["event_id"], current["event_id"]]),
        )
    result = select(learning, current, policy=replace(SemanticPolicy(), max_sources=1))
    assert not result.episodes
    assert result.sampling["publication_restricted"]
    assert "source_budget" in result.sampling["limitations"]


def test_candidate_and_dependency_sources_respect_the_historical_sequence_cutoff(learning):
    old = publish(learning, source(learning, "Study conversation preference.", conversation="old"))
    current = source(learning, "Study conversation?")
    future = publish(learning, source(learning, "Study conversation changed after the query.", conversation="old"))
    result = select(learning, current)
    assert (old["memory_id"], 1) in versions(result)
    assert (future["memory_id"], 1) not in versions(result)
    assert all(event["sequence"] <= current["sequence"] for event in result.sources)
    with pytest.raises(DomainError, match="Required cognitive input"):
        select(learning, current, through=current["sequence"] - 1)


def test_exception_relation_cannot_pull_a_future_source_into_an_old_snapshot(learning):
    positive = publish(learning, source(learning, "Study conversation preference.", conversation="old"))
    current = source(learning, "Study conversation?")
    later = source(learning, "Deadline requires silence.", conversation="old")
    counter = publish(learning, later)
    link_exception(learning, positive, counter, event_ids=[later["event_id"]])
    result = select(learning, current)
    assert not versions(result)
    assert later["event_id"] not in {event["event_id"] for event in result.sources}
    assert result.sampling["publication_restricted"]


@pytest.mark.parametrize("restricted", ["branch", "conversation"])
def test_authorization_filters_candidates_before_the_model_sees_them(learning, restricted):
    kwargs = {"branch": "fork"} if restricted == "branch" else {"conversation": "private-other"}
    event = source(learning, "Study conversation secret.", **kwargs)
    memory = publish(learning, event)
    if restricted == "conversation":
        with learning.database.transaction(learning.principal) as conn:
            memory["scope"]["visibility"] = "conversation"
            for path in memory["disclosure_basis"]["support_paths"]:
                path["permitted_scope"]["visibility"] = "conversation"
            conn.execute("UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(memory), learning.principal.tenant_id, learning.brain, memory["memory_id"]))
    result = select(learning, source(learning, "Study conversation?"))
    assert (memory["memory_id"], 1) not in versions(result)
    assert event["event_id"] not in {event["event_id"] for event in result.sources}


def test_unavailable_exception_scope_prevents_unqualified_learning_of_its_parent(learning):
    positive = publish(learning, source(learning, "Study conversation preference.", conversation="old"))
    counter = publish(learning, source(learning, "Deadline requires silence.", branch="fork"))
    link_exception(learning, positive, counter)
    result = select(learning, source(learning, "Study conversation?"))
    assert not versions(result)
    assert result.sampling["publication_restricted"]
    assert "exception_unavailable" in result.sampling["limitations"]


def test_missing_target_of_an_active_exception_edge_cannot_be_silently_ignored(learning):
    positive = publish(learning, source(learning, "Study conversation preference.", conversation="old"))
    counter = publish(learning, source(learning, "Deadline requires silence.", conversation="old"))
    link_exception(learning, positive, counter)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_assertions SET suppressed=true WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                     (learning.principal.tenant_id, learning.brain, counter["memory_id"]))
    result = select(learning, source(learning, "Study conversation?"))
    assert not versions(result)
    assert result.sampling["publication_restricted"]
    assert "exception_unavailable" in result.sampling["limitations"]


@pytest.mark.parametrize("change", ["revision", "hash", "erasure"])
def test_changed_old_source_cannot_enter_a_neighborhood(learning, change):
    event = source(learning, "Study conversation preference.", conversation="old")
    memory = publish(learning, event)
    if change == "revision":
        source(learning, "A different source revision.", conversation="old",
               source_key=event["observed"]["source_event_key"], revision=2)
    elif change == "erasure":
        learning.memory.forget([event["event_id"]], kind="events", mode="erase_content", expected_scope_generation=1)
    else:
        changed = copy.deepcopy(event)
        changed["observed"]["content"]["text"] = "Changed without matching its persisted hash."
        with learning.database.transaction(learning.principal) as conn:
            conn.execute("UPDATE memory_events SET document=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(changed), learning.principal.tenant_id, learning.brain, event["event_id"]))
    result = select(learning, source(learning, "Study conversation?"))
    assert (memory["memory_id"], 1) not in versions(result)
    assert event["event_id"] not in {source["event_id"] for source in result.sources}


def test_orientation_registers_old_neighborhood_sources_and_rejects_mid_call_changes(learning):
    old = source(learning, "Study conversation preference.", conversation="previous")
    publish(learning, old)
    journal, row, token = learning.capture("Study conversation?")

    def revise(data):
        assert old["event_id"] in {event["event_id"] for event in data["sources"]}
        assert data["sampling"]["search_exhaustive"] is False
        source(learning, "Changed previous preference.", conversation="previous",
               source_key=old["observed"]["source_event_key"], revision=2)

    learning.during_proposal = revise
    with pytest.raises(httpx.HTTPStatusError) as error:
        journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    assert error.value.response.status_code == 409
    job = learning.job(journal.get(row["turn_id"])["orientation_job_id"])
    assert job["state"] == "superseded" and job["result"] is None
    assert old["event_id"] in job["source_event_ids"]


def test_completed_orientation_reuses_exact_neighborhood_snapshot_without_rebilling(learning):
    old = source(learning, "Study conversation preference.", conversation="previous")
    memory = publish(learning, old)
    journal, row, token = learning.capture("Study conversation is my current focus.")
    first = journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    second = journal.orient(row["turn_id"], lease_token=token, expected_revision=first["revision"])
    assert first["context_event_refs"] == second["context_event_refs"]
    assert {"event_id": old["event_id"], "revision": old["revision"]} in second["context_event_refs"]
    job = learning.job(second["orientation_job_id"])
    assert job["model_calls"] == 2 and learning.calls == ["proposal", "verification"]
    assert {"memory_id": memory["memory_id"], "revision": memory["revision"], "content_sha256": digest(memory)} in job["result"]["memory_snapshot"]


def test_snapshot_retry_keeps_sampling_stable_when_only_index_progress_advances(learning):
    current = source(learning, "Study conversation is my current focus.")
    job = JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(), kinds=["consolidate"])
    engine = LearningEngine(learning.service)
    first, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert "index_lag" in first.sampling["limitations"]
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_brains SET indexed_sequence=%s WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                     (current["sequence"], learning.principal.tenant_id, learning.principal.principal_id, learning.brain))
    second, repeated = engine._snapshot(learning.principal, learning.brain, job)
    assert second.sampling == first.sampling == snapshot["sampling"] == repeated["sampling"]
    assert learning.calls == []
