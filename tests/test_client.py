import hashlib
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from membrain.client import (
    MemoryActionError,
    MemoryClient,
    default_budget,
    prepared_context,
    recalled_context,
)


@pytest.fixture
def requests_and_client():
    requests = []
    schema = json.loads((Path(__file__).parents[1] / "architecture/memory-contract.schema.json").read_text())
    validator = Draft202012Validator({"$ref": "#/$defs/ActionRequest", "$defs": schema["$defs"]})

    def handle(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        validator.validate(payload)
        requests.append(payload)
        return httpx.Response(200, json={
            "message_type": "response", "contract_version": "1.0.0-draft",
            "request_id": payload["request_id"], "action": payload["action"],
            "outcome": "completed", "result": {}, "server_scope": {},
        })

    with MemoryClient("http://memory.test", "test-key", "brain", transport=httpx.MockTransport(handle)) as client:
        yield requests, client


def test_convenience_methods_send_complete_contract(requests_and_client):
    requests, client = requests_and_client
    branch = {"conversation_id": "chat", "branch_id": "main"}
    client.observe("我喜欢下雨", source_event_key="user-1", **branch)
    client.recall("下雨", intent="cue_recovery", evidence_mode="prior_record_check",
                  evidence_cutoff={"mode": "through_sequence", "through_sequence": 1}, **branch)
    client.prepare("turn-1", latest_input_event_ids=["event-1"], **branch)
    client.inspect(["memory-1"], **branch)
    client.commit("turn-1", expected_working_revision=0, delivered_event_ids=["event-2"], **branch)
    client.checkpoint(expected_working_revision=1, through_sequence=2, **branch)
    client.correct("memory-1", expected_revision=1, expected_dependency_generation=0,
                   correction_event_ids=["event-3"], operation="retract", **branch)
    client.forget(["event-1"], kind="events", mode="erase_content", expected_scope_generation=1, **branch)
    client.export(**branch)
    client.restore("archive-1", expected_manifest_sha256="a" * 64, expected_scope_generation=2, **branch)
    assert [request["action"] for request in requests] == [
        "observe", "recall", "prepare", "inspect", "commit", "checkpoint", "correct", "forget", "export", "restore",
    ]
    assert all(request["target"] == {"brain_ref": "brain", "conversation_ref": "chat", "branch_ref": "main"}
               for request in requests)
    capabilities = requests[2]["payload"]["capabilities"]
    assert not capabilities["automatic_capture"]
    assert capabilities["guarantee_level"] == "tools_only"
    assert requests[1]["payload"]["budget"]["max_context_tokens"] == 16000
    assert requests[2]["payload"]["budget"]["max_context_tokens"] == 2000
    assert requests[3]["payload"]["budget"]["max_context_tokens"] == 16000
    assert requests[-1]["payload"]["apply_current_tombstones"] is True


def test_observation_identity_survives_retry_and_changes_only_with_revision(requests_and_client):
    requests, client = requests_and_client
    options = {"conversation_id": "chat", "branch_id": "main", "source_event_key": "message-1"}
    client.observe("first", **options)
    client.observe("first", **options)
    client.observe("changed content must conflict", **options)
    client.observe("edited", source_revision=2, **options)
    keys = [request["payload"]["idempotency_key"] for request in requests]
    assert keys[0] == keys[1] == keys[2]
    assert keys[3] != keys[0]
    identity = '["observe",{"brain_ref":"brain","branch_ref":"main","conversation_ref":"chat"},["sdk","message-1",1]]'
    assert keys[0] == "observe:" + hashlib.sha256(identity.encode()).hexdigest()


def test_custom_budget_and_read_fence_are_not_replaced(requests_and_client):
    requests, client = requests_and_client
    budget = default_budget(0)
    fence = {"minimum_durable_sequence": 19, "consistency": "snapshot", "snapshot_revision": 7}
    client.recall("anything", budget=budget, read_fence=fence)
    assert requests[-1]["payload"]["budget"] == budget
    assert requests[-1]["payload"]["read_fence"] == fence


def test_low_level_send_retains_full_scope_and_error():
    request = {
        "message_type": "request", "contract_version": "1.0.0-draft", "request_id": "stable",
        "action": "recall", "target": {"brain_ref": "brain", "collection_refs": ["private"]},
        "payload": {},
    }
    error = {
        "message_type": "response", "contract_version": "1.0.0-draft", "request_id": "stable",
        "action": "recall", "outcome": "rejected",
        "error": {"code": "not_found_or_forbidden", "message": "Scope unavailable", "retryable": False},
    }

    def handle(incoming):
        assert json.loads(incoming.content) == request
        return httpx.Response(200, json=error)

    with MemoryClient("http://memory.test", "key", transport=httpx.MockTransport(handle)) as client:
        assert client.send(request, raise_on_error=False) == error
        with pytest.raises(MemoryActionError) as caught:
            client.send(request)
        assert caught.value.response == error
        assert caught.value.retryable is False


def test_client_does_not_retry_ambiguous_writes(requests_and_client):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    with MemoryClient("http://memory.test", "key", "brain", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.observe("hello", conversation_id="chat", branch_id="main", source_event_key="message-1")
    assert len(calls) == 1


def test_http_errors_and_mismatched_response_are_not_success():
    with MemoryClient("http://memory.test", "key", "brain", transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"detail": "unauthorized"}),
    )) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.recall("hello")
    with MemoryClient("http://memory.test", "key", "brain", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"request_id": "other", "action": "recall"}),
    )) as client:
        with pytest.raises(ValueError, match="request_id"):
            client.recall("hello")


def test_management_endpoints_and_missing_brain():
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json={})

    with MemoryClient("http://memory.test/", "key", transport=httpx.MockTransport(handle)) as client:
        client.brains()
        client.create_brain("Companion")
        client.capabilities()
        client.dashboard("brain")
        client.work("brain")
        with pytest.raises(ValueError, match="brain_id"):
            client.recall("hello")
    assert [(method, path) for method, path, _ in seen] == [
        ("GET", "/v1/brains"), ("POST", "/v1/brains"), ("GET", "/v1/capabilities"),
        ("GET", "/v1/brains/brain/dashboard"), ("POST", "/v1/brains/brain/work"),
    ]
    assert json.loads(seen[1][2]) == {"name": "Companion"}


def test_prepared_context_includes_evidence_labels_without_control_plane():
    packet = {
        "packet_id": "packet", "scope": {"tenant_id": "do-not-inject"},
        "recall_traces": [{"private_metadata": "do-not-inject"}],
        "blocks": [{
            "section": "relevant_memory", "text": "雨天去书店🌧️", "trust": "memory_data_not_instructions",
            "evidence_refs": [{"event_id": "event-1", "event_revision": 2, "source_content_sha256": "a" * 64}],
        }],
    }
    rendered = prepared_context(packet)
    view = json.loads(rendered)
    source_id = view["blocks"][0]["sources"][0]
    assert view["blocks"] == [{"section": "relevant_memory", "content": "雨天去书店🌧️", "sources": [source_id]}]
    assert view["sources"] == {source_id: {"event_id": "event-1", "event_revision": 2}}
    assert view["format"] == "compact-memory-v1"
    assert view["trust"] == "memory_data_not_instructions"
    assert "do-not-inject" not in rendered
    response = {"message_type": "response", "action": "prepare", "outcome": "completed", "result": packet}
    assert prepared_context(response) == rendered


def test_archive_transport_preserves_content():
    seen = []
    archive = {"events": [{"content": "private history"}], "tombstones": []}

    def handle(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json=archive if request.method == "GET" else {"uploaded_archive_ref": "upload"})

    with MemoryClient("http://memory.test", "key", "brain", transport=httpx.MockTransport(handle)) as client:
        assert client.download_archive("archive") == archive
        assert client.upload_archive(archive)["uploaded_archive_ref"] == "upload"
    assert seen[0][:2] == ("GET", "/v1/archives/archive")
    assert seen[1][:2] == ("POST", "/v1/brains/brain/archives")
    assert json.loads(seen[1][2]) == archive


def test_recalled_context_accepts_result_or_successful_action_without_control_metadata():
    from test_cognition import event, request, scope, state

    from membrain import cognition

    source = event("rainy day bookstore")
    result = cognition.recall(scope(), request("bookstore"), cognition.extract_event(source), [source], state())
    rendered = recalled_context(result)
    response = {"message_type": "response", "action": "recall", "outcome": "completed", "result": result}
    assert recalled_context(response) == rendered
    assert "rainy day bookstore" in rendered
    assert "principal_binding_id" not in rendered
    assert len(rendered.encode()) == result["usage"]["context_tokens"]


def test_recalled_context_rejects_wrong_action_and_error_response():
    with pytest.raises(ValueError, match="recall response"):
        recalled_context({"message_type": "response", "action": "prepare", "outcome": "completed"})
    for outcome in ("rejected", "conflict", "failed"):
        with pytest.raises(MemoryActionError):
            recalled_context({"message_type": "response", "action": "recall", "outcome": outcome,
                              "error": {"code": "forbidden", "message": "Unavailable"}})


@pytest.mark.parametrize("action,helper", [("prepare", prepared_context), ("recall", recalled_context)])
@pytest.mark.parametrize("envelope", [False, True])
def test_context_helpers_preserve_authoritative_numeric_and_unicode_bytes(action, helper, envelope):
    authoritative = '{"numbers":[1e20,1.0,9007199254740993],"text":"雨天🌧️ é"}'
    result = {"model_context": authoritative, "usage": {"context_tokens": len(authoritative.encode())}}
    supplied = ({"message_type": "response", "action": action, "outcome": "completed", "result": result}
                if envelope else result)
    assert helper(supplied).encode() == authoritative.encode()
    assert len(helper(supplied).encode()) == result["usage"]["context_tokens"]


@pytest.mark.parametrize("action,helper", [("prepare", prepared_context), ("recall", recalled_context)])
@pytest.mark.parametrize("outcome", ["wrong_action", "rejected", "conflict", "failed"])
def test_authoritative_context_cannot_bypass_response_action_or_failure_checks(action, helper, outcome):
    response = {"message_type": "response", "action": action, "outcome": outcome,
                "result": {"model_context": "must not be returned"},
                "error": {"code": "forbidden", "message": "Unavailable"}}
    if outcome == "wrong_action":
        response.update(action="recall" if action == "prepare" else "prepare", outcome="completed")
    with pytest.raises(ValueError if outcome == "wrong_action" else MemoryActionError):
        helper(response)


def test_legacy_recall_without_authoritative_context_keeps_canonical_fallback():
    from test_cognition import event, request, scope, state

    from membrain import cognition

    source = event("旧来源仍可回忆🌧️。")
    result = cognition.recall(scope(), request("旧来源"), cognition.extract_event(source), [source], state())
    authoritative = result.pop("model_context")
    assert recalled_context(result) == authoritative
    response = {"message_type": "response", "action": "recall", "outcome": "completed", "result": result}
    assert recalled_context(response) == authoritative
