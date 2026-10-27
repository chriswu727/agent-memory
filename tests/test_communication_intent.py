import copy
import json
from dataclasses import replace

import pytest
from psycopg.types.json import Jsonb
from test_current_request_runtime import orientation_job
from test_learning import learning as learning
from test_semantic import batch, encoded, event, situation, verifier

from membrain.helpers import digest
from membrain.learning import LearningEngine
from membrain.semantic import (
    COMMUNICATION_INTENT_INPUT_VERSION,
    EPISODE_SCOPE_INPUT_VERSION,
    GENERAL_PROPOSAL_MODE,
    LATEST_INPUT_CONTRACT_VERSION,
    ORIENT_PROPOSAL_MODE,
    ORIENT_VERIFICATION_SCHEMA,
    VERIFICATION_SCHEMA,
    SemanticProcessor,
    proposal_data,
    proposal_schema,
    request_payload,
)


@pytest.mark.parametrize("version,mode,phase,expected", [
    (None, GENERAL_PROPOSAL_MODE, "proposal", "77e8ac048499cf7ed7f88a9c3b91be69813eaa2de0e6647a7cb5836b0d5081e8"),
    (None, GENERAL_PROPOSAL_MODE, "verification", "86c9de92e7f7b19c268daefdbff4f5e5004464068412d06f5de2e358a3124876"),
    (None, ORIENT_PROPOSAL_MODE, "proposal", "a2724f4012561471d2b377396ef041280ca5f5d090befec2ebd185be5df49531"),
    (None, ORIENT_PROPOSAL_MODE, "verification", "cba72a691c67752eb17643aac0c2241246d377b7b7b5a29e4a5cf32d4648c97f"),
    (EPISODE_SCOPE_INPUT_VERSION, GENERAL_PROPOSAL_MODE, "proposal",
     "0805de66b333ba15987afd5582ae52bffb14e99d1f9e70314b25c19c09ef1414"),
    (EPISODE_SCOPE_INPUT_VERSION, GENERAL_PROPOSAL_MODE, "verification",
     "86c9de92e7f7b19c268daefdbff4f5e5004464068412d06f5de2e358a3124876"),
    (EPISODE_SCOPE_INPUT_VERSION, ORIENT_PROPOSAL_MODE, "proposal",
     "a2724f4012561471d2b377396ef041280ca5f5d090befec2ebd185be5df49531"),
    (EPISODE_SCOPE_INPUT_VERSION, ORIENT_PROPOSAL_MODE, "verification",
     "cba72a691c67752eb17643aac0c2241246d377b7b7b5a29e4a5cf32d4648c97f"),
])
def test_frozen_legacy_wire_messages_remain_byte_equivalent(version, mode, phase, expected):
    schema = proposal_schema(mode) if phase == "proposal" else (
        ORIENT_VERIFICATION_SCHEMA if mode == ORIENT_PROPOSAL_MODE else VERIFICATION_SCHEMA)
    messages, _ = request_payload(phase, {"probe": "frozen historical input"}, schema, 4096,
                                  proposal_mode=mode, input_contract_version=version)
    assert digest(messages) == expected


@pytest.mark.parametrize("mode", [GENERAL_PROPOSAL_MODE, ORIENT_PROPOSAL_MODE])
@pytest.mark.parametrize("authorized", [True, False])
def test_current_contract_separates_source_authority_from_request_intent(mode, authorized):
    source = event("I prefer tea now.")
    references = [{"event_id": source["event_id"], "revision": source["revision"]}] if authorized else []
    before = copy.deepcopy(source)
    old = proposal_data([source], [], {}, [], {}, [], current_input_refs=references,
                        proposal_mode=mode, input_contract_version=EPISODE_SCOPE_INPUT_VERSION)
    current = proposal_data([source], [], {}, [], {}, [], current_input_refs=references, proposal_mode=mode)
    assert current.pop("communication_intent_contract") == {
        "version": COMMUNICATION_INTENT_INPUT_VERSION, "current_input_citations_authorized": authorized,
        "reference_semantics": "citation_authority_not_request_evidence",
    }
    assert current == old and source == before
    assert current["current_input_refs"] == references
    if mode == GENERAL_PROPOSAL_MODE:
        assert "episode_operation_scope" in current and current["learning_input_refs"] == []
    else:
        assert "episode_operation_scope" not in current


def current_batch(source):
    return batch(source, proposal_mode=ORIENT_PROPOSAL_MODE, current_input_refs=(
        {"event_id": source["event_id"], "revision": source["revision"]},))


@pytest.mark.parametrize("text", [
    "I prefer tea now.",
    "我现在换成先散步，再复习。",
    "When I am working, I prefer direct advice.",
    'My friend said, "Please remind me to study."',
    "Yesterday I asked you to remember my preference.",
])
def test_unknown_request_intent_still_rejects_accurate_report_without_repair_or_extra_calls(text):
    source = event(text)
    proposed = situation(source, evidence_use="current_request", value="The user requests that this be remembered.")
    calls = []

    def propose(request):
        calls.append(request)
        return encoded({"situation_updates": [proposed]})

    def verify(request):
        calls.append(request)
        response = json.loads(verifier(request))
        response["assessments"][0]["request_verdict"] = "unknown"
        return encoded(response)

    result = SemanticProcessor(propose, verify).process(current_batch(source), reserve=lambda **_: None)
    assert result.model_calls == len(calls) == 2
    assert result.status == "partial" and not result.situation_updates and not result.claims
    assert result.rejected == ({"proposal_id": proposed["proposal_id"], "reason": "request_intent_unconfirmed"},)
    assert calls[1].response_schema == ORIENT_VERIFICATION_SCHEMA
    assert json.loads(calls[1].messages[-1]["content"])["proposals"][0]["proposal"] == proposed


@pytest.mark.parametrize("text,value", [
    ("Please remember that I prefer tea.", "The user asks the assistant to remember their tea preference."),
    ("Could you tell me whether we agreed to study by the sea?",
     "The user asks whether there was an earlier agreement to study by the sea."),
    ("请帮我回忆之前约好的时间。", "用户请求回忆先前约定的时间，尚未确认是否存在该约定。"),
    ("I prefer tea now; please compare it with my previous order.",
     "The user asks the assistant to compare their reported tea preference with their previous order."),
])
def test_supported_actual_request_retains_its_subject_without_asserting_presuppositions(text, value):
    source = event(text)
    proposed = situation(source, evidence_use="current_request", value=value)
    requests = []
    processor = SemanticProcessor(
        lambda request: requests.append(request) or encoded({"situation_updates": [proposed]}), verifier)
    result = processor.process(current_batch(source), reserve=lambda **_: None)
    assert result.status == "completed" and result.model_calls == 2
    assert result.situation_updates[0]["value"] == value
    assert result.situation_updates[0]["request_verification"]["state"] == "supported_request"
    assert not result.claims and not result.episode_actions
    assert "observation" not in result.situation_updates[0]
    assert json.loads(requests[0].messages[-1]["content"])["communication_intent_contract"][
        "current_input_citations_authorized"] is True


def test_report_can_be_supported_without_request_or_can_produce_no_update():
    source = event("I prefer tea now.")
    proposed = situation(source, kind="context", key="reported_preference", value="The user reports preferring tea.",
                         evidence_use="source_content")
    processor = SemanticProcessor(lambda _: encoded({"situation_updates": [proposed]}), verifier)
    result = processor.process(current_batch(source), reserve=lambda **_: None)
    assert result.status == "completed" and result.situation_updates[0]["evidence_use"] == "source_content"
    assert "request_verification" not in result.situation_updates[0]
    empty = SemanticProcessor(lambda _: encoded({"situation_updates": []}),
                              lambda _: pytest.fail("No proposal needs verification"))
    result = empty.process(current_batch(source), reserve=lambda **_: None)
    assert result.status == "completed" and result.model_calls == 1 and result.learning_outcome == "no_proposals"


def test_new_contract_changes_operation_identity_and_counts_all_prompt_bytes():
    source = event("I prefer tea now.")
    requests = []
    processor = SemanticProcessor(lambda request: requests.append(request) or encoded({"situation_updates": []}))
    current = current_batch(source)
    for version in (None, EPISODE_SCOPE_INPUT_VERSION, COMMUNICATION_INTENT_INPUT_VERSION):
        result = processor.process(replace(current, input_contract_version=version), reserve=lambda **_: None)
        request = requests[-1]
        _, size = request_payload("proposal", json.loads(request.messages[-1]["content"]), request.response_schema,
                                  request.max_output_tokens, proposal_mode=ORIENT_PROPOSAL_MODE,
                                  input_contract_version=version)
        assert result.input_bytes == size
    assert requests[0].request_id == requests[1].request_id
    assert requests[1].request_id != requests[2].request_id


@pytest.mark.integration
@pytest.mark.parametrize("version", [None, EPISODE_SCOPE_INPUT_VERSION, COMMUNICATION_INTENT_INPUT_VERSION])
def test_frozen_orientation_contract_is_exactly_preserved_across_restart(learning, version):
    engine, _, job = orientation_job(learning)
    current, snapshot = engine._snapshot(learning.principal, learning.brain, job)
    assert current.input_contract_version == LATEST_INPUT_CONTRACT_VERSION
    snapshot["input_contract_version"] = version
    with learning.database.transaction(learning.principal) as connection:
        connection.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                           (Jsonb(snapshot), learning.principal.tenant_id, learning.principal.principal_id,
                            learning.brain, job["id"]))
    batch_before, frozen_before = engine._snapshot(learning.principal, learning.brain, job)
    batch_after, frozen_after = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, job)
    assert frozen_before == frozen_after == snapshot
    assert batch_before == batch_after and batch_after.input_contract_version == version
    calls = []
    processor = SemanticProcessor(lambda request: calls.append(request) or encoded({"situation_updates": []}))
    for selected in (batch_before, batch_after):
        processor.process(selected, reserve=lambda **_: None)
    assert calls[0].messages == calls[1].messages and calls[0].request_id == calls[1].request_id
