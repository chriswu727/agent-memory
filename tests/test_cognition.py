import copy
import json

import pytest

from agent_memory import cognition
from agent_memory.contracts import validate
from agent_memory.helpers import DomainError, digest

STAMP = "2026-10-22T12:00:00Z"
UNKNOWN_TIME = {"start": None, "end": None, "precision": "unknown", "basis": "unknown"}


def scope(**changes):
    result = {
        "tenant_id": "tenant", "brain_id": "brain", "principal_binding_id": "owner",
        "conversation_id": "conversation", "branch_id": "main", "visibility": "private",
        "access_policy_id": "private-owner", "policy_revision": 1, "scope_generation": 1,
    }
    result.update(changes)
    return result


def event(text, sequence=1, role="user", **changes):
    content = {"media_type": "text/plain", "text": text}
    result = {
        "event_id": f"event-{sequence}", "revision": 1, "sequence": sequence, "scope": scope(),
        "observed": {
            "source_event_key": f"source-{sequence}", "source_revision": 1,
            "producer_ref": "adapter", "actor_ref": role, "actor_role": role,
            "event_kind": "tool_result" if role == "tool" else "message", "content": content,
            "occurred_time": copy.deepcopy(UNKNOWN_TIME), "interpretation_hint": "unknown",
            "delivery_state": "delivered" if role == "assistant" else "received",
            "parent_source_event_keys": [],
        },
        "system_time": {"recorded_at": STAMP, "superseded_at": None},
        "content_sha256": digest(content),
        "origin": {"user": "user_statement", "assistant": "assistant_output",
                   "tool": "tool_observation"}[role],
        "origin_verification": "verified_producer", "previous_revision": None,
        "utterance_segments": cognition.classify_segments(content),
    }
    result.update(changes)
    return result


def budget(tokens=100000):
    return {
        "max_context_tokens": tokens, "max_model_calls": 0, "max_embedding_calls": 0,
        "max_expansion_steps": 0, "max_wall_time_ms": 30000, "max_cost_microunits": 0,
        "currency": "USD", "token_counting": "conservative_estimate", "tokenizer_id": None,
    }


@pytest.mark.parametrize("kind", ["source_correction", "delivery_receipt"])
def test_internal_audit_records_never_become_conversation_memories(kind):
    source = event("系统内部的更正审计记录")
    old_projection = cognition.extract_event(source)
    source["observed"]["event_kind"] = kind
    assert cognition.extract_event(source) == []
    recalled = cognition.recall(scope(), request("更正"), old_projection, [source], state())
    assert recalled["items"] == []


def fence(sequence=0):
    return {"minimum_durable_sequence": sequence, "consistency": "read_your_writes",
            "snapshot_revision": None}


def request(query="书店", **changes):
    result = {
        "query": query, "intent": "explore", "cue_event_ids": [], "additional_cues": [],
        "read_fence": fence(), "budget": budget(),
    }
    result.update(changes)
    return result


def state(sequence=1, **changes):
    result = {"durable_sequence": sequence, "indexed_sequence": sequence,
              "dependency_generation": 0, "scope_generation": 1, "candidate_total": 1}
    result.update(changes)
    return result


def capabilities():
    return {
        "adapter_id": "test", "adapter_version": "1", "protocol_version": "1.0.0-draft",
        "transport": "sdk", "supported_actions": ["prepare", "recall"],
        "capture": dict.fromkeys(("user_messages", "delivered_assistant_messages", "tool_results",
                                  "message_edits", "message_deletions", "stable_source_ids",
                                  "branch_identity"), False),
        "hooks": dict.fromkeys(("before_turn", "after_delivery", "before_compaction", "on_resume"),
                               False),
        "context_control": "tool_output_only", "active_recall": True,
        "automatic_capture": False, "automatic_recall": False, "guarantee_level": "tools_only",
        "verification_state": "declared", "verified_at": None, "limitations": [],
    }


def prepare_payload(tokens=100000):
    return {"turn_id": "turn", "latest_input_event_ids": [], "read_fence": fence(),
            "budget": budget(tokens), "capabilities": capabilities()}


def working():
    return {"scope": scope(), "revision": 0, "through_sequence": 0, "items": [],
            "open_loops": [], "updated_at": STAMP}


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: STAMP)


def test_literal_capture_preserves_question_and_statement_boundaries():
    source = event("昨天书店关门了。你还记得我们去过海边吗？")
    source["utterance_segments"] = [{"act": "direct_statement"}]
    memories = cognition.extract_event(source)
    assert len(memories) == 2
    assert "direct_statement" in memories[0]["statement"]
    assert "inquiry" in memories[1]["statement"]
    for memory in memories:
        validate("MemoryAssertion", memory)
        assert memory["memory_kind"] == "episodic"
        ref = memory["evidence_refs"][0]
        span = ref["span"]
        assert source["observed"]["content"]["text"][span["start"]:span["end"]] in (
            memory["statement"]
        )


@pytest.mark.parametrize("text,world", [
    ("我梦见我们去了海边。", "dream"),
    ("如果我们去了海边，我会游泳。", "hypothetical"),
    ("角色扮演：我们去了海边。", "roleplay"),
    ("引用原文如下：我们去了海边。", "quoted"),
])
def test_world_boundaries_override_optimistic_hint(text, world):
    source = event(text)
    source["observed"]["interpretation_hint"] = "real_world_report"
    assert cognition.extract_event(source)[0]["world"] == world


@pytest.mark.parametrize("delivery", ["draft", "cancelled", "unknown"])
def test_unexperienced_outputs_are_not_encoded(delivery):
    source = event("我们去了海边。", role="assistant")
    source["observed"]["delivery_state"] = delivery
    assert cognition.extract_event(source) == []


def test_tool_observation_and_assistant_speech_remain_distinct():
    tool = cognition.extract_event(event("exit_code=0", role="tool"))[0]
    assistant = cognition.extract_event(event("任务已完成。", role="assistant"))[0]
    assert tool["epistemic_status"] == "observed_result"
    assert assistant["epistemic_status"] == "supported_report"
    assert assistant["statement"].startswith("assistant ")
    assert assistant["memory_kind"] != "procedural"


def test_false_current_prompt_cannot_prove_a_historical_event():
    cue = event("还记得我们去过海边吗？")
    output = cognition.recall(scope(), request(
        cue["observed"]["content"]["text"], cue_event_ids=[cue["event_id"]],
        evidence_mode="contextual",
        evidence_cutoff={"mode": "through_sequence", "through_sequence": 100},
    ), cognition.extract_event(cue), [cue], state())
    assert output["items"] == []
    assert output["retrieval_snapshot"]["resolved_evidence_through_sequence"] == 0
    assert output["retrieval_snapshot"]["historical_evidence_status"] == "enforced"
    assert output["status"] == "no_match"


def test_repeated_questions_and_assistant_echo_do_not_become_old_evidence():
    sources = [event("还记得我们去过海边吗？"),
               event("我们曾经去过海边。", 2, "assistant"),
               event("海边那一次，还记得吗？", 3)]
    sources[1]["observed"]["parent_source_event_keys"] = ["source-1"]
    sources[2]["observed"]["parent_source_event_keys"] = ["source-1"]
    memories = [memory for source in sources for memory in cognition.extract_event(source)]
    output = cognition.recall(scope(), request(
        sources[2]["observed"]["content"]["text"], intent="cue_recovery",
        cue_event_ids=["event-1", "event-3"],
    ), memories, sources, state(3, candidate_total=len(memories)))
    assert output["items"] == []
    assert output["retrieval_snapshot"]["excluded_cue_event_ids"] == [
        "event-1", "event-2", "event-3"
    ]


def test_cue_recovers_additional_original_detail_and_not_current_presupposition():
    old = event("下雨那天书店关门了，门口有只橘猫。")
    cue = event("还记得下雨那天的书店吗？", 2)
    memories = cognition.extract_event(old) + cognition.extract_event(cue)
    output = cognition.recall(scope(), request(
        cue["observed"]["content"]["text"], cue_event_ids=["event-2"],
    ), memories, [old, cue], state(2, candidate_total=2))
    assert len(output["items"]) == 1
    assert "橘猫" in output["items"][0]["memory"]["statement"]
    assert output["items"][0]["memory"]["evidence_refs"][0]["event_id"] == "event-1"
    assert output["status"] == "partial"


def test_unavailable_history_boundary_is_explicit_and_not_reconstructed():
    source = event("海边有只橘猫。")
    output = cognition.recall(scope(), request("还记得海边吗？"),
                              cognition.extract_event(source), [source], state())
    assert output["status"] == "partial"
    assert output["items"] == []
    assert output["retrieval_snapshot"]["historical_evidence_status"] == "unavailable"


def test_deleted_or_revised_sources_cannot_be_returned():
    source = event("书店门口有只橘猫。")
    memories = cognition.extract_event(source)
    output = cognition.recall(scope(), request(), memories, [], state())
    assert output["items"] == []
    assert "橘猫" not in json.dumps(output, ensure_ascii=False)
    assert "source_unavailable" in output["coverage"]["limitations"]
    source["revision"] = 2
    output = cognition.recall(scope(), request(), memories, [source], state())
    assert output["items"] == []


def test_hash_tampering_is_rejected_at_both_boundaries():
    source = event("书店门口有只橘猫。")
    memories = cognition.extract_event(source)
    source["observed"]["content"]["text"] = "书店里藏有不同的内容。"
    with pytest.raises(DomainError, match="hash"):
        cognition.extract_event(source)
    assert cognition.recall(scope(), request(), memories, [source], state())["items"] == []


def test_invalidated_generation_does_not_wait_for_worker():
    source = event("小林是我的伴侣。")
    memories = cognition.extract_event(source)
    output = cognition.recall(scope(), request("小林"), memories, [source],
                              state(dependency_generation=1))
    assert output["items"] == []
    assert output["coverage"]["rebuilding"] is True
    assert output["status"] == "partial"


def test_private_source_cannot_be_widened_by_a_derived_shared_scope():
    source = event("书店旁边的医院是我的复查地点。")
    memories = cognition.extract_event(source)
    public = scope(visibility="conversation")
    memories[0]["scope"] = copy.deepcopy(public)
    memories[0]["disclosure_basis"]["support_paths"][0]["permitted_scope"] = copy.deepcopy(public)
    output = cognition.recall(public, request(), memories, [source], state())
    assert output["items"] == []


def test_same_owner_private_memories_can_cross_sessions_but_not_tenants():
    source = event("书店门口有只橘猫。", scope=scope(conversation_id="older", branch_id="old"))
    memories = cognition.extract_event(source)
    assert cognition.recall(scope(), request(), memories, [source], state())["items"]
    assert not cognition.recall(scope(tenant_id="other"), request(), memories, [source],
                                 state())["items"]


def test_temporal_filter_uses_instants_not_lexical_timezone_order():
    source = event("书店会议在这个时刻开始。")
    source["observed"]["occurred_time"] = {
        "start": "2026-10-22T04:00:00-08:00", "end": "2026-10-22T04:00:00-08:00",
        "precision": "exact", "basis": "source_explicit",
    }
    memories = cognition.extract_event(source)
    wanted = {"start": "2026-10-22T11:59:00Z", "end": "2026-10-22T12:01:00Z",
              "precision": "exact", "basis": "source_explicit"}
    assert cognition.recall(scope(), request(valid_time_filter=wanted), memories, [source],
                             state())["items"]
    wanted["end"] = "2026-10-22T11:58:00Z"
    with pytest.raises(DomainError, match="ends before"):
        cognition.recall(scope(), request(valid_time_filter=wanted), memories, [source], state())


def test_dream_is_only_retrieved_when_its_world_is_requested():
    source = event("我梦见书店门口有只橘猫。")
    memories = cognition.extract_event(source)
    assert not cognition.recall(scope(), request(), memories, [source], state())["items"]
    result = cognition.recall(scope(), request(world_filter=["dream"]), memories, [source], state())
    assert result["items"][0]["memory"]["world"] == "dream"


@pytest.mark.parametrize("blocked_by", [None, "world", "scope", "evidence", "revision"])
def test_model_selection_changes_relevance_but_not_evidence_eligibility(blocked_by):
    source = event("河畔有一只小狗。")
    memories = cognition.extract_event(source)
    selected = [{"memory_id": memories[0]["memory_id"], "revision": memories[0]["revision"]}]
    if blocked_by == "world":
        memories[0]["world"] = "dream"
    elif blocked_by == "scope":
        memories[0]["scope"]["tenant_id"] = "other"
    elif blocked_by == "evidence":
        memories[0]["evidence_refs"] = []
    elif blocked_by == "revision":
        selected[0]["revision"] += 1
    result = cognition.recall(scope(), request("unrelated cue"), memories, [source],
                              state(selected_versions=selected))
    if blocked_by:
        assert not result["items"]
    else:
        assert result["items"][0]["memory"]["memory_id"] == selected[0]["memory_id"]
        assert [signal["method"] for signal in result["items"][0]["rank_signals"]] == ["reranker"]


def test_activation_cannot_create_evidence_and_recall_does_not_mutate_memory():
    source = event("书店门口有只橘猫。")
    memories = cognition.extract_event(source)
    before = copy.deepcopy(memories)
    output = cognition.recall(scope(), request(), memories, [source], state())
    assert output["items"][0]["rank_signals"][0]["meaning"] == "ranking_only_not_probability"
    assert output["recall_traces"][0]["kind"] == "candidate_hit"
    assert output["recall_traces"][0]["allowed_effect"] == "none"
    assert memories == before
    memories[0]["dynamics"]["activation"] = 1
    memories[0]["evidence_refs"] = []
    assert not cognition.recall(scope(), request(), memories, [source], state())["items"]


def test_unindexed_tail_provides_immediate_read_your_writes():
    source = event("书店门口有只橘猫。")
    result = cognition.recall(scope(), request(read_fence=fence(1)), [], [source],
                              state(indexed_sequence=0, candidate_total=0))
    assert result["items"][0]["retrieved_from"] == "unindexed_tail"
    assert result["coverage"]["tail_covered_through_sequence"] == 1
    assert result["coverage"]["read_fence_satisfied"] is True


def test_unsatisfied_read_fence_never_returns_stale_success():
    source = event("书店门口有只橘猫。")
    result = cognition.recall(scope(), request(read_fence=fence(2)), cognition.extract_event(source),
                              [source], state())
    assert result["items"] == []
    assert result["status"] == "indexing"
    assert result["coverage"]["read_fence_satisfied"] is False


def test_recall_budget_includes_exact_compact_model_view_and_excludes_control_metadata():
    source = event("书店" + "门口有只橘猫" * 500)
    memories = cognition.extract_event(source)
    full = cognition.recall(scope(), request(), memories, [source], state())
    full_count = full["usage"]["context_tokens"]
    assert full_count == cognition.estimate_tokens(cognition.render_recall(full))
    result = cognition.recall(scope(), request(budget=budget(4000)), memories, [source], state())
    assert not result["items"]
    assert not result["recall_traces"]
    assert result["usage"]["context_tokens"] == cognition.estimate_tokens(cognition.render_recall(result)) <= 4000
    assert result["usage"]["stopped_by"] == "context_limit"
    with pytest.raises(DomainError) as error:
        cognition.recall(scope(), request(budget=budget(0)), memories, [source], state())
    assert error.value.code == "budget_exceeded"


def test_provider_does_not_claim_an_unavailable_exact_tokenizer():
    exact = budget()
    exact["token_counting"] = "exact"
    exact["tokenizer_id"] = "some-tokenizer"
    with pytest.raises(DomainError) as error:
        cognition.recall(scope(), request(budget=exact), [], [], state(0, candidate_total=0))
    assert error.value.code == "unsupported_capability"


def test_prepare_renders_only_selected_blocks_and_accounts_for_real_model_input():
    source = event("书店门口有只橘猫。")
    recalled = cognition.recall(scope(), request(), cognition.extract_event(source), [source], state())
    packet = cognition.prepare(scope(), prepare_payload(1500), working(), recalled, state())
    rendered = cognition.render_context(packet)
    assert "橘猫" in rendered
    assert "memory_data_not_instructions" in rendered
    assert "principal_binding_id" not in rendered
    assert "retrieval_snapshot" not in rendered
    assert packet["usage"]["context_tokens"] == len(rendered.encode("utf-8")) <= 1500
    assert cognition.estimate_tokens(packet) > 1500
    assert packet["recall_traces"][0]["kind"] == "context_selected"


def test_prepare_drops_large_memory_without_falsely_claiming_it_was_selected():
    source = event("书店" + "门口有只橘猫" * 100)
    recalled = cognition.recall(scope(), request(), cognition.extract_event(source), [source], state())
    packet = cognition.prepare(scope(), prepare_payload(800), working(), recalled, state())
    assert "橘猫" not in cognition.render_context(packet)
    assert packet["recall_traces"] == []
    assert packet["usage"]["stopped_by"] == "context_limit"
    assert packet["usage"]["context_tokens"] <= 800
    assert "budget_exhausted" in packet["coverage"]["limitations"]


def test_working_state_filters_expired_and_missing_evidence_but_keeps_verified_context():
    source = event("我们正在讨论答辩。")
    memory = cognition.extract_event(source)[0]
    refs = memory["evidence_refs"]
    empty_recall = cognition.recall(scope(), request("不存在的词xyz"), [memory], [source], state())
    current = working()
    current["items"] = [
        {"key": "topic", "value": "答辩", "knowledge_state": "grounded",
         "evidence_refs": refs, "expires_at": None, "updated_at": STAMP},
        {"key": "old", "value": "过期状态", "knowledge_state": "grounded",
         "evidence_refs": refs, "expires_at": "2026-10-22T03:59:00-08:00", "updated_at": STAMP},
    ]
    dropped = cognition.prepare(scope(), prepare_payload(), current, empty_recall, state())
    assert dropped["working_state"]["items"] == []
    kept = cognition.prepare(scope(), prepare_payload(), current, empty_recall,
                              state(verified_working_evidence_refs=refs,
                                    working_event_metadata={source["event_id"]: source}))
    assert [item["key"] for item in kept["working_state"]["items"]] == ["topic"]
    assert "答辩" in cognition.render_context(kept)
    assert "过期状态" not in cognition.render_context(kept)


def test_prepare_rejects_a_snapshot_invalidated_between_recall_and_preparation():
    source = event("书店门口有只橘猫。")
    recalled = cognition.recall(scope(), request(), cognition.extract_event(source), [source], state())
    with pytest.raises(DomainError) as error:
        cognition.prepare(scope(), prepare_payload(), working(), recalled,
                          state(dependency_generation=1))
    assert error.value.code == "version_conflict"


def test_prepare_never_reuses_another_conversations_working_state():
    recalled = cognition.recall(scope(), request(), [], [], state(0, candidate_total=0))
    other = working()
    other["scope"]["conversation_id"] = "another-conversation"
    other["items"] = [{"key": "topic", "value": "a different private thread",
                       "knowledge_state": "unknown", "evidence_refs": [],
                       "expires_at": None, "updated_at": STAMP}]
    packet = cognition.prepare(scope(), prepare_payload(), other, recalled,
                                state(0, candidate_total=0))
    assert packet["working_state"]["items"] == []
    assert "a different private thread" not in cognition.render_context(packet)


def test_prepare_cannot_weaken_a_newer_read_fence_to_the_recall_fence():
    recalled = cognition.recall(scope(), request(), [], [], state(0, candidate_total=0))
    payload = prepare_payload()
    payload["read_fence"] = fence(1)
    with pytest.raises(DomainError) as error:
        cognition.prepare(scope(), payload, working(), recalled, state(0, candidate_total=0))
    assert error.value.code == "dependency_unavailable"


def test_truncated_sources_do_not_claim_the_missing_tail_was_read():
    source = event("书店门口有只橘猫。", sequence=10)
    result = cognition.recall(scope(), request(), [], [source],
                              state(10, indexed_sequence=0, candidate_total=0,
                                    source_truncated=True))
    assert result["coverage"]["tail_covered_through_sequence"] == 0
    assert "partial_scope" in result["coverage"]["limitations"]


def test_historical_working_state_cannot_bypass_query_cutoff_or_speech_act():
    old_question = event("我们去过海边吗？")
    current_question = event("还记得我们去过海边吗？", 2)
    sources = [old_question, current_question]
    memories = [memory for source in sources for memory in cognition.extract_event(source)]
    recalled = cognition.recall(scope(), request(
        current_question["observed"]["content"]["text"], cue_event_ids=["event-2"],
    ), memories, sources, state(2, candidate_total=2))
    assert recalled["status"] == "no_match"
    current = working()
    current["items"] = [{
        "key": f"unsupported-{index}", "value": "我们去过海边",
        "knowledge_state": "grounded", "evidence_refs": memory["evidence_refs"],
        "expires_at": None, "updated_at": STAMP,
    } for index, memory in enumerate(memories)]
    current["items"].append({
        "key": "question", "value": "我们去过海边吗？", "knowledge_state": "unknown",
        "evidence_refs": [], "expires_at": None, "updated_at": STAMP,
    })
    packet = cognition.prepare(scope(), prepare_payload(), current, recalled, state(
        2, candidate_total=2,
        verified_working_evidence_refs=[ref for memory in memories for ref in memory["evidence_refs"]],
        working_event_metadata={source["event_id"]: source for source in sources},
    ))
    assert [item["key"] for item in packet["working_state"]["items"]] == ["question"]
    block = next(block for block in packet["blocks"] if block["section"] == "working_state")
    assert json.loads(block["text"])["use"] == "query_context_only"
    assert json.loads(block["text"])["knowledge_state"] == "unknown"


def test_historical_working_state_accepts_a_verified_pre_query_report():
    old = event("书店门口有只橘猫。")
    cue = event("还记得那只橘猫吗？", 2)
    memory = cognition.extract_event(old)[0]
    recalled = cognition.recall(scope(), request(
        cue["observed"]["content"]["text"], cue_event_ids=["event-2"],
    ), [memory], [old, cue], state(2))
    current = working()
    current["items"] = [{
        "key": "topic", "value": memory["statement"], "knowledge_state": "grounded",
        "evidence_refs": memory["evidence_refs"], "expires_at": None, "updated_at": STAMP,
    }]
    packet = cognition.prepare(scope(), prepare_payload(), current, recalled,
                                state(2, working_event_metadata={"event-1": old, "event-2": cue}))
    assert len(packet["working_state"]["items"]) == 1


def test_working_evidence_cannot_smuggle_a_sibling_branch_into_context():
    other = event("书店暗号是青蛙。", scope=scope(branch_id="sibling"))
    memory = cognition.extract_event(other)[0]
    current = working()
    current["items"] = [{
        "key": "branch-secret", "value": memory["statement"], "knowledge_state": "grounded",
        "evidence_refs": memory["evidence_refs"], "expires_at": None, "updated_at": STAMP,
    }]
    recalled = cognition.recall(scope(), request(), [], [], state())
    packet = cognition.prepare(scope(), prepare_payload(), current, recalled, state(
        verified_working_evidence_refs=memory["evidence_refs"],
        working_event_metadata={other["event_id"]: other},
    ))
    assert packet["working_state"]["items"] == []
    assert "青蛙" not in cognition.render_context(packet)
