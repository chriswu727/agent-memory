import copy
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from test_cognition import event, scope
from test_episode_semantics import action, existing
from test_semantic import batch, claim, encoded, episode, proposals

from agent_memory.consolidation import _check_result
from agent_memory.episode_semantics import (
    EPISODE_FIDELITY_INSTRUCTIONS,
    EPISODE_FIDELITY_VERIFY_INSTRUCTIONS,
    episode_assessment,
    episode_fidelity_schema,
    episode_fidelity_supported,
)
from agent_memory.helpers import DomainError, digest
from agent_memory.learning import _snapshot_input_contract
from agent_memory.semantic import (
    COMMUNICATION_INTENT_INPUT_VERSION,
    EPISODE_FIDELITY_INPUT_VERSION,
    EPISODE_SCOPE_INPUT_VERSION,
    EPISODE_SOURCE_VIEW_INPUT_VERSION,
    GENERAL_PROPOSAL_MODE,
    LATEST_INPUT_CONTRACT_VERSION,
    LINK_DERIVED_INPUT_VERSION,
    ORIENT_PROPOSAL_MODE,
    ORIENT_PROPOSAL_SCHEMA,
    ORIENT_VERIFICATION_SCHEMA,
    PROPOSAL_SCHEMA,
    RELATION_CAPABILITIES_INPUT_VERSION,
    VERIFICATION_SCHEMA,
    SemanticProcessor,
    SemanticResult,
    proposal_data,
    request_payload,
)

FACETS = ("attribution", "modality", "specificity", "boundary")
LEGACY_VERSIONS = (None, EPISODE_SCOPE_INPUT_VERSION, COMMUNICATION_INTENT_INPUT_VERSION)


def assessment(identifier="episode-1", count=1):
    return {"proposal_id": identifier, "proposal_verdict": "supports", "link_verdict": None,
            "relations": [{"evidence_index": index, "verdict": "supports"} for index in range(count)],
            "organization_fidelity": {facet: "supports" for facet in FACETS}}


def process(sources, proposed, assessments, *, episodes=(), version=EPISODE_FIDELITY_INPUT_VERSION):
    requests = []

    def propose(request):
        requests.append(request)
        return encoded(proposed)

    def verify(request):
        requests.append(request)
        return encoded({"assessments": assessments})

    data = batch(*sources, episodes=episodes, input_contract_version=version)
    result = SemanticProcessor(propose, verify).process(data, reserve=lambda **_: None)
    return data, result, requests


def snapshot(data):
    return {"events": {(source["event_id"], source["revision"]): source for source in data.sources},
            "assertions": {}, "superseded_evidence": []}


@pytest.mark.parametrize("facet", FACETS)
@pytest.mark.parametrize("verdict", ["unknown", "contradicts"])
def test_each_unsupported_fidelity_facet_vetoes_episode_without_rewriting_or_extra_calls(facet, verdict):
    source = event("I cannot confirm this was saved.")
    proposed = episode(source, organization_statement="The user said this was not saved.")
    checked = assessment()
    checked["organization_fidelity"][facet] = verdict
    saved = copy.deepcopy(proposed)
    _, result, requests = process([source], proposals(episodes=[proposed]), [checked])
    assert result.status == "partial" and result.model_calls == len(requests) == 2
    assert result.episode_actions == ()
    assert result.rejected == ({"proposal_id": proposed["proposal_id"], "reason": "semantic_support_unconfirmed"},)
    assert result.verification_assessments == (checked,)
    assert proposed == saved
    assert json.loads(requests[1].messages[1]["content"])["proposals"][0]["proposal"] == proposed


@pytest.mark.parametrize("facet", FACETS)
def test_missing_fidelity_facet_is_invalid_even_with_supported_whole_proposal_and_citations(facet):
    source = event("We discussed a study routine.")
    checked = assessment()
    del checked["organization_fidelity"][facet]
    _, result, requests = process([source], proposals(episodes=[episode(source)]), [checked])
    assert result.status == "invalid_response" and result.model_calls == len(requests) == 2
    assert not result.episode_actions


@pytest.mark.parametrize("malformed", [None, True, [], {}, "supports", {**dict.fromkeys(FACETS, "supports"), "extra": "supports"}])
def test_malformed_fidelity_object_fails_schema(malformed):
    source = event("We discussed a study routine.")
    checked = assessment()
    checked["organization_fidelity"] = malformed
    _, result, _ = process([source], proposals(episodes=[episode(source)]), [checked])
    assert result.status == "invalid_response" and not result.episode_actions


def test_entire_fidelity_object_is_required_for_new_version():
    source = event("We discussed a study routine.")
    checked = assessment()
    checked.pop("organization_fidelity")
    _, result, _ = process([source], proposals(episodes=[episode(source)]), [checked])
    assert result.status == "invalid_response" and not result.episode_actions


@pytest.mark.parametrize("source_text,statement,facet", [
    ("I cannot confirm it was saved.", "The user said it was not saved.", "modality"),
    ("It was not saved.", "The user was uncertain whether it was saved.", "modality"),
    ("We discussed a study routine.", "The user described an exam-preparation routine.", "specificity"),
    ("I attended a school; I did not provide its city.", "The user attended school in Paris.", "specificity"),
    ("My friend wondered whether the plan was saved.", "The user confirmed saving the plan.", "attribution"),
    ("We began discussing a plan.", "The completed external project forms one closed episode.", "boundary"),
])
def test_explicit_adverse_assessment_blocks_meaning_changes_with_authentic_complete_source(source_text, statement, facet):
    source = event(source_text)
    proposed = episode(source, organization_statement=statement)
    checked = assessment()
    checked["organization_fidelity"][facet] = "contradicts"
    _, result, requests = process([source], proposals(episodes=[proposed]), [checked])
    entry = json.loads(requests[1].messages[1]["content"])["proposals"][0]
    assert entry["evidence"][0]["source"] == source
    assert entry["evidence"][0]["excerpt"] == source_text
    assert entry["proposal"]["organization_statement"] == statement
    assert EPISODE_FIDELITY_INSTRUCTIONS in requests[0].messages[0]["content"]
    assert EPISODE_FIDELITY_VERIFY_INSTRUCTIONS in requests[1].messages[0]["content"]
    assert result.status == "partial" and not result.episode_actions


@pytest.mark.parametrize("source_text,statement", [
    ("I cannot confirm it was saved.", "The user reported uncertainty about whether it was saved."),
    ("It was not saved.", "The user reported that it was not saved."),
    ("We discussed a study routine.", "The user reported a discussion of a study routine."),
    ("Did we agree to meet?", "The user asked whether there had been an agreement to meet."),
])
def test_supported_attributed_meaning_retains_original_text_and_assessment(source_text, statement):
    source = event(source_text)
    proposed = episode(source, organization_statement=statement)
    checked = assessment()
    data, result, _ = process([source], proposals(episodes=[proposed]), [checked])
    assert result.status == "completed" and len(result.episode_actions) == 1
    assert result.episode_actions[0]["organization_statement"] == statement
    assert result.verification_assessments == (checked,)
    _check_result(data, result, snapshot(data))


@pytest.mark.parametrize("operation", ["continue", "close", "split", "reopen"])
def test_fidelity_uses_complete_old_and_new_members_for_each_transition(operation):
    first = event("We began discussing a study routine.")
    second = event("The discussion has ended; saving it remains unconfirmed.", sequence=2)
    previous = existing(first, state="closed" if operation == "reopen" else "open")
    proposed = action(second, action=operation, episode_id=previous["episode_id"], expected_revision=3,
                      boundary_ref={"event_id": second["event_id"], "revision": 1}
                      if operation in {"close", "split"} else None)
    data, result, requests = process([first, second], proposals(episodes=[proposed]),
                                     [assessment(proposed["proposal_id"], 2)], episodes=(previous,))
    entry = json.loads(requests[1].messages[1]["content"])["proposals"][0]
    assert [item["source"] for item in entry["evidence"]] == [first, second]
    assert result.status == "completed" and len(result.episode_actions) == 1
    _check_result(data, result, snapshot(data))
    incomplete = replace(result, verification_assessments=(assessment(proposed["proposal_id"], 1),))
    with pytest.raises(DomainError, match="Episode fidelity"):
        _check_result(data, incomplete, snapshot(data))


def test_episode_veto_does_not_reject_separately_supported_claim():
    source = event("The user reports an unconfirmed saving status.")
    proposed_claim = claim(source, statement="The user reports that saving is unconfirmed.",
                           claim_kind="reported_fact", conditions={"op": "always"})
    claim_assessment = assessment(proposed_claim["proposal_id"])
    claim_assessment.pop("organization_fidelity")
    checked = assessment()
    checked["organization_fidelity"]["modality"] = "unknown"
    _, result, _ = process([source], proposals(proposed_claim, episodes=[episode(source)]),
                           [claim_assessment, checked])
    assert result.status == "partial" and len(result.claims) == 1 and not result.episode_actions


def test_schema_requires_fidelity_only_for_episode_ids_without_mutating_legacy_schema():
    original = copy.deepcopy(VERIFICATION_SCHEMA)
    schema = episode_fidelity_schema(VERIFICATION_SCHEMA, ["episode-1"])
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"assessments": [assessment()]})
    non_episode = assessment("claim-1")
    assert not validator.is_valid({"assessments": [non_episode]})
    non_episode.pop("organization_fidelity")
    assert validator.is_valid({"assessments": [non_episode]})
    assert VERIFICATION_SCHEMA == original
    assert episode_fidelity_schema(VERIFICATION_SCHEMA, []) == original
    assert not Draft202012Validator(original).is_valid({"assessments": [assessment()]})


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_id", "facet_unknown", "facet_extra",
                                     "whole_unknown", "link", "missing_relation", "duplicate_index",
                                     "bool_index", "float_index", "wrong_relation", "extra_assessment_field"])
def test_publication_cannot_bypass_fidelity_with_a_supported_marker(mutation):
    first, second = event("I cannot confirm saving."), event("We discussed a routine.", sequence=2)
    proposed = action(first, second)
    data, result, _ = process([first, second], proposals(episodes=[proposed]),
                              [assessment(proposed["proposal_id"], 2)])
    checked = copy.deepcopy(result.verification_assessments[0])
    ledger = [checked]
    if mutation == "missing":
        ledger = []
    elif mutation == "duplicate":
        ledger *= 2
    elif mutation == "wrong_id":
        checked["proposal_id"] = "other-episode"
    elif mutation == "facet_unknown":
        checked["organization_fidelity"]["modality"] = "unknown"
    elif mutation == "facet_extra":
        checked["organization_fidelity"]["extra"] = "supports"
    elif mutation == "whole_unknown":
        checked["proposal_verdict"] = "unknown"
    elif mutation == "link":
        checked["link_verdict"] = "supports"
    elif mutation == "missing_relation":
        checked["relations"].pop()
    elif mutation == "duplicate_index":
        checked["relations"][1]["evidence_index"] = 0
    elif mutation == "bool_index":
        checked["relations"][1]["evidence_index"] = True
    elif mutation == "float_index":
        checked["relations"][1]["evidence_index"] = 1.0
    elif mutation == "wrong_relation":
        checked["relations"][1]["verdict"] = "unknown"
    else:
        checked["request_verdict"] = "supports"
    with pytest.raises(DomainError, match="Episode fidelity"):
        _check_result(data, replace(result, verification_assessments=tuple(ledger)), snapshot(data))


def test_unknown_contract_cannot_bypass_publication_guard():
    data = batch(event("An observed contribution."), input_contract_version="unrecognized-fidelity-version")
    with pytest.raises(DomainError, match="Unknown cognitive input contract"):
        _check_result(data, SemanticResult("completed"), snapshot(data))


@pytest.mark.parametrize("version", LEGACY_VERSIONS)
def test_legacy_episode_assessments_remain_accepted_without_new_labels(version):
    source = event("An observed contribution.")
    checked = assessment()
    checked.pop("organization_fidelity")
    data, result, requests = process([source], proposals(episodes=[episode(source)]), [checked], version=version)
    assert result.status == "completed" and len(result.episode_actions) == 1
    assert requests[1].response_schema == VERIFICATION_SCHEMA
    assert all(EPISODE_FIDELITY_INSTRUCTIONS not in request.messages[0]["content"] for request in requests)
    _check_result(data, result, snapshot(data))
    _check_result(data, replace(result, verification_assessments=()), snapshot(data))


def test_new_version_is_explicit_and_preserves_existing_default_and_current_request_contract():
    source = event("Could you explain the routine?")
    assert LATEST_INPUT_CONTRACT_VERSION == "source-composition-v3"
    assert LINK_DERIVED_INPUT_VERSION == "history-link-coverage-v1"
    assert batch(source).input_contract_version == COMMUNICATION_INTENT_INPUT_VERSION
    for mode in (GENERAL_PROPOSAL_MODE, ORIENT_PROPOSAL_MODE):
        old = proposal_data([source], [], {}, [], {}, [], scope=scope(), proposal_mode=mode)
        new = proposal_data([source], [], {}, [], {}, [], scope=scope(), proposal_mode=mode,
                            input_contract_version=EPISODE_FIDELITY_INPUT_VERSION)
        assert old == new
    for phase, schema in (("proposal", ORIENT_PROPOSAL_SCHEMA), ("verification", ORIENT_VERIFICATION_SCHEMA)):
        old = request_payload(phase, {}, schema, 4096, proposal_mode=ORIENT_PROPOSAL_MODE)
        new = request_payload(phase, {}, schema, 4096, proposal_mode=ORIENT_PROPOSAL_MODE,
                              input_contract_version=EPISODE_FIDELITY_INPUT_VERSION)
        assert new == old
    assert _snapshot_input_contract({}) is None
    for version in (*LEGACY_VERSIONS, EPISODE_FIDELITY_INPUT_VERSION, EPISODE_SOURCE_VIEW_INPUT_VERSION,
                    RELATION_CAPABILITIES_INPUT_VERSION, LINK_DERIVED_INPUT_VERSION):
        assert _snapshot_input_contract({"input_contract_version": version}) == version


LEGACY_WIRE_HASHES = {
    (None, "general-v1", "proposal"): "5985394810e25efc968a0aac08634d782365186fd53c482cd97ee4ec1d33af77",
    (None, "general-v1", "verification"): "2d16a9a5643e6dcefab02a1c1aac0f25c0e53664594326642f423b69a0103a20",
    (None, "orient-v1", "proposal"): "a1553cf81b4d6a7b767f0104137b431fa33ad1aada7de27b056dda76aa7b9678",
    (None, "orient-v1", "verification"): "a0d51800933c97bb489510790e301fcfe28bb5977b1ea39755ff9a2e60c6df24",
    (EPISODE_SCOPE_INPUT_VERSION, "general-v1", "proposal"): "814b8e98c248313aaa806b983c90e015a6e1b5ad6b8855f1c3b437915338f82a",
    (EPISODE_SCOPE_INPUT_VERSION, "general-v1", "verification"): "2d16a9a5643e6dcefab02a1c1aac0f25c0e53664594326642f423b69a0103a20",
    (EPISODE_SCOPE_INPUT_VERSION, "orient-v1", "proposal"): "a1553cf81b4d6a7b767f0104137b431fa33ad1aada7de27b056dda76aa7b9678",
    (EPISODE_SCOPE_INPUT_VERSION, "orient-v1", "verification"): "a0d51800933c97bb489510790e301fcfe28bb5977b1ea39755ff9a2e60c6df24",
    (COMMUNICATION_INTENT_INPUT_VERSION, "general-v1", "proposal"): "9c62433026bb824ab57a58ddc91e3e1256e0665e837df979a261303d31cb0b89",
    (COMMUNICATION_INTENT_INPUT_VERSION, "general-v1", "verification"): "1186019e5d173204d48ea8de2ce9d5c5db5be7765b06c557247f77639828400b",
    (COMMUNICATION_INTENT_INPUT_VERSION, "orient-v1", "proposal"): "11d1331feb3cf4509b69667c57775f412b6f4b6fe61ceff19a0d3defbba3c538",
    (COMMUNICATION_INTENT_INPUT_VERSION, "orient-v1", "verification"): "364122c49bf3c4d67cd41a425ade9a2052d389467e60ff0443f7ca84115afa94",
}


@pytest.mark.parametrize("version,mode,phase", LEGACY_WIRE_HASHES)
def test_all_legacy_request_schema_and_byte_counts_match_prechange_goldens(version, mode, phase):
    source = event("I cannot confirm it was saved. We discussed a study routine.")
    proposed = episode(source, organization_statement=(
        "The user reported uncertainty about saving during a discussion of a study routine."))
    assessed = episode_assessment(proposed, scope(), {(source["event_id"], source["revision"]): source}, {})
    entry = {"group": "episode_actions", "proposal": proposed,
             "evidence": [{"evidence_index": 0, "reference": assessed["references"][0],
                           "source": source, "excerpt": source["observed"]["content"]["text"]}],
             "episode_context": assessed["context"]}
    if phase == "proposal":
        schema = ORIENT_PROPOSAL_SCHEMA if mode == ORIENT_PROPOSAL_MODE else PROPOSAL_SCHEMA
        body = proposal_data([source], [], {}, [], {}, [], scope=scope(), proposal_mode=mode,
                             input_contract_version=version)
    else:
        schema = ORIENT_VERIFICATION_SCHEMA if mode == ORIENT_PROPOSAL_MODE else VERIFICATION_SCHEMA
        body = {"proposals": [] if mode == ORIENT_PROPOSAL_MODE else [entry]}
    messages, size = request_payload(phase, body, schema, 8192, proposal_mode=mode, input_contract_version=version)
    assert digest({"messages": messages, "response_schema": schema, "input_bytes": size}) == (
        LEGACY_WIRE_HASHES[version, mode, phase])


def test_new_request_identity_changes_and_legacy_schema_stays_immutable_after_new_call():
    source = event("I cannot confirm it was saved.")
    proposed = proposals(episodes=[episode(source)])
    checked = assessment()
    saved_schema = copy.deepcopy(VERIFICATION_SCHEMA)
    _, _, new = process([source], proposed, [checked])
    checked.pop("organization_fidelity")
    _, _, old = process([source], proposed, [checked], version=COMMUNICATION_INTENT_INPUT_VERSION)
    assert [request.request_id for request in new] != [request.request_id for request in old]
    assert old[1].response_schema == VERIFICATION_SCHEMA == saved_schema
    assert old[1].response_schema != new[1].response_schema
    assert episode_fidelity_supported(assessment(), "episode-1", [{"relationship": "supports"}])
