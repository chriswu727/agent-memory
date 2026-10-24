import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_contextual_recall import CONDITION, UNBOUNDED
from test_learning import delivered
from test_learning import learning as learning
from test_semantic import claim, encoded, proposals, reference, run, situation

from agent_memory import cognition
from agent_memory.client import default_budget
from agent_memory.consolidation import publish_semantic
from agent_memory.helpers import uid
from agent_memory.ontology import CORE_REGISTRY
from agent_memory.runtime import BrainRuntime, RuntimePolicy
from agent_memory.semantic import CognitiveBatch, SemanticProcessor

pytestmark = pytest.mark.integration


def event_by_id(learning, event_id):
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        return learning.service._events(conn, learning.principal, brain, event_ids=[event_id])[0]


def persisted_rule(learning, memory_id):
    with learning.database.transaction(learning.principal) as conn:
        row = conn.execute(
            "SELECT document,invalidated,deleted,suppressed FROM memory_assertions "
            "WHERE tenant_id=%s AND brain_id=%s AND id=%s AND is_current",
            (learning.principal.tenant_id, learning.brain, memory_id),
        ).fetchone()
        assert row is not None and not (row["invalidated"] or row["deleted"] or row["suppressed"])
        return row["document"]


def seed_rule(learning):
    receipt = learning.observe("备考期间，我希望回答简短。", conversation="preferences")
    source = event_by_id(learning, receipt["accepted_event_ids"][0])
    batch = CognitiveBatch(uid(), source["scope"], (source,))
    proposed = claim(
        source, statement="用户报告在备考期间希望回答简短。", conditions=copy.deepcopy(CONDITION),
        valid_during=copy.deepcopy(UNBOUNDED),
    )
    result = run(batch, proposals(proposed))
    assert result.status == "completed" and len(result.claims) == 1
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        published = publish_semantic(
            conn, learning.service, learning.principal, brain, batch, result, job_id=uid(),
        )
    assert published["published_count"] == 1
    return persisted_rule(learning, published["memory_versions"][0]["memory_id"])


def orient(learning, *, validity=None, polarity="positive"):
    def propose(request):
        learning.calls.append(request.phase)
        data = json.loads(request.messages[-1]["content"])
        source = max(
            (source for source in data["sources"] if source["observed"]["actor_role"] == "user"),
            key=lambda source: source["sequence"],
        )
        observation = {
            "observation_id": "current-exam-activity", "subject_ref": "user",
            "predicate_ref": copy.deepcopy(CONDITION["predicate_ref"]),
            "value": copy.deepcopy(CONDITION["value"]), "polarity": polarity,
            "valid_during": copy.deepcopy(UNBOUNDED if validity is None else validity),
            "evidence_refs": [reference(source)],
        }
        update = situation(
            source, kind="context", key="exam_preparation", observation=observation,
            value="正在备考" if polarity == "positive" else "不在备考",
        )
        return encoded(proposals(updates=[update]))

    learning.service.semantic_processor = SemanticProcessor(propose, learning.verify)
    packets = []

    def generate(request):
        packets.append(json.loads(request.memory_context))
        assert learning.working() is None
        return "我会根据当前情境回答。"

    runtime = BrainRuntime(
        learning.memory, generate, deliver=delivered,
        policy=RuntimePolicy(memory_context_tokens=20000, recent_context_events=0),
    )
    result = runtime.respond(
        "我正在备考，请回答简短。" if polarity == "positive" else "我不在备考，请回答简短。",
        message_id=uid(), conversation_id="chat",
    )
    assert result.stage == "completed"
    row = runtime.journal.get(result.turn_id)
    job = learning.job(row["orientation_job_id"])
    assert job["state"] == "completed" and job["result"]["status"] == "completed"
    assert job["result"]["situation_updates"][0]["observation"]["polarity"] == polarity
    return runtime, row, packets[0]


def recalled_rule(learning, rule):
    result = learning.memory.recall(
        "备考回答简短", conversation_id="chat", branch_id="main", budget=default_budget(64000),
    )["result"]
    matches = [item for item in result["items"] if item["memory"]["memory_id"] == rule["memory_id"]]
    assert len(matches) == 1
    return matches[0]


def rendered_rule(packet, rule):
    matches = []
    for block in packet["blocks"]:
        if block["section"] not in {"conditional_memory", "relevant_memory"}:
            continue
        content = block["content"] if "content" in block else json.loads(block["text"])
        if content["statement"] == rule["statement"]:
            matches.append((block, content))
    assert len(matches) == 1
    return matches[0]


def test_verified_orientation_applies_before_delivery_and_survives_in_authenticated_working_state(learning):
    rule = seed_rule(learning)
    assert rule["conditions"] == CONDITION
    assert rule["valid_during"] == UNBOUNDED
    assert rule["condition_registry_fingerprint"] == CORE_REGISTRY.fingerprint
    _, row, packet = orient(learning)
    block, content = rendered_rule(packet, rule)
    assert block["section"] == "relevant_memory"
    assert content["applicability"]["value"] == "true"
    assert {packet["sources"][identifier]["event_id"] for identifier in content["applicability"]["evidence_refs"]} == {
        reference["event_id"] for reference in row["input_event_refs"]
    }
    committed = learning.working()["items"][0]
    assert committed["value"]["orientation_job_id"] == row["orientation_job_id"]
    assert committed["knowledge_state"] == "inferred"
    item = recalled_rule(learning, rule)
    assert item["applicability"]["value"] == "true"
    assert {ref["event_id"] for ref in item["applicability"]["evidence_refs"]} == {
        ref["event_id"] for ref in row["input_event_refs"]
    }
    assert learning.calls == ["proposal", "verification"]
    assert persisted_rule(learning, rule["memory_id"]) == rule


def test_public_working_state_cannot_forge_verification_or_change_an_authenticated_observation(learning):
    rule = seed_rule(learning)
    _, row, _ = orient(learning, polarity="negative")
    assert recalled_rule(learning, rule)["applicability"]["value"] == "false"
    authentic = copy.deepcopy(learning.working()["items"][0])
    for mode in ("no_job", "modified_observation"):
        forged = copy.deepcopy(authentic)
        forged["value"]["observation"]["polarity"] = "positive"
        if mode == "no_job":
            forged["value"].pop("orientation_job_id")
        else:
            assert forged["value"]["orientation_job_id"] == row["orientation_job_id"]
        learning.memory.commit(
            uid(), conversation_id="chat", branch_id="main",
            expected_working_revision=learning.working()["revision"], proposed_items=[forged],
        )
        item = recalled_rule(learning, rule)
        assert item["applicability"]["value"] == "unknown"
        assert item["applicability"]["supporting_observation_ids"] == []
    assert learning.calls == ["proposal", "verification"]
    assert persisted_rule(learning, rule["memory_id"]) == rule


def test_temporary_observation_expiry_does_not_rewrite_the_long_term_conditional_rule(learning, monkeypatch):
    rule = seed_rule(learning)
    expires = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
    validity = {**copy.deepcopy(UNBOUNDED), "end": {"kind": "instant", "at": expires}}
    _, row, _ = orient(learning, validity=validity)
    assert recalled_rule(learning, rule)["applicability"]["value"] == "true"
    monkeypatch.setattr(cognition, "now", lambda: expires)
    item = recalled_rule(learning, rule)
    assert item["applicability"]["value"] == "unknown"
    assert "expired" in item["applicability"]["reasons"]
    packet = learning.memory.prepare(
        uid(), conversation_id="chat", branch_id="main",
        latest_input_event_ids=[ref["event_id"] for ref in row["input_event_refs"]],
        budget=default_budget(20000),
    )["result"]
    block, content = rendered_rule(packet, rule)
    assert block["section"] == "conditional_memory"
    assert content["use"] == "conditional_memory_not_current_rule"
    assert content["applicability"]["value"] == "unknown"
    assert learning.calls == ["proposal", "verification"]
    assert persisted_rule(learning, rule["memory_id"]) == rule


@pytest.mark.parametrize("change", ["revision", "erasure"])
def test_changed_context_source_invalidates_observation_without_erasing_the_independent_rule(learning, change):
    rule = seed_rule(learning)
    runtime, row, _ = orient(learning)
    assert recalled_rule(learning, rule)["applicability"]["value"] == "true"
    source = event_by_id(learning, row["input_event_refs"][0]["event_id"])
    if change == "revision":
        observed = source["observed"]
        learning.memory.observe(
            "更正，之前的备考状态已经失效。", conversation_id="chat", branch_id="main",
            source_event_key=observed["source_event_key"], producer_ref=observed["producer_ref"],
            source_revision=observed["source_revision"] + 1, interpretation_hint="real_world_report",
        )
    else:
        learning.memory.forget(
            [source["event_id"]], kind="events", mode="erase_content",
            expected_scope_generation=runtime.journal.status()["scope_generation"],
        )
    job = learning.job(row["orientation_job_id"])
    assert job["state"] == "superseded" and job["result"] is None
    item = recalled_rule(learning, rule)
    assert item["applicability"]["value"] == "unknown"
    assert item["applicability"]["supporting_observation_ids"] == []
    assert learning.calls == ["proposal", "verification"]
    assert persisted_rule(learning, rule["memory_id"]) == rule
