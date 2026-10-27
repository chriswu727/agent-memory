import copy
import json

import httpx
import pytest
from test_runtime import memory as memory

from membrain.client import MemoryActionError, default_budget, prepared_context
from membrain.helpers import DomainError, uid
from membrain.recall_control import RecallController, RecallPolicy
from membrain.runtime import BrainRuntime, DeliveryReceipt

pytestmark = pytest.mark.integration


def decision(action="finish", queries=(), selected=(), outcome="sufficient"):
    return json.dumps({"action": action, "queries": list(queries),
                       "selected_memory_ids": list(selected), "outcome": outcome})


def delivered(_):
    return DeliveryReceipt("delivered")


def seed(memory, text, conversation="past", role="user"):
    result = memory.observe(text, source_event_key=uid(), conversation_id=conversation,
                            branch_id="main", role=role,
                            delivery_state="delivered" if role == "assistant" else "received")
    return result["result"]["accepted_event_ids"][0]


def search_then_select(needle, records):
    def reason(request):
        data = json.loads(request.messages[-1]["content"])
        records.append(data)
        if len(records) == 1:
            return decision("search", queries=[needle], outcome="insufficient")
        selected = [item["memory_id"] for item in data["candidates"] if needle in item["statement"]]
        assert selected
        return decision(selected=selected)
    return reason


def test_semantic_bridge_selects_existing_evidence_without_membrain_tools(memory):
    source = seed(memory, "备考期间，晚上先聊十分钟，再安排复习。")
    while memory.work()["processed"]:
        pass
    records, prompts = [], []
    runtime = BrainRuntime(memory, lambda request: prompts.append(request) or "我们约好先聊十分钟。",
                           deliver=delivered,
                           recall_controller=RecallController(search_then_select("十分钟", records)))
    result = runtime.respond("那晚说好的事情，还记得吗？", message_id="new", conversation_id="now")
    assert result.stage == "completed" and len(records) == 2
    assert "十分钟" in prompts[0].memory_context
    row = runtime.journal.get(result.turn_id)
    plan = row["recall_plan"]
    assert plan["outcome"] == "sufficient" and plan["model_calls"] == 2
    assert plan["search_calls"] == 1 and row["recall_model_calls"] == 2
    assert {"event_id": source, "revision": 1} in row["context_event_refs"]
    assert row["recall_input_bytes"] == plan["input_bytes"] > 0
    assert "十分钟" not in json.dumps(row, ensure_ascii=False)
    assert all(warning["coverage"]["search_exhaustive"] is False for warning in records[-1]["warnings"])


def test_current_false_presupposition_never_becomes_old_evidence(memory):
    records, prompts = [], []

    def reason(request):
        data = json.loads(request.messages[-1]["content"])
        records.append(data)
        if len(records) == 1:
            return decision("search", queries=["巴黎"], outcome="insufficient")
        assert not any("巴黎" in item["statement"] for item in data["candidates"])
        return decision(outcome="insufficient")

    runtime = BrainRuntime(memory, lambda request: prompts.append(request) or "没有找到足够的旧记录。",
                           deliver=delivered, recall_controller=RecallController(reason))
    result = runtime.respond("你还记得我们约好去巴黎吗？", message_id="new", conversation_id="now")
    assert result.stage == "completed"
    assert "巴黎" not in prompts[0].memory_context
    assert runtime.journal.get(result.turn_id)["recall_plan"]["outcome"] == "insufficient"


def test_ambiguity_retains_multiple_sources_and_uncertainty(memory):
    seed(memory, "第一次约定，我们在银杏书店见面。")
    seed(memory, "第二次约定，我们在银杏公园见面。")
    prompts = []

    def reason(request):
        candidates = json.loads(request.messages[-1]["content"])["candidates"]
        selected = [item["memory_id"] for item in candidates if "银杏" in item["statement"]]
        assert len(selected) == 2
        return decision(selected=selected, outcome="ambiguous")

    runtime = BrainRuntime(memory, lambda request: prompts.append(request) or "你指的是哪次约定？",
                           deliver=delivered, recall_controller=RecallController(reason))
    result = runtime.respond("银杏的那次约定？", message_id="new", conversation_id="now")
    assert result.stage == "completed"
    assert all(word in prompts[0].memory_context for word in ("书店", "公园", "ambiguous"))


def test_recent_dialogue_is_chronological_and_scoped(memory):
    seed(memory, "前一句只在当前会话中。", conversation="now")
    seed(memory, "后一句是实际送达的回复。", conversation="now", role="assistant")
    seed(memory, "另一会话秘密不会作为最近对话。", conversation="elsewhere")
    prompts = []

    def reason(request):
        context = json.loads(request.messages[-1]["content"])["context"]
        recent = [block["content"] for block in context if block["section"] == "recent_context"]
        assert [event["actor_role"] for event in recent] == ["user", "assistant"]
        return decision(outcome="not_needed")

    runtime = BrainRuntime(memory, lambda request: prompts.append(request) or "继续。", deliver=delivered,
                           recall_controller=RecallController(reason))
    runtime.respond("继续", message_id="new", conversation_id="now")
    blocks = json.loads(prompts[0].memory_context)["blocks"]
    recent = [block["content"] for block in blocks if block["section"] == "recent_context"]
    assert [event["sequence"] for event in recent] == sorted(event["sequence"] for event in recent)
    assert [event["actor_role"] for event in recent] == ["user", "assistant"]
    assert "另一会话秘密" not in prompts[0].memory_context


def test_prepared_plan_survives_restart_without_reasoning_again(memory):
    seed(memory, "往后见面地点是青石桥。")
    reasoning_calls, prompts = [], []

    def reason(request):
        reasoning_calls.append(request)
        candidates = json.loads(request.messages[-1]["content"])["candidates"]
        return decision(selected=[item["memory_id"] for item in candidates if "青石桥" in item["statement"]])

    def model(request):
        prompts.append(request)
        raise TimeoutError("Generation response was lost")

    runtime = BrainRuntime(memory, model, deliver=delivered, recall_controller=RecallController(reason))
    with pytest.raises(TimeoutError):
        runtime.respond("青石桥的约定是什么？", message_id="new", conversation_id="now")
    row = runtime.journal.list()[0]
    assert row["stage"] == "generating" and row["recall_plan"] is not None
    while memory.work()["processed"]:
        pass
    seed(memory, "新加入当前会话的一条消息。", conversation="now")

    def recovered(request):
        assert request.messages == prompts[0].messages
        assert request.request_id == prompts[0].request_id
        return "见面地点是青石桥。"

    resumed = BrainRuntime(memory, recovered, deliver=delivered, generation_is_idempotent=True,
                           recall_controller=RecallController(lambda _: pytest.fail("Do not replan")))
    assert resumed.resume(row["turn_id"]).stage == "completed"
    assert len(reasoning_calls) == 1


def test_model_budget_reservation_survives_process_interruption(memory):
    class Interrupted(BaseException):
        pass

    def interrupt(_):
        raise Interrupted()

    runtime = BrainRuntime(memory, lambda _: pytest.fail("Generation must not start"),
                           recall_controller=RecallController(interrupt, RecallPolicy(max_model_calls=1)))
    with pytest.raises(Interrupted):
        runtime.respond("有没有旧约定？", message_id="new", conversation_id="now")
    row = runtime.journal.list()[0]
    assert row["stage"] == "captured" and row["recall_model_calls"] == 1
    resumed = BrainRuntime(memory, lambda _: "暂时没有足够的记忆信息。", deliver=delivered,
                           recall_controller=RecallController(lambda _: pytest.fail("Budget already spent"),
                                                              RecallPolicy(max_model_calls=1)))
    result = resumed.resume(row["turn_id"])
    assert result.stage == "completed"
    plan = resumed.journal.get(row["turn_id"])["recall_plan"]
    assert plan["outcome"] == "budget_exhausted" and plan["model_calls"] == 1


def test_delete_unselected_observed_source_blocks_generation(memory):
    secret = seed(memory, "枫叶约定中的私密细节。")

    def reason(request):
        assert "私密细节" in request.messages[-1]["content"]
        row = runtime.journal.list()[0]
        assert row["recall_model_calls"] == 1
        memory.forget([secret], kind="events", mode="erase_content",
                      expected_scope_generation=runtime.journal.status()["scope_generation"])
        return decision(outcome="not_needed")

    runtime = BrainRuntime(memory, lambda _: pytest.fail("Erased evidence cannot reach generation"),
                           recall_controller=RecallController(reason))
    with pytest.raises((RuntimeError, httpx.HTTPStatusError, DomainError)):
        runtime.respond("枫叶约定", message_id="new", conversation_id="now")
    row = runtime.journal.list()[0]
    assert row["stage"] == "captured" and not row["sources_available"]
    assert "私密细节" not in json.dumps(row, ensure_ascii=False)


def test_server_rejects_foreign_or_changed_memory_selection(memory):
    seed(memory, "见面地点是红杉林。")
    input_id = seed(memory, "红杉林在哪里？", conversation="now")
    initial = memory.prepare("check", conversation_id="now", branch_id="main",
                             latest_input_event_ids=[input_id], evidence_mode="prior_record_check")["result"]
    controller = RecallController(lambda request: decision(selected=[
        item["memory_id"] for item in json.loads(request.messages[-1]["content"])["candidates"]
        if "红杉林" in item["statement"]]))
    plan = controller.plan(memory, turn_id="check", input_text="红杉林在哪里？", conversation_id="now",
                           branch_id="main", initial_packet=initial, reserve=lambda **_: None, check=lambda: None)
    assert plan["memory_versions"]
    for change in ("id", "revision"):
        invalid = copy.deepcopy(plan)
        if change == "id":
            invalid["memory_versions"][0]["memory_id"] = "memory-other-brain"
        else:
            invalid["memory_versions"][0]["revision"] += 1
        with pytest.raises(MemoryActionError, match="invalid_evidence"):
            memory.prepare("check", conversation_id="now", branch_id="main",
                           latest_input_event_ids=[input_id], evidence_mode="prior_record_check",
                           recall_plan=invalid, expected_dependency_generation=initial["coverage"]["dependency_generation"])


def test_recent_context_cannot_pull_future_messages_or_other_branches(memory):
    seed(memory, "先前这一句。", conversation="now")
    input_id = seed(memory, "当前这一句。", conversation="now")
    seed(memory, "之后才发来的未来一句。", conversation="now")
    memory.observe("另一分支秘密。", source_event_key=uid(), conversation_id="now", branch_id="other")
    packet = memory.prepare("check", conversation_id="now", branch_id="main",
                            latest_input_event_ids=[input_id], recent_event_limit=8, budget=default_budget(4000))
    recent = [json.loads(block["text"]) for block in packet["result"]["blocks"] if block["section"] == "recent_context"]
    assert [event["content"]["text"] for event in recent] == ["先前这一句。"]
    assert "另一分支秘密" not in prepared_context(packet)
