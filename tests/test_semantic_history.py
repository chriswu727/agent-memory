import pytest
from test_cognition import event, request, scope, state

from agent_memory import cognition


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
