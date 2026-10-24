import copy
import json

import pytest

from agent_memory.helpers import DomainError
from agent_memory.recall_control import RecallController, RecallPolicy


def candidate(key="promise", *, statement="user person (direct_statement): 备考先聊十分钟。", event=None):
    return {
        "memory_id": key, "revision": 1, "statement": statement, "world": "real_world",
        "epistemic_status": "supported_report", "valid_time": {"start": None, "end": None},
        "evidence_refs": [{"event_id": event or "event-" + key, "event_revision": 1}],
    }


def result(*candidates):
    return {
        "scope": {"brain_id": "brain", "conversation_id": "chat", "branch_id": "main"},
        "retrieval_snapshot": {
            "dependency_generation": 2, "scope_generation": 1,
            "evidence_cutoff": {"mode": "before_event", "event_id": "current"},
            "resolved_evidence_through_sequence": 9, "observed_through_sequence": 10,
            "query_event_ids": ["current"], "excluded_cue_event_ids": ["current"],
            "historical_evidence_status": "established",
        },
        "coverage": {"limitations": ["approximate_search"], "read_fence_satisfied": True,
                     "search_exhaustive": False},
        "status": "partial", "unknowns": ["Source claims are not independently verified."],
        "ambiguities": [], "conflicts": [],
        "items": [{"memory": value, "display_mode": "explicit_recollection"} for value in candidates],
    }


def packet(*candidates):
    recalled = result(*candidates)
    return {
        **{key: copy.deepcopy(recalled[key]) for key in ("scope", "retrieval_snapshot", "coverage")},
        "recall_results": [recalled],
        "blocks": [{
            "section": "recent_context", "text": "user: 最近在备考。",
            "evidence_refs": [{"event_id": "recent", "event_revision": 1}],
        }],
    }


def decision(action="finish", *, selected=(), queries=(), outcome="sufficient"):
    return json.dumps({"action": action, "queries": list(queries),
                       "selected_memory_ids": list(selected), "outcome": outcome})


class Memory:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.calls = []

    def recall(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return {"result": next(self.responses)}


def run(reason, *, initial=None, memory=None, policy=None, reserve=None, check=None, **prior):
    return RecallController(reason, policy).plan(
        memory or Memory(), turn_id="turn", input_text="那晚说好的事情是什么？",
        conversation_id="chat", branch_id="main", initial_packet=initial or packet(),
        reserve=reserve or (lambda **_: None), check=check or (lambda: None), **prior,
    )


def test_semantic_rewrite_retains_original_boundary_and_selects_only_seen_evidence():
    memory = Memory([result(candidate()), result(candidate("second", statement="user: 周六再复习。"))])
    requests, reservations = [], []

    def reason(request):
        assert len(reservations) == len(requests) + 1
        requests.append(request)
        data = json.loads(request.messages[1]["content"])
        if len(requests) == 1:
            assert "备考" in data["context"][0]["content"]
            return decision("search", queries=["备考约定", "晚上聊天"], outcome="insufficient")
        assert {item["memory_id"] for item in data["candidates"]} == {"promise", "second"}
        assert data["candidates"][0]["statement"].startswith("user person (direct_statement)")
        return decision(selected=["promise"])

    plan = run(reason, memory=memory, reserve=lambda **values: reservations.append(values))
    assert plan["memory_versions"] == [{"memory_id": "promise", "revision": 1}]
    assert plan["model_calls"] == plan["search_calls"] == 2
    assert plan["coverage_limitations"] == ["approximate_search"]
    assert {ref["event_id"] for ref in plan["observed_event_refs"]} == {
        "recent", "event-promise", "event-second",
    }
    assert plan["recent_event_refs"] == [{"event_id": "recent", "revision": 1}]
    for _, arguments in memory.calls:
        assert arguments["evidence_mode"] == "prior_record_check"
        assert arguments["evidence_cutoff"] == {"mode": "before_event", "event_id": "current"}
        assert arguments["query_event_ids"] == arguments["cue_event_ids"] == ["current"]
        assert arguments["conversation_id"] == "chat" and arguments["branch_id"] == "main"
        assert arguments["read_fence"]["minimum_durable_sequence"] == 10
    expected_bytes = sum(len(json.dumps(
        request.__dict__, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()) for request in requests)
    assert plan["input_bytes"] == expected_bytes == sum(value["input_bytes"] for value in reservations)
    assert "备考约定" not in json.dumps(plan, ensure_ascii=False)
    assert len(plan["trace"][0]["query_hashes"]) == 2


def test_ambiguity_preserves_both_alternatives_and_candidate_worlds():
    first, second = candidate("one"), candidate("two")
    second["world"] = "dream"

    def reason(request):
        shown = json.loads(request.messages[1]["content"])["candidates"]
        assert [item["world"] for item in shown] == ["real_world", "dream"]
        return decision(selected=["two", "one"], outcome="ambiguous")

    plan = run(reason, initial=packet(first, second))
    assert plan["outcome"] == "ambiguous"
    assert [item["memory_id"] for item in plan["memory_versions"]] == ["two", "one"]


@pytest.mark.parametrize("raw", [
    "not json",
    decision(selected=["forged"]),
    decision(selected=[]),
    decision(selected=["promise"], queries=["unexpected"]),
    decision(selected=["promise"], outcome="not_needed"),
    '{"action":"finish","action":"search","queries":[],"selected_memory_ids":[],"outcome":"not_needed"}',
    '{"action":"finish","queries":[],"selected_memory_ids":[],"outcome":"not_needed","claim":"invented"}',
])
def test_untrusted_or_malformed_decisions_do_not_create_facts(raw):
    plan = run(lambda _: raw, initial=packet(candidate()))
    assert plan["outcome"] == "invalid_decision"
    assert plan["memory_versions"] == []
    assert plan["model_calls"] == 1


def test_candidates_removed_for_input_budget_cannot_be_selected():
    oversized = candidate("large", statement="secret" * 10000)
    reserved = []

    def reason(request):
        assert json.loads(request.messages[1]["content"])["candidates"] == []
        assert "secret" not in request.messages[1]["content"]
        return decision(selected=["large"])

    plan = run(reason, initial=packet(oversized), reserve=lambda **value: reserved.append(value))
    assert plan["outcome"] == "invalid_decision"
    assert plan["observed_event_refs"] == [{"event_id": "recent", "revision": 1}]
    assert reserved[0]["source_refs"] == plan["observed_event_refs"]


def test_prior_counters_reduce_call_and_aggregate_input_budgets():
    plan = run(lambda _: pytest.fail("Budget already spent"), prior_model_calls=3, prior_input_bytes=44)
    assert plan["outcome"] == "budget_exhausted"
    assert plan["model_calls"] == 3 and plan["input_bytes"] == 44
    plan = run(lambda _: pytest.fail("Input cannot fit"), prior_input_bytes=47999)
    assert plan["outcome"] == "budget_exhausted" and plan["model_calls"] == 0
    plan = run(lambda _: decision(outcome="not_needed"), prior_model_calls=1, prior_input_bytes=200)
    assert plan["model_calls"] == 2 and plan["input_bytes"] > 200


def test_duplicate_queries_are_normalized_and_do_not_loop():
    memory = Memory([result()])
    plan = run(lambda _: decision("search", queries=["  EXAM   Promise ", "exam promise"],
                                  outcome="insufficient"), memory=memory)
    assert [query for query, _ in memory.calls] == ["exam promise"]
    assert plan["outcome"] == "invalid_decision"
    assert plan["model_calls"] == 2 and plan["search_calls"] == 1
    assert plan["memory_versions"] == []


def test_search_budget_is_checked_before_queries_run():
    memory = Memory()
    plan = run(lambda _: decision("search", queries=["one", "two"], outcome="insufficient"),
               memory=memory, policy=RecallPolicy(max_search_calls=1))
    assert plan["outcome"] == "budget_exhausted"
    assert memory.calls == [] and plan["search_calls"] == 0


def test_reasoner_errors_are_redacted_and_do_not_select_evidence():
    def reason(_):
        raise RuntimeError("secret provider response")

    plan = run(reason, initial=packet(candidate()))
    assert plan["outcome"] == "reasoner_unavailable"
    assert plan["memory_versions"] == [] and plan["model_calls"] == 1
    assert "secret" not in json.dumps(plan)


@pytest.mark.parametrize("raw", ["x" * 8193, "[" * 1500 + "]" * 1500])
def test_oversized_or_deep_responses_stop_without_selection(raw):
    plan = run(lambda _: raw)
    assert plan["outcome"] == "invalid_decision" and plan["memory_versions"] == []


def test_reasoner_never_sees_sources_beyond_the_dependency_limit():
    initial = packet(*(candidate(str(index)) for index in range(80)))
    reserved = []

    def reason(request):
        data = json.loads(request.messages[1]["content"])
        assert len(data["candidates"]) == 63
        assert data["context_truncated"]
        return decision(outcome="not_needed")

    plan = run(reason, initial=initial, policy=RecallPolicy(max_request_bytes=262144,
               max_input_bytes=262144), reserve=lambda **value: reserved.append(value))
    assert len(plan["observed_event_refs"]) == len(reserved[0]["source_refs"]) == 64


def test_failed_reservation_cannot_trigger_a_model_call():
    def reserve(**_):
        raise DomainError("context_stale", "Source was deleted")

    with pytest.raises(DomainError) as error:
        run(lambda _: pytest.fail("Reservation must happen first"), reserve=reserve)
    assert error.value.code == "context_stale"


def test_reservation_latency_cannot_start_a_model_after_the_deadline(monkeypatch):
    current = [100.0]
    monkeypatch.setattr("agent_memory.recall_control.time.monotonic", lambda: current[0])

    def reserve(**_):
        current[0] += 20

    plan = run(lambda _: pytest.fail("The deadline has elapsed"), reserve=reserve)
    assert plan["outcome"] == "budget_exhausted" and plan["model_calls"] == 1


def test_deadline_is_cooperative_and_late_result_is_discarded(monkeypatch):
    current = [100.0]
    monkeypatch.setattr("agent_memory.recall_control.time.monotonic", lambda: current[0])

    def reason(request):
        assert request.timeout_seconds == 15.0
        current[0] += 16
        return decision(selected=["promise"])

    plan = run(reason, initial=packet(candidate()))
    assert plan["outcome"] == "budget_exhausted" and plan["memory_versions"] == []
    assert plan["elapsed_ms"] == 16000


@pytest.mark.parametrize("field", ["dependency_generation", "scope_generation", "evidence_cutoff"])
def test_changed_snapshot_cannot_be_downgraded_to_a_safe_empty_result(field):
    changed = result(candidate())
    changed["retrieval_snapshot"][field] = 99
    with pytest.raises(DomainError) as error:
        run(lambda _: decision("search", queries=["old promise"], outcome="insufficient"),
            memory=Memory([changed]))
    assert error.value.code == "context_stale"


def test_cross_scope_results_fail_closed():
    changed = result(candidate())
    changed["scope"]["branch_id"] = "other"
    with pytest.raises(DomainError) as error:
        run(lambda _: decision("search", queries=["promise"], outcome="insufficient"),
            memory=Memory([changed]))
    assert error.value.code == "context_stale"


def test_missing_historical_boundary_does_not_search_from_a_false_presupposition():
    initial = packet()
    initial["retrieval_snapshot"]["evidence_cutoff"] = None
    initial["retrieval_snapshot"]["resolved_evidence_through_sequence"] = None
    initial["recall_results"] = []
    plan = run(lambda _: pytest.fail("No historical boundary"), initial=initial)
    assert plan["outcome"] == "insufficient" and plan["model_calls"] == 0


def test_warning_union_and_recent_dependencies_survive_context_trimming():
    initial = packet()
    initial["blocks"][0]["text"] = "very large recent context" * 2000
    later = result(candidate())
    later["coverage"]["limitations"] = ["source_unavailable"]
    responses = iter([decision("search", queries=["promise"], outcome="insufficient"),
                      decision(selected=["promise"])])

    def reason(request):
        data = json.loads(request.messages[1]["content"])
        assert data["context"] == [] and data["context_truncated"]
        assert any("approximate_search" in warning.get("coverage", {}).get("limitations", [])
                   for warning in data["warnings"])
        return next(responses)

    plan = run(reason, initial=initial, memory=Memory([later]))
    assert plan["coverage_limitations"] == ["approximate_search", "source_unavailable"]
    assert {ref["event_id"] for ref in plan["observed_event_refs"]} == {"recent", "event-promise"}
    assert plan["recent_event_refs"] == [{"event_id": "recent", "revision": 1}]


def test_lease_check_failure_propagates_even_after_a_provider_exception():
    calls = [0]

    def check():
        calls[0] += 1
        if calls[0] >= 3:
            raise DomainError("lease_lost", "Stop")

    def reason(_):
        raise RuntimeError("provider failure")

    with pytest.raises(DomainError) as error:
        run(reason, check=check)
    assert error.value.code == "lease_lost"


@pytest.mark.parametrize("values", [
    {"max_model_calls": 9}, {"max_model_calls": True}, {"max_search_calls": -1},
    {"max_input_bytes": 262145}, {"max_request_bytes": 0}, {"max_wall_time_ms": 0},
])
def test_policy_rejects_invalid_budgets(values):
    with pytest.raises(ValueError):
        RecallPolicy(**values)
