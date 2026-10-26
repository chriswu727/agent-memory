import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "longitudinal_scenario", Path(__file__).parents[1] / "examples" / "longitudinal_scenario.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
scenario = _MODULE.scenario
validate_scenario = _MODULE.validate_scenario
actor_request = _MODULE.actor_request
selected_utterance = _MODULE.selected_utterance


def delivered_prefix(value, session_index, turn_index, followup_choice=0):
    records = []
    for index, session in enumerate(value["sessions"][:session_index + 1]):
        for turn in session["turns"][:turn_index if index == session_index else None]:
            choice = turn["choices"][0 if len(turn["choices"]) == 1 else followup_choice]
            records.append({"session_id": session["id"], "turn_id": turn["id"],
                            "choice_id": choice["id"], "user_text": choice["text"],
                            "assistant_text": f"Actual delivered answer for {turn['id']}."})
    return records


def test_authored_scenario_has_bounded_irregular_interwoven_sessions_and_review_boundaries():
    value = scenario()
    assert validate_scenario(value) is None
    assert len(value["sessions"]) == 16
    assert sum(len(session["turns"]) for session in value["sessions"]) == 33
    days = [session["day_offset"] for session in value["sessions"]]
    assert days[0] == 0 and days[-1] == 56
    assert len({right - left for left, right in zip(days, days[1:])}) >= 4
    assert len({session["conversation_id"] for session in value["sessions"]}) == 16
    assert len(value["private_review"]["cases"]) == 16
    assert sum(case["kind"] == "practical" for case in value["private_review"]["cases"]) == 15
    assert value["time_mode"] == "accelerated-narrative"
    positions = {turn["id"]: index for index, turn in enumerate(
        turn for session in value["sessions"] for turn in session["turns"]
    )}
    for case in value["private_review"]["cases"]:
        assert all(positions[ref["turn_id"]] <= positions[case["at_turn"]]
                   for ref in case["source_refs"])
    assert any(positions[case["at_turn"]] >= 25 for case in value["private_review"]["cases"])


def test_scenario_factory_does_not_share_mutable_state():
    value = scenario()
    value["sessions"][0]["turns"][0]["choices"][0]["text"] = "Changed"
    value["private_review"]["cases"].clear()
    assert scenario()["sessions"][0]["turns"][0]["choices"][0]["text"] != "Changed"
    assert scenario()["private_review"]["cases"]


def test_expired_exception_cases_have_communicated_elapsed_context_without_a_simulated_clock():
    value = scenario()
    turns = {turn["id"]: turn for session in value["sessions"] for turn in session["turns"]}
    assert "following Monday" in turns["story-return"]["choices"][0]["text"]
    assert "Two weeks have slipped by" in turns["late-week-plan"]["choices"][0]["text"]
    cases = {case["id"]: case for case in value["private_review"]["cases"]}
    for case_id in ("first-return-without-reteaching", "late-project-continuity"):
        case = cases[case_id]
        assert {"turn_id": "story-return"} in case["source_refs"]
        assert case["temporal_scope"]
    request = actor_request(value, 5, 0, delivered_prefix(value, 5, 0))
    assert "temporal_scope" not in request
    assert "decay" not in json.dumps(request)


def test_first_turn_can_be_delivered_without_actor_and_exposes_only_current_choice():
    value = scenario()
    request = actor_request(value, 0, 0, [])
    assert request["released_user_knowledge"] == []
    assert request["current_session_transcript"] == []
    assert request["choices"] == [{
        "id": "start", "text": value["sessions"][0]["turns"][0]["choices"][0]["text"],
    }]
    utterance = selected_utterance(value, 0, 0, "start")
    assert utterance["branch"] == "ordinary"
    utterance["text"] = "An actor cannot rewrite the stored choice."
    assert selected_utterance(value, 0, 0, "start")["text"] == request["choices"][0]["text"]


def test_actual_answer_controls_actor_context_and_is_not_promoted_to_user_knowledge():
    value = scenario()
    transcript = delivered_prefix(value, 0, 1)
    transcript[0]["assistant_text"] = "Let's schedule writing during class on Tuesday."
    request = actor_request(value, 0, 1, transcript)
    assert request["current_session_transcript"] == [
        {"role": "user", "content": transcript[0]["user_text"]},
        {"role": "assistant", "content": transcript[0]["assistant_text"]},
    ]
    assert transcript[0]["assistant_text"] not in json.dumps(request["released_user_knowledge"])
    assert selected_utterance(value, 0, 1, "correct")["branch"] == "correction"
    assert all(set(choice) == {"id", "text"} for choice in request["choices"])
    transcript[0]["assistant_text"] = "A different actually delivered answer."
    other = actor_request(value, 0, 1, transcript)
    assert request["current_session_transcript"] != other["current_session_transcript"]
    assert request["released_user_knowledge"] == other["released_user_knowledge"]


def test_future_events_or_review_answers_never_enter_actor_request():
    value = scenario()
    transcript = delivered_prefix(value, 5, 1)
    baseline = actor_request(value, 5, 1, transcript)
    changed = deepcopy(value)
    changed["private_review"]["cases"][0]["required"].append("PRIVATE_ORACLE_CANARY")
    changed["private_review"]["hidden_world_facts"].append("HIDDEN_WORLD_CANARY")
    changed["sessions"][15]["intent"] = "FUTURE_INTENT_CANARY"
    changed["sessions"][15]["turns"][1]["choices"][0]["text"] = "FUTURE_UTTERANCE_CANARY"
    changed["sessions"][0]["oracle"] = "UNEXPECTED_METADATA_CANARY"
    assert actor_request(changed, 5, 1, transcript) == baseline
    payload = json.dumps(baseline)
    assert "hallway drawer" not in payload
    assert "Harbor" not in payload
    assert "1,800 words" in payload
    assert not ({"private_review", "required", "prohibited", "source_refs", "branch"} & baseline.keys())


def test_new_session_exposes_prior_user_statements_but_no_old_assistant_transcript():
    value = scenario()
    transcript = delivered_prefix(value, 1, 0)
    request = actor_request(value, 1, 0, transcript)
    assert request["current_session_transcript"] == []
    assert len(request["released_user_knowledge"]) == 2
    assert [item["text"] for item in request["released_user_knowledge"]] == [
        record["user_text"] for record in transcript
    ]
    assert all(record["assistant_text"] not in json.dumps(request) for record in transcript)


def test_undelivered_optional_branch_is_not_released_as_personal_knowledge():
    value = scenario()
    ordinary = delivered_prefix(value, 11, 0)
    request = actor_request(value, 11, 0, ordinary)
    assert "no wall mounting" not in json.dumps(request)
    selected = next(record for record in ordinary if record["turn_id"] == "desk-followup")
    choice = selected_utterance(value, 10, 1, "clarify")
    selected["choice_id"], selected["user_text"] = choice["id"], choice["text"]
    assisted = actor_request(value, 11, 0, ordinary)
    assert "no wall mounting" in json.dumps(assisted)
    assert all(ref["turn_id"] != "desk-followup" for case in value["private_review"]["cases"]
               for ref in case["source_refs"])


@pytest.mark.parametrize("choice_id", ["not-a-choice", "continue ", "CONTINUE", "", None, {}, []])
def test_actor_cannot_invent_or_loosely_match_a_choice(choice_id):
    with pytest.raises(ValueError):
        selected_utterance(scenario(), 0, 1, choice_id)


@pytest.mark.parametrize("mutation", [
    lambda records: records.pop(),
    lambda records: records.append(deepcopy(records[-1])),
    lambda records: records.reverse(),
    lambda records: records[0].update(session_id="another-session"),
    lambda records: records[0].update(turn_id="future-turn"),
    lambda records: records[1].update(choice_id="invented"),
    lambda records: records[1].update(user_text="The actor invented a new world fact."),
    lambda records: records[0].update(private_review="oracle"),
    lambda records: records[0].update(assistant_text=""),
    lambda records: records[0].update(assistant_text="\ud800"),
    lambda records: records[1].update(choice_id=[]),
])
def test_actor_rejects_missing_future_reordered_tampered_or_unsettled_transcript(mutation):
    value = scenario()
    records = delivered_prefix(value, 1, 1)
    mutation(records)
    with pytest.raises(ValueError):
        actor_request(value, 1, 1, records)


@pytest.mark.parametrize("session_index,turn_index", [
    (-1, 0), (16, 0), (True, 0), (0, -1), (0, 2), (0, False), ("0", 0), (0, "0"),
])
def test_invalid_positions_are_not_interpreted_as_other_turns(session_index, turn_index):
    with pytest.raises(ValueError):
        selected_utterance(scenario(), session_index, turn_index, "start")


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(version="unfrozen-v2"),
    lambda value: value.update(time_mode="real-time"),
    lambda value: value["sessions"][1].update(day_offset=0),
    lambda value: value["sessions"][0].update(day_offset=True),
    lambda value: value["sessions"][1].update(id=value["sessions"][0]["id"]),
    lambda value: value["sessions"][1]["turns"][0].update(id="story-start"),
    lambda value: value["sessions"][0]["turns"][0]["choices"].append(
        {"id": "another", "text": "Not a fixed first turn", "branch": "ordinary"}),
    lambda value: value["sessions"][0]["turns"][1]["choices"].clear(),
    lambda value: value["sessions"][0]["turns"][0]["choices"][0].update(branch="correction"),
    lambda value: value["sessions"][0]["turns"][1]["choices"][0].update(branch=[]),
    lambda value: value["sessions"][0]["turns"][1]["choices"][0].update(text="\ud800"),
    lambda value: value["private_review"]["cases"][0]["source_refs"].append(
        {"turn_id": "draft-finished"}),
    lambda value: value["private_review"]["cases"][0]["source_refs"].append(
        {"turn_id": "story-start", "choice_id": "undelivered"}),
    lambda value: value["private_review"]["cases"][0].update(when_choice_id="undelivered"),
    lambda value: value["private_review"]["cases"][0].update(at_turn=[]),
    lambda value: value["private_review"]["cases"][0]["source_refs"].append({"turn_id": []}),
])
def test_validator_rejects_invalid_frozen_plans_and_future_source_authority(mutation):
    value = scenario()
    mutation(value)
    with pytest.raises(ValueError):
        validate_scenario(value)


def test_valid_conditional_source_reference_and_case_are_supported():
    value = scenario()
    case = value["private_review"]["cases"][1]
    case["when_choice_id"] = "clarify"
    case["source_refs"].append({"turn_id": "story-slots", "choice_id": "clarify"})
    assert validate_scenario(value) is None


def test_every_exact_prefix_can_form_an_isolated_actor_request_without_mutation():
    value = scenario()
    original = deepcopy(value)
    for session_index, session in enumerate(value["sessions"]):
        for turn_index, turn in enumerate(session["turns"]):
            transcript = delivered_prefix(value, session_index, turn_index)
            before = deepcopy(transcript)
            request = actor_request(value, session_index, turn_index, transcript)
            assert request["turn_id"] == turn["id"]
            assert len(request["current_session_transcript"]) == turn_index * 2
            assert request["choices"] == [
                {"id": item["id"], "text": item["text"]} for item in turn["choices"]
            ]
            for item in turn["choices"]:
                assert selected_utterance(value, session_index, turn_index, item["id"]) == item
            assert transcript == before
    assert value == original
