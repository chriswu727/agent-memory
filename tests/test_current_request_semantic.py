import json

import pytest
from test_semantic import (
    batch,
    claim,
    encoded,
    event,
    proposals,
    reference,
    run,
    situation,
    verifier,
)

from agent_memory.helpers import DomainError


def current_batch(*sources, **changes):
    return batch(*sources, current_input_refs=tuple(
        {"event_id": source["event_id"], "revision": source["revision"]} for source in sources), **changes)


def request_update(source, **changes):
    return situation(source, evidence_use="current_request", value="用户正在询问是否曾约定在海边复习。", **changes)


@pytest.mark.parametrize("text", [
    "我们以前约好去海边复习，对吧？", "请回忆我们以前的复习地点。",
    "I would like to know whether we agreed to study by the sea.",
    "你记得我梦里去过海边吗？",
])
def test_bound_request_can_be_represented_without_proving_its_subject(text):
    source = event(text)
    data = current_batch(source)
    proposed = request_update(source)
    seen = []

    def verify(request):
        entry = json.loads(request.messages[-1]["content"])["proposals"][0]
        assert entry["current_input_refs"] == list(data.current_input_refs)
        assert entry["proposal"] == proposed
        seen.append(entry)
        return verifier(request)

    result = run(data, proposals(updates=[proposed]), verify=verify)
    assert result.status == "completed" and len(seen) == 1
    assert result.claims == () and result.episode_actions == ()
    accepted = result.situation_updates[0]
    assert accepted["current_only"] is True
    assert accepted["evidence_use"] == "current_request"
    assert accepted["request_verification"] == {
        "state": "supported_request", "method": "semantic_model", "not_a_truth_certificate": True}
    assert accepted["value"] == proposed["value"]


@pytest.mark.parametrize("damage", [
    "unbound", "old_source", "assistant", "tool", "draft", "wrong_conversation", "partial_span",
    "completed", "context", "observation", "conditional", "hypothetical",
])
def test_request_use_does_not_bypass_structural_or_source_authority(damage):
    source = event("我们以前约好去海边复习，对吧？")
    proposed = request_update(source)
    data = current_batch(source)
    if damage == "unbound":
        data = batch(source)
    elif damage == "old_source":
        current = event("还有别的事情吗？", sequence=2)
        data = batch(source, current, current_input_refs=(
            {"event_id": current["event_id"], "revision": current["revision"]},))
    elif damage in {"assistant", "tool"}:
        source["observed"]["actor_role"] = damage
    elif damage == "draft":
        source["observed"]["delivery_state"] = "draft"
    elif damage == "wrong_conversation":
        source["scope"]["conversation_id"] = None
    elif damage == "partial_span":
        proposed["evidence_refs"][0]["span"]["end"] -= 3
    elif damage == "completed":
        proposed["state"] = "completed"
    elif damage == "context":
        proposed["kind"] = "context"
    elif damage == "observation":
        from test_semantic_context import context

        proposed["observation"] = context(source)["observation"]
    elif damage == "conditional":
        proposed["conditions"] = {"op": "unresolved", "label": "if needed"}
    else:
        proposed["world"] = "hypothetical"
    result = run(data, proposals(updates=[proposed]),
                 verify=lambda _: pytest.fail("Invalid request cannot reach verification"))
    assert result.status == "partial" and result.situation_updates == ()
    assert result.rejected[0]["reason"] == "invalid_proposal"


@pytest.mark.parametrize("refs", [
    [{"event_id": "event-1", "revision": 2}],
    [{"event_id": "event-1", "revision": 1}] * 2,
    [{"event_id": "event-1", "revision": True}],
    [{"event_id": "missing", "revision": 1}],
])
def test_invalid_input_binding_is_rejected_before_any_model_call(refs):
    source = event("你记得我们的约定吗？")
    with pytest.raises(DomainError, match="Current inputs|current input"):
        run(batch(source, current_input_refs=tuple(refs)), proposals(),
            reserve=lambda **_: pytest.fail("Invalid binding cannot spend"))


@pytest.mark.parametrize("request_verdict", [None, "unknown", "contradicts"])
def test_whole_support_cannot_replace_request_intent_verification(request_verdict):
    source = event("我们在海边复习过吗？")

    def verify(request):
        response = json.loads(verifier(request))
        assessment = response["assessments"][0]
        if request_verdict is None:
            assessment.pop("request_verdict")
        else:
            assessment["request_verdict"] = request_verdict
        return encoded(response)

    result = run(current_batch(source), proposals(updates=[request_update(source)]), verify=verify)
    assert result.status == "partial" and result.situation_updates == ()
    assert result.rejected[0]["reason"] == "request_intent_unconfirmed"


def test_request_support_cannot_replace_the_full_update_verdict():
    source = event("你记得我们的约定吗？")

    def verify(request):
        response = json.loads(verifier(request))
        response["assessments"][0]["proposal_verdict"] = "unknown"
        return encoded(response)

    result = run(current_batch(source), proposals(updates=[request_update(source)]), verify=verify)
    assert result.situation_updates == ()
    assert result.rejected[0]["reason"] == "semantic_support_unconfirmed"


def test_question_is_still_ineligible_for_a_fact_or_ordinary_situation():
    source = event("我们在海边复习过吗？")
    response = proposals(claim(source, statement="用户曾在海边复习。", conditions={"op": "always"}),
                         updates=[situation(source), request_update(source, proposal_id="request-1")])
    result = run(current_batch(source), response)
    assert result.status == "partial" and result.claims == ()
    assert [item["proposal_id"] for item in result.situation_updates] == ["request-1"]
    assert len(result.rejected) == 2


def test_ordinary_assertions_cannot_acquire_request_verification():
    source = event("我喜欢简短回答。")

    def verify(request):
        response = json.loads(verifier(request))
        response["assessments"][0]["request_verdict"] = "supports"
        return encoded(response)

    result = run(batch(source), proposals(claim(source)), verify=verify)
    assert result.status == "invalid_response" and result.claims == ()


def test_request_cannot_reference_a_supporting_old_statement_as_current():
    prior = event("我们上次在海边复习。")
    current = event("请告诉我上次在哪复习。", sequence=2)
    proposed = request_update(current)
    proposed["evidence_refs"].append(reference(prior))
    data = batch(prior, current, current_input_refs=({"event_id": current["event_id"], "revision": 1},))
    result = run(data, proposals(updates=[proposed]))
    assert result.status == "partial" and result.situation_updates == ()
    assert data.current_input_refs == ({"event_id": current["event_id"], "revision": 1},)
