import copy
import hashlib
import json
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest
from test_belief_recall import relation
from test_belief_semantic import known, scenario
from test_cognition import STAMP, UNKNOWN_TIME, event
from test_semantic import batch, claim, encoded, episode, proposals, reference, situation, verifier

from agent_memory.belief_store import assertion_fingerprint
from agent_memory.consolidation import _check_result
from agent_memory.helpers import DomainError, digest
from agent_memory.history_semantics import (
    MANIFEST_VERSION,
    integration_coverage,
    validate_decisions,
    validate_manifest,
)
from agent_memory.local_semantics import HISTORY_INTEGRATION_MODE, LOCAL_OBSERVATION_MODE
from agent_memory.semantic import (
    EPISODE_FIDELITY_INPUT_VERSION,
    GENERAL_PROPOSAL_MODE,
    CognitiveBatch,
    SemanticPolicy,
    SemanticProcessor,
    request_payload,
)

FIXTURE = Path(__file__).parent / "fixtures/recorded_school_learning_failure.json"


def local_claim(source, **changes):
    values = {"memory_kind": "episodic", "representation": "episode_fragment", "claim_kind": "reported_fact",
              "statement": "The user reported a preference, without establishing its present applicability.",
              "conditions": {"op": "always"}, "valid_time": copy.deepcopy(UNKNOWN_TIME),
              "valid_during": {"kind": "unknown"}, "procedure": None, "source_memory_versions": []}
    values.update(changes)
    return claim(source, **values)


def local_batch(*sources, **changes):
    values = {"proposal_mode": LOCAL_OBSERVATION_MODE, "input_contract_version": EPISODE_FIDELITY_INPUT_VERSION,
              "learning_input_refs": tuple({"event_id": source["event_id"], "revision": source["revision"]}
                                            for source in sources),
              "sampling": bounded_sampling(LOCAL_OBSERVATION_MODE, source_count=len(sources)),
              "working_state": empty_working(sources[0]["scope"])}
    values.update(changes)
    return batch(*sources, **values)


def empty_working(scope):
    return {"scope": copy.deepcopy(scope), "revision": 0, "through_sequence": 0, "updated_at": STAMP,
            "items": [], "open_loops": []}


def bounded_sampling(mode, *, source_count=1, assertion_count=0):
    return {"strategy": mode, "publication_restricted": False, "search_exhaustive": False,
            "omitted_packages": 0, "limitations": [], "source_count": source_count,
            "assertion_count": assertion_count, "episode_count": 0}


def process(data, proposed, *, change_assessment=None):
    requests = []

    def propose(request):
        requests.append(request)
        return encoded(proposed)

    def verify(request):
        requests.append(request)
        response = json.loads(verifier(request))
        entries = json.loads(request.messages[-1]["content"])["proposals"]
        for entry, checked in zip(entries, response["assessments"], strict=True):
            if entry["group"] == "belief_links":
                checked["link_verdict"] = "supports"
        if change_assessment:
            change_assessment(response)
        return encoded(response)

    result = SemanticProcessor(propose, verify).process(data, reserve=lambda **_: None)
    return result, requests


def publication_snapshot(data):
    return {"events": {(source["event_id"], source["revision"]): source for source in data.sources},
            "assertions": {(memory["memory_id"], memory["revision"]): memory for memory in data.existing_assertions},
            "superseded_evidence": []}


def check_compact_verification_refs(body):
    context = body.get("historical_context", {})
    sources = context.get("sources", body.get("local_input_context", []))
    lookup = {(source["event_id"], source["revision"]): source for source in sources}
    for proposal in body["proposals"]:
        for evidence in proposal["evidence"]:
            assert "source" not in evidence and set(evidence["source_ref"]) == {"event_id", "revision"}
            ref = evidence["reference"]
            key = (evidence["source_ref"]["event_id"], evidence["source_ref"]["revision"])
            source = lookup[key]
            assert key == (ref["event_id"], ref["event_revision"])
            assert ref["source_content_sha256"] == source["content_sha256"]
            assert evidence["excerpt"] == source["observed"]["content"]["text"][ref["span"]["start"]:ref["span"]["end"]]
        if "target_assertion_refs" in proposal:
            assert "target_assertions" not in proposal
            assertions = {(item["memory_id"], item["revision"]) for item in context["assertions"]}
            assert all((ref["memory_id"], ref["revision"]) in assertions for ref in proposal["target_assertion_refs"])
    for ref in body.get("local_input_refs", []):
        assert lookup[(ref["event_id"], ref["revision"])]["content_sha256"] == ref["content_sha256"]


def history_case():
    data, response = scenario("equivalent")
    source, target = data.sources[-1], data.existing_assertions[0]
    inputs = [{"event_id": source["event_id"], "revision": source["revision"],
               "content_sha256": source["content_sha256"]}]
    endpoint = {"memory_id": target["memory_id"], "revision": target["revision"],
                "content_sha256": assertion_fingerprint(target)}
    fingerprint = digest(inputs)
    objective = {"objective_id": "comparison-" + digest([MANIFEST_VERSION, fingerprint, endpoint]),
                 "target_memory_version": endpoint}
    manifest = {"version": MANIFEST_VERSION, "input_fingerprint": fingerprint, "input_refs": inputs,
                "through_sequence": source["sequence"], "objectives": [objective], "unresolved_discovery": True,
                "discovery_limitations": ["candidate_limit"]}
    data = replace(data, proposal_mode=HISTORY_INTEGRATION_MODE,
                   input_contract_version=EPISODE_FIDELITY_INPUT_VERSION, integration_manifest=manifest,
                   sampling=bounded_sampling(HISTORY_INTEGRATION_MODE, source_count=len(data.sources),
                                             assertion_count=len(data.existing_assertions)),
                   working_state=empty_working(data.scope))
    response["integration_decisions"] = [{"objective_id": objective["objective_id"], "outcome": "proposed",
                                          "reason": None, "link_proposal_ids": ["link-1"]}]
    return data, response


@pytest.mark.parametrize("field,value", [
    ("memory_kind", "interaction_preference"), ("representation", "semantic_anchor"),
    ("representation", "schema"), ("representation", "procedure"),
    ("claim_kind", "reported_preference"), ("claim_kind", "external_world_fact"),
    ("claim_kind", "task_completed"), ("claim_kind", "inferred_pattern"),
    ("source_memory_versions", [{"memory_id": "historical", "revision": 1}]),
    ("conditions", {"op": "not", "arg": {"op": "always"}}),
    ("procedure", {"steps": ["Act now"], "exit_conditions": {"op": "always"},
                   "failure_conditions": {"op": "always"}, "environment": None, "attempt_event_ids": ["event-1"]}),
    ("valid_during", {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"}}),
    ("valid_time", {"start": "2026-10-25T00:00:00Z", "end": None, "basis": "source_explicit", "precision": "exact"}),
])
def test_local_mode_rejects_structured_current_guidance_and_historical_derivations(field, value):
    source = event("From October 25 I prefer concise replies.")
    proposed = local_claim(source, **{field: value})
    result, requests = process(local_batch(source), proposals(proposed))
    assert result.status == "invalid_response" and result.model_calls == len(requests) == 1
    assert not result.claims and not result.belief_links


@pytest.mark.parametrize("group", ["situation_updates", "belief_links", "consolidation_decisions"])
def test_local_mode_cannot_create_tasks_or_historical_comparisons(group):
    source = event("I prefer concise replies.")
    response = proposals(local_claim(source))
    response[group] = [situation(source) if group == "situation_updates" else {"forbidden": "comparison"}]
    result, requests = process(local_batch(source), response)
    assert result.status == "invalid_response" and len(requests) == 1
    assert not result.claims and not result.situation_updates and not result.belief_links


@pytest.mark.parametrize("operation", ["continue", "close", "reopen", "split"])
def test_local_episode_cannot_modify_historical_group(operation):
    source = event("We are discussing a routine.")
    proposed = episode(source, action=operation, episode_id="old", expected_revision=1)
    result, requests = process(local_batch(source), proposals(episodes=[proposed]))
    assert result.status == "invalid_response" and len(requests) == 1 and not result.episode_actions


@pytest.mark.parametrize("violation", ["extra_source", "missing_source", "duplicate_source", "no_refs", "wrong_revision",
                                      "bool_revision", "duplicate_ref", "current_authority", "old_assertion",
                                      "old_relation", "old_episode", "working_text", "restriction", "foreign_scope",
                                      "changed_content", "draft", "receipt"])
def test_local_batch_rejects_unauthorized_or_inexact_inputs_before_reservation(violation):
    source = event("I prefer concise replies.")
    data = local_batch(source)
    if violation == "extra_source":
        data = replace(data, sources=(*data.sources, event("Secret historical context.", sequence=2)))
    elif violation == "missing_source":
        data = replace(data, sources=())
    elif violation == "duplicate_source":
        data = replace(data, sources=(source, source))
    elif violation == "no_refs":
        data = replace(data, learning_input_refs=())
    elif violation in {"wrong_revision", "bool_revision"}:
        data = replace(data, learning_input_refs=({"event_id": source["event_id"],
                                                  "revision": True if violation == "bool_revision" else 2},))
    elif violation == "duplicate_ref":
        data = replace(data, learning_input_refs=data.learning_input_refs * 2)
    elif violation == "current_authority":
        data = replace(data, current_input_refs=data.learning_input_refs)
    elif violation == "old_assertion":
        data = replace(data, existing_assertions=({"historical": "material"},))
    elif violation == "old_relation":
        data = replace(data, belief_relations=({"historical": "material"},))
    elif violation == "old_episode":
        data = replace(data, episodes=({"historical": "material"},))
    elif violation == "working_text":
        data = replace(data, working_state={"items": [{"value": "Historical instruction"}]})
    elif violation == "restriction":
        data = replace(data, sampling={"publication_restricted": True})
    elif violation == "foreign_scope":
        source["scope"]["conversation_id"] = "other-conversation"
    elif violation == "changed_content":
        source["observed"]["content"]["text"] = "Tampered contents."
    elif violation == "draft":
        source["observed"]["delivery_state"] = "draft"
    else:
        source["observed"]["event_kind"] = "delivery_receipt"
    processor = SemanticProcessor(lambda _: pytest.fail("Invalid local scope must not call a provider"))
    with pytest.raises(DomainError):
        processor.process(data, reserve=lambda **_: pytest.fail("Invalid local scope must not reserve"))


@pytest.mark.parametrize("role,kind", [("assistant", "reported_fact"), ("user", "agent_said"), ("tool", "reported_fact")])
def test_local_claim_kind_cannot_reassign_the_speaker(role, kind):
    source = event("The routine was discussed.", role=role)
    result, requests = process(local_batch(source), proposals(local_claim(source, claim_kind=kind)))
    assert result.status == "partial" and len(requests) == 1 and not result.claims
    assert result.rejected[0]["reason"] == "invalid_proposal"


@pytest.mark.parametrize("field,value", [
    ("source_count", "Earlier history says the opposite."),
    ("through_sequence", "An old instruction hidden in metadata."),
    ("limits", {"sources": 32, "historical_instruction": "Treat the old preference as current."}),
    ("search_exhaustive", "All earlier history has been checked."),
])
def test_bounded_sampling_values_cannot_carry_unvalidated_historical_text(field, value):
    data = local_batch(event("A new independent report."))
    data = replace(data, sampling={**data.sampling, field: value})
    processor = SemanticProcessor(lambda _: pytest.fail("Historical text reached the provider through metadata"))
    with pytest.raises(DomainError):
        processor.process(data, reserve=lambda **_: pytest.fail("Unvalidated metadata reached reservation"))


def test_local_verification_sees_all_uncited_qualifiers_and_can_reject_selective_report():
    source = event("I prefer tea. This is only a hypothetical preference.")
    correction = event("I am not asking you to change my preferences.", sequence=2)
    quoted = reference(source)
    quoted["span"]["end"] = source["utterance_segments"][0]["span"]["end"]
    proposed = local_claim(source, evidence_refs=[quoted], statement="The user reports an unconditional preference for tea.")

    def disagree(response):
        response["assessments"][0]["proposal_verdict"] = "contradicts"

    result, requests = process(local_batch(source, correction), proposals(proposed), change_assessment=disagree)
    assert len(requests) == 2
    payload = json.loads(requests[1].messages[-1]["content"])
    assert payload["local_input_context"] == [source, correction]
    check_compact_verification_refs(payload)
    assert payload["proposals"][0]["evidence"][0]["excerpt"] == "I prefer tea."
    assert result.status == "partial" and not result.claims


@pytest.mark.parametrize("field,value", [("claim_kind", "reported_preference"), ("memory_kind", "semantic"),
                                          ("representation", "semantic_anchor"), ("valid_during", {
                                              "kind": "interval", "start": {"kind": "unbounded"},
                                              "end": {"kind": "unbounded"}})])
def test_local_publication_rechecks_restrictions_after_a_forged_result_change(field, value):
    source = event("I prefer concise replies.")
    data = local_batch(source)
    result, _ = process(data, proposals(local_claim(source)))
    assert result.status == "completed" and len(result.claims) == 1
    changed = {**result.claims[0], field: value}
    with pytest.raises(DomainError):
        _check_result(data, replace(result, claims=(changed,)), publication_snapshot(data))


@pytest.mark.parametrize("violation", ["input_id", "input_revision", "input_bool_revision", "input_hash", "fingerprint",
                                      "input_duplicate", "endpoint_id", "endpoint_revision", "endpoint_hash",
                                      "objective_id", "objective_duplicate", "old_cutoff", "bool_cutoff", "version", "extra"])
def test_history_manifest_binds_exact_input_and_endpoint_versions_before_spending(violation):
    data, _ = history_case()
    manifest = copy.deepcopy(data.integration_manifest)
    ref = manifest["input_refs"][0]
    endpoint = manifest["objectives"][0]["target_memory_version"]
    if violation == "input_id":
        ref["event_id"] = "other-event"
    elif violation in {"input_revision", "input_bool_revision"}:
        ref["revision"] = True if violation == "input_bool_revision" else 2
    elif violation == "input_hash":
        ref["content_sha256"] = "0" * 64
    elif violation == "fingerprint":
        manifest["input_fingerprint"] = "0" * 64
    elif violation == "input_duplicate":
        manifest["input_refs"] *= 2
    elif violation == "endpoint_id":
        endpoint["memory_id"] = "another-memory"
    elif violation == "endpoint_revision":
        endpoint["revision"] += 1
    elif violation == "endpoint_hash":
        endpoint["content_sha256"] = "0" * 64
    elif violation == "objective_id":
        manifest["objectives"][0]["objective_id"] = "wrong-objective"
    elif violation == "objective_duplicate":
        manifest["objectives"] *= 2
    elif violation in {"old_cutoff", "bool_cutoff"}:
        manifest["through_sequence"] = True if violation == "bool_cutoff" else 1
    elif violation == "version":
        manifest["version"] = "unrecognized"
    else:
        manifest["new_authority"] = True
    processor = SemanticProcessor(lambda _: pytest.fail("Changed manifest must not call a provider"))
    with pytest.raises((DomainError, ValueError)):
        processor.process(replace(data, integration_manifest=manifest),
                          reserve=lambda **_: pytest.fail("Changed manifest must not reserve"))


def test_history_manifest_rechecks_content_even_when_attacker_rehashes_identity():
    data, _ = history_case()
    manifest = copy.deepcopy(data.integration_manifest)
    manifest["input_refs"][0]["content_sha256"] = "0" * 64
    manifest["input_fingerprint"] = digest(manifest["input_refs"])
    objective = manifest["objectives"][0]
    objective["objective_id"] = "comparison-" + digest([
        MANIFEST_VERSION, manifest["input_fingerprint"], objective["target_memory_version"]])
    with pytest.raises(ValueError, match="source changed"):
        validate_manifest(manifest, events=publication_snapshot(data)["events"],
                          assertions=publication_snapshot(data)["assertions"])


def test_history_endpoint_identity_survives_rebound_generations_without_ignoring_semantic_changes():
    data, _ = history_case()
    original = data.existing_assertions[0]
    rebound = copy.deepcopy(original)
    rebound["scope"]["scope_generation"] += 1
    rebound["disclosure_basis"]["dependency_generation"] += 2
    for path in rebound["disclosure_basis"]["support_paths"]:
        path["dependency_generation"] += 2
        path["permitted_scope"]["scope_generation"] += 1
    key = (original["memory_id"], original["revision"])
    assert digest(original) != digest(rebound)
    assert assertion_fingerprint(original) == assertion_fingerprint(rebound)
    validate_manifest(data.integration_manifest, events=publication_snapshot(data)["events"], assertions={key: rebound})
    rebound["statement"] = "A different preference with the same identity."
    with pytest.raises(ValueError, match="endpoint changed"):
        validate_manifest(data.integration_manifest, events=publication_snapshot(data)["events"], assertions={key: rebound})


@pytest.mark.parametrize("violation", ["missing", "duplicate", "wrong_id", "proposed_empty", "no_change_links",
                                      "no_change_no_reason", "unrelated_link"])
def test_history_requires_exact_explicit_decisions_and_no_orphan_links(violation):
    data, response = history_case()
    decision = response["integration_decisions"][0]
    if violation == "missing":
        response["integration_decisions"] = []
    elif violation == "duplicate":
        response["integration_decisions"] *= 2
    elif violation == "wrong_id":
        decision["objective_id"] = "unknown-objective"
    elif violation == "proposed_empty":
        decision["link_proposal_ids"] = []
    elif violation == "no_change_links":
        decision.update(outcome="no_change", reason="insufficient_evidence")
    elif violation == "no_change_no_reason":
        decision.update(outcome="no_change", link_proposal_ids=[])
        response["belief_links"] = []
        response["claims"] = []
    else:
        extra = copy.deepcopy(response["belief_links"][0])
        extra.update(proposal_id="unrelated-link", target_memory_versions=[{"memory_id": "outside-manifest", "revision": 1}])
        response["belief_links"].append(extra)
    result, requests = process(data, response)
    assert result.status == "invalid_response" and len(requests) == 1
    assert not result.claims and not result.belief_links and result.history_integration["assessed_count"] == 0


def test_history_publishes_only_linked_claims_and_keeps_nonexhaustive_scope_visible():
    data, response = history_case()
    before = copy.deepcopy(data.integration_manifest)
    result, requests = process(data, response)
    assert result.status == "completed" and len(result.claims) == len(result.belief_links) == 1
    assert result.history_integration["assessed_count"] == result.history_integration["published_count"] == 1
    assert {item["objective_id"] for item in result.history_integration["objectives"]} == {
        before["objectives"][0]["objective_id"]}
    for request in requests:
        payload = json.loads(request.messages[-1]["content"])
        assert payload["integration_manifest"] == before
        assert payload["integration_manifest"]["unresolved_discovery"] is True
    assert data.integration_manifest == before
    _check_result(data, result, publication_snapshot(data))


def test_history_drops_otherwise_supported_claim_when_its_relation_is_unconfirmed():
    data, response = history_case()

    def reject_relation(verified):
        verified["assessments"][1]["link_verdict"] = "unknown"

    result, _ = process(data, response, change_assessment=reject_relation)
    assert result.status == "partial" and not result.claims and not result.belief_links
    assert result.history_integration["assessed_count"] == 1 and result.history_integration["published_count"] == 0
    assert result.integration_proposal_links == tuple(response["belief_links"])
    _check_result(data, result, publication_snapshot(data))


def history_with_uncited_context():
    data, response = history_case()
    qualification = event("The earlier brevity preference is qualified by a different audience.", sequence=3)
    exception = known(qualification, "The user reports an audience-specific qualification.")
    related = relation(exception, data.existing_assertions, kind="contradicts")
    manifest = {**data.integration_manifest, "through_sequence": qualification["sequence"]}
    data = replace(data, sources=(*data.sources, qualification),
                   existing_assertions=(*data.existing_assertions, exception), belief_relations=(related,),
                   integration_manifest=manifest,
                   sampling=bounded_sampling(HISTORY_INTEGRATION_MODE, source_count=3, assertion_count=2))
    return data, response, qualification


def test_history_verification_retains_all_uncited_qualifying_sources_assertions_and_relations():
    data, response, qualification = history_with_uncited_context()
    result, requests = process(data, response)
    assert result.status == "completed" and len(requests) == 2
    body = json.loads(requests[1].messages[-1]["content"])
    assert body["historical_context"] == {"sources": list(data.sources), "assertions": list(data.existing_assertions),
                                          "belief_relations": list(data.belief_relations)}
    assert body["local_input_refs"] == data.integration_manifest["input_refs"] and "local_input_context" not in body
    cited = {entry["source_ref"]["event_id"] for proposal in body["proposals"] for entry in proposal["evidence"]}
    assert qualification["event_id"] not in cited
    assert qualification["observed"]["content"]["text"] in requests[1].messages[-1]["content"]
    check_compact_verification_refs(body)
    _check_result(data, result, publication_snapshot(data))


def test_shared_complete_context_keeps_26kb_uncited_source_within_default_two_call_budget():
    data, response = history_case()
    new = event("Brevity suits me. " + "q" * 26000 + ".", sequence=2)
    manifest = copy.deepcopy(data.integration_manifest)
    manifest["input_refs"][0]["content_sha256"] = new["content_sha256"]
    manifest["input_fingerprint"] = digest(manifest["input_refs"])
    objective = manifest["objectives"][0]
    objective["objective_id"] = "comparison-" + digest([
        MANIFEST_VERSION, manifest["input_fingerprint"], objective["target_memory_version"]])
    response["integration_decisions"][0]["objective_id"] = objective["objective_id"]
    for group in ("claims", "belief_links"):
        for item in response[group]:
            for ref in item["evidence_refs"]:
                if ref["event_id"] == new["event_id"]:
                    ref["source_content_sha256"] = new["content_sha256"]
    data = replace(data, sources=(data.sources[0], new), integration_manifest=manifest)
    result, requests = process(data, response)
    assert result.status == "completed" and len(requests) == result.model_calls == 2
    sizes = [request_payload(request.phase, json.loads(request.messages[-1]["content"]), request.response_schema,
                             request.max_output_tokens, proposal_mode=data.proposal_mode,
                             input_contract_version=data.input_contract_version)[1] for request in requests]
    assert max(sizes) <= SemanticPolicy().max_request_bytes and sum(sizes) <= SemanticPolicy().max_input_bytes
    body = json.loads(requests[1].messages[-1]["content"])
    assert body["historical_context"]["sources"] == list(data.sources)
    assert requests[1].messages[-1]["content"].count("q" * 26000) == 1
    check_compact_verification_refs(body)
    _check_result(data, result, publication_snapshot(data))


def test_history_verification_budget_cannot_be_met_by_silently_dropping_uncited_context():
    data, response, _ = history_with_uncited_context()
    baseline, requests = process(data, response)
    assert baseline.status == "completed"
    sizes = [request_payload(request.phase, json.loads(request.messages[-1]["content"]), request.response_schema,
                             request.max_output_tokens, proposal_mode=data.proposal_mode,
                             input_contract_version=data.input_contract_version)[1] for request in requests]
    policy = replace(SemanticPolicy(), max_input_bytes=sum(sizes) - 1)
    reservations = []
    proposals_seen = []

    def propose(request):
        proposals_seen.append(request)
        return encoded(response)

    processor = SemanticProcessor(propose, lambda _: pytest.fail("Incomplete context must not reach verification"), policy)
    result = processor.process(data, reserve=lambda **values: reservations.append(values))
    assert result.status == "budget_exhausted" and result.model_calls == 1
    assert len(proposals_seen) == len(reservations) == 1
    assert reservations[0]["input_bytes"] == sizes[0]
    assert not result.claims and not result.belief_links and result.history_integration["assessed_count"] == 0
    _check_result(data, result, publication_snapshot(data))


def test_history_rejects_extra_unlinked_claim_and_forged_publication():
    data, response = history_case()
    original, _ = process(data, response)
    unlinked = {**response["claims"][0], "proposal_id": "unlinked-claim"}
    response["claims"].append(unlinked)
    result, _ = process(data, response)
    assert "unlinked-claim" not in {item["proposal_id"] for item in result.claims}
    forged = {**original.claims[0], "proposal_id": "unlinked-claim"}
    with pytest.raises(DomainError):
        _check_result(data, replace(original, claims=(*original.claims, forged)), publication_snapshot(data))


def test_history_no_change_is_assessed_without_claiming_publication_or_complete_history():
    data, response = history_case()
    response["claims"] = response["belief_links"] = []
    response["integration_decisions"][0].update(outcome="no_change", reason="insufficient_shared_meaning", link_proposal_ids=[])
    assert validate_decisions(data.integration_manifest, response) == response["integration_decisions"]
    result, requests = process(data, response)
    assert result.status == "completed" and len(requests) == 1
    assert result.history_integration["assessed_count"] == 1 and result.history_integration["published_count"] == 0
    assert result.history_integration["objectives"][0]["status"] == "assessed_no_publication"
    assert data.integration_manifest["unresolved_discovery"] is True


@pytest.mark.parametrize("violation", ["missing", "duplicate", "unknown_reason", "undeclared_link"])
def test_history_publication_rechecks_explicit_decision_shape_and_coverage(violation):
    data, response = history_case()
    response["claims"] = response["belief_links"] = []
    response["integration_decisions"][0].update(outcome="no_change", reason="insufficient_shared_meaning", link_proposal_ids=[])
    result, _ = process(data, response)
    decisions = copy.deepcopy(list(result.integration_decisions))
    if violation == "missing":
        decisions = []
    elif violation == "duplicate":
        decisions *= 2
    elif violation == "unknown_reason":
        decisions[0]["reason"] = "fabricated_external_success"
    else:
        decisions[0].update(outcome="proposed", reason=None, link_proposal_ids=["unobserved-link"])
    forged = replace(result, integration_decisions=tuple(decisions),
                     history_integration=integration_coverage(data.integration_manifest, decisions, (), assessed=True))
    with pytest.raises(DomainError):
        _check_result(data, forged, publication_snapshot(data))


@pytest.mark.parametrize("outcome", ["accepted", "rejected"])
def test_history_publication_requires_retained_original_link_for_each_decision(outcome):
    data, response = history_case()

    def assess(verified):
        if outcome == "rejected":
            verified["assessments"][1]["link_verdict"] = "unknown"

    result, _ = process(data, response, change_assessment=assess)
    assert result.status == ("completed" if outcome == "accepted" else "partial")
    assert result.integration_proposal_links == tuple(response["belief_links"])
    with pytest.raises(DomainError):
        _check_result(data, replace(result, integration_proposal_links=()), publication_snapshot(data))


def test_history_publication_requires_a_retained_outcome_for_original_rejected_links():
    data, response = history_case()

    def reject_relation(verified):
        verified["assessments"][1]["link_verdict"] = "unknown"

    result, _ = process(data, response, change_assessment=reject_relation)
    assert result.status == "partial" and not result.belief_links
    remaining = tuple(item for item in result.rejected if item["proposal_id"] != "link-1")
    with pytest.raises(DomainError):
        _check_result(data, replace(result, rejected=remaining), publication_snapshot(data))


def test_history_publication_cannot_replace_the_original_accepted_link_proposal():
    data, response = history_case()
    result, _ = process(data, response)
    original = copy.deepcopy(result.integration_proposal_links[0])
    original["kind"] = "contradicts"
    with pytest.raises(DomainError):
        _check_result(data, replace(result, integration_proposal_links=(original,)), publication_snapshot(data))


def test_recorded_school_fixture_matches_immutable_public_evidence():
    fixture = json.loads(FIXTURE.read_text())
    provenance = fixture["provenance"]
    root = Path(__file__).parents[1]
    archive_path = root / provenance["archive"]
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == provenance["archive_sha256"]
    with tarfile.open(archive_path, "r:gz") as archive:
        raw = archive.extractfile(provenance["report_member"]).read()
    assert hashlib.sha256(raw).hexdigest() == provenance["report_sha256"]
    stage = json.loads(raw)["stages"][provenance["stage_index"]]
    payload = json.loads(stage["cognitive_calls"][0]["request"]["messages"][1]["content"])
    keys = {(ref["event_id"], ref["revision"]) for ref in payload["learning_input_refs"]}
    assert fixture["local_sources"] == [source for source in payload["sources"]
                                         if (source["event_id"], source["revision"]) in keys]
    assert fixture["recorded_general"]["proposal"] == stage["cognitive_calls"][0]["response"]
    assert fixture["recorded_general"]["sampling"] == payload["sampling"]
    assert fixture["recorded_general"]["rejected"] == stage["learning_jobs"][0]["result"]["rejected"]


def test_existing_general_guard_still_rejects_independently_supported_school_proposals():
    fixture = json.loads(FIXTURE.read_text())
    old = fixture["recorded_general"]
    data = CognitiveBatch("school-general-regression", fixture["scope"], tuple(fixture["local_sources"]),
                          proposal_mode=GENERAL_PROPOSAL_MODE, input_contract_version=old["input_contract_version"],
                          learning_input_refs=tuple(fixture["learning_input_refs"]), sampling=old["sampling"])
    result, requests = process(data, old["proposal"])
    assert result.status == old["status"] == "partial" and len(requests) == 1
    assert list(result.rejected) == old["rejected"]
    assert not result.claims and not result.episode_actions


def test_school_local_contract_preserves_all_original_text_and_unknown_city_without_history():
    fixture = json.loads(FIXTURE.read_text())
    original = copy.deepcopy(fixture)
    response = copy.deepcopy(fixture["recorded_general"]["proposal"])
    for proposed in response["claims"]:
        proposed.update(memory_kind="episodic", representation="episode_fragment")
    data = CognitiveBatch("school-local-contract", fixture["scope"], tuple(fixture["local_sources"]),
                          proposal_mode=LOCAL_OBSERVATION_MODE, input_contract_version=EPISODE_FIDELITY_INPUT_VERSION,
                          learning_input_refs=tuple(fixture["learning_input_refs"]),
                          sampling=bounded_sampling(LOCAL_OBSERVATION_MODE, source_count=len(fixture["local_sources"])),
                          working_state=empty_working(fixture["scope"]))
    result, requests = process(data, response)
    assert result.status == "completed" and len(result.claims) == 3 and len(result.episode_actions) == 1
    assert [item["statement"] for item in result.claims] == [item["statement"] for item in response["claims"]]
    proposal_input = json.loads(requests[0].messages[-1]["content"])
    assert proposal_input["sources"] == fixture["local_sources"]
    assert proposal_input["existing_assertions"] == proposal_input["episodes"] == proposal_input["belief_relations"] == []
    assert proposal_input["current_input_refs"] == []
    assert proposal_input["local_observation_contract"]["history_intentionally_withheld"] is True
    assert proposal_input["local_observation_contract"]["historical_integration"] == "pending"
    verification = json.loads(requests[1].messages[-1]["content"])
    assert verification["local_input_context"] == fixture["local_sources"]
    assert "我没有告诉你学校所在的城市。" in verification["local_input_context"][0]["observed"]["content"]["text"]
    assert result.verification_assessments[-1]["organization_fidelity"]["specificity"] == "supports"
    _check_result(data, result, publication_snapshot(data))
    assert fixture == original
