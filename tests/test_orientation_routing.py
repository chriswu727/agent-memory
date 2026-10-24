import copy

import pytest

from agent_memory.orientation import ORIENTATION_ROUTE_VERSION, assess_orientation
from agent_memory.runtime import RuntimePolicy


def route_inputs():
    turn = {"turn_id": "turn", "conversation_id": "chat", "branch_id": "main",
            "input_event_refs": [{"event_id": "input", "revision": 1}], "orientation_job_id": None}
    working = {"revision": 0, "items": [], "open_loops": [], "updated_at": "2026-10-24T00:00:00Z"}
    state = {"belief_memories": [], "belief_relations": [], "context_observations": [],
             "source_truncated": False, "rebuilding": False, "belief_incomplete_versions": [],
             "context_observations_complete": True, "scope_generation": 1,
             "dependency_generation": 0, "durable_sequence": 1, "indexed_sequence": 0,
             "working_event_metadata": {"input": {"event_id": "input", "revision": 1,
                                                  "content_sha256": "source-hash"}}}
    return turn, working, state


def memory(**changes):
    return {"memory_id": "memory", "revision": 1, "conditions": {"op": "always"},
            "procedure": None, "valid_during": {"kind": "unknown"}, **changes}


def test_orientation_is_opt_in_and_policy_rejects_unknown_values():
    assert RuntimePolicy().orientation_policy == "always"
    assert RuntimePolicy(orientation_policy="on_demand").orientation_policy == "on_demand"
    with pytest.raises(ValueError, match="orientation_policy"):
        RuntimePolicy(orientation_policy="automatic")


@pytest.mark.parametrize("memories", [[], [memory()], [memory(valid_during={
    "kind": "interval", "start": {"kind": "instant", "at": "2030-01-01T00:00:00Z"},
    "end": {"kind": "unbounded"},
})]])
def test_empty_or_unconditional_history_needs_no_situation_inference(memories):
    turn, working, state = route_inputs()
    state["belief_memories"] = memories
    result = assess_orientation(turn, working, state, historical=True)
    assert result["version"] == ORIENTATION_ROUTE_VERSION
    assert result["decision"] == "skip"
    assert result["reasons"] == ["no_contextual_dependencies"]
    assert "semantic_status" not in result


@pytest.mark.parametrize("area,field,value,reason", [
    ("turn", "orientation_job_id", "already-bound", "existing_orientation"),
    ("state", "belief_memories", [memory(conditions={"op": "unknown"})], "conditional_memory"),
    ("state", "belief_memories", [memory(procedure={"steps": []})], "conditional_memory"),
    ("state", "belief_relations", [{"relation_id": "relation", "revision": 1,
                                    "conditions": {"op": "unknown"}}], "conditional_relation"),
    ("working", "items", [{"key": "goal", "value": "Finish the current task"}], "working_state"),
    ("working", "open_loops", [{"description": "Unfinished task"}], "working_state"),
    ("state", "context_observations", [{"observation_id": "old-context"}], "working_state"),
    ("state", "source_truncated", True, "incomplete_dependencies"),
    ("state", "rebuilding", True, "incomplete_dependencies"),
    ("state", "belief_incomplete_versions", [("missing", 1)], "incomplete_dependencies"),
    ("state", "context_observations_complete", False, "incomplete_dependencies"),
])
def test_any_contextual_or_incomplete_dependency_requires_orientation(area, field, value, reason):
    turn, working, state = route_inputs()
    {"turn": turn, "working": working, "state": state}[area][field] = copy.deepcopy(value)
    result = assess_orientation(turn, working, state, historical=True)
    assert result["decision"] == "orient" and reason in result["reasons"]


@pytest.mark.parametrize("flags,reason", [
    ({"planned": True, "historical": True}, "recall_plan"),
    ({"embedded": True, "historical": True}, "embedding_recall"),
    ({"historical": False}, "historical_boundary_required"),
])
def test_retrieval_paths_without_a_replayable_local_proof_cannot_skip(flags, reason):
    result = assess_orientation(*route_inputs(), **flags)
    assert result["decision"] == "orient" and reason in result["reasons"]


def test_routing_never_uses_post_filter_visibility_or_applicability_as_permission():
    turn, working, state = route_inputs()
    state["belief_memories"] = [memory(conditions={"op": "unknown"}, applicability={"value": "false"},
                                      display_mode="do_not_surface")]
    assert assess_orientation(turn, working, state, historical=True)["decision"] == "orient"


def test_proof_ignores_pure_evaluation_time_but_binds_dependency_contents():
    turn, working, state = route_inputs()
    state["belief_memories"] = [memory(), memory(memory_id="other")]
    first = assess_orientation(turn, working, state, historical=True)
    working["updated_at"] = "2026-10-25T00:00:00Z"
    state["belief_memories"].reverse()
    assert assess_orientation(turn, working, state, historical=True) == first
    state["belief_memories"][0]["valid_during"] = {
        "kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"},
    }
    assert assess_orientation(turn, working, state, historical=True)["dependency_fingerprint"] != first[
        "dependency_fingerprint"]


@pytest.mark.parametrize("field,value", [
    ("turn_id", "another-turn"), ("conversation_id", "another-chat"),
    ("branch_id", "other-branch"), ("input_event_refs", [{"event_id": "input", "revision": 2}]),
])
def test_proof_binds_exact_turn_scope_and_input_revision(field, value):
    turn, working, state = route_inputs()
    first = assess_orientation(turn, working, state, historical=True)
    turn[field] = value
    assert assess_orientation(turn, working, state, historical=True)["input_fingerprint"] != first[
        "input_fingerprint"]
