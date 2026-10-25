import copy
import hashlib
import json
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest
from test_belief_semantic import abstraction, known, link_verifier, scenario, version
from test_bounded_learning_semantics import history_case, process, publication_snapshot
from test_cognition import event
from test_episode_fidelity import assessment
from test_episode_fidelity import process as episode_process
from test_episode_source_views import WRONG_LABEL, continuation
from test_semantic import batch, claim, encoded, proposals, reference, run

from agent_memory import cognition
from agent_memory.belief_capabilities import (
    CAPABILITY_INSTRUCTIONS,
    RELATION_CAPABILITIES_INPUT_VERSION,
)
from agent_memory.consolidation import _check_result
from agent_memory.helpers import DomainError, digest
from agent_memory.semantic import (
    EPISODE_SOURCE_VIEW_INPUT_VERSION,
    CognitiveBatch,
    SemanticPolicy,
    SemanticProcessor,
    _json,
    belief_capabilities,
    proposal_data,
    request_payload,
)

VERSION = RELATION_CAPABILITIES_INPUT_VERSION
ARCHIVE = Path(__file__).parents[1] / "evaluations/deepseek-2026-10-25-learning-repair-stopped.evidence.tar.gz"


def capabilities(data):
    return belief_capabilities(data.scope, data.sources, data.existing_assertions)


@pytest.fixture(scope="module")
def recorded():
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == (
        "d3d7c49a590c70f6f99036e75c8d80503c778b9818fc24ae7fa47dbf30023cbd")
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        raw = archive.extractfile("run/memory/report.json").read()
    assert hashlib.sha256(raw).hexdigest() == "a2e979847c10f6e136aaed3f699dc379b2230cf182dc74a5cb6c2c9de65f85fe"
    stage = json.loads(raw)["stages"][7]
    call, snapshot = stage["cognitive_calls"][2], stage["learning_jobs"][1]["result"]
    body = json.loads(call["request"]["messages"][-1]["content"])
    data = CognitiveBatch(
        stage["learning_jobs"][1]["job_id"], snapshot["scope"], tuple(body["sources"]),
        existing_assertions=tuple(body["existing_assertions"]), working_state=body["working_state"],
        episodes=tuple(body["episodes"]), sampling=body["sampling"],
        belief_relations=tuple(body["belief_relations"]), consolidation_plan=body["consolidation_plan"],
        current_input_refs=tuple(body["current_input_refs"]), proposal_mode=snapshot["proposal_mode"],
        learning_input_refs=tuple(body["learning_input_refs"]),
        input_contract_version=snapshot["input_contract_version"], integration_manifest=body["integration_manifest"],
    )
    return data, call, SemanticPolicy(**snapshot["semantic_policy"]["limits"])


def test_recorded_impossible_abstraction_keeps_exact_old_request_and_rejection(recorded):
    data, call, policy = recorded
    seen = []

    def propose(request):
        seen.append(request)
        return encoded(call["response"])

    result = SemanticProcessor(propose, lambda _: pytest.fail("No eligible proposal"), policy).process(
        data, reserve=lambda **_: None)
    assert len(seen) == result.model_calls == 1 and result.status == "partial"
    assert list(seen[0].messages) == call["request"]["messages"]
    assert seen[0].response_schema == call["request"]["response_schema"]
    assert seen[0].request_id == call["request"]["request_id"]
    assert result.rejected == ({"proposal_id": "claim-schema-001", "reason": "invalid_proposal"},
                               {"proposal_id": "link-schema-001", "reason": "invalid_proposal"})
    assert not result.claims and not result.belief_links


def test_recorded_endpoints_expose_unavailable_edges_before_generation_without_repairing_candidate(recorded):
    old, call, policy = recorded
    data = replace(old, input_contract_version=VERSION)
    seen = []

    def propose(request):
        seen.append(request)
        body = json.loads(request.messages[-1]["content"])
        table = body["belief_relation_capabilities"]
        assert table["interpretation"] == "structural_limits_not_semantic_support"
        assert len(table["endpoints"]) == 3
        for endpoint in table["endpoints"]:
            assert "abstracts" not in endpoint["conditional_relations"]
            assert endpoint["unavailable_relations"]["abstracts"] == "abstraction_attribution_unavailable"
            assert endpoint["claim_kinds_for_endpoint_evidence"] == ["agent_said", "proposal_offered", "commitment_expressed"]
            assert endpoint["abstraction_partner_indices"] == []
        assert sum("equivalent" in item["conditional_relations"] for item in table["endpoints"]) == 1
        assert body["sources"] == list(data.sources)
        assert body["existing_assertions"] == list(data.existing_assertions)
        assert CAPABILITY_INSTRUCTIONS in request.messages[0]["content"]
        return encoded(call["response"])

    result = SemanticProcessor(propose, lambda _: pytest.fail("Still no eligible proposal"), policy).process(
        data, reserve=lambda **_: None)
    assert len(seen) == result.model_calls == 1 and result.status == "partial"
    assert len(result.rejected) == 2 and result.history_integration["assessed_count"] == 0
    assert not result.claims and not result.belief_links


def test_no_change_remains_a_one_call_assessment_for_unavailable_relations(recorded):
    old, _, policy = recorded
    data = replace(old, input_contract_version=VERSION)
    response = {**proposals(), "integration_decisions": [
        {"objective_id": item["objective_id"], "outcome": "no_change",
         "reason": "insufficient_evidence", "link_proposal_ids": []}
        for item in data.integration_manifest["objectives"]]}
    result = run(data, response, verify=lambda _: pytest.fail("No verification for no-change"), policy=policy)
    assert result.status == "completed" and result.model_calls == 1
    assert result.history_integration["assessed_count"] == 3
    assert result.history_integration["published_count"] == 0
    assert data.integration_manifest["unresolved_discovery"] is True
    _check_result(data, result, publication_snapshot(data))


def test_exact_user_instances_keep_conditional_abstraction_and_original_evidence():
    old, response = abstraction()
    data = replace(old, input_contract_version=VERSION)
    before = copy.deepcopy((data, response))
    table = capabilities(data)
    assert [item["abstraction_partner_indices"] for item in table["endpoints"]] == [[1], [0]]
    assert all("abstracts" in item["conditional_relations"] for item in table["endpoints"])
    result = run(data, response, verify=link_verifier)
    assert result.status == "completed" and result.model_calls == 2
    assert len(result.claims) == len(result.belief_links) == 1
    assert result.claims[0]["source_memory_versions"] == [version(item) for item in data.existing_assertions]
    _check_result(data, result, publication_snapshot(data))
    assert (data, response) == before


@pytest.mark.parametrize("kind", ["equivalent", "supersedes", "exception", "contradicts"])
def test_conditional_relations_keep_legal_mixed_endpoint_and_new_source_unions(kind):
    old, response = scenario(kind)
    data = replace(old, input_contract_version=VERSION)
    assert kind in capabilities(data)["endpoints"][0]["conditional_relations"]
    result = run(data, response, verify=link_verifier)
    assert result.status == "completed" and len(result.belief_links) == 1
    _check_result(data, result, publication_snapshot(data))


def test_assistant_speech_can_be_compared_without_becoming_external_fact():
    a, b = event("I suggested a brief reply.", role="assistant"), event("I proposed brevity.", 2, role="assistant")
    target = known(a)
    target.update(memory_kind="episodic", claim_kind="agent_said", valid_during={"kind": "unknown"})
    proposed = claim(b, statement="The assistant proposed brevity.", memory_kind="episodic",
                     claim_kind="agent_said", conditions={"op": "always"})
    link = {"proposal_id": "link", "kind": "equivalent", "claim_proposal_id": proposed["proposal_id"],
            "target_memory_versions": [version(target)], "evidence_refs": [reference(a), reference(b)],
            "world": "real_world", "conditions": {"op": "always"}, "valid_during": {"kind": "unknown"}}
    data = batch(a, b, existing_assertions=(target,), input_contract_version=VERSION)
    table = capabilities(data)["endpoints"][0]
    assert table["claim_kinds_for_endpoint_evidence"] == ["agent_said", "proposal_offered", "commitment_expressed"]
    assert "equivalent" in table["conditional_relations"] and "abstracts" not in table["conditional_relations"]
    result = run(data, {**proposals(proposed), "belief_links": [link]}, verify=link_verifier)
    assert result.status == "completed" and len(result.belief_links) == 1
    _check_result(data, result, publication_snapshot(data))


def test_literal_metadata_is_not_inferred_and_all_other_relations_remain_conditional():
    source = event("I described a routine.")
    target = cognition.extract_event(source)[0]
    data = batch(source, existing_assertions=(target,), input_contract_version=VERSION)
    before = copy.deepcopy(target)
    entry = capabilities(data)["endpoints"][0]
    assert entry["unavailable_relations"]["equivalent"] == "endpoint_metadata_incomplete"
    assert entry["conditional_relations"] == ["exception", "supersedes", "contradicts"]
    assert target == before and "claim_kind" not in target and "conditions" not in target


@pytest.mark.parametrize("change", ["shared_source", "actor", "conditions", "world", "contested", "missing_conditions"])
def test_pair_constraints_are_derived_from_full_existing_guard(change):
    data, response = abstraction()
    a, b = data.existing_assertions
    if change == "shared_source":
        b["evidence_refs"] = copy.deepcopy(a["evidence_refs"])
    elif change == "actor":
        data.sources[1]["observed"]["actor_ref"] = "another-person"
    elif change == "conditions":
        b["conditions"] = {"op": "not", "arg": {"op": "always"}}
    elif change == "world":
        b["world"] = "hypothetical"
    elif change == "contested":
        b["counterexamples"] = [reference(data.sources[0], "contradicts")]
    else:
        del b["conditions"]
    table = capabilities(data)
    assert all("abstracts" not in item["conditional_relations"] for item in table["endpoints"])
    assert all(not item["abstraction_partner_indices"] for item in table["endpoints"])
    result = run(replace(data, input_contract_version=VERSION), response, verify=link_verifier)
    assert not result.belief_links


@pytest.mark.parametrize("change", ["missing", "hash", "span", "scope", "draft"])
def test_unavailable_exact_endpoint_evidence_never_advertises_a_relation(change):
    data, _ = scenario()
    if change == "missing":
        data = replace(data, sources=data.sources[1:])
    elif change == "hash":
        data.existing_assertions[0]["evidence_refs"][0]["source_content_sha256"] = "0" * 64
    elif change == "span":
        data.existing_assertions[0]["evidence_refs"][0]["span"]["start"] = 1
    elif change == "scope":
        data.sources[0]["scope"]["brain_id"] = "another-brain"
    else:
        data.sources[0]["observed"].update(actor_role="assistant", delivery_state="draft")
    entry = capabilities(data)["endpoints"][0]
    assert entry["claim_kinds_for_endpoint_evidence"] == [] and entry["conditional_relations"] == []


def test_endpoint_capability_does_not_authorize_mixed_speaker_union():
    data, response = scenario("equivalent")
    assert "reported_preference" in capabilities(data)["endpoints"][0]["claim_kinds_for_endpoint_evidence"]
    new = event("I proposed a short response.", 2, role="assistant")
    data = replace(data, sources=(data.sources[0], new), input_contract_version=VERSION)
    response["claims"][0]["evidence_refs"] = [reference(new)]
    response["belief_links"][0]["evidence_refs"] = [reference(data.sources[0]), reference(new)]
    result = run(data, response, verify=lambda _: pytest.fail("Mixed speaker claims remain invalid"))
    assert not result.claims and not result.belief_links and result.model_calls == 1
    assert len(result.rejected) == 2


def test_conditional_capability_cannot_override_negative_independent_assessment():
    old, response = history_case()
    data = replace(old, input_contract_version=VERSION)

    def reject_relation(checked):
        checked["assessments"][-1]["link_verdict"] = "unknown"

    result, requests = process(data, response, change_assessment=reject_relation)
    assert len(requests) == 2 and result.status == "partial"
    assert not result.claims and not result.belief_links
    assert result.history_integration["assessed_count"] == 1
    assert result.history_integration["published_count"] == 0
    _check_result(data, result, publication_snapshot(data))


def test_publication_rechecks_unavailable_endpoint_without_trusting_prior_capability():
    data, response = abstraction()
    data = replace(data, input_contract_version=VERSION)
    result = run(data, response, verify=link_verifier)
    assert len(result.belief_links) == 1
    altered = copy.deepcopy(data.existing_assertions)
    altered[1]["evidence_refs"] = copy.deepcopy(altered[0]["evidence_refs"])
    changed = replace(data, existing_assertions=altered)
    with pytest.raises(DomainError, match="abstraction is unsupported"):
        _check_result(changed, result, publication_snapshot(changed))


def test_capability_bytes_are_counted_before_reservation_and_are_not_silently_dropped():
    old, response = scenario()
    data = replace(old, input_contract_version=VERSION)
    result, requests = process(data, response)
    assert result.status == "completed"
    request = requests[0]
    body = json.loads(request.messages[-1]["content"])
    _, total = request_payload("proposal", body, request.response_schema, request.max_output_tokens,
                               input_contract_version=VERSION)
    reduced = {key: value for key, value in body.items() if key != "belief_relation_capabilities"}
    _, without = request_payload("proposal", reduced, request.response_schema, request.max_output_tokens,
                                 input_contract_version=VERSION)
    assert total > without
    processor = SemanticProcessor(lambda _: pytest.fail("Request budget exhausted before provider"),
                                  policy=replace(SemanticPolicy(), max_request_bytes=total - 1))
    failed = processor.process(data, reserve=lambda **_: pytest.fail("No over-budget reservation"))
    assert failed.status == "budget_exhausted" and failed.model_calls == 0


@pytest.mark.parametrize("phase,expected", [
    ("proposal", "b100be15646bdecb76b1d9001f38f6d183886bf77e7a6e47077ae3fb709baaa7"),
    ("verification", "c65ef79cd855131444bef02367432beb48b3595a45a5ed5578df93b859605fa5"),
])
def test_previous_source_view_contract_preserves_exact_wire_bytes(phase, expected):
    first, second, previous, proposed = continuation()
    _, result, requests = episode_process([first, second], proposals(episodes=[proposed]),
                                         [assessment(proposed["proposal_id"], 2)], episodes=(previous,),
                                         version=EPISODE_SOURCE_VIEW_INPUT_VERSION)
    assert result.status == "completed"
    request = next(item for item in requests if item.phase == phase)
    size = len(_json({"messages": request.messages, "response_schema": request.response_schema,
                      "max_output_tokens": request.max_output_tokens}).encode())
    assert digest({"messages": request.messages, "response_schema": request.response_schema,
                   "input_bytes": size}) == expected
    assert CAPABILITY_INSTRUCTIONS not in request.messages[0]["content"]


def test_new_capability_contract_keeps_source_view_and_all_original_source_qualifiers():
    first, second, previous, proposed = continuation()
    data, result, requests = episode_process([first, second], proposals(episodes=[proposed]),
                                           [assessment(proposed["proposal_id"], 2)], episodes=(previous,), version=VERSION)
    assert result.status == "completed" and len(requests) == 2
    for request in requests:
        assert WRONG_LABEL not in _json(request.messages)
        assert all(source["observed"]["content"]["text"] in request.messages[-1]["content"]
                   for source in (first, second))
    body = proposal_data(data.sources, [], {}, data.episodes, {}, [], scope=data.scope,
                         input_contract_version=VERSION)
    assert body["belief_relation_capabilities"]["endpoints"] == []
    assert body["episodes"][0]["view_version"] == EPISODE_SOURCE_VIEW_INPUT_VERSION
