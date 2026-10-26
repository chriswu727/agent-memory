import copy
import json
from dataclasses import replace

import pytest
from psycopg.types.json import Jsonb
from test_bounded_learning_semantics import local_batch, local_claim, process, publication_snapshot
from test_citation_coverage import case, snapshot_job
from test_cognition import event
from test_learning import learning as learning
from test_semantic import batch, proposals

from agent_memory.citation_coverage import (
    CITATION_COVERAGE_INPUT_VERSION,
    CITATION_COVERAGE_VERIFY_INSTRUCTIONS,
    CITATION_PROVENANCE_VERIFY_INSTRUCTIONS,
)
from agent_memory.citation_coverage import (
    CITATION_PROVENANCE_INPUT_VERSION as VERSION,
)
from agent_memory.consolidation import _check_result
from agent_memory.helpers import DomainError, digest
from agent_memory.learning import LearningEngine
from agent_memory.semantic import SemanticProcessor

LEGACY_REQUEST_HASHES = {
    "general": ("7876b7255f78a0444335ea44261ad9898d63ff81f8702ee23630ccd0186cbfe5",
                "cb0d97d9ea162bc32c1d43aa32b0d395e1ffed433352594a6ba274876c8134a8"),
    "local": ("fa96dbf6544dbac9cac0712b9bbbfbae6bffdbbbe43d10e111e92373bd96aae9",
              "f8c6ca01bb021754349508d75ee674560afccb02ecab93529774d0955c1e1d52"),
    "orient": ("e266873ff1a61c8fead3c28b6aa51bc72e71b61d778e2ea2b8d0f3f1f733538c",
               "b9135f8052a282f9e72cde34ee0a4c1a55e2081f45bf998f3649c88369d06789"),
    "situation": ("7876b7255f78a0444335ea44261ad9898d63ff81f8702ee23630ccd0186cbfe5",
                  "c612446d548e26639680818acfce3022f60e75ba66f39ea5a20bc7117d0988fc"),
    "history": ("0e9f2e02020690b037791bad53ee3b917f713bb3b290b240f93b564e5e80240a",
                "731ce329d2640a88037103d789eb2c2573dd978fe419cbf1c73938e924c06b34"),
}


@pytest.mark.parametrize("kind", LEGACY_REQUEST_HASHES)
def test_old_coverage_messages_payload_and_schema_remain_byte_identical(kind):
    data, proposed, _ = case(kind)
    result, requests = process(data, proposed)
    assert result.status == "completed"
    assert tuple(digest({"messages": request.messages, "response_schema": request.response_schema})
                 for request in requests) == LEGACY_REQUEST_HASHES[kind]
    new_result, new_requests = process(replace(data, input_contract_version=VERSION), proposed)
    assert new_result.status == "completed" and new_result.model_calls == 2
    assert new_requests[0].messages == requests[0].messages
    assert new_requests[1].response_schema == requests[1].response_schema
    assert new_requests[1].messages != requests[1].messages
    assert new_requests[1].request_id != requests[1].request_id


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("role", ["user", "assistant"])
def test_attribution_uses_only_selected_source_role_and_delivery_metadata(bounded, role):
    text = ("Make it a bit shorter, please. We talk pretty casually." if role == "user"
            else "I can help if you send the draft.")
    source = event(text, role=role)
    source["observed"]["actor_ref"] = "Unverified human identity"
    other = event("A different participant's unrelated report.", 2)
    create_batch = local_batch if bounded else batch
    data = create_batch(source, other, input_contract_version=VERSION)
    candidate = local_claim(source, claim_kind="agent_said" if role == "assistant" else "reported_fact",
                            statement=f'{role.title()} said: "{text}"')
    result, requests = process(data, proposals(candidate))
    assert result.status == "completed" and result.model_calls == 2 and len(result.claims) == 1
    assert all(result.claims[0][key] == value for key, value in candidate.items())
    assert result.claims[0]["verification"]["not_a_truth_certificate"] is True
    body = json.loads(requests[1].messages[-1]["content"])
    entry = body["proposals"][0]
    evidence = entry["evidence"][0]
    authority = entry["citation_scope"]
    assert authority["positive_evidence_indices"] == [0]
    assert authority["provenance_authority"] == {
        "fields": ["observed.actor_role", "observed.delivery_state"],
        "source_binding": "selected_positive_reference_event_id_revision_and_content_sha256",
        "use": "recorded_communication_attribution_and_delivery_only",
    }
    assert authority["other_proposals_authority"] == "none"
    assert authority["context_authority"] == "qualifications_and_counterevidence_except_listed_provenance"
    if bounded:
        lookup = {(item["event_id"], item["revision"]): item for item in body["local_input_context"]}
        resolved = lookup[(evidence["source_ref"]["event_id"], evidence["source_ref"]["revision"])]
    else:
        resolved = evidence["source"]
    assert resolved == source and evidence["excerpt"] == text
    assert evidence["reference"]["source_content_sha256"] == source["content_sha256"]
    assert CITATION_PROVENANCE_VERIFY_INSTRUCTIONS in requests[1].messages[0]["content"]
    assert CITATION_COVERAGE_VERIFY_INSTRUCTIONS not in requests[1].messages[0]["content"]
    _check_result(data, result, publication_snapshot(data))
    damaged = publication_snapshot(data)
    damaged["events"][(source["event_id"], source["revision"])] = copy.deepcopy(source)
    damaged["events"][(source["event_id"], source["revision"])]["observed"]["actor_role"] = (
        "user" if role == "assistant" else "assistant")
    with pytest.raises(DomainError, match="unsupported"):
        _check_result(data, result, damaged)


@pytest.mark.parametrize("damage", ["other_actor", "undelivered", "revision", "hash", "external_completion"])
def test_provenance_does_not_override_binding_delivery_or_external_fact_guards(damage):
    source = event("I finished the handoff.", role="assistant")
    other = event("I finished the handoff.", 2)
    candidate = local_claim(source, claim_kind="agent_said", statement="The assistant said the handoff was finished.")
    if damage == "other_actor":
        candidate["evidence_refs"][0].update(event_id=other["event_id"], source_content_sha256=other["content_sha256"])
    elif damage == "undelivered":
        source["observed"]["delivery_state"] = "draft"
    elif damage == "revision":
        candidate["evidence_refs"][0]["event_revision"] = 2
    elif damage == "hash":
        candidate["evidence_refs"][0]["source_content_sha256"] = "0" * 64
    else:
        candidate.update(claim_kind="task_completed", statement="The handoff is complete.")
    result, requests = process(batch(source, other, input_contract_version=VERSION), proposals(candidate))
    assert result.status == "partial" and result.model_calls == len(requests) == 1
    assert result.rejected == ({"proposal_id": candidate["proposal_id"], "reason": "invalid_proposal"},)
    assert not result.claims


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["orient", "manual"])
def test_frozen_coverage_v1_snapshot_keeps_exact_request_contract_after_restart(learning, kind):
    engine, job = snapshot_job(learning, kind)
    current, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert current.input_contract_version == VERSION
    snapshot["input_contract_version"] = CITATION_COVERAGE_INPUT_VERSION
    with learning.database.transaction(learning.principal) as connection:
        if kind == "manual":
            brain = learning.service._brain(connection, learning.principal, learning.brain)
            learning.service._working_save(connection, learning.principal, brain, job["payload"]["target"],
                                           current.working_state)
        connection.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                           (Jsonb(snapshot), learning.principal.tenant_id, learning.principal.principal_id,
                            learning.brain, job["id"]))
    selected = [LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, job) for _ in range(2)]
    assert selected[0] == selected[1]
    assert selected[0][0].input_contract_version == CITATION_COVERAGE_INPUT_VERSION
    assert selected[0][1] == snapshot
    calls = []
    processor = SemanticProcessor(lambda request: calls.append(request) or json.dumps({"situation_updates": []}))
    for data, _ in selected:
        processor.process(data, reserve=lambda **_: None)
    assert len(calls) == 2 and calls[0].messages == calls[1].messages and calls[0].request_id == calls[1].request_id
