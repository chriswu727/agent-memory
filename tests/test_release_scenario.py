import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_memory.runtime import RuntimePolicy


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_longitudinal")



def test_release_story_interweaves_tasks_and_keeps_future_knowledge_out_of_actor_inputs(evaluation):
    from examples.release_scenario import scenario

    story = scenario()
    evaluation.validate_scenario(story)
    assert len(story["sessions"]) == 14
    assert len(story["private_review"]["cases"]) == 18
    assert story["sessions"][-1]["day_offset"] == 70
    request = evaluation.actor_request(story, 0, 0, [])
    assert request["released_user_knowledge"] == []
    assert "Mara" not in str(request) and "participant_actions" not in str(request)
    positions = {turn["id"]: index for index, turn in enumerate(
        turn for session in story["sessions"] for turn in session["turns"])}
    for case in story["private_review"]["cases"]:
        assert all(positions[ref["turn_id"]] <= positions[case["at_turn"]] for ref in case["source_refs"])


def test_release_budget_is_cumulative_and_callback_switch_is_frozen(evaluation):
    value = evaluation.plan(fixture="release")
    turns = [turn for session in value["scenario"]["sessions"] for turn in session["turns"]]
    assert value["allocation"]["host"] == len(turns)
    assert value["max_calls"] == sum(value["allocation"].values())
    assert value["alternate_callback_from_session"] == 7
    assert "release_scenario.py" in value["code_fingerprints"]
    assert value["runtime_policy"] == evaluation.plan()["runtime_policy"]
    evaluation.validate_plan(value)
    value["alternate_callback_from_session"] = 6
    with pytest.raises(ValueError, match="changed"):
        evaluation.validate_plan(value)


def test_callback_switch_uses_completed_session_boundary_and_never_falls_back(monkeypatch, evaluation):
    journal = SimpleNamespace(plan=evaluation.plan(fixture="release"), events=[])
    memory = SimpleNamespace(brain_id="brain")
    seen = []
    monkeypatch.setattr("examples.evaluate_longitudinal.BrainRuntime", lambda *a, **kw: seen.append(a[1]))
    primary, alternate = lambda _: "primary", lambda _: "alternate"
    runner = evaluation.SessionRunner(journal, memory, None, None, None, primary, None, alternate)
    runner.runtime()
    assert seen == [primary]
    journal.events = [{"kind": "session_completed"}] * 7
    runner.runtime()
    assert seen[-1] is alternate
    runner.alternate_host = None
    with pytest.raises(evaluation.CampaignStopped, match="alternate_callback_missing"):
        runner.runtime()
    assert RuntimePolicy(**journal.plan["runtime_policy"]).orientation_policy == "on_demand"
