import copy
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb
from test_cognition import STAMP, request, scope
from test_contextual_learning import event_by_id, orient, seed_rule
from test_contextual_recall import UNBOUNDED, case
from test_learning import learning as learning
from test_semantic import reference

from agent_memory import cognition
from agent_memory.ontology import CORE_REGISTRY


def variant(observation, index):
    value = copy.deepcopy(observation)
    value["observation_id"] = f"observation-{index}"
    value["valid_during"] = {
        **copy.deepcopy(UNBOUNDED),
        "start": {"kind": "instant", "at": (datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=index)).isoformat()},
    }
    return value


def assess(memory, current):
    return cognition._memory_applicability(memory, current, scope(), current["working_event_metadata"], STAMP)


@pytest.mark.parametrize("first", ["positive", "negative"])
def test_direct_context_overflow_cannot_turn_a_truncated_side_into_a_decision(first):
    memory, _, current = case("conflict")
    first_index = 0 if first == "positive" else 1
    selected = current["context_observations"][first_index]
    contrary = current["context_observations"][1 - first_index]
    current["context_observations"] = [variant(selected, index) for index in range(128)] + [contrary]
    result = assess(memory, current)
    assert result["value"] == "unknown"
    assert "context_incomplete" in result["reasons"]
    assert result["evidence_refs"] == selected["evidence_refs"]


def test_model_generated_ids_do_not_make_duplicate_observations_consume_the_limit():
    memory, _, current = case("conflict")
    positive, negative = current["context_observations"]
    current["context_observations"] = [
        {**copy.deepcopy(positive), "observation_id": f"random-model-id-{index}"} for index in range(160)
    ] + [negative]
    result = assess(memory, current)
    assert result["value"] == "unknown" and result["conflict"]
    assert "context_incomplete" not in result["reasons"]
    assert {ref["event_id"] for ref in result["evidence_refs"]} == {"event-2", "event-3"}


@pytest.mark.parametrize("condition,expired,expected", [
    (None, False, "unknown"),
    ({"op": "eq", "key": "activity", "value": "exam_preparation"}, False, "unknown"),
    ({"op": "always"}, False, "true"),
    (None, True, "false"),
])
def test_incomplete_context_keeps_independent_unconditional_and_temporal_decisions(condition, expired, expected):
    memory, _, current = case()
    memory["condition_registry_fingerprint"] = CORE_REGISTRY.fingerprint
    if condition is not None:
        memory["conditions"] = condition
    if expired:
        memory["valid_during"] = {**copy.deepcopy(UNBOUNDED), "end": {"kind": "instant", "at": STAMP}}
    current["context_observations_complete"] = False
    result = assess(memory, current)
    assert result["value"] == expected
    if condition != {"op": "always"}:
        assert "context_incomplete" in result["reasons"]
    if expired:
        assert "expired" in result["reasons"]


def test_legacy_always_rule_requires_qualification_but_literal_captures_do_not():
    memory, _, current = case()
    memory["conditions"] = {"op": "always"}
    result = assess(memory, current)
    assert result["value"] == "unknown"
    assert "legacy_unqualified_rule" in result["reasons"]
    del memory["conditions"]
    assert assess(memory, current)["value"] == "true"


def test_fingerprinted_always_rule_and_contextual_value_do_not_require_observation_completeness():
    memory, _, current = case()
    memory["conditions"] = {"op": "always"}
    current["context_observations_complete"] = False
    memory["condition_registry_fingerprint"] = CORE_REGISTRY.fingerprint
    assert assess(memory, current)["value"] == "true"
    del memory["condition_registry_fingerprint"], memory["memory_id"], memory["valid_during"]
    assert assess(memory, current)["value"] == "true"


def test_fingerprinted_always_rule_still_requires_declared_validity():
    memory, _, current = case()
    memory["conditions"] = {"op": "always"}
    memory["condition_registry_fingerprint"] = CORE_REGISTRY.fingerprint
    del memory["valid_during"]
    result = assess(memory, current)
    assert result["value"] == "unknown"
    assert "validity_unstated" in result["reasons"]
    assert "legacy_unqualified_rule" not in result["reasons"]


def retrieve(learning, rule, observations=()):
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        result, _, state = learning.service._retrieval(
            conn, learning.principal, brain, {"conversation_ref": "chat", "branch_ref": "main"},
            request("备考回答简短"), context_observations=list(observations),
        )
    item = next(item for item in result["items"] if item["memory"]["memory_id"] == rule["memory_id"])
    return item["applicability"], state


def install_completed_jobs(learning, row, groups):
    original = learning.job(row["orientation_job_id"])
    working = learning.working()
    items = []
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        for index, observations in enumerate(groups):
            job_id = f"budget-context-{index:03d}"
            result = copy.deepcopy(original["result"])
            result["situation_updates"] = []
            event_ids = {ref["event_id"] for observation in observations for ref in observation["evidence_refs"]}
            sources = learning.service._events(conn, learning.principal, brain, event_ids=event_ids)
            known = {source["event_id"] for source in result["source_snapshot"]}
            result["source_snapshot"].extend(
                {"event_id": source["event_id"], "revision": source["revision"], "content_sha256": source["content_sha256"]}
                for source in sources if source["event_id"] not in known
            )
            for offset, observation in enumerate(observations):
                update = copy.deepcopy(original["result"]["situation_updates"][0])
                update.update(key=f"bounded-{index}-{offset}", observation=observation,
                              evidence_refs=observation["evidence_refs"])
                result["situation_updates"].append(update)
                item = copy.deepcopy(working["items"][0])
                item.update(key="situation:" + update["key"], evidence_refs=observation["evidence_refs"])
                item["value"].update(orientation_job_id=job_id, observation=observation)
                items.append(item)
            conn.execute(
                "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,state,payload,result,completed_at) "
                "VALUES(%s,%s,%s,%s,'orient','completed',%s,%s,clock_timestamp())",
                (*learning.service._params(learning.principal, learning.brain), job_id,
                 Jsonb(original["payload"]), Jsonb(result)),
            )
    learning.memory.commit(
        "install-budget-context", conversation_id="chat", branch_id="main",
        expected_working_revision=working["revision"], proposed_items=items,
    )


def contrary_observation(learning, positive):
    receipt = learning.observe("我现在不在备考。")
    source = event_by_id(learning, receipt["accepted_event_ids"][0])
    return {**copy.deepcopy(positive), "observation_id": "current-negative", "polarity": "negative",
            "evidence_refs": [reference(source)]}


@pytest.mark.integration
def test_service_deduplicates_random_ids_without_dropping_current_opposition(learning):
    rule = seed_rule(learning)
    _, _, _ = orient(learning)
    positive = learning.working()["items"][0]["value"]["observation"]
    duplicates = [{**copy.deepcopy(positive), "observation_id": f"model-id-{index}"} for index in range(160)]
    result, state = retrieve(learning, rule, duplicates)
    assert state["context_observations_complete"] is True
    assert len(state["context_observations"]) == 1 and result["value"] == "true"
    negative = contrary_observation(learning, positive)
    result, state = retrieve(learning, rule, [*duplicates, negative])
    assert state["context_observations_complete"] is True
    assert len(state["context_observations"]) == 2
    assert result["value"] == "unknown" and result["conflict"]
    assert {ref["event_id"] for ref in result["evidence_refs"]} == {
        positive["evidence_refs"][0]["event_id"], negative["evidence_refs"][0]["event_id"],
    }


@pytest.mark.integration
def test_service_observation_limit_is_explicit_and_keeps_current_input_first(learning):
    rule = seed_rule(learning)
    _, row, _ = orient(learning)
    positive = learning.working()["items"][0]["value"]["observation"]
    old = [variant(positive, index) for index in range(128)]
    install_completed_jobs(learning, row, [old[index:index + 16] for index in range(0, 128, 16)])
    negative = contrary_observation(learning, positive)
    result, state = retrieve(learning, rule, [negative])
    assert state["context_observations_complete"] is False
    assert len(state["context_observations"]) == 128
    assert state["context_observations"][0]["polarity"] == "negative"
    assert result["value"] == "unknown" and result["conflict"]
    assert "context_incomplete" in result["reasons"]
    assert negative["evidence_refs"][0] in result["evidence_refs"]


@pytest.mark.integration
def test_service_job_limit_cannot_hide_the_only_opposing_observation(learning):
    rule = seed_rule(learning)
    _, row, _ = orient(learning)
    positive = learning.working()["items"][0]["value"]["observation"]
    negative = contrary_observation(learning, positive)
    install_completed_jobs(learning, row, [[variant(positive, index)] for index in range(32)] + [[negative]])
    result, state = retrieve(learning, rule)
    assert state["context_observations_complete"] is False
    assert len(state["context_observations"]) == 32
    assert result["value"] == "unknown"
    assert "context_incomplete" in result["reasons"]
    assert result["evidence_refs"] == positive["evidence_refs"]
