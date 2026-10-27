import copy
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb
from test_feedback_dynamics import Feedback
from test_runtime_api import HEADERS, TURN
from test_runtime_api import runtime_api as runtime_api

from membrain import prepared_selection
from membrain.client import default_budget, default_read_fence, integration_capabilities
from membrain.config import Settings
from membrain.helpers import DomainError, digest, uid
from membrain.prepared_selection import (
    sanitize_prepared_selection,
    selection_for_turn,
    text_hash,
)
from membrain.storage import Database
from membrain.turns import TurnStore


def selection():
    return {
        "version": "prepared-memory-selection-v1", "state": "complete", "unavailable_reason": None,
        "turn_id": "turn", "brain_id": "brain", "conversation_id": "conversation", "branch_id": "main",
        "packet_id": "packet", "model_context_sha256": "a" * 64, "context_fingerprint": "b" * 64,
        "context_generation": 0, "working_revision": 0, "prepared_at": "2026-10-25T00:00:00Z",
        "input_event_refs": [{"event_id": "input", "revision": 1}],
        "source_event_refs": [{"event_id": "source", "revision": 1, "content_sha256": "c" * 64}],
        "memories": [{"memory_id": "memory", "memory_revision": 1, "statement_sha256": "d" * 64,
                      "trace_id": "trace", "recall_id": "recall", "evidence_refs": [{
                          "event_id": "source", "event_revision": 1,
                          "source_content_sha256": "c" * 64, "relationship": "supports"}]}],
    }


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(private_text="private-content"),
    lambda value: value.update(context_generation=True),
    lambda value: value.update(working_revision=1.0),
    lambda value: value.update(input_event_refs=[]),
    lambda value: value["source_event_refs"].append({**value["source_event_refs"][0], "revision": 2}),
    lambda value: value["input_event_refs"].append({"event_id": "input", "revision": 2}),
    lambda value: value["memories"].append({**value["memories"][0], "memory_revision": 2}),
    lambda value: value["memories"][0].update(memory_revision=1.0),
    lambda value: value["memories"][0].update(evidence_refs=[]),
    lambda value: value["memories"][0]["evidence_refs"][0].update(event_id="unbound"),
    lambda value: value["memories"][0]["evidence_refs"][0].update(source_content_sha256="e" * 64),
    lambda value: value["memories"][0]["evidence_refs"][0].update(event_revision=True),
])
def test_corrupt_binding_cannot_authorize_feedback_or_leak_extra_fields(mutation):
    value = selection()
    mutation(value)
    assert sanitize_prepared_selection(value) is None


def test_binding_is_detached_and_matches_turn_identity_without_rebasing_prepared_revision():
    value = selection()
    detached = sanitize_prepared_selection(value)
    assert detached == value and detached is not value
    detached["memories"].clear()
    assert value["memories"]
    row = {**value, "working_revision": 9, "prepared_memory_selection": value,
           "context_event_refs": [{"event_id": "source", "revision": 1}]}
    assert selection_for_turn(row) == value
    for changes in ({"turn_id": "different"}, {"conversation_id": "other"}, {"brain_id": "other"},
                    {"context_generation": 1}, {"context_fingerprint": "f" * 64},
                    {"input_event_refs": [{"event_id": "new", "revision": 1}]}, {"context_event_refs": []}):
        assert selection_for_turn({**row, **changes}) is None


def test_explicit_custom_context_is_valid_metadata_but_never_an_attribution_binding():
    sentinel = {"version": "prepared-memory-selection-v1", "state": "unavailable",
                "unavailable_reason": "custom_context"}
    assert sanitize_prepared_selection(sentinel) == sentinel
    assert selection_for_turn({"prepared_memory_selection": sentinel}) is None
    assert sanitize_prepared_selection({**sentinel, "text": "private-context"}) is None
    assert sanitize_prepared_selection({**sentinel, "unavailable_reason": "unknown"}) is None


def test_monitoring_rejects_private_or_invalid_selection_metadata(runtime_api):
    client, turns = runtime_api
    record = turns.record
    for value in ({"private_text": "private-content"}, {**selection(), "private_text": "private-content"}):
        turns.record = lambda *args, **kwargs: {**record(*args, **kwargs), "prepared_memory_selection": value}
        response = client.get(TURN, headers=HEADERS)
        assert response.status_code == 200 and response.json()["prepared_memory_selection"] is None
        assert "private-content" not in response.text
    turns.record = lambda *args, **kwargs: {**record(*args, **kwargs), "prepared_memory_selection": selection()}
    assert client.get(TURN, headers=HEADERS).json()["prepared_memory_selection"] == selection()


@pytest.fixture
def captured_selection():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("A dedicated PostgreSQL database is required")
    data = Feedback(Database(settings.database_url))
    source, memory = data.seed()
    event = data.observe("我喜欢哪种香味，松木吗？")
    turn_id = uid()
    store = TurnStore(data.database, service=data.service)
    store.begin(data.principal, data.brain, turn_id=turn_id, conversation_id="chat", branch_id="main",
                input_fingerprint=digest(event["observed"]["content"]))
    row = store.claim(data.principal, data.brain, turn_id, worker_id="binding-tests")
    token = row["lease_token"]
    row = store.advance(data.principal, data.brain, turn_id, lease_token=token, expected_revision=row["revision"],
                        stage="captured", input_event_refs=[{"event_id": event["event_id"], "revision": event["revision"]}])

    def prepare(**changes):
        return data.action("prepare", {
            "turn_id": turn_id, "latest_input_event_ids": [event["event_id"]],
            "read_fence": default_read_fence(), "budget": default_budget(64000),
            "capabilities": integration_capabilities(), **changes,
        })

    packet = prepare()

    def fields(value):
        refs = sorted({(ref["event_id"], ref["event_revision"]) for block in value["blocks"]
                       for ref in block["evidence_refs"]})
        return {
            "lease_token": token, "expected_revision": row["revision"], "stage": "prepared",
            "context_event_refs": [{"event_id": key, "revision": revision} for key, revision in refs],
            "context_fingerprint": digest(value["model_context"]),
            "context_generation": value["coverage"]["dependency_generation"],
            "working_revision": value["working_state"]["revision"], "context_tokens": value["usage"]["context_tokens"],
            "prepared_packet_id": value["packet_id"], "memory_context_sha256": text_hash(value["model_context"]),
        }

    def advance(value=None, **changes):
        return store.advance(data.principal, data.brain, turn_id, **{**fields(value or packet), **changes})

    return SimpleNamespace(data=data, source=source, memory=memory, event=event, turn_id=turn_id,
                           store=store, row=row, token=token, packet=packet, prepare=prepare,
                           fields=fields, advance=advance)


@pytest.mark.integration
@pytest.mark.parametrize("change,code", [
    ({"prepared_packet_id": "not-server-issued"}, "context_stale"),
    ({"memory_context_sha256": "f" * 64}, "invalid_evidence"),
    ({"context_event_refs": []}, "invalid_evidence"),
    ({"prepared_packet_id": None}, "validation_failed"),
    ({"memory_context_sha256": None}, "validation_failed"),
    ({"stage": "generating"}, "validation_failed"),
    ({"selection_omitted_reason": "custom_context"}, "validation_failed"),
    ({"prepared_packet_id": None, "memory_context_sha256": None,
      "selection_omitted_reason": "invented"}, "validation_failed"),
])
def test_prepared_transition_rejects_forged_or_incomplete_binding(captured_selection, change, code):
    case = captured_selection
    with pytest.raises(DomainError) as error:
        case.advance(**change)
    assert error.value.code == code
    row = case.store.get(case.data.principal, case.data.brain, case.turn_id)
    assert row["stage"] == "captured" and row["prepared_memory_selection"] is None


@pytest.mark.integration
@pytest.mark.parametrize("different", ["turn", "input", "scope", "edited_source", "erased_source"])
def test_packet_binding_rejects_wrong_identity_and_invalidated_sources(captured_selection, different):
    case = captured_selection
    if different == "turn":
        packet = case.prepare(turn_id=uid())
    elif different == "input":
        other = case.data.observe("Another original input.")
        packet = case.prepare(latest_input_event_ids=[other["event_id"]])
    elif different == "scope":
        other = case.data.observe("我喜欢哪种香味，松木吗？", conversation="elsewhere")
        packet = case.data.action("prepare", {
            "turn_id": case.turn_id, "latest_input_event_ids": [other["event_id"]],
            "read_fence": default_read_fence(), "budget": default_budget(64000),
            "capabilities": integration_capabilities(),
        }, conversation="elsewhere")
    else:
        packet = case.packet
        case.data.invalidate(case.source, operation="edit" if different == "edited_source" else "erase")
    with pytest.raises(DomainError) as error:
        case.advance(packet)
    assert error.value.code in {"context_stale", "source_unavailable"}
    assert case.store.get(case.data.principal, case.data.brain, case.turn_id)["prepared_memory_selection"] is None


@pytest.mark.integration
def test_prepared_retry_uses_durable_binding_after_request_cache_eviction(captured_selection):
    case = captured_selection
    first = case.advance()
    with case.data.database.transaction(case.data.principal) as conn:
        conn.execute("UPDATE memory_requests SET response=NULL WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                     "AND action='prepare'", case.data.params)
    replay = case.advance()
    assert replay["revision"] == first["revision"]
    assert replay["prepared_memory_selection"] == first["prepared_memory_selection"]
    with pytest.raises(DomainError):
        case.advance(expected_revision=first["revision"], memory_context_sha256="a" * 64)


@pytest.mark.integration
def test_final_packet_not_earlier_preparation_controls_selection(captured_selection):
    case = captured_selection
    assert case.packet["recall_traces"]
    empty = case.prepare(budget=default_budget(800))
    assert empty["recall_traces"] == []
    row = case.advance(empty)
    assert row["prepared_memory_selection"]["memories"] == []
    assert row["prepared_memory_selection"]["packet_id"] == empty["packet_id"]


@pytest.mark.integration
def test_legacy_prepare_keeps_binding_absent(captured_selection):
    case = captured_selection
    row = case.advance(prepared_packet_id=None, memory_context_sha256=None)
    assert row["stage"] == "prepared" and row["prepared_memory_selection"] is None


@pytest.mark.integration
def test_explicit_opt_out_is_persisted_through_actual_delivery(captured_selection):
    case = captured_selection
    row = case.advance(prepared_packet_id=None, memory_context_sha256=None,
                       selection_omitted_reason="custom_context")

    def advance(stage):
        nonlocal row
        row = case.store.advance(case.data.principal, case.data.brain, case.turn_id, lease_token=case.token,
                                 expected_revision=row["revision"], stage=stage)

    def output(state):
        nonlocal row
        row = case.store.record_output(case.data.principal, case.data.brain, case.turn_id, lease_token=case.token,
                                       expected_revision=row["revision"], text=case.memory["statement"], delivery_state=state)

    advance("generating")
    output("draft")
    advance("delivering")
    output("delivered")
    case.store.finish(case.data.principal, case.data.brain, case.turn_id, lease_token=case.token,
                      expected_revision=row["revision"])
    event = case.data.observe("yes")
    assert case.data.access(case.memory) is None
    selected = next(trace for trace in case.packet["recall_traces"] if trace["memory_id"] == case.memory["memory_id"])
    response = case.data.commit(case.data.proposal(case.memory, selected["recall_id"], event))
    assert response["outcome"] == "completed"
    assert case.data.access(case.memory) is None
    assert case.store.get(case.data.principal, case.data.brain, case.turn_id)["prepared_memory_selection"] == {
        "version": "prepared-memory-selection-v1", "state": "unavailable", "unavailable_reason": "custom_context",
    }


@pytest.mark.integration
@pytest.mark.parametrize("custom", [True, False])
def test_custom_context_sentinel_blocks_an_otherwise_valid_legacy_manual_attribution(captured_selection, custom):
    data = Feedback(captured_selection.data.database)
    _, memory = data.seed()
    trace = data.select(memory)
    if custom:
        with data.database.transaction(data.principal) as conn:
            conn.execute("UPDATE memory_runtime_turns SET prepared_memory_selection=%s WHERE tenant_id=%s "
                         "AND owner_id=%s AND brain_id=%s AND turn_id=%s", (
                             Jsonb({"version": "prepared-memory-selection-v1", "state": "unavailable",
                                    "unavailable_reason": "custom_context"}), *data.params, trace["turn_id"],
                         ))
    event = data.observe("yes")
    response = data.commit(data.proposal(memory, trace["recall_id"], event))
    assert response["outcome"] == "completed"
    if custom:
        assert data.access(memory) is None
    else:
        assert data.access(memory)["external_uses"] == 1


@pytest.mark.integration
def test_selection_limit_is_explicit_and_never_credits_a_truncated_subset(captured_selection, monkeypatch):
    case = captured_selection
    case.data.seed()
    packet = case.prepare()
    assert len({trace["memory_id"] for trace in packet["recall_traces"]}) >= 2
    monkeypatch.setattr(prepared_selection, "MAX_MEMORIES", 1)
    value = case.advance(packet)["prepared_memory_selection"]
    assert value["state"] == "unavailable" and value["unavailable_reason"] == "selection_limit"
    assert value["memories"] == []


def test_selection_size_and_count_bounds_are_enforced_before_feedback():
    value = selection()
    value["memories"] = [{**copy.deepcopy(value["memories"][0]), "memory_id": str(index)} for index in range(65)]
    assert sanitize_prepared_selection(value) is None
    value = selection()
    value["memories"][0]["evidence_refs"] = [{
        **value["memories"][0]["evidence_refs"][0], "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    } for index in range(1024)]
    value["memories"] = [{**copy.deepcopy(value["memories"][0]), "memory_id": str(index)} for index in range(2)]
    assert sanitize_prepared_selection(value) is None
