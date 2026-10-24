import copy
import json

import pytest
from test_cognition import UNKNOWN_TIME, event
from test_semantic import batch, claim, encoded, proposals, reference, run, verifier

from agent_memory import cognition
from agent_memory.ontology import CORE_REGISTRY

UNBOUNDED = {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"}}
SINCE = {"kind": "interval", "start": {"kind": "instant", "at": "2026-10-24T00:00:00Z"},
         "end": {"kind": "unbounded"}}
TEMPORARY = {"kind": "interval", "start": {"kind": "instant", "at": "2026-10-24T00:00:00Z"},
             "end": {"kind": "instant", "at": "2026-10-25T00:00:00Z"}}


def version(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def known(source, statement="I prefer concise replies."):
    result = cognition.extract_event(source)[0]
    result.update(statement=statement, memory_kind="interaction_preference", representation="semantic_anchor",
                  claim_kind="reported_preference", conditions={"op": "always"},
                  valid_during=copy.deepcopy(UNBOUNDED), procedure=None,
                  condition_registry_fingerprint=CORE_REGISTRY.fingerprint)
    return result


def link_verifier(request, verdict="supports"):
    data = json.loads(request.messages[-1]["content"])
    result = json.loads(verifier(request))
    for proposal, assessment in zip(data["proposals"], result["assessments"], strict=True):
        if proposal["group"] == "belief_links":
            assert proposal["linked_claim"]["proposal_id"] == proposal["proposal"]["claim_proposal_id"]
            assert proposal["target_assertions"]
            assessment["link_verdict"] = verdict
    return encoded(result)


def scenario(kind="supersedes"):
    old = event("I prefer concise replies.")
    new = event("From October 24, 2026 I prefer detailed explanations.", 2)
    target = known(old)
    proposed = claim(new, statement="I prefer detailed explanations.", conditions={"op": "always"},
                     valid_during=copy.deepcopy(SINCE), valid_time=copy.deepcopy(UNKNOWN_TIME))
    validity = SINCE
    if kind == "equivalent":
        new = event("Brevity suits me.", 2)
        proposed = claim(new, statement="Short replies suit me.", conditions={"op": "always"},
                         valid_during=copy.deepcopy(UNBOUNDED))
        validity = UNBOUNDED
    elif kind == "exception":
        validity = TEMPORARY
        proposed["valid_during"] = copy.deepcopy(TEMPORARY)
    elif kind == "contradicts":
        validity = UNBOUNDED
        proposed["valid_during"] = copy.deepcopy(UNBOUNDED)
    link = {"proposal_id": "link-1", "kind": kind, "claim_proposal_id": proposed["proposal_id"],
            "target_memory_versions": [version(target)], "evidence_refs": [reference(old), reference(new)],
            "conditions": {"op": "always"}, "valid_during": copy.deepcopy(validity), "world": "real_world"}
    response = {**proposals(proposed), "belief_links": [link]}
    return batch(old, new, existing_assertions=(target,)), response


@pytest.mark.parametrize("kind", ["supersedes", "equivalent", "exception", "contradicts"])
def test_verified_relation_is_a_separate_inference_and_does_not_mutate_old_memory(kind):
    data, response = scenario(kind)
    before = copy.deepcopy(data.existing_assertions)
    reserved = []
    result = run(data, response, verify=link_verifier, reserve=lambda **values: reserved.append(values))
    assert result.status == "completed"
    assert result.model_calls == len(reserved) == 2
    assert len(result.claims) == len(result.belief_links) == 1
    assert result.belief_links[0]["verification"] == {
        "state": "supported_inference", "method": "semantic_model", "not_a_truth_certificate": True,
    }
    assert result.belief_links[0]["condition_registry_fingerprint"] == CORE_REGISTRY.fingerprint
    assert data.existing_assertions == before
    assert before[0]["lifecycle"] == "active"


@pytest.mark.parametrize("verdict", [None, "unknown", "contradicts"])
def test_citations_alone_do_not_establish_a_relationship(verdict):
    data, response = scenario()
    verify = verifier if verdict is None else lambda request: link_verifier(request, verdict)
    result = run(data, response, verify=verify)
    assert len(result.claims) == 1
    assert result.belief_links == ()
    assert {item["reason"] for item in result.rejected} == {"relation_support_unconfirmed"}


def test_rejected_new_claim_cannot_be_published_as_a_verified_change():
    data, response = scenario()

    def reject_claim(request):
        result = json.loads(link_verifier(request))
        result["assessments"][0]["relations"][0]["verdict"] = "unknown"
        return encoded(result)

    result = run(data, response, verify=reject_claim)
    assert result.claims == result.belief_links == ()
    assert {item["reason"] for item in result.rejected} == {"semantic_support_unconfirmed", "linked_claim_unconfirmed"}


@pytest.mark.parametrize("change", ["missing_old_evidence", "missing_new_evidence", "wrong_revision",
                                    "unknown_target", "duplicate_target", "different_world", "unknown_claim"])
def test_relationship_requires_every_exact_endpoint_and_its_own_sources(change):
    data, response = scenario()
    link = response["belief_links"][0]
    if change == "missing_old_evidence":
        link["evidence_refs"].pop(0)
    elif change == "missing_new_evidence":
        link["evidence_refs"].pop()
    elif change == "wrong_revision":
        link["target_memory_versions"][0]["revision"] += 1
    elif change == "unknown_target":
        link["target_memory_versions"][0]["memory_id"] = "unknown"
    elif change == "duplicate_target":
        link["target_memory_versions"] *= 2
    elif change == "different_world":
        link["world"] = "hypothetical"
    else:
        link["claim_proposal_id"] = "not-proposed"
    result = run(data, response, verify=link_verifier)
    assert len(result.claims) == 1
    assert result.belief_links == ()
    assert result.rejected == ({"proposal_id": "link-1", "reason": "invalid_proposal"},)


@pytest.mark.parametrize("validity", [{"kind": "unknown"}, UNBOUNDED, TEMPORARY])
def test_lasting_change_cannot_be_inferred_without_a_permanent_absolute_start(validity):
    data, response = scenario()
    response["belief_links"][0]["valid_during"] = copy.deepcopy(validity)
    result = run(data, response, verify=link_verifier)
    assert result.belief_links == ()


@pytest.mark.parametrize("conditions", [
    {"op": "always"},
    {"op": "any", "args": [{"op": "always"}, {"op": "always"}]},
    {"op": "all", "args": [{"op": "always"}, {"op": "not", "arg": {"op": "not", "arg": {"op": "always"}}}]},
])
def test_exception_does_not_turn_into_an_unconditional_permanent_replacement(conditions):
    data, response = scenario("exception")
    response["belief_links"][0]["valid_during"] = copy.deepcopy(UNBOUNDED)
    response["belief_links"][0]["conditions"] = conditions
    assert run(data, response, verify=link_verifier).belief_links == ()


@pytest.mark.parametrize("field,value", [
    ("conditions", {"op": "not", "arg": {"op": "always"}}),
    ("valid_during", {"kind": "unknown"}), ("memory_kind", "relationship"),
    ("claim_kind", "reported_fact"),
    ("valid_time", {"start": "2026-01-01T00:00:00Z", "end": None, "precision": "exact", "basis": "source_explicit"}),
])
def test_equivalence_does_not_merge_different_conditions_time_or_claim_semantics(field, value):
    data, response = scenario("equivalent")
    response["claims"][0][field] = copy.deepcopy(value)
    result = run(data, response, verify=link_verifier)
    assert result.belief_links == ()


def abstraction():
    a = event("Before studying, I talk about my day.")
    b = event("I chat briefly before opening my textbooks.", 2)
    targets = (known(a, "I talk about my day before studying."), known(b, "I chat before studying."))
    proposed = claim(a, statement="In the two recorded instances, conversation preceded study.",
                     memory_kind="semantic", representation="schema", claim_kind="inferred_pattern",
                     conditions={"op": "always"}, valid_during={"kind": "unknown"},
                     evidence_refs=[reference(a), reference(b)],
                     source_memory_versions=[version(target) for target in targets])
    link = {"proposal_id": "abstract-1", "kind": "abstracts", "claim_proposal_id": proposed["proposal_id"],
            "target_memory_versions": [version(target) for target in targets],
            "evidence_refs": [reference(a), reference(b)], "conditions": {"op": "always"},
            "valid_during": {"kind": "unknown"}, "world": "real_world"}
    return batch(a, b, existing_assertions=targets), {**proposals(proposed), "belief_links": [link]}


def test_different_wording_can_support_a_verified_bounded_abstraction_with_original_instances():
    data, response = abstraction()
    result = run(data, response, verify=link_verifier)
    assert result.status == "completed"
    assert result.claims[0]["representation"] == "schema"
    assert result.claims[0]["valid_during"] == {"kind": "unknown"}
    assert result.belief_links[0]["target_memory_versions"] == [version(target) for target in data.existing_assertions]
    assert len(result.claims[0]["source_memory_versions"]) == 2
    assert result.model_calls == 2


@pytest.mark.parametrize("change", ["one_instance", "missing_parent", "not_schema", "not_pattern", "shared_source"])
def test_abstraction_cannot_invent_an_independent_instance_or_drop_its_parent(change):
    data, response = abstraction()
    claim, link = response["claims"][0], response["belief_links"][0]
    if change == "one_instance":
        link["target_memory_versions"].pop()
    elif change == "missing_parent":
        claim["source_memory_versions"].pop()
    elif change == "not_schema":
        claim["representation"] = "semantic_anchor"
    elif change == "not_pattern":
        claim["claim_kind"] = "reported_fact"
    else:
        data.existing_assertions[1]["evidence_refs"] = copy.deepcopy(data.existing_assertions[0]["evidence_refs"])
    result = run(data, response, verify=link_verifier)
    assert result.belief_links == ()


def test_incomplete_history_cannot_publish_new_belief_relations():
    data, response = scenario()
    data.sampling["publication_restricted"] = True
    result = run(data, response, verify=link_verifier)
    assert result.claims == result.belief_links == ()
    assert result.model_calls == 1
