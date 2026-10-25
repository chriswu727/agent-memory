import httpx
import pytest
from test_runtime_api import BASE, BEGIN, HEADERS, PRINCIPAL, TURN
from test_runtime_api import runtime_api as runtime_api

from agent_memory.client import MemoryClient
from agent_memory.delivery_receipts import receipt_for
from agent_memory.helpers import DomainError
from agent_memory.runtime_client import RuntimeClient


def test_scope_conflict_is_an_authenticated_conflict_without_starting_turn(runtime_api):
    client, turns = runtime_api
    turns.failure = DomainError("scope_conflict", "Another turn owns this conversation and branch.", retryable=True)
    response = client.post(BASE + "/turns", headers=HEADERS, json=BEGIN)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scope_conflict"
    assert turns.calls == [("begin", PRINCIPAL, "brain", "turn", {
        "conversation_id": "conversation", "branch_id": "branch", "input_fingerprint": "a" * 64,
    })]


@pytest.mark.parametrize("receipt", [
    None, [], "private-receipt", {"text": "private-content"},
    {"version": "private-version", "state": "delivered", "stale_reasons": ["private-reason"]},
])
def test_invalid_receipt_metadata_cannot_leak_through_monitoring(runtime_api, receipt):
    client, turns = runtime_api
    record = turns.record

    def with_receipt(*args, **kwargs):
        return {**record(*args, **kwargs), "scope_reserved": True, "delivery_receipt": receipt}

    turns.record = with_receipt
    for path in (TURN, BASE + "/turns"):
        response = client.get(path, headers=HEADERS)
        assert response.status_code == 200
        assert "private-" not in response.text
        value = response.json() if path == TURN else response.json()["turns"][0]
        assert value["delivery_receipt"] is None
        assert value["scope_reserved"] is True


def test_runtime_status_distinguishes_reserved_scopes_from_active_leases(runtime_api):
    client, turns = runtime_api
    turns.status = lambda *_: {"brain_id": "brain", "active_lease_count": 0, "reserved_scope_count": 2,
                               "private_metadata": "private-reservations"}
    response = client.get(BASE + "/status", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"brain_id": "brain", "active_lease_count": 0, "reserved_scope_count": 2}


def test_valid_receipt_metadata_strips_nested_non_public_fields(runtime_api):
    client, turns = runtime_api
    receipt = receipt_for({
        "output_event_refs": [{"event_id": "draft", "revision": 1}],
        "context_fingerprint": "b" * 64, "context_generation": 2,
        "working_revision": 3, "fence": 1, "revision": 7,
    }, "Sent reply", "delivered", content_recorded=True)
    expected = {**receipt, "authorization": {
        **receipt["authorization"], "draft_event_ref": dict(receipt["authorization"]["draft_event_ref"]),
    }}
    receipt["text"] = "private-response-body"
    receipt["authorization"]["lease_token"] = "private-lease-token"
    receipt["authorization"]["draft_event_ref"]["content"] = "private-draft-body"
    record = turns.record
    turns.record = lambda *args, **kwargs: {**record(*args, **kwargs), "delivery_receipt": receipt}
    response = client.get(TURN, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["delivery_receipt"] == expected
    assert "private-" not in response.text


def test_active_scope_is_authenticated_explicit_and_redacted(runtime_api):
    client, turns = runtime_api
    turns.active_scope = lambda principal, brain_id, **scope: {
        **turns.record("active_scope", principal, brain_id, **scope), "scope_reserved": True,
    }
    query = {"conversation_id": "conversation", "branch_id": "branch"}
    assert client.get(BASE + "/scope", params=query).status_code == 401
    assert turns.calls == []
    response = client.get(BASE + "/scope", headers=HEADERS, params=query)
    assert response.status_code == 200
    assert response.json()["turn"]["scope_reserved"] is True
    assert "private-" not in response.text
    assert turns.calls == [("active_scope", PRINCIPAL, "brain", None, query)]
    turns.active_scope = lambda *_args, **_kwargs: None
    assert client.get(BASE + "/scope", headers=HEADERS, params=query).json() == {"turn": None}


@pytest.mark.parametrize("query", [
    "", "conversation_id=c", "branch_id=b", "conversation_id=&branch_id=b",
    "conversation_id=c&branch_id=b&branch_id=b", "conversation_id=c&branch_id=b&owner_id=other",
    "conversation_id=" + "x" * 257 + "&branch_id=b",
])
def test_active_scope_rejects_missing_extra_duplicate_or_unbounded_parameters(runtime_api, query):
    client, turns = runtime_api
    turns.active_scope = lambda *_args, **_kwargs: pytest.fail("Invalid scope must not reach storage")
    assert client.get(BASE + "/scope?" + query, headers=HEADERS).status_code == 422
    assert turns.calls == []


def test_active_scope_sdk_preserves_scope_ids_in_query_parameters():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"turn": None})

    with MemoryClient("http://memory.test", "key", "brain/a", transport=httpx.MockTransport(handle)) as memory:
        assert RuntimeClient(memory).active_scope("conversation/a", "branch?b") == {"turn": None}
    assert seen[0].url.raw_path.startswith(b"/v1/brains/brain%2Fa/runtime/scope?")
    assert dict(seen[0].url.params) == {"conversation_id": "conversation/a", "branch_id": "branch?b"}
