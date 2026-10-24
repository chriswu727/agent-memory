import json
from dataclasses import replace

import pytest
from psycopg.types.json import Jsonb
from test_belief_store import linked, version
from test_belief_store import store as store
from test_consolidation import memory as memory
from test_semantic import claim, proposals, run

from agent_memory.helpers import DomainError, uid
from agent_memory.neighborhood import select_neighborhood
from agent_memory.semantic import CognitiveBatch, SemanticPolicy, SemanticProcessor

pytestmark = pytest.mark.integration


def select(store, current, *, policy=None):
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        return select_neighborhood(
            conn, store.service, store.principal, brain,
            {"conversation_ref": "test", "branch_ref": "main"},
            through_sequence=current["sequence"],
            required_event_refs=[{"event_id": current["event_id"], "revision": current["revision"]}],
            policy=policy or SemanticPolicy(),
        )


def test_learning_model_receives_complete_change_relationship_and_all_observed_roots(store):
    sources, old, new, _, _, _, _ = linked(store, hidden=True)
    current = store.observe("平时直接获得建议的偏好有没有例外？")
    neighborhood = select(store, current)
    assert len(neighborhood.belief_relations) == 1
    assert {tuple(version(item).values()) for item in neighborhood.assertions} >= {
        tuple(version(old).values()), tuple(version(new).values())}
    assert {item["event_id"] for item in neighborhood.sources} >= {item["event_id"] for item in sources}
    assert not neighborhood.sampling["publication_restricted"]
    observed = []

    def propose(request):
        observed.append(json.loads(request.messages[-1]["content"]))
        return json.dumps(proposals())

    batch = CognitiveBatch(uid(), current["scope"], neighborhood.sources, neighborhood.assertions,
                           episodes=neighborhood.episodes, sampling=neighborhood.sampling,
                           belief_relations=neighborhood.belief_relations)
    result = SemanticProcessor(propose).process(batch, reserve=lambda **kwargs: None)
    assert result.status == "completed"
    assert observed[0]["belief_relations"] == list(neighborhood.belief_relations)


@pytest.mark.parametrize("damage", ["hidden_source", "source_budget", "changed_endpoint"])
def test_incomplete_relationship_neighborhood_blocks_unqualified_long_term_relearning(store, damage):
    sources, old, new, _, _, _, _ = linked(store, hidden=True)
    current = store.observe("平时直接获得建议的偏好仍然适用吗？")
    policy = SemanticPolicy()
    if damage == "source_budget":
        policy = replace(policy, max_sources=2)
    else:
        with store.database.transaction(store.principal) as conn:
            params = store.service._params(store.principal, store.brain_id)
            if damage == "hidden_source":
                conn.execute("UPDATE memory_events SET retired=true WHERE tenant_id=%s AND owner_id=%s "
                             "AND brain_id=%s AND id=%s", (*params, sources[2]["event_id"]))
            else:
                conn.execute("UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND owner_id=%s "
                             "AND brain_id=%s AND id=%s",
                             (Jsonb({**new, "statement": "An unrelated changed assertion."}), *params, new["memory_id"]))
    neighborhood = select(store, current, policy=policy)
    assert neighborhood.sampling["publication_restricted"]
    assert not neighborhood.belief_relations
    assert version(old) not in [version(item) for item in neighborhood.assertions]
    result = run(CognitiveBatch(uid(), current["scope"], neighborhood.sources,
                               sampling=neighborhood.sampling), proposals(claim(current)))
    assert not result.claims
    assert result.rejected[0]["reason"] == "incomplete_history_package"


def test_byte_budget_omits_whole_change_package_without_hiding_the_limitation(store):
    _, old, _, _, _, _, _ = linked(store)
    current = store.observe("平时直接获得建议的偏好仍然适用吗？")
    neighborhood = select(store, current, policy=replace(SemanticPolicy(), max_request_bytes=24000))
    assert "request_bytes_budget" in neighborhood.sampling["limitations"]
    assert neighborhood.sampling["publication_restricted"]
    assert version(old) not in [version(item) for item in neighborhood.assertions]
    assert not neighborhood.belief_relations
    assert current["event_id"] in {item["event_id"] for item in neighborhood.sources}


def test_direct_publication_cannot_reuse_a_revoked_observed_relationship(store):
    linked(store)
    current = store.observe("平时直接获得建议的偏好仍然适用吗？")
    neighborhood = select(store, current)
    batch = CognitiveBatch(uid(), current["scope"], neighborhood.sources, neighborhood.assertions,
                           episodes=neighborhood.episodes, sampling=neighborhood.sampling,
                           belief_relations=neighborhood.belief_relations)
    outcome = run(batch, proposals())
    with store.database.transaction(store.principal) as conn:
        conn.execute("UPDATE memory_belief_relations SET invalidated=true,document=NULL "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                     store.service._params(store.principal, store.brain_id))
    with pytest.raises(DomainError, match="Cognitive source snapshot"):
        store.publish(batch, outcome)
