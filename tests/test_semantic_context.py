import copy
import json

import pytest
from test_cognition import event
from test_semantic import batch, claim, encoded, proposals, reference, run, situation, verifier

from agent_memory.applicability import SCHEMA_DEFINITIONS
from agent_memory.contracts import schema
from agent_memory.helpers import DomainError
from agent_memory.ontology import CORE_REGISTRY
from agent_memory.semantic import PROPOSAL_SCHEMA, SemanticProcessor
from agent_memory.temporal import evaluate_valid_during, resolve_today


def condition():
    return {"op": "holds", "subject_ref": "user", "predicate_ref": {"id": "core.activity", "version": 1},
            "value": {"type": "concept", "id": "activity:exam_preparation"}}


def observation(source, **changes):
    result = {key: value for key, value in condition().items() if key != "op"}
    result.update(observation_id="current-activity", polarity="positive", valid_during={"kind": "unknown"},
                  evidence_refs=[reference(source)])
    result.update(changes)
    return result


def context(source, **changes):
    return situation(source, kind="context", observation=observation(source), **changes)


def test_typed_rules_and_context_remain_separate_proposals():
    rule = event("备考时我喜欢安静。")
    current = event("我正在备考。", sequence=2)
    result = run(batch(rule, current), proposals(
        claim(rule, conditions=condition(), valid_during={"kind": "unknown"}), updates=[context(current)]))
    assert result.status == "completed"
    assert result.claims[0]["conditions"] == condition()
    assert result.claims[0]["condition_registry_fingerprint"] == CORE_REGISTRY.fingerprint
    assert result.claims[0]["valid_during"] == {"kind": "unknown"}
    assert result.situation_updates[0]["observation"]["evidence_refs"] == [reference(current)]
    assert result.situation_updates[0]["current_only"] is True


def test_new_claim_cannot_omit_typed_validity_before_verification():
    source = event("备考时我喜欢安静。")
    proposal = claim(source, conditions=condition())
    proposal.pop("valid_during")
    assert "valid_during" in PROPOSAL_SCHEMA["properties"]["claims"]["items"]["required"]
    result = run(batch(source), proposals(proposal), verify=lambda _: pytest.fail("Incomplete validity must not reach verification"))
    assert result.status == "invalid_response"
    assert result.model_calls == 1 and result.claims == ()


@pytest.mark.parametrize("validity,at,expected", [
    ({"kind": "unknown"}, "2026-10-24T00:00:00Z", "unknown"),
    ({"kind": "interval", "start": {"kind": "instant", "at": "2026-10-20T00:00:00Z"},
      "end": {"kind": "unbounded"}}, "2026-10-19T23:59:59Z", "false"),
    ({"kind": "interval", "start": {"kind": "instant", "at": "2026-10-20T00:00:00Z"},
      "end": {"kind": "unbounded"}}, "2026-10-20T00:00:00Z", "true"),
    ({"kind": "interval", "start": {"kind": "instant", "at": "2026-10-20T00:00:00Z"},
      "end": {"kind": "unknown"}}, "2026-10-24T00:00:00Z", "unknown"),
])
def test_verifier_sees_the_exact_validity_that_will_be_saved(validity, at, expected):
    start = validity.get("start", {}).get("at")
    text = "从2026年10月20日00:00 UTC起，我希望回答简短。" if start else "我希望回答简短。"
    if validity.get("end", {}).get("kind") == "unbounded":
        text += "没有预定结束日期。"
    source = event(text)
    proposed = claim(source, statement=text.replace("我", "用户"),
                     conditions={"op": "always"}, valid_during=copy.deepcopy(validity),
                     valid_time={"start": start, "end": None, "precision": "exact" if start else "unknown",
                                 "basis": "source_explicit" if start else "unknown"})
    observed = []

    def verify(request):
        observed.extend(item["proposal"] for item in json.loads(request.messages[-1]["content"])["proposals"])
        return verifier(request)

    result = run(batch(source), proposals(proposed), verify=verify)
    assert result.status == "completed" and observed == [proposed]
    assert result.claims[0]["valid_during"] == observed[0]["valid_during"] == validity
    assert result.claims[0]["valid_time"]["end"] is None
    assert evaluate_valid_during(result.claims[0]["valid_during"], at=at).value == expected


def test_proposal_schema_keeps_optional_context_but_requires_observation_and_link_validity():
    from jsonschema import Draft202012Validator

    source = event("我正在备考。")
    validator = Draft202012Validator(PROPOSAL_SCHEMA)
    assert validator.is_valid(proposals(updates=[situation(source)]))
    update = context(source)
    update["observation"].pop("valid_during")
    assert not validator.is_valid(proposals(updates=[update]))
    assert "valid_during" in PROPOSAL_SCHEMA["properties"]["belief_links"]["items"]["required"]


def test_claim_relative_today_remains_unknown_without_authenticated_timezone():
    source = event("今天我希望回答简短。")
    anchored = resolve_today(anchor_time="2026-10-22T12:00:00Z", timezone_name="America/Vancouver",
                             anchor_ref={"event_id": source["event_id"], "revision": 1}, reliable=True)
    result = run(batch(source), proposals(claim(source, valid_during=anchored)),
                 verify=lambda _: pytest.fail("Untrusted time anchor cannot reach verification"))
    assert result.status == "partial" and result.claims == ()
    result = run(batch(source), proposals(claim(source, valid_during={"kind": "unknown"})))
    assert result.status == "completed" and result.claims[0]["valid_during"] == {"kind": "unknown"}


def test_unsupported_open_end_is_rejected_by_verification_with_validity_visible():
    source = event("从2026年10月20日00:00 UTC起，我希望回答简短。")
    validity = {"kind": "interval", "start": {"kind": "instant", "at": "2026-10-20T00:00:00Z"},
                "end": {"kind": "unbounded"}}

    def verify(request):
        proposed = json.loads(request.messages[-1]["content"])["proposals"][0]["proposal"]
        assert proposed["valid_during"] == validity
        response = json.loads(verifier(request))
        response["assessments"][0]["proposal_verdict"] = "unknown"
        return encoded(response)

    result = run(batch(source), proposals(claim(source, valid_during=validity)), verify=verify)
    assert result.status == "partial" and result.claims == ()
    assert result.rejected[0]["reason"] == "semantic_support_unconfirmed"


@pytest.mark.parametrize("changes", [
    {"kind": "goal"}, {"state": "uncertain"}, {"conditions": condition()},
])
def test_context_observation_requires_an_unconditional_active_context_update(changes):
    source = event("我正在备考。")
    update = context(source)
    update.update(changes)
    result = run(batch(source), proposals(updates=[update]))
    assert not result.situation_updates
    assert result.rejected[0]["reason"] == "invalid_proposal"


def test_assistant_cannot_make_its_own_context_fact():
    source = event("用户正在备考。", role="assistant")
    result = run(batch(source), proposals(updates=[context(source)]))
    assert result.status == "partial"
    assert not result.situation_updates


def test_question_does_not_establish_context():
    source = event("我是不是正在备考？")
    result = run(batch(source), proposals(updates=[context(source)]))
    assert not result.situation_updates


def test_nested_observation_cannot_smuggle_an_unverified_reference():
    source = event("我正在备考。")
    unrelated = event("天气晴朗。", sequence=2)
    update = context(source)
    update["observation"]["evidence_refs"] = [reference(unrelated)]
    result = run(batch(source, unrelated), proposals(updates=[update]))
    assert not result.situation_updates
    assert result.model_calls == 1


@pytest.mark.parametrize("field,value", [
    ("subject_ref", "someone_else"),
    ("predicate_ref", {"id": "core.activity", "version": 2}),
    ("value", {"type": "concept", "id": "activity:unregistered"}),
    ("value", {"type": "string", "value": "备考"}),
])
def test_unknown_or_mistyped_observation_is_not_coerced(field, value):
    source = event("我正在备考。")
    update = context(source)
    update["observation"][field] = value
    result = run(batch(source), proposals(updates=[update]))
    assert not result.situation_updates
    assert result.rejected[0]["reason"] == "invalid_proposal"


def test_negative_context_retains_polarity_and_its_own_evidence():
    source = event("我现在没有备考。")
    update = context(source)
    update["observation"]["polarity"] = "negative"
    result = run(batch(source), proposals(updates=[update]))
    assert result.situation_updates[0]["observation"]["polarity"] == "negative"


def test_verifier_must_support_observation_not_just_rule_evidence():
    source = event("备考时我喜欢安静。")

    def independent_verifier(request):
        assert "Conditions in a rule are not observations" in request.messages[0]["content"]
        result = json.loads(verifier(request))
        result["assessments"][0]["relations"][0]["verdict"] = "unknown"
        return encoded(result)

    result = run(batch(source), proposals(updates=[context(source)]), verify=independent_verifier)
    assert not result.situation_updates
    assert result.rejected[0]["reason"] == "semantic_support_unconfirmed"


def test_today_anchor_requires_host_timezone_provenance():
    source = event("今天我正在备考。")
    valid = resolve_today(anchor_time="2026-10-22T12:00:00Z", timezone_name="America/Vancouver",
                          anchor_ref={"event_id": source["event_id"], "revision": 1}, reliable=True)
    update = context(source)
    update["observation"]["valid_during"] = valid
    assert not run(batch(source), proposals(updates=[update])).situation_updates
    source["observed"]["occurred_time"] = {
        "start": "2026-10-22T12:00:00Z", "end": None, "precision": "exact", "basis": "source_explicit"}
    assert not run(batch(source), proposals(updates=[update])).situation_updates
    changed = copy.deepcopy(update)
    changed["observation"]["valid_during"]["anchor"]["event_id"] = "another-source"
    assert not run(batch(source), proposals(updates=[changed])).situation_updates
    update["observation"]["valid_during"] = {"kind": "unknown"}
    assert run(batch(source), proposals(updates=[update])).situation_updates


def test_incomplete_related_history_blocks_long_term_changes_only():
    source = event("我正在备考。")
    result = run(batch(source, sampling={"publication_restricted": True}),
                 proposals(claim(source, conditions=condition()), updates=[context(source)]))
    assert result.status == "partial"
    assert not result.claims
    assert result.situation_updates
    assert result.rejected[0]["reason"] == "incomplete_history_package"


def test_model_receives_registry_definitions_and_sampling_limits():
    source = event("我正在备考。")
    received = []

    def propose(request):
        received.append(json.loads(request.messages[-1]["content"]))
        return encoded(proposals())

    SemanticProcessor(propose, verifier).process(
        batch(source, sampling={"search_exhaustive": False}), reserve=lambda **_: None)
    assert received[0]["registry"] == CORE_REGISTRY.to_dict()
    assert received[0]["sampling"] == {"search_exhaustive": False}


def test_protocol_and_processor_share_typed_definitions():
    for name, definition in SCHEMA_DEFINITIONS.items():
        assert schema()["$defs"][name] == definition


@pytest.mark.parametrize("sampling", [None, [], {"publication_restricted": "false"},
                                     {"publication_restricted": 1}, {"data": "x" * 16385}])
def test_malformed_sampling_is_rejected_before_any_model_call(sampling):
    source = event("我正在备考。")
    processor = SemanticProcessor(lambda _: pytest.fail("Invalid input must not call a model"))
    with pytest.raises(DomainError) as error:
        processor.process(batch(source, sampling=sampling), reserve=lambda **_: pytest.fail("No reservation"))
    assert error.value.code == "validation_failed"
