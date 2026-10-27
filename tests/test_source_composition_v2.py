import copy
import json
from dataclasses import replace

import pytest
from test_bounded_learning_semantics import local_batch, local_claim, publication_snapshot
from test_cognition import event
from test_semantic import encoded, proposals, verifier

from membrain.consolidation import _check_result
from membrain.helpers import DomainError, digest
from membrain.semantic import SemanticProcessor, proposal_schema
from membrain.source_composition import (
    COMPOSITION_V2_INPUT_VERSION,
    SOURCE_COMPOSITION_INPUT_VERSION,
    canonical_references,
)


def run(*, alias=False, version=COMPOSITION_V2_INPUT_VERSION, verdict="supports", mutate=None):
    source = event("Uncle Ito laid out the cloth; I stitched the seam.")
    data = local_batch(source, input_contract_version=version)
    item = local_claim(source, statement="The user reported that Uncle Ito laid out cloth and the user stitched the seam.")
    if alias:
        item["evidence_refs"][0]["revision"] = item["evidence_refs"][0].pop("event_revision")
    if mutate:
        mutate(item)
    raw = encoded(proposals(item))
    requests, operations = [], []

    def verify(request):
        requests.append(request)
        response = json.loads(verifier(request))
        if verdict is None:
            response["assessments"][0].pop("participant_actions_verdict", None)
        elif version == COMPOSITION_V2_INPUT_VERSION:
            response["assessments"][0]["participant_actions_verdict"] = verdict
        return encoded(response)

    result = SemanticProcessor(lambda _: raw, verify).process(
        data, reserve=lambda **_: None, on_call_result=lambda **value: operations.append(value))
    return data, result, requests, operations, raw


def test_declared_alias_is_losslessly_canonicalized_before_independent_verification():
    data, result, requests, operations, raw = run(alias=True)
    assert result.status == "completed" and result.model_calls == 2
    _check_result(data, result, publication_snapshot(data))
    ref = result.claims[0]["evidence_refs"][0]
    assert ref["event_revision"] == 1 and "revision" not in ref
    verified = json.loads(requests[0].messages[-1]["content"])["proposals"][0]["proposal"]
    assert verified["evidence_refs"][0] == ref
    assert operations[0]["response_fingerprint"] == digest(raw)
    assert '"revision"' in raw


def test_same_alias_remains_invalid_for_frozen_v1_contract():
    _, result, requests, _, _ = run(alias=True, version=SOURCE_COMPOSITION_INPUT_VERSION)
    assert result.status == "invalid_response" and result.model_calls == 1 and not requests


@pytest.mark.parametrize("value", [1, 2])
def test_both_spellings_are_rejected_even_when_values_agree(value):
    _, result, requests, _, _ = run(mutate=lambda item: item["evidence_refs"][0].update(revision=value))
    assert result.status == "invalid_response" and not requests and not result.claims


@pytest.mark.parametrize("change", [{"revision": 2}, {"source_content_sha256": "a" * 64}, {"event_id": "other"}])
def test_alias_never_bypasses_source_version_hash_or_identity_validation(change):
    _, result, requests, _, _ = run(alias=True, mutate=lambda item: item["evidence_refs"][0].update(change))
    assert result.status == "partial" and result.rejected[0]["reason"] == "invalid_proposal"
    assert result.model_calls == 1 and not requests


def test_normalization_does_not_touch_memory_versions_episode_members_or_literal_condition_values():
    literal = {"event_id": "x", "revision": 2, "source_content_sha256": "a" * 64,
               "relationship": "supports", "span": {"start": 0, "end": 1}}
    item = {"claims": [{"conditions": {"op": "eq", "key": "x", "value": literal},
                         "source_memory_versions": [{"memory_id": "memory", "revision": 3}]}],
            "episode_actions": [{"member_refs": [{"event_id": "event", "revision": 4}]}]}
    saved = copy.deepcopy(item)
    normalized, count = canonical_references(item)
    assert count == 0 and normalized == saved and item == saved
    old = proposal_schema("local-observation-v1", input_contract_version=SOURCE_COMPOSITION_INPUT_VERSION)
    ref = old["properties"]["claims"]["items"]["properties"]["evidence_refs"]["items"]
    assert "event_revision" in ref["required"] and "revision" not in ref["properties"]


@pytest.mark.parametrize("verdict", ["unknown", "contradicts"])
def test_participant_action_veto_applies_to_claims_even_with_supported_whole_claim_and_citations(verdict):
    _, result, _, _, _ = run(verdict=verdict)
    assert result.status == "partial" and not result.claims
    assert result.rejected == ({"proposal_id": "claim-1", "reason": "participant_actions_unconfirmed"},)


def test_missing_participant_facet_is_invalid_and_cannot_be_removed_at_publication():
    _, invalid, _, _, _ = run(verdict=None)
    assert invalid.status == "invalid_response" and not invalid.claims
    data, result, _, _, _ = run()
    _check_result(data, result, publication_snapshot(data))
    for verdict in (None, "unknown"):
        assessments = copy.deepcopy(result.verification_assessments)
        if verdict is None:
            assessments[0].pop("participant_actions_verdict")
        else:
            assessments[0]["participant_actions_verdict"] = verdict
        with pytest.raises(DomainError, match="Participant-action"):
            _check_result(data, replace(result, verification_assessments=assessments), publication_snapshot(data))
