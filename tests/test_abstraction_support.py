import copy
import json
from dataclasses import replace

import pytest
from test_belief_semantic import known, link_verifier, version
from test_cognition import event
from test_semantic import batch, claim, encoded, proposals, reference, run

from agent_memory.consolidation_planning import plan_consolidation


def sampled_walks():
    sources = tuple(event(text, index) for index, text in enumerate((
        "I walked before studying.", "I wore a blue coat.",
        "I walked before studying.", "I wore no coat.",
    ), 1))
    parents = []
    for action, clothing in (sources[:2], sources[2:]):
        parent = known(action, action["observed"]["content"]["text"] + " "
                       + clothing["observed"]["content"]["text"])
        parent.update(memory_kind="episodic", representation="episode_fragment", claim_kind="reported_fact",
                      epistemic_status="derived_inference", evidence_refs=[reference(action), reference(clothing)])
        parents.append(parent)
    shared = claim(sources[0], statement="In both sampled instances, walking preceded studying.",
                   memory_kind="semantic", representation="schema", claim_kind="inferred_pattern",
                   conditions={"op": "always"}, valid_during={"kind": "unknown"},
                   evidence_refs=[reference(sources[0]), reference(sources[2])],
                   source_memory_versions=[version(parent) for parent in parents])
    link = {"proposal_id": "abstract-1", "kind": "abstracts", "claim_proposal_id": shared["proposal_id"],
            "target_memory_versions": [version(parent) for parent in parents],
            "evidence_refs": [reference(source) for source in sources], "conditions": {"op": "always"},
            "valid_during": {"kind": "unknown"}, "world": "real_world"}
    data = batch(*sources, existing_assertions=tuple(parents))
    objective = plan_consolidation(sources, parents)["objectives"][0]
    response = {**proposals(shared), "belief_links": [link], "consolidation_decisions": [{
        "objective_id": objective["objective_id"], "outcome": "proposed", "reason": None,
        "link_proposal_ids": [link["proposal_id"]],
    }]}
    return data, response


def assessment_lookup(request):
    response = json.loads(link_verifier(request))
    return response, {item["proposal_id"]: item for item in response["assessments"]}


def select_support(assessment, excluded, verdict="irrelevant"):
    for relation in assessment["relations"]:
        if relation["evidence_index"] in excluded:
            relation["verdict"] = verdict
    assessment["supporting_evidence_indices"] = [item["evidence_index"] for item in assessment["relations"]
                                                if item["verdict"] == "supports"]


def test_common_claim_uses_only_action_sources_while_link_preserves_complete_parent_evidence():
    data, response = sampled_walks()
    before = copy.deepcopy(data.existing_assertions)
    captured = []

    def verify(request):
        captured.extend(json.loads(request.messages[-1]["content"])["proposals"])
        return link_verifier(request)

    result = run(data, response, verify=verify)
    assert result.status == "completed" and result.model_calls == 2
    assert len(result.claims) == len(result.belief_links) == 1
    assert result.claims[0]["evidence_refs"] == response["claims"][0]["evidence_refs"]
    assert result.belief_links[0]["evidence_refs"] == response["belief_links"][0]["evidence_refs"]
    link = next(item for item in captured if item["group"] == "belief_links")
    assert link["instance_support"] == [
        {"memory_version": version(before[0]), "evidence_indices": [0]},
        {"memory_version": version(before[1]), "evidence_indices": [2]},
    ]
    assert data.existing_assertions == before


def test_complete_shared_segments_can_be_narrower_than_exact_parent_source_spans():
    action = "I walked before studying."
    sources = (event(action + " I wore a blue coat."), event(action + " I wore no coat.", 2))
    parents = tuple(known(source, source["observed"]["content"]["text"]) for source in sources)
    refs = [reference(source) for source in sources]
    for ref in refs:
        ref["span"]["end"] = len(action)
    shared = claim(sources[0], statement="In both sampled instances, walking preceded studying.",
                   memory_kind="semantic", representation="schema", claim_kind="inferred_pattern",
                   conditions={"op": "always"}, evidence_refs=refs,
                   source_memory_versions=[version(parent) for parent in parents])
    link = {"proposal_id": "abstract-1", "kind": "abstracts", "claim_proposal_id": shared["proposal_id"],
            "target_memory_versions": [version(parent) for parent in parents],
            "evidence_refs": [*[reference(source) for source in sources], *refs], "conditions": {"op": "always"},
            "valid_during": {"kind": "unknown"}, "world": "real_world"}

    def verify(request):
        entry = next(item for item in json.loads(request.messages[-1]["content"])["proposals"]
                     if item["group"] == "belief_links")
        assert entry["instance_support"] == [
            {"memory_version": version(parents[0]), "evidence_indices": [2]},
            {"memory_version": version(parents[1]), "evidence_indices": [3]},
        ]
        return link_verifier(request)

    result = run(batch(*sources, existing_assertions=parents), {**proposals(shared), "belief_links": [link]}, verify=verify)
    assert result.status == "completed" and result.model_calls == 2
    assert len(result.claims) == len(result.belief_links) == 1


def test_verified_common_core_survives_independently_rejected_difference():
    data, response = sampled_walks()
    difference = copy.deepcopy(response["claims"][0])
    difference.update(proposal_id="claim-difference", statement="The two sampled walks lasted different amounts of time.")
    difference_link = copy.deepcopy(response["belief_links"][0])
    difference_link.update(proposal_id="abstract-difference", claim_proposal_id=difference["proposal_id"])
    response["claims"].append(difference)
    response["belief_links"].append(difference_link)
    response["consolidation_decisions"][0]["link_proposal_ids"].append(difference_link["proposal_id"])

    def verify(request):
        verified, assessments = assessment_lookup(request)
        assessments[difference["proposal_id"]]["proposal_verdict"] = "unknown"
        assessment = assessments[difference_link["proposal_id"]]
        assessment.update(proposal_verdict="unknown", link_verdict="unknown")
        for instance in assessment["instance_verdicts"]:
            instance["verdict"] = "unknown"
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.status == "partial" and result.model_calls == 2
    assert [item["proposal_id"] for item in result.claims] == [response["claims"][0]["proposal_id"]]
    assert [item["proposal_id"] for item in result.belief_links] == [response["belief_links"][0]["proposal_id"]]
    assert result.claims[0]["statement"] == response["claims"][0]["statement"]
    assert result.consolidation["satisfied_count"] == 1


def test_compound_commonality_with_unknown_difference_is_rejected_without_rewriting():
    data, response = sampled_walks()
    response["claims"][0]["statement"] = (
        "In both sampled instances, walking preceded studying, and the walks lasted different amounts of time.")
    original = copy.deepcopy(response)

    def verify(request):
        verified, assessments = assessment_lookup(request)
        assessments[response["claims"][0]["proposal_id"]]["proposal_verdict"] = "unknown"
        assessment = assessments[response["belief_links"][0]["proposal_id"]]
        assessment.update(proposal_verdict="unknown", link_verdict="unknown")
        for instance in assessment["instance_verdicts"]:
            instance["verdict"] = "unknown"
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.status == "partial" and result.model_calls == 2
    assert result.claims == result.belief_links == ()
    assert response == original
    assert result.consolidation["satisfied_count"] == 0


@pytest.mark.parametrize("failure", ["missing", "missing_instance", "wrong_id", "wrong_revision", "duplicate", "extra"])
def test_incomplete_instance_verdicts_fail_closed_without_permanent_dedup(failure):
    data, response = sampled_walks()

    def verify(request):
        verified, assessments = assessment_lookup(request)
        assessment = assessments[response["belief_links"][0]["proposal_id"]]
        instances = assessment["instance_verdicts"]
        if failure == "missing":
            del assessment["instance_verdicts"]
        elif failure == "missing_instance":
            instances.pop()
        elif failure == "wrong_id":
            instances[0]["memory_version"]["memory_id"] = "unknown-memory"
        elif failure == "wrong_revision":
            instances[0]["memory_version"]["revision"] += 1
        elif failure == "duplicate":
            instances[1] = copy.deepcopy(instances[0])
        else:
            instances.append({"memory_version": {"memory_id": "unknown-memory", "revision": 1}, "verdict": "supports"})
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.claims == result.belief_links == ()
    assert result.model_calls == 2
    objective = result.consolidation["objectives"][0]
    assert objective["status"] == "unresolved" and objective["dedup_eligible"] is False


@pytest.mark.parametrize("verdict", ["unknown", "contradicts"])
def test_non_supporting_instance_blocks_shared_claim_even_when_joint_verdicts_support(verdict):
    data, response = sampled_walks()

    def verify(request):
        verified, assessments = assessment_lookup(request)
        assessments[response["belief_links"][0]["proposal_id"]]["instance_verdicts"][1]["verdict"] = verdict
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.claims == result.belief_links == ()
    assert result.model_calls == 2
    objective = result.consolidation["objectives"][0]
    assert objective["status"] == "unresolved" and objective["dedup_eligible"] is True


def test_instance_verdicts_cannot_be_attached_to_an_ordinary_claim_assessment():
    data, response = sampled_walks()

    def verify(request):
        verified, assessments = assessment_lookup(request)
        assessments[response["claims"][0]["proposal_id"]]["instance_verdicts"] = copy.deepcopy(
            assessments[response["belief_links"][0]["proposal_id"]]["instance_verdicts"])
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.status == "invalid_response" and result.claims == result.belief_links == ()
    assert result.consolidation["objectives"][0]["dedup_eligible"] is False


def test_shared_claim_requires_supporting_source_segments_from_every_parent():
    data, response = sampled_walks()
    response["claims"][0]["evidence_refs"].pop()
    result = run(data, response, verify=lambda _: pytest.fail("Missing instance support must fail before verification"))
    assert result.claims == result.belief_links == () and result.model_calls == 1
    assert result.consolidation["objectives"][0]["dedup_eligible"] is False


def test_ordinary_parent_claim_cannot_omit_parent_source_events():
    data, response = sampled_walks()
    response["belief_links"] = []
    response["consolidation_decisions"][0].update(outcome="no_change", reason="insufficient_shared_meaning",
                                                 link_proposal_ids=[])
    result = run(data, response, verify=lambda _: pytest.fail("No eligible proposal should reach verification"))
    assert result.claims == () and result.model_calls == 1
    assert result.rejected == ({"proposal_id": response["claims"][0]["proposal_id"], "reason": "invalid_proposal"},)


def test_abstraction_cannot_drop_residual_parent_evidence_from_link():
    data, response = sampled_walks()
    response["belief_links"][0]["evidence_refs"] = copy.deepcopy(response["claims"][0]["evidence_refs"])
    result = run(data, response, verify=lambda _: pytest.fail("No eligible proposal should reach verification"))
    assert result.claims == result.belief_links == () and result.model_calls == 1
    assert {item["reason"] for item in result.rejected} == {"invalid_proposal"}


def test_no_change_cannot_hide_an_unlisted_matching_abstraction():
    data, response = sampled_walks()
    response["consolidation_decisions"][0].update(outcome="no_change", reason="insufficient_shared_meaning",
                                                 link_proposal_ids=[])
    result = run(data, response, verify=lambda _: pytest.fail("Contradictory decision must fail before verification"))
    assert result.status == "invalid_response" and result.model_calls == 1
    assert result.claims == result.belief_links == ()
    assert result.consolidation["objectives"][0]["attempted"] is False


def test_proposed_decision_cannot_hide_a_second_matching_abstraction():
    data, response = sampled_walks()
    extra = copy.deepcopy(response["belief_links"][0])
    extra["proposal_id"] = "unlisted-abstract"
    response["belief_links"].append(extra)
    result = run(data, response)
    assert result.status == "invalid_response" and result.model_calls == 1
    assert result.claims == result.belief_links == ()


def test_explicit_irrelevant_coat_references_leave_the_shared_claim_but_remain_in_parent_link():
    data, response = sampled_walks()
    response["claims"][0]["evidence_refs"] = copy.deepcopy(response["belief_links"][0]["evidence_refs"])
    original = copy.deepcopy(response)

    def verify(request):
        entries = json.loads(request.messages[-1]["content"])["proposals"]
        assert all(item["evidence_selection"] == "minimal_shared_support" for item in entries)
        link = next(item for item in entries if item["group"] == "belief_links")
        assert [item["role"] for item in link["evidence"]] == ["shared_claim_candidate"] * 4
        verified, assessments = assessment_lookup(request)
        for assessment in assessments.values():
            select_support(assessment, {1, 3})
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.status == "completed" and result.model_calls == 2
    assert len(result.claims) == len(result.belief_links) == 1
    assert result.claims[0]["evidence_refs"] == [original["claims"][0]["evidence_refs"][index] for index in (0, 2)]
    for field in ("statement", "conditions", "valid_time", "valid_during", "world", "source_memory_versions",
                  "counterexamples"):
        assert result.claims[0][field] == original["claims"][0][field]
    assert result.belief_links[0]["evidence_refs"] == original["belief_links"][0]["evidence_refs"]
    assert response == original
    assert result.consolidation["satisfied_count"] == 1


def test_parent_only_references_have_an_explicit_endpoint_context_role():
    data, response = sampled_walks()

    def verify(request):
        entries = json.loads(request.messages[-1]["content"])["proposals"]
        link = next(item for item in entries if item["group"] == "belief_links")
        assert [item["role"] for item in link["evidence"]] == [
            "shared_claim_candidate", "retained_endpoint_context", "shared_claim_candidate", "retained_endpoint_context"]
        verified, assessments = assessment_lookup(request)
        select_support(assessments[response["belief_links"][0]["proposal_id"]], {1, 3})
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.status == "completed" and result.model_calls == 2
    assert result.claims[0]["evidence_refs"] == response["claims"][0]["evidence_refs"]
    assert result.belief_links[0]["evidence_refs"] == response["belief_links"][0]["evidence_refs"]


@pytest.mark.parametrize("verdict", ["unknown", "contradicts"])
def test_uncertain_or_opposing_coat_references_cannot_be_discarded_as_irrelevant(verdict):
    data, response = sampled_walks()
    response["claims"][0]["evidence_refs"] = copy.deepcopy(response["belief_links"][0]["evidence_refs"])

    def verify(request):
        verified, assessments = assessment_lookup(request)
        for assessment in assessments.values():
            select_support(assessment, {1, 3}, verdict)
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.claims == result.belief_links == () and result.model_calls == 2
    assert result.consolidation["satisfied_count"] == 0


@pytest.mark.parametrize("group", ["claims", "belief_links"])
@pytest.mark.parametrize("failure", ["missing", "duplicate", "out_of_range", "omitted_support", "empty", "includes_irrelevant"])
def test_missing_or_inconsistent_selected_support_basis_cannot_publish_or_permanently_dedup(group, failure):
    data, response = sampled_walks()
    response["claims"][0]["evidence_refs"] = copy.deepcopy(response["belief_links"][0]["evidence_refs"])

    def verify(request):
        verified, assessments = assessment_lookup(request)
        for assessment in assessments.values():
            select_support(assessment, {1, 3})
        assessment = assessments[response[group][0]["proposal_id"]]
        basis = assessment["supporting_evidence_indices"]
        if failure == "missing":
            del assessment["supporting_evidence_indices"]
        elif failure == "duplicate":
            basis.append(basis[0])
        elif failure == "out_of_range":
            basis.append(99)
        elif failure == "omitted_support":
            basis.pop()
        elif failure == "empty":
            basis.clear()
        else:
            basis.append(1)
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.claims == result.belief_links == () and result.model_calls == 2
    assert result.consolidation["objectives"][0]["dedup_eligible"] is False


def test_irrelevant_selection_cannot_remove_an_instances_entire_support():
    data, response = sampled_walks()
    response["claims"][0]["evidence_refs"] = copy.deepcopy(response["belief_links"][0]["evidence_refs"])

    def verify(request):
        verified, assessments = assessment_lookup(request)
        for assessment in assessments.values():
            select_support(assessment, {0, 1, 3})
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.claims == result.belief_links == () and result.model_calls == 2
    assert result.consolidation["satisfied_count"] == 0


@pytest.mark.parametrize("group", ["claims", "belief_links"])
def test_selected_support_does_not_replace_joint_semantic_support(group):
    data, response = sampled_walks()
    response["claims"][0]["evidence_refs"] = copy.deepcopy(response["belief_links"][0]["evidence_refs"])

    def verify(request):
        verified, assessments = assessment_lookup(request)
        for assessment in assessments.values():
            select_support(assessment, {1, 3})
        assessments[response[group][0]["proposal_id"]]["proposal_verdict"] = "unknown"
        return encoded(verified)

    result = run(data, response, verify=verify)
    assert result.claims == result.belief_links == () and result.model_calls == 2


@pytest.mark.parametrize("has_counterexample", [False, True])
def test_ordinary_claims_cannot_use_irrelevance_to_remove_support_or_counterevidence(has_counterexample):
    source = event("I walked before studying.")
    contrary = event("I did not walk before studying.", 2)
    proposed = claim(source, statement="I walked before studying.", conditions={"op": "always"})
    if has_counterexample:
        proposed["counterexamples"] = [reference(contrary, "contradicts")]

    def verify(request):
        verified, assessments = assessment_lookup(request)
        assessment = assessments[proposed["proposal_id"]]
        assessment["relations"][-1]["verdict"] = "irrelevant"
        return encoded(verified)

    result = run(batch(source, contrary), proposals(proposed), verify=verify)
    assert result.claims == () and result.model_calls == 2


def test_contested_endpoint_is_rejected_before_evidence_selection():
    data, response = sampled_walks()
    contrary = event("I did not walk before studying.", 5)
    data = replace(data, sources=(*data.sources, contrary))
    data.existing_assertions[0]["counterexamples"] = [reference(contrary, "contradicts")]
    response["belief_links"][0]["evidence_refs"].append(reference(contrary))
    response["consolidation_decisions"] = []
    original = copy.deepcopy(data.existing_assertions)

    result = run(data, response, verify=lambda _: pytest.fail("Contested endpoints must fail before verification"))
    assert result.claims == result.belief_links == () and result.model_calls == 1
    assert data.existing_assertions == original
