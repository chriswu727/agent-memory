import copy
import json

import pytest
from test_abstraction_support import select_support
from test_belief_semantic import link_verifier
from test_learning import learning as learning
from test_learning_windows import drain, jobs
from test_semantic import claim, encoded, proposals, reference

from membrain.client import default_budget, prepared_context, recalled_context
from membrain.helpers import digest
from membrain.semantic import SemanticProcessor

pytestmark = pytest.mark.integration


def test_selected_abstraction_survives_publication_and_preserves_retained_context_dependencies(learning):
    proposed_batches = []

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        if not proposed_batches:
            sources = sorted(data["sources"], key=lambda source: source["sequence"])
            assert len(sources) == 4
            instances = []
            for index, (action, clothing) in enumerate((sources[:2], sources[2:])):
                instances.append(claim(
                    action, proposal_id=f"instance-{index}", memory_kind="episodic", representation="episode_outline",
                    claim_kind="reported_fact", conditions={"op": "always"},
                    statement=action["observed"]["content"]["text"] + " " + clothing["observed"]["content"]["text"],
                    evidence_refs=[reference(action), reference(clothing)],
                ))
            proposed = proposals(*instances)
        else:
            assert len(data["consolidation_plan"]["objectives"]) == 1
            objective = data["consolidation_plan"]["objectives"][0]
            lookup = {(item["memory_id"], item["revision"]): item for item in data["existing_assertions"]}
            parents = [lookup[(version["memory_id"], version["revision"])]
                       for version in objective["instance_memory_versions"]]
            refs = [copy.deepcopy(ref) for parent in parents for ref in parent["evidence_refs"]]
            shared = claim(data["sources"][0], proposal_id="shared-walking", memory_kind="semantic",
                           representation="schema", claim_kind="inferred_pattern", conditions={"op": "always"},
                           statement="In both recorded instances, walking preceded studying.", evidence_refs=refs,
                           source_memory_versions=copy.deepcopy(objective["instance_memory_versions"]))
            link = {"proposal_id": "abstract-walking", "kind": "abstracts", "claim_proposal_id": shared["proposal_id"],
                    "target_memory_versions": copy.deepcopy(objective["instance_memory_versions"]),
                    "evidence_refs": copy.deepcopy(refs), "world": "real_world", "conditions": {"op": "always"},
                    "valid_during": {"kind": "unknown"}}
            proposed = {**proposals(shared), "belief_links": [link], "consolidation_decisions": [{
                "objective_id": objective["objective_id"], "outcome": "proposed", "reason": None,
                "link_proposal_ids": [link["proposal_id"]],
            }]}
        proposed_batches.append(copy.deepcopy(proposed))
        return encoded(proposed)

    def verify(request):
        entries = json.loads(request.messages[-1]["content"])["proposals"]
        verified = json.loads(link_verifier(request))
        assessments = {item["proposal_id"]: item for item in verified["assessments"]}
        for entry in entries:
            if entry.get("evidence_selection") == "minimal_shared_support":
                residual = {item["evidence_index"] for item in entry["evidence"] if "coat" in item["excerpt"]}
                select_support(assessments[entry["proposal"]["proposal_id"]], residual)
        return encoded(verified)

    learning.service.semantic_processor = SemanticProcessor(propose, verify)
    observations = [
        learning.observe(text, source_key=key) for key, text in (
            ("first-action", "I walked before studying algebra."),
            ("first-clothing", "I wore a blue coat."),
            ("second-action", "I walked before studying biology."),
            ("second-clothing", "I wore no coat."),
        )
    ]
    drain(learning)
    assert jobs(learning)[0]["result"]["publication"]["published_count"] == 2
    reminder = learning.observe("Recall walking before studying and what I wore.")
    drain(learning)
    completed = jobs(learning)[1]
    assert completed["state"] == "completed" and completed["model_calls"] == 2
    assert completed["result"]["status"] == "completed"
    assert completed["result"]["consolidation"]["satisfied_count"] == 1
    publication = completed["result"]["publication"]
    assert publication["published_count"] == 1
    memory_id = publication["memory_versions"][0]["memory_id"]
    relation_id = publication["belief_relation_versions"][0]["relation_id"]

    with learning.database.transaction(learning.principal) as conn:
        params = learning.service._params(learning.principal, learning.brain)
        stored = conn.execute(
            "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, memory_id),
        ).fetchone()["document"]
        relation = conn.execute(
            "SELECT document FROM memory_belief_relations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, relation_id),
        ).fetchone()["document"]
    original = proposed_batches[1]["claims"][0]
    action_ids = {observations[index]["accepted_event_ids"][0] for index in (0, 2)}
    all_ids = {receipt["accepted_event_ids"][0] for receipt in observations}
    assert stored["statement"] == original["statement"]
    assert stored["derivation"]["source_memory_versions"] == original["source_memory_versions"]
    assert {ref["event_id"] for ref in stored["evidence_refs"]} == action_ids
    assert sorted(relation["evidence_refs"], key=digest) == sorted(
        proposed_batches[1]["belief_links"][0]["evidence_refs"], key=digest)
    assert {ref["event_id"] for ref in relation["evidence_refs"]} == all_ids

    result = completed["result"]
    assert len(result["verification_assessments"]) == 2
    assert all(sum(item["verdict"] == "irrelevant" for item in assessment["relations"]) == 2
               for assessment in result["verification_assessments"])
    selection = result["evidence_selections"][0]
    assert selection["proposal_id"] == original["proposal_id"]
    assert selection["original_evidence_refs"] == original["evidence_refs"]
    assert [original["evidence_refs"][index] for index in selection["supporting_evidence_indices"]] == stored["evidence_refs"]
    monitor = learning.memory._request("GET", f"v1/brains/{learning.brain}/runtime/jobs/{completed['id']}")
    serialized = json.dumps(monitor)
    assert all(value not in serialized for value in (
        "verification_assessments", "evidence_selections", "original_evidence_refs", original["statement"],
        *all_ids, *[version["memory_id"] for version in original["source_memory_versions"]],
    ))

    recalled = learning.memory.recall("walking before studying", conversation_id="chat", branch_id="main",
                                      budget=default_budget(60000))
    prepared = learning.memory.prepare("inspect-selected-abstraction", conversation_id="chat", branch_id="main",
                                       latest_input_event_ids=reminder["accepted_event_ids"], budget=default_budget(60000))
    for context in (json.loads(recalled_context(recalled)), json.loads(prepared_context(prepared))):
        assert memory_id in {version["memory_id"] for version in context["memory_versions"].values()}
        assert all_ids <= {source["event_id"] for source in context["sources"].values()}
        assert len(context["relations"]) == 1
        for certificate in context["relations"].values():
            assert all(ref in context["sources"] for ref in certificate["evidence_refs"])

    retained_context = observations[1]["accepted_event_ids"][0]
    assert retained_context not in action_ids
    learning.observe("I wore a red coat instead.", source_key="first-clothing", revision=2)
    with learning.database.transaction(learning.principal) as conn:
        params = learning.service._params(learning.principal, learning.brain)
        assertion = conn.execute(
            "SELECT invalidated FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, memory_id),
        ).fetchone()
        relation = conn.execute(
            "SELECT invalidated,document FROM memory_belief_relations "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s", (*params, relation_id),
        ).fetchone()
    assert assertion["invalidated"] and relation["invalidated"] and relation["document"] is None
    current = learning.memory.recall("walking before studying", conversation_id="chat", branch_id="main",
                                     budget=default_budget(60000))["result"]
    assert memory_id not in {item["memory"]["memory_id"] for item in current["items"]}
