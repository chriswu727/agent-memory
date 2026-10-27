import copy
import json

import pytest
from test_belief_semantic import TEMPORARY, abstraction, known, link_verifier, scenario, version
from test_cognition import event
from test_semantic import batch, claim, encoded, proposals, reference, run

from membrain.semantic import SemanticProcessor

BASELINE = "从2026年10月20日00:00 UTC起，我的一般回复偏好是简短回答。这项偏好没有预定结束日期，也没有其他情境条件。"
EXCEPTION = "仅在2026年10月24日00:00 UTC到2026年10月25日00:00 UTC期间，我的一般回复偏好是详细解释。这只是一天的临时例外；结束后恢复简短回答。"


def split_references(source, boundary):
    first, second = reference(source), reference(source)
    first["span"]["end"] = boundary
    second["span"]["start"] = boundary
    return [first, second]


def recorded_exception_proposal():
    old, new = event(BASELINE), event(EXCEPTION, 2)
    target = known(old, BASELINE.replace("我", "用户"))
    target["evidence_refs"] = split_references(old, 37)
    proposed = claim(
        new, proposal_id="claim-20261024-temp-detail-exception",
        statement=EXCEPTION.replace("我", "用户"), conditions={"op": "always"},
        evidence_refs=split_references(new, 60), valid_during=copy.deepcopy(TEMPORARY),
        valid_time={"basis": "source_explicit", "start": "2026-10-24T00:00:00Z",
                    "end": "2026-10-25T00:00:00Z", "precision": "exact"},
    )
    link = {
        "proposal_id": "belief-link-20261024-temp-detail-exception", "kind": "exception",
        "claim_proposal_id": proposed["proposal_id"], "target_memory_versions": [version(target)],
        "evidence_refs": copy.deepcopy(proposed["evidence_refs"]), "world": "real_world",
        "conditions": {"op": "always"}, "valid_during": copy.deepcopy(TEMPORARY),
    }
    return batch(old, new, existing_assertions=(target,)), proposals(proposed) | {"belief_links": [link]}


def test_recorded_deepseek_exception_cannot_omit_old_endpoint_evidence():
    data, response = recorded_exception_proposal()
    link = response["belief_links"][0]
    with pytest.raises(ValueError, match="Relationship omits endpoint evidence"):
        SemanticProcessor._validate_proposal(
            "belief_links", link, data.scope,
            {(source["event_id"], source["revision"]): source for source in data.sources},
            {(target["memory_id"], target["revision"]): target for target in data.existing_assertions},
            {}, {item["proposal_id"]: item for item in response["claims"]},
        )

    def verify(request):
        candidates = json.loads(request.messages[-1]["content"])["proposals"]
        assert [candidate["group"] for candidate in candidates] == ["claims"]
        return link_verifier(request)

    result = run(data, response, verify=verify)
    assert len(result.claims) == 1 and result.belief_links == ()
    assert result.rejected == ({"proposal_id": link["proposal_id"], "reason": "invalid_proposal"},)


def test_recorded_legacy_verifier_response_without_joint_verdict_fails_closed():
    data, response = recorded_exception_proposal()
    recorded = {"assessments": [{
        "proposal_id": "claim-20261024-temp-detail-exception",
        "relations": [{"evidence_index": 0, "verdict": "unknown"},
                      {"evidence_index": 1, "verdict": "unknown"}],
        "link_verdict": "supports",
    }]}
    result = run(data, response, verify=lambda _: encoded(recorded))
    assert result.status == "invalid_response"
    assert result.claims == result.belief_links == ()


@pytest.mark.parametrize("link_verdict", ["supports", "unknown", "contradicts", None])
def test_complete_endpoint_union_reaches_independent_relationship_verification(link_verdict):
    data, response = recorded_exception_proposal()
    link = response["belief_links"][0]
    link["evidence_refs"] += copy.deepcopy(data.existing_assertions[0]["evidence_refs"])

    def verify(request):
        candidates = json.loads(request.messages[-1]["content"])["proposals"]
        assert [candidate["group"] for candidate in candidates] == ["claims", "belief_links"]
        candidate = candidates[1]
        assert candidate["linked_claim"] == response["claims"][0]
        assert candidate["target_assertions"] == list(data.existing_assertions)
        assert [item["excerpt"] for item in candidate["evidence"]] == [
            EXCEPTION[:60], EXCEPTION[60:], BASELINE[:37], BASELINE[37:],
        ]
        return link_verifier(request, link_verdict)

    result = run(data, response, verify=verify)
    assert len(result.claims) == 1
    if link_verdict == "supports":
        assert len(result.belief_links) == 1 and not result.rejected
    else:
        assert result.belief_links == ()
        assert result.rejected == ({"proposal_id": link["proposal_id"],
                                    "reason": "relation_support_unconfirmed"},)


def test_broader_reference_does_not_replace_exact_endpoint_premises():
    data, response = recorded_exception_proposal()
    response["belief_links"][0]["evidence_refs"].append(reference(data.sources[0]))
    result = run(data, response, verify=link_verifier)
    assert len(result.claims) == 1 and not result.belief_links
    assert result.rejected[0]["reason"] == "invalid_proposal"


@pytest.mark.parametrize("premise_relationship", ["supports", "contradicts"])
def test_opposing_claim_evidence_supports_the_existence_of_a_contradiction(premise_relationship):
    data, response = scenario("contradicts")
    response["claims"][0]["counterexamples"] = [reference(data.sources[0], "contradicts")]
    response["belief_links"][0]["evidence_refs"][0]["relationship"] = premise_relationship
    result = run(data, response, verify=link_verifier)
    assert len(result.claims) == 1
    assert result.claims[0]["verification"]["state"] == "contested"
    if premise_relationship == "supports":
        assert len(result.belief_links) == 1 and not result.rejected
    else:
        assert not result.belief_links
        assert result.rejected == ({"proposal_id": "link-1", "reason": "invalid_proposal"},)


@pytest.mark.parametrize("premise_verdict,joint_verdict,accepted", [
    ("supports", "supports", True),
    ("unknown", "supports", False),
    ("contradicts", "supports", False),
    ("supports", "unknown", False),
    ("supports", "contradicts", False),
])
def test_independent_instances_require_each_premise_and_joint_support(
    premise_verdict, joint_verdict, accepted,
):
    data, response = abstraction()

    def verify(request):
        candidates = json.loads(request.messages[-1]["content"])["proposals"]
        first, second = candidates[0]["evidence"]
        assert first["reference"]["event_id"] != second["reference"]["event_id"]
        assert first["excerpt"] == "Before studying, I talk about my day."
        assert second["excerpt"] == "I chat briefly before opening my textbooks."
        assessments = json.loads(link_verifier(request))
        assessments["assessments"][0]["relations"][1]["verdict"] = premise_verdict
        assessments["assessments"][0]["proposal_verdict"] = joint_verdict
        assessments["assessments"][0]["supporting_evidence_indices"] = [
            item["evidence_index"] for item in assessments["assessments"][0]["relations"]
            if item["verdict"] == "supports"]
        return encoded(assessments)

    result = run(data, response, verify=verify)
    if accepted:
        assert len(result.claims) == len(result.belief_links) == 1
        assert result.claims[0]["statement"] == "In the two recorded instances, conversation preceded study."
    else:
        assert result.claims == result.belief_links == ()
        expected = ({"semantic_support_unconfirmed", "linked_claim_unconfirmed"}
                    if premise_verdict == "supports" else {"instance_support_unconfirmed"})
        assert {item["reason"] for item in result.rejected} == expected


def test_matching_reference_verdicts_cannot_substitute_for_missing_joint_verdict():
    data, response = abstraction()

    def verify(request):
        assessments = json.loads(link_verifier(request))
        del assessments["assessments"][0]["proposal_verdict"]
        return encoded(assessments)

    result = run(data, response, verify=verify)
    assert result.status == "invalid_response"
    assert result.claims == result.belief_links == ()


@pytest.mark.parametrize("texts,statement", [
    (("I walked in the park on October 20.", "I studied on October 22."),
     "In two recorded instances, a walk preceded studying."),
    (("Before studying, I talk about my day.", "I chat briefly before opening my textbooks."),
     "I always talk before studying, and talking causes better study results."),
    (("Alice prefers concise answers.", "Bob prefers detailed answers."),
     "Alice prefers both concise and detailed answers."),
])
def test_supported_premises_do_not_publish_unsupported_combination(texts, statement):
    first, second = event(texts[0]), event(texts[1], 2)
    proposed = claim(first, statement=statement, conditions={"op": "always"},
                     claim_kind="inferred_pattern", memory_kind="semantic", representation="schema",
                     evidence_refs=[reference(first), reference(second)])

    def verify(request):
        candidate = json.loads(request.messages[-1]["content"])["proposals"][0]
        assert candidate["proposal"]["statement"] == statement
        assert [evidence["excerpt"] for evidence in candidate["evidence"]] == list(texts)
        return encoded({"assessments": [{
            "proposal_id": proposed["proposal_id"], "proposal_verdict": "unknown", "link_verdict": None,
            "relations": [{"evidence_index": 0, "verdict": "supports"},
                          {"evidence_index": 1, "verdict": "supports"}],
        }]})

    result = run(batch(first, second), proposals(proposed), verify=verify)
    assert not result.claims
    assert result.rejected == ({"proposal_id": proposed["proposal_id"],
                                "reason": "semantic_support_unconfirmed"},)


@pytest.mark.parametrize("group", ["claims", "belief_links"])
def test_missing_link_verdict_is_invalid_even_when_joint_and_premises_support(group):
    data, response = abstraction()

    def verify(request):
        assessments = json.loads(link_verifier(request))
        candidates = json.loads(request.messages[-1]["content"])["proposals"]
        for candidate, assessment in zip(candidates, assessments["assessments"], strict=True):
            if candidate["group"] == group:
                del assessment["link_verdict"]
        return encoded(assessments)

    result = run(data, response, verify=verify)
    assert result.status == "invalid_response"
    assert result.claims == result.belief_links == ()


def test_non_relationship_assessment_requires_null_link_verdict():
    data, response = abstraction()

    def verify(request):
        assessments = json.loads(link_verifier(request))
        assessments["assessments"][0]["link_verdict"] = "supports"
        return encoded(assessments)

    result = run(data, response, verify=verify)
    assert result.status == "invalid_response"
    assert result.claims == result.belief_links == ()
