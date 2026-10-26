import copy
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb
from test_abstraction_support import sampled_walks, select_support
from test_bounded_learning_semantics import (
    bounded_sampling,
    empty_working,
    history_case,
    local_batch,
    local_claim,
    process,
    publication_snapshot,
)
from test_cognition import event
from test_current_request_runtime import orientation_job
from test_history_attention import manifest
from test_learning import learning as learning
from test_semantic import batch, encoded, episode, proposals, reference, run, situation, verifier

from agent_memory.citation_coverage import (
    CITATION_COVERAGE_INPUT_VERSION as VERSION,
)
from agent_memory.citation_coverage import (
    CITATION_COVERAGE_INPUT_VERSIONS,
    CITATION_COVERAGE_VERIFY_INSTRUCTIONS,
    CITATION_PROVENANCE_VERIFY_INSTRUCTIONS,
    citation_coverage_schema,
    citation_scope,
)
from agent_memory.consolidation import _check_result
from agent_memory.helpers import DomainError, uid
from agent_memory.history_semantics import LINK_DERIVED_INPUT_VERSION
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine, _snapshot_input_contract
from agent_memory.local_semantics import HISTORY_INTEGRATION_MODE, LINK_DERIVED_INSTRUCTIONS
from agent_memory.semantic import (
    COMMUNICATION_INTENT_INPUT_VERSION,
    LATEST_INPUT_CONTRACT_VERSION,
    ORIENT_PROPOSAL_MODE,
    VERIFICATION_SCHEMA,
    proposal_schema,
)
from agent_memory.verification_sources import SHARED_VERIFICATION_INPUT_VERSION


def case(kind):
    source = event("The user described a writing plan.")
    if kind == "local":
        return local_batch(source, input_contract_version=VERSION), proposals(local_claim(source)), "claim-1"
    if kind == "orient":
        source = event("Please explain the writing plan.")
        data = batch(source, input_contract_version=VERSION, proposal_mode=ORIENT_PROPOSAL_MODE,
                     current_input_refs=({"event_id": source["event_id"], "revision": source["revision"]},))
        return data, {"situation_updates": [situation(source, evidence_use="current_request")]}, "situation-1"
    if kind == "situation":
        return (batch(source, input_contract_version=VERSION),
                proposals(updates=[situation(source, kind="context", evidence_use="source_content")]), "situation-1")
    if kind == "history":
        data, proposed = history_case()
        proposed.pop("integration_decisions")
        proposed["integration_abstentions"] = []
        return replace(data, input_contract_version=VERSION), proposed, "link-1"
    return batch(source, input_contract_version=VERSION), proposals(local_claim(source)), "claim-1"


def modify(identifier, value):
    def change(response):
        checked = next(item for item in response["assessments"] if item["proposal_id"] == identifier)
        if value == "missing":
            checked.pop("citation_coverage_verdict")
        else:
            checked["citation_coverage_verdict"] = value
    return change


@pytest.mark.parametrize("kind", ["general", "local", "orient", "situation", "history"])
@pytest.mark.parametrize("verdict", ["unknown", "contradicts"])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_coverage_is_an_independent_gate_in_every_mode_without_extra_calls(kind, verdict, version):
    data, proposed, identifier = case(kind)
    data = replace(data, input_contract_version=version)
    original = copy.deepcopy(proposed)
    result, requests = process(data, proposed, change_assessment=modify(identifier, verdict))
    assert result.status == "partial" and result.model_calls == len(requests) == 2
    assert {"proposal_id": identifier, "reason": "citation_coverage_unconfirmed"} in result.rejected
    assert identifier not in {item["proposal_id"] for group in (
        result.claims, result.situation_updates, result.belief_links) for item in group}
    assert proposed == original


@pytest.mark.parametrize("value", ["missing", None, True, {}, "complete"])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_missing_or_malformed_coverage_is_invalid_even_when_all_old_verdicts_support(value, version):
    data, proposed, identifier = case("general")
    data = replace(data, input_contract_version=version)
    result, requests = process(data, proposed, change_assessment=modify(identifier, value))
    assert result.status == "invalid_response" and result.model_calls == len(requests) == 2
    assert not result.claims


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_scope_keeps_full_context_and_episode_but_only_own_excerpts_can_cover_claim(complete, version):
    source = event("One image. A small routine. The letter's physical detail.", role="assistant")
    segments = source["utterance_segments"]
    refs = [{**reference(source), "span": copy.deepcopy(segment["span"])} for segment in segments]
    selected = refs if complete else refs[:2]
    candidate = local_claim(source, claim_kind="agent_said", evidence_refs=selected,
                            statement="The assistant proposed an image, a routine and the letter's physical detail.")
    proposed = proposals(candidate, episodes=[episode(source)])
    data = batch(source, input_contract_version=version)

    def check(response):
        response["assessments"][0]["citation_coverage_verdict"] = "supports" if complete else "unknown"

    result, requests = process(data, proposed, change_assessment=check)
    body = json.loads(requests[1].messages[-1]["content"])
    entries = body["proposals"]
    claim_entry, episode_entry = entries
    assert claim_entry["citation_scope"] == citation_scope(selected, input_contract_version=version)
    assert claim_entry["citation_scope"]["positive_evidence_indices"] == list(range(len(selected)))
    if version == SHARED_VERIFICATION_INPUT_VERSION:
        assert body["verification_sources"] == [source]
        assert all(item["source_ref"] == {"event_id": source["event_id"], "revision": source["revision"]}
                   for item in claim_entry["evidence"])
    else:
        assert all(item["source"] == source for item in claim_entry["evidence"])
    assert episode_entry["evidence"][0]["excerpt"] == source["observed"]["content"]["text"]
    assert "citation_scope" not in episode_entry
    instructions = (CITATION_COVERAGE_VERIFY_INSTRUCTIONS if version == VERSION
                    else CITATION_PROVENANCE_VERIFY_INSTRUCTIONS)
    assert instructions in requests[1].messages[0]["content"]
    assert bool(result.claims) is complete and len(result.episode_actions) == 1
    _check_result(data, result, publication_snapshot(data))


@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_episode_cannot_receive_a_claim_coverage_verdict(version):
    source = event("We discussed the writing plan.")
    data = batch(source, input_contract_version=version)
    proposed = proposals(local_claim(source), episodes=[episode(source)])
    result, _ = process(data, proposed, change_assessment=modify("episode-1", "supports"))
    assert result.status == "invalid_response" and not result.claims and not result.episode_actions


@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_counterexample_cannot_enter_positive_scope_or_be_overridden_by_coverage(version):
    first, second = event("I like tea."), event("I do not like tea.", 2)
    candidate = local_claim(first, counterexamples=[reference(second, "contradicts")])
    data = batch(first, second, input_contract_version=version)

    def wrong_counterexample(response):
        response["assessments"][0]["relations"][1]["verdict"] = "supports"

    result, requests = process(data, proposals(candidate), change_assessment=wrong_counterexample)
    scope = json.loads(requests[1].messages[-1]["content"])["proposals"][0]["citation_scope"]
    assert scope["positive_evidence_indices"] == [0]
    assert not result.claims
    assert result.rejected[0]["reason"] == "semantic_support_unconfirmed"


@pytest.mark.parametrize("kind", ["general", "local", "orient", "situation", "history"])
@pytest.mark.parametrize("damage", ["missing", "duplicate", "unknown"])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_publication_rechecks_coverage_in_general_and_bounded_modes(kind, damage, version):
    data, proposed, identifier = case(kind)
    data = replace(data, input_contract_version=version)
    result, _ = process(data, proposed)
    assert result.status == "completed"
    _check_result(data, result, publication_snapshot(data), current_input_refs=data.current_input_refs)
    assessments = copy.deepcopy(list(result.verification_assessments))
    target = next(item for item in assessments if item["proposal_id"] == identifier)
    if damage == "missing":
        target.pop("citation_coverage_verdict")
    elif damage == "duplicate":
        assessments.append(copy.deepcopy(target))
    else:
        target["citation_coverage_verdict"] = "unknown"
    changed = replace(result, verification_assessments=tuple(assessments))
    with pytest.raises(DomainError, match="Citation coverage"):
        _check_result(data, changed, publication_snapshot(data), current_input_refs=data.current_input_refs)


def shared_result(*, change=None, history=False, version=VERSION):
    data, proposed = sampled_walks()
    data = replace(data, input_contract_version=version)
    proposed["claims"][0]["evidence_refs"] = copy.deepcopy(proposed["belief_links"][0]["evidence_refs"])
    if history:
        data = replace(data, proposal_mode=HISTORY_INTEGRATION_MODE,
                       integration_manifest=manifest(data.sources[-1], data.existing_assertions),
                       working_state=empty_working(data.scope),
                       sampling=bounded_sampling(HISTORY_INTEGRATION_MODE, source_count=len(data.sources),
                                                 assertion_count=len(data.existing_assertions)))
        proposed["consolidation_decisions"] = []
        proposed["integration_abstentions"] = []
    seen = []

    def verify(request):
        seen.extend(json.loads(request.messages[-1]["content"])["proposals"])
        response = json.loads(verifier(request))
        assessments = {item["proposal_id"]: item for item in response["assessments"]}
        for entry, assessment in zip(json.loads(request.messages[-1]["content"])["proposals"],
                                     response["assessments"], strict=True):
            if entry["group"] == "belief_links":
                assessment["link_verdict"] = "supports"
            select_support(assessment, {1, 3})
        if change:
            change(assessments)
        return encoded(response)

    result = run(data, proposed, verify=verify)
    return data, proposed, result, seen


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_abstraction_scope_requires_coverage_of_actual_selected_positive_basis(history, version):
    data, proposed, result, entries = shared_result(history=history, version=version)
    assert result.status == "completed" and len(result.claims) == len(result.belief_links) == 1
    assert all(entry["citation_scope"]["positive_evidence_indices"] == [0, 1, 2, 3] for entry in entries)
    assert all(entry["citation_scope"]["support_selection"] == "supporting_evidence_indices" for entry in entries)
    assert result.claims[0]["evidence_refs"] == [proposed["claims"][0]["evidence_refs"][index] for index in (0, 2)]
    _check_result(data, result, publication_snapshot(data))


@pytest.mark.parametrize("identifier", ["claim-1", "abstract-1"])
@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_failed_shared_claim_or_link_coverage_cannot_leave_a_detached_abstraction(identifier, history, version):
    def change(assessments):
        assessments[identifier]["citation_coverage_verdict"] = "unknown"

    _, _, result, _ = shared_result(change=change, history=history, version=version)
    assert result.status == "partial" and not result.claims and not result.belief_links


@pytest.mark.parametrize("damage", ["restored_irrelevant", "dropped_selected", "selection_metadata", "assessment_basis"])
@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_publication_binds_stored_abstraction_refs_to_verified_selection(damage, history, version):
    data, proposed, result, _ = shared_result(history=history, version=version)
    _check_result(data, result, publication_snapshot(data))
    claim = copy.deepcopy(result.claims[0])
    selections = copy.deepcopy(list(result.evidence_selections))
    assessments = copy.deepcopy(list(result.verification_assessments))
    if damage == "restored_irrelevant":
        claim["evidence_refs"] = copy.deepcopy(proposed["claims"][0]["evidence_refs"])
    elif damage == "dropped_selected":
        claim["evidence_refs"].pop()
    elif damage == "selection_metadata":
        selections[0]["supporting_evidence_indices"] = [0]
    else:
        assessments[0]["supporting_evidence_indices"] = [0]
    changed = replace(result, claims=(claim,), evidence_selections=tuple(selections),
                      verification_assessments=tuple(assessments))
    with pytest.raises(DomainError):
        _check_result(data, changed, publication_snapshot(data))


@pytest.mark.parametrize("damage", ["instance_verdict", "missing_instance", "selected_instance"])
@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("version", CITATION_COVERAGE_INPUT_VERSIONS)
def test_publication_requires_selected_support_for_every_abstract_instance(damage, history, version):
    data, _, result, _ = shared_result(history=history, version=version)
    assessments = copy.deepcopy(list(result.verification_assessments))
    target = next(item for item in assessments if item["proposal_id"] == "abstract-1")
    if damage == "instance_verdict":
        target["instance_verdicts"][0]["verdict"] = "unknown"
    elif damage == "missing_instance":
        target["instance_verdicts"].pop()
    else:
        target["relations"][2]["verdict"] = "irrelevant"
        target["supporting_evidence_indices"] = [0]
    changed = replace(result, verification_assessments=tuple(assessments))
    with pytest.raises(DomainError, match="Citation coverage instance"):
        _check_result(data, changed, publication_snapshot(data))


@pytest.mark.parametrize("version", [None, COMMUNICATION_INTENT_INPUT_VERSION, LINK_DERIVED_INPUT_VERSION])
def test_legacy_requests_and_response_contract_do_not_gain_coverage_fields(version):
    source = event("The user described a plan.")
    seen = []

    def verify(request):
        seen.append(request)
        assert request.response_schema == VERIFICATION_SCHEMA
        assert CITATION_COVERAGE_VERIFY_INSTRUCTIONS not in request.messages[0]["content"]
        assert all("citation_scope" not in item for item in json.loads(request.messages[-1]["content"])["proposals"])
        return verifier(request)

    result = run(batch(source, input_contract_version=version), proposals(local_claim(source)), verify=verify)
    assert result.status == "completed" and len(seen) == 1


def test_new_schema_does_not_mutate_legacy_and_history_accounting_is_inherited():
    original = copy.deepcopy(VERIFICATION_SCHEMA)
    schema = citation_coverage_schema(VERIFICATION_SCHEMA, ["claim-1"])
    assert VERIFICATION_SCHEMA == original and schema != original
    assert citation_coverage_schema(VERIFICATION_SCHEMA, []) == original
    old = proposal_schema(HISTORY_INTEGRATION_MODE, input_contract_version=LINK_DERIVED_INPUT_VERSION)
    current = proposal_schema(HISTORY_INTEGRATION_MODE, input_contract_version=VERSION)
    assert old == current
    assert "integration_abstentions" in current["required"] and "integration_decisions" not in current["properties"]
    data, proposed, _ = case("history")
    result, requests = process(data, proposed)
    assert result.status == "completed" and len(result.integration_decisions) == 1
    assert LINK_DERIVED_INSTRUCTIONS in requests[0].messages[0]["content"]
    assert Draft202012Validator(requests[1].response_schema).is_valid({"assessments": list(result.verification_assessments)})
    assert LATEST_INPUT_CONTRACT_VERSION == "source-composition-v1"
    assert batch(event("A direct report.")).input_contract_version == COMMUNICATION_INTENT_INPUT_VERSION
    for version in (None, COMMUNICATION_INTENT_INPUT_VERSION, LINK_DERIVED_INPUT_VERSION, VERSION):
        assert _snapshot_input_contract({"input_contract_version": version}) == version


def snapshot_job(learning, kind):
    if kind == "orient":
        engine, _, job = orientation_job(learning)
    else:
        receipt = learning.observe("I described a writing plan.")
        with learning.database.transaction(learning.principal) as connection:
            brain = learning.service._brain(connection, learning.principal, learning.brain)
            job_id = learning.service._job(connection, learning.principal, brain, "consolidate", {
                "event_id": receipt["accepted_event_ids"][0], "revision": 1,
                "target": {"conversation_ref": "chat", "branch_ref": "main"},
                "through_sequence": receipt["durable_through_sequence"],
            })["job_id"]
        job = JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(), job_id=job_id)
        engine = LearningEngine(learning.service)
    return engine, job


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["orient", "manual"])
def test_new_orientation_and_manual_jobs_preserve_coverage_contract_across_restart(learning, kind, monkeypatch):
    engine, job = snapshot_job(learning, kind)
    before, after = "2026-10-25T10:00:00Z", "2026-10-25T10:01:00Z"
    monkeypatch.setattr("agent_memory.service.now", lambda: before)
    current, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert current.input_contract_version == snapshot["input_contract_version"] == LATEST_INPUT_CONTRACT_VERSION
    monkeypatch.setattr("agent_memory.service.now", lambda: after)
    resumed, frozen = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, job)
    assert frozen == snapshot
    if kind == "orient":
        assert resumed == current
    else:
        assert "working_snapshot" not in snapshot and learning.working() is None
        assert current.working_state == {
            "scope": current.scope, "revision": 0, "through_sequence": 0,
            "items": [], "open_loops": [], "updated_at": before,
        }
        assert resumed == replace(current, working_state={**current.working_state, "updated_at": after})


@pytest.mark.integration
@pytest.mark.parametrize("version", ["absent", None, COMMUNICATION_INTENT_INPUT_VERSION, LINK_DERIVED_INPUT_VERSION, VERSION])
def test_saved_manual_input_contract_is_not_upgraded_without_working_snapshot(learning, version):
    engine, job = snapshot_job(learning, "manual")
    _, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert "working_snapshot" not in snapshot
    if version == "absent":
        snapshot.pop("input_contract_version")
    else:
        snapshot["input_contract_version"] = version
    with learning.database.transaction(learning.principal) as connection:
        connection.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                           (Jsonb(snapshot), learning.principal.tenant_id, learning.principal.principal_id,
                            learning.brain, job["id"]))
    for _ in range(2):
        resumed, frozen = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, job)
        assert resumed.input_contract_version == (None if version == "absent" else version)
        assert frozen == snapshot
