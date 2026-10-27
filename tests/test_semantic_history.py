import copy

import pytest
from test_cognition import event, request, scope, state

from membrain import cognition


def instruction_preference(source):
    memory = cognition.extract_event(source)[0]
    memory.update(statement="用户希望回答简短。", claim_kind="reported_preference",
                  epistemic_status="derived_inference", memory_kind="interaction_preference",
                  representation="semantic_anchor", conditions={"op": "always"},
                  valid_during={"kind": "unknown"}, verification={"state": "supported_inference",
                  "method": "semantic_model", "not_a_truth_certificate": True})
    return memory


def recall_preference(memory, source, *, cue=None):
    cue = cue or event("我的回答偏好是什么？", sequence=2)
    return cognition.recall(scope(), request("回答简短", intent="continue_conversation",
        evidence_mode="prior_record_check", cue_event_ids=[cue["event_id"]], query_event_ids=[cue["event_id"]]),
        [memory], [source, cue], state(2))


@pytest.mark.parametrize("verification_state", ["supported_inference", "contested"])
def test_verified_prior_preference_can_recall_a_complete_user_instruction(verification_state):
    source = event("请用简短回答。")
    memory = instruction_preference(source)
    memory["verification"]["state"] = verification_state
    assert len(recall_preference(memory, source)["items"]) == 1


@pytest.mark.parametrize("change", [
    "literal", "ordinary_claim", "supported_report", "missing_verification", "unchecked", "wrong_method",
    "partial_span", "no_span", "current_input", "assistant", "user_delivered", "question", "mixed_question",
])
def test_preference_instruction_exception_cannot_admit_unverified_or_ineligible_history(change):
    text = "请用简短回答。"
    if change == "question":
        text = "请用简短回答，好吗？"
    elif change == "mixed_question":
        text += "我们约好了去海边，对吗？"
    source = event(text)
    memory = instruction_preference(source)
    if change == "literal":
        memory = cognition.extract_event(source)[0]
    elif change == "ordinary_claim":
        memory["claim_kind"] = "reported_fact"
    elif change == "supported_report":
        memory["epistemic_status"] = "supported_report"
    elif change == "missing_verification":
        memory.pop("verification")
    elif change == "unchecked":
        memory["verification"]["state"] = "unverified"
    elif change == "wrong_method":
        memory["verification"]["method"] = "human_review"
    elif change in {"partial_span", "no_span", "mixed_question"}:
        for reference in [*memory["evidence_refs"], *memory["disclosure_basis"]["support_paths"][0]["evidence_refs"]]:
            if change == "partial_span":
                reference["span"]["start"] += 1
            elif change == "no_span":
                reference.pop("span")
            else:
                reference["span"]["end"] = len(text)
    elif change == "assistant":
        source["observed"].update(actor_role="assistant", delivery_state="delivered")
    elif change == "user_delivered":
        source["observed"]["delivery_state"] = "delivered"
    cue = copy.deepcopy(source) if change == "current_input" else None
    assert recall_preference(memory, source, cue=cue)["items"] == []


def test_working_episode_eligibility_does_not_inherit_preference_instruction_exception():
    source = event("请用简短回答。")
    reference = instruction_preference(source)["evidence_refs"][0]
    assert not cognition._eligible_source(source, reference, True, "episode")


def historical(memory, sources, **changes):
    return cognition.recall(scope(), request("书店", evidence_mode="prior_record_check",
                                            evidence_cutoff={"mode": "through_sequence", "through_sequence": 2}),
                            [memory], sources, state(3, **changes))


def test_prior_assistant_context_root_is_allowed_without_treating_it_as_positive_world_evidence():
    source = event("昨天我去过书店。")
    assistant = event("我们可以继续聊聊。", 2, "assistant")
    memory = cognition.extract_event(source)[0]
    memory["root_event_ids"] = [source["event_id"], assistant["event_id"]]
    result = historical(memory, [source, assistant])
    assert len(result["items"]) == 1
    assert result["items"][0]["memory"]["evidence_refs"][0]["event_id"] == source["event_id"]


@pytest.mark.parametrize("violation", ["future", "missing", "wrong_scope", "corrected"])
def test_context_root_cannot_bypass_historical_source_availability(violation):
    source = event("昨天我去过书店。")
    context = event("本轮可能影响记忆的上下文。", 3 if violation == "future" else 2)
    memory = cognition.extract_event(source)[0]
    memory["root_event_ids"] = [source["event_id"], context["event_id"]]
    sources = [source, context]
    changes = {}
    if violation == "missing":
        sources = [source]
    elif violation == "wrong_scope":
        context["scope"] = scope(conversation_id="other", visibility="conversation")
    elif violation == "corrected":
        changes["superseded_evidence_refs"] = cognition.extract_event(context)[0]["evidence_refs"]
    assert historical(memory, sources, **changes)["items"] == []


def test_excluded_old_cue_root_is_rejected_even_before_explicit_cutoff():
    source = event("昨天我去过书店。")
    cue = event("我去过书店吗？", 2)
    memory = cognition.extract_event(source)[0]
    memory["root_event_ids"] = [source["event_id"], cue["event_id"]]
    result = cognition.recall(scope(), request(
        "书店", evidence_mode="prior_record_check", cue_event_ids=[cue["event_id"]],
        evidence_cutoff={"mode": "through_sequence", "through_sequence": 3},
    ), [memory], [source, cue], state(3))
    assert result["items"] == []
