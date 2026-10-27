import copy
import json
from dataclasses import replace

import pytest
from test_bounded_learning_semantics import local_claim, process, publication_snapshot
from test_citation_coverage import case
from test_cognition import event
from test_semantic import batch, encoded, episode, proposals, reference, verifier

from membrain.citation_coverage import (
    CITATION_PROVENANCE_INPUT_VERSION,
    CITATION_PROVENANCE_VERIFY_INSTRUCTIONS,
)
from membrain.consolidation import _check_result
from membrain.helpers import DomainError, digest
from membrain.semantic import SemanticPolicy, SemanticProcessor, _json
from membrain.verification_sources import (
    SHARED_VERIFICATION_INPUT_VERSION as VERSION,
)
from membrain.verification_sources import (
    SHARED_VERIFICATION_INSTRUCTIONS,
    shared_verification_sources,
)

LEGACY_PROVENANCE_HASHES = {
    "general": ("7876b7255f78a0444335ea44261ad9898d63ff81f8702ee23630ccd0186cbfe5",
                "4a2d8099497493d58482b7c7be3f1f593b5a4eb2ad620a9bcfab69d9f13588a1"),
    "local": ("fa96dbf6544dbac9cac0712b9bbbfbae6bffdbbbe43d10e111e92373bd96aae9",
              "be9d553f46ba2f7ae8d3e40edef81882a776a473fb109ee7095c5f3d72d77566"),
    "orient": ("e266873ff1a61c8fead3c28b6aa51bc72e71b61d778e2ea2b8d0f3f1f733538c",
               "5622abc5d54ee033eae31fe25d09652accf597c16815b9bad725f4677f2f20ba"),
    "situation": ("7876b7255f78a0444335ea44261ad9898d63ff81f8702ee23630ccd0186cbfe5",
                  "02e58713014182c1e23eb0125cc54cef92b818f9685c26c928bd8cae9799b3a3"),
    "history": ("0e9f2e02020690b037791bad53ee3b917f713bb3b290b240f93b564e5e80240a",
                "21a2376620079e0069450f3430639e1606196a0c22b413171692c20cd1371ffc"),
}


def expand_sources(body):
    original = copy.deepcopy(body)
    sources = {(source["event_id"], source["revision"]): source
               for source in original.pop("verification_sources")}
    for entry in original["proposals"]:
        for evidence in entry["evidence"]:
            ref = evidence.pop("source_ref")
            evidence["source"] = copy.deepcopy(sources[(ref["event_id"], ref["revision"])])
    return original


@pytest.mark.parametrize("kind", LEGACY_PROVENANCE_HASHES)
def test_old_provenance_requests_remain_byte_identical_and_new_contract_retains_gates(kind):
    data, proposed, _ = case(kind)
    old_data = replace(data, input_contract_version=CITATION_PROVENANCE_INPUT_VERSION)
    old_result, old_requests = process(old_data, proposed)
    assert old_result.status == "completed"
    assert tuple(digest({"messages": request.messages, "response_schema": request.response_schema})
                 for request in old_requests) == LEGACY_PROVENANCE_HASHES[kind]
    new_data = replace(data, input_contract_version=VERSION)
    result, requests = process(new_data, proposed)
    assert result.status == "completed" and result.model_calls == len(requests) == 2
    assert requests[0].messages == old_requests[0].messages
    assert requests[1].response_schema == old_requests[1].response_schema
    old = json.loads(old_requests[1].messages[-1]["content"])
    new = json.loads(requests[1].messages[-1]["content"])
    if kind in {"general", "orient", "situation"}:
        assert SHARED_VERIFICATION_INSTRUCTIONS in requests[1].messages[0]["content"]
        new = expand_sources(new)
    else:
        assert "verification_sources" not in new
        assert SHARED_VERIFICATION_INSTRUCTIONS not in requests[1].messages[0]["content"]
    for entry in new["proposals"]:
        if "citation_scope" in entry:
            assert entry["citation_scope"]["version"] == VERSION
            entry["citation_scope"]["version"] = CITATION_PROVENANCE_INPUT_VERSION
    assert new == old
    assert CITATION_PROVENANCE_VERIFY_INSTRUCTIONS in requests[1].messages[0]["content"]
    _check_result(new_data, result, publication_snapshot(new_data), current_input_refs=new_data.current_input_refs)


def inline_data(source):
    return {"proposals": [{"group": "claims", "proposal": {"proposal_id": "one"}, "evidence": [
        {"evidence_index": 0, "reference": reference(source), "source": copy.deepcopy(source),
         "excerpt": source["observed"]["content"]["text"], "role": "shared_claim_candidate"},
        {"evidence_index": 1, "reference": reference(source, "contradicts"), "source": copy.deepcopy(source),
         "excerpt": source["observed"]["content"]["text"], "role": "retained_endpoint_context"},
    ]}]}


def test_projection_is_lossless_and_does_not_reinterpret_nested_untrusted_objects():
    source = event("A report, not an established outcome.")
    source["observed"]["content"]["structured"] = {"untrusted_source": event("A quoted fake record.", 2)}
    source["content_sha256"] = digest(source["observed"]["content"])
    data = inline_data(source)
    original = copy.deepcopy(data)
    projected = shared_verification_sources(data)
    assert projected["verification_sources"] == [source]
    assert expand_sources(projected) == original and data == original
    projected["verification_sources"][0]["observed"]["actor_ref"] = "changed"
    assert data == original


@pytest.mark.parametrize("damage", ["collision", "event_id", "revision", "hash", "source_hash", "span", "excerpt",
                                    "existing_ref", "existing_table"])
def test_projection_rejects_ambiguous_or_mismatched_source_bindings(damage):
    data = inline_data(event("A complete source with its qualifications."))
    evidence = data["proposals"][0]["evidence"][1]
    if damage == "collision":
        evidence["source"]["observed"]["actor_ref"] = "a different source envelope"
    elif damage == "event_id":
        evidence["reference"]["event_id"] = "another-source"
    elif damage == "revision":
        evidence["reference"]["event_revision"] = 2
    elif damage == "hash":
        evidence["reference"]["source_content_sha256"] = "0" * 64
    elif damage == "source_hash":
        for item in data["proposals"][0]["evidence"]:
            item["source"]["content_sha256"] = item["reference"]["source_content_sha256"] = "0" * 64
    elif damage == "span":
        evidence["reference"]["span"]["end"] += 1
    elif damage == "excerpt":
        evidence["excerpt"] = "An invented excerpt."
    elif damage == "existing_ref":
        evidence["source_ref"] = {"event_id": "another-source", "revision": 1}
    else:
        data["verification_sources"] = []
    original = copy.deepcopy(data)
    with pytest.raises((ValueError, DomainError)):
        shared_verification_sources(data)
    assert data == original


def repeated_source_case():
    source = event("I reported " + "an extended and carefully qualified account " * 60)
    claims = [local_claim(source, proposal_id=f"claim-{index}") for index in range(6)]
    return batch(source, input_contract_version=VERSION), proposals(*claims, episodes=[episode(source)])


def payload_bytes(request):
    return len(_json({"messages": request.messages, "response_schema": request.response_schema,
                      "max_output_tokens": request.max_output_tokens}).encode())


def test_all_sources_and_evidence_survive_compaction_with_actual_budget_accounting():
    data, proposed = repeated_source_case()
    result, requests = process(data, proposed)
    assert result.status == "completed" and len(result.claims) == 6 and len(result.episode_actions) == 1
    body = json.loads(requests[1].messages[-1]["content"])
    assert body["verification_sources"] == list(data.sources)
    assert len(body["proposals"]) == 7
    assert all(entry["evidence"][0]["source_ref"] == {"event_id": "event-1", "revision": 1}
               for entry in body["proposals"])
    old_result, old_requests = process(replace(data, input_contract_version=CITATION_PROVENANCE_INPUT_VERSION), proposed)
    assert old_result.status == "completed"
    assert payload_bytes(requests[1]) < payload_bytes(old_requests[1]) * 0.8
    assert result.input_bytes == sum(payload_bytes(request) for request in requests)

    def run_at(limit):
        calls, reservations = [], []

        def propose(request):
            calls.append(request)
            return encoded(proposed)

        def verify(request):
            calls.append(request)
            return verifier(request)

        processor = SemanticProcessor(propose, verify, SemanticPolicy(max_input_bytes=limit))
        outcome = processor.process(data, reserve=lambda **row: reservations.append(row))
        return outcome, calls, reservations

    completed, calls, reservations = run_at(result.input_bytes)
    assert completed.status == "completed" and completed.model_calls == len(calls) == len(reservations) == 2
    assert sum(row["input_bytes"] for row in reservations) == result.input_bytes
    exhausted, calls, reservations = run_at(result.input_bytes - 1)
    assert exhausted.status == "budget_exhausted" and len(calls) == len(reservations) == 1
    assert not exhausted.claims and not exhausted.episode_actions
