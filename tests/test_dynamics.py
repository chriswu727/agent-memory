import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agent_memory.dynamics import AccessPolicy, access_state, rank_weight

START = datetime(2026, 1, 1, tzinfo=UTC)


def memory(**dynamics):
    return {"memory_id": "memory-1", "revision": 7, "epistemic_status": "supported_report",
            "representation": "episode_fragment", "system_time": {"recorded_at": START.isoformat()},
            "dynamics": {"activation": 0.5, "retention_priority": 0.5, "user_pinned": False,
                         "reasons": [], **dynamics}}


def test_lazy_decay_moves_between_access_tiers_without_erasing_or_mutating():
    item = memory()
    original = copy.deepcopy(item)
    snapshots = [access_state(item, None, at=START + timedelta(days=days)) for days in (0, 60, 365)]
    assert [state["tier"] for state in snapshots] == ["everyday", "cue", "deep"]
    assert snapshots[0]["activation"] > snapshots[1]["activation"] > snapshots[2]["activation"]
    assert all(state["utility"] == 0.5 and not state["protected"] for state in snapshots)
    assert item == original
    assert access_state(item, None, at=START + timedelta(days=60)) == snapshots[1]
    assert "epistemic_status" not in snapshots[0]
    assert all(rank_weight(state) > 0 for state in snapshots)


def test_representation_half_lives_are_configurable_engineering_parameters():
    policy = AccessPolicy(retention_half_life_gain=0)
    item = memory(activation=0.8)
    for representation, days in policy.half_life_days.items():
        item["representation"] = representation
        state = access_state(item, {}, at=START + timedelta(days=days), policy=policy)
        assert state["activation"] == pytest.approx(0.4)
    custom_lives = dict(policy.half_life_days)
    custom_lives["episode_fragment"] = 7
    faster = replace(policy, half_life_days=custom_lives)
    item["representation"] = "episode_fragment"
    assert access_state(item, {}, at=START + timedelta(days=14), policy=faster)["activation"] == pytest.approx(0.2)
    custom_lives["episode_fragment"] = 999
    assert faster.half_life_days["episode_fragment"] == 7
    with pytest.raises(TypeError):
        faster.half_life_days["episode_fragment"] = 9


@pytest.mark.parametrize("fields,reason,tier", [
    ({"user_pinned": True}, "user_pinned", "everyday"),
    ({"reasons": ["open_commitment"]}, "open_commitment", "everyday"),
    ({"retention_priority": 0.95}, "high_retention", "cue"),
])
def test_protection_preserves_accessibility_without_changing_facts(fields, reason, tier):
    item = memory(**fields)
    original = copy.deepcopy(item)
    state = access_state(item, {}, at=START + timedelta(days=36500))
    assert state["protected"]
    assert state["tier"] == tier
    assert reason in state["reasons"]
    assert item == original


def test_real_external_usage_reactivates_then_decays_again():
    item = memory()
    at = START + timedelta(days=365)
    usage = {"external_uses": 12, "successes": 8, "failures": 2, "last_external_use_at": at}
    before = access_state(item, {}, at=at)
    after = access_state(item, usage, at=at)
    later = access_state(item, usage, at=at + timedelta(days=365))
    assert before["tier"] == "deep"
    assert after["tier"] == "everyday"
    assert after["activation"] > before["activation"]
    assert later["tier"] == "deep"
    assert after["utility"] == pytest.approx(9 / 12)
    assert not after["protected"]
    assert "external_use" in after["reasons"]


def test_internal_repetition_does_not_reactivate_or_promote_retention():
    at = START + timedelta(days=365)
    item = memory(last_recalled_at=at.isoformat(), last_external_reinforcement_at=at.isoformat())
    expected = access_state(item, {}, at=at)
    for count in (1, 5, 50, 1000000):
        internal = {"candidate_hit": count, "context_selected": count, "model_estimate": count,
                    "quoted": count, "last_recalled_at": at.isoformat(), "retention_priority": 1.0}
        assert access_state(item, internal, at=at) == expected
    assert expected["tier"] == "deep"
    assert not expected["protected"]


def test_outcome_utility_changes_rank_but_not_validity_or_retention():
    item = memory()
    good = {"external_uses": 20, "successes": 20, "failures": 0, "last_external_use_at": START}
    bad = {**good, "successes": 0, "failures": 20}
    successful = access_state(item, good, at=START)
    unsuccessful = access_state(item, bad, at=START)
    assert successful["utility"] > unsuccessful["utility"]
    assert rank_weight(successful) > rank_weight(unsuccessful) > 0
    assert successful["activation"] == unsuccessful["activation"]
    assert item["epistemic_status"] == "supported_report"
    assert item["revision"] == 7
    assert item["dynamics"]["retention_priority"] == 0.5


def test_missing_external_timestamp_does_not_make_frequency_permanently_fresh():
    at = START + timedelta(days=365)
    unknown = access_state(memory(), {"external_uses": 10}, at=at)
    unused = access_state(memory(), {}, at=at)
    assert unknown["activation"] == unused["activation"]
    assert "external_use_time_unknown" in unknown["reasons"]


def test_future_times_clamp_age_to_zero_and_never_amplify_activation():
    item = memory()
    usage = {"external_uses": 100, "last_external_use_at": START + timedelta(days=100)}
    future = access_state(item, usage, at=START - timedelta(days=365))
    current = access_state(item, {**usage, "last_external_use_at": START}, at=START)
    assert future == current
    assert 0 <= future["activation"] <= 1


def test_equivalent_aware_timezones_produce_identical_results():
    utc = access_state(memory(), {}, at=START + timedelta(days=30))
    offset = (START + timedelta(days=30)).astimezone(timezone(timedelta(hours=-8)))
    assert access_state(memory(), {}, at=offset) == utc
    assert access_state(memory(), {}, at=offset.isoformat()) == utc


def test_distinctive_low_frequency_detail_keeps_rank_floor_and_direct_lookup_bypasses_decay():
    at = START + timedelta(days=36500)
    ordinary = access_state(memory(), {}, at=at)
    distinctive = access_state(memory(reasons=["distinctive_event"]), {}, at=at)
    assert ordinary["tier"] == distinctive["tier"] == "deep"
    assert rank_weight(distinctive) > rank_weight(ordinary) > 0
    assert rank_weight(ordinary, direct=True) == rank_weight(distinctive, direct=True) == 1.0


def test_legacy_assertions_have_explicit_representation_fallbacks():
    item = memory()
    del item["representation"]
    for kind, expected in (("episodic", "episode_fragment"), ("semantic", "semantic_anchor"), ("procedural", "procedure")):
        item["memory_kind"] = kind
        assert expected in access_state(item, {}, at=START)["reasons"]


@pytest.mark.parametrize("value", [None, "not-time", "2026-01-01", "2026-01-01T01:00:00", datetime(2026, 1, 1), 0, float("nan")])
def test_all_access_timestamps_require_timezone(value):
    with pytest.raises(ValueError, match="timezone"):
        access_state(memory(), {}, at=value)
    item = memory()
    item["system_time"]["recorded_at"] = value
    with pytest.raises(ValueError, match="timezone"):
        access_state(item, {}, at=START)
    if value is not None:
        with pytest.raises(ValueError, match="timezone"):
            access_state(memory(), {"external_uses": 1, "last_external_use_at": value}, at=START)


@pytest.mark.parametrize("field,value", [("activation", float("nan")), ("activation", float("inf")),
                                        ("activation", True), ("activation", -0.1), ("activation", 1.1),
                                        ("retention_priority", float("inf")), ("user_pinned", 1), ("reasons", "open_commitment")])
def test_memory_parameters_are_strict_and_finite(field, value):
    with pytest.raises(ValueError):
        access_state(memory(**{field: value}), {}, at=START)


@pytest.mark.parametrize("usage", [{"external_uses": -1}, {"successes": True}, {"failures": float("inf")},
                                   {"external_uses": 2**63}, {"external_uses": 2, "successes": 2, "failures": 1}])
def test_usage_counters_require_consistent_external_observations(usage):
    with pytest.raises(ValueError):
        access_state(memory(), usage, at=START)


@pytest.mark.parametrize("changes", [{"cue_threshold": 0}, {"cue_threshold": 0.5}, {"everyday_threshold": 0.9},
                                     {"external_gain": float("nan")}, {"external_half_life_days": 0},
                                     {"retention_half_life_gain": float("inf")}, {"rank_floor": 0},
                                     {"distinctive_rank_floor": 0.1}, {"half_life_days": {}}, {"version": ""}])
def test_invalid_policy_cannot_create_permanent_or_nonfinite_ranking(changes):
    with pytest.raises(ValueError):
        AccessPolicy(**changes)
