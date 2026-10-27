import copy
import json

import pytest
from test_longitudinal_repairs import BLACKOUT, legacy_blackout
from test_semantic import batch, claim, encoded, proposals, reference, verifier

from membrain.citation_coverage import (
    CITATION_PROVENANCE_INPUT_VERSION,
    SOURCE_ACT_INPUT_VERSION,
)
from membrain.semantic import SemanticProcessor


@pytest.mark.parametrize("coverage", ["supports", "unknown"])
def test_new_contract_revalidates_legacy_declaration_through_both_independent_calls(coverage):
    source = legacy_blackout()
    before = copy.deepcopy(source)
    ref = reference(source)
    ref["span"]["end"] = len(BLACKOUT)
    proposed = claim(
        source,
        statement="The user reported Grandma lighting a lantern during power cuts.",
        claim_kind="reported_fact",
        memory_kind="episodic",
        conditions={"op": "always"},
        evidence_refs=[ref],
    )
    calls = []

    def propose(request):
        calls.append(request)
        return encoded(proposals(proposed))

    def verify(request):
        calls.append(request)
        response = json.loads(verifier(request))
        response["assessments"][0]["citation_coverage_verdict"] = coverage
        return encoded(response)

    result = SemanticProcessor(propose, verify).process(
        batch(source, input_contract_version=SOURCE_ACT_INPUT_VERSION), reserve=lambda **_: None
    )
    assert result.model_calls == len(calls) == 2
    assert len(result.claims) == (1 if coverage == "supports" else 0)
    proposal_view = json.loads(calls[0].messages[-1]["content"])["source_act_views"]
    verification_view = json.loads(calls[1].messages[-1]["content"])["source_act_views"]
    assert proposal_view == verification_view
    assert proposal_view[0]["content_sha256"] == source["content_sha256"]
    assert proposal_view[0]["segments"][0]["act"] == "direct_statement"
    assert proposal_view[0]["segments"][1]["act"] == "inquiry"
    assert source == before
    calls.clear()
    old = SemanticProcessor(propose, verify).process(
        batch(source, input_contract_version=CITATION_PROVENANCE_INPUT_VERSION),
        reserve=lambda **_: None,
    )
    assert old.model_calls == len(calls) == 1 and not old.claims
    assert old.rejected[0]["reason"] == "invalid_proposal"
