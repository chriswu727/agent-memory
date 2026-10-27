import hashlib
import os

import httpx
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from membrain.api import create_app
from membrain.client import MemoryClient, default_budget, prepared_context
from membrain.config import Settings
from membrain.helpers import Principal, uid

pytestmark = pytest.mark.integration


@pytest.fixture
def clients():
    load_dotenv()
    dsn = os.environ.get("MEMORY_DATABASE_URL")
    if not dsn:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    first, second = Principal(uid(), uid()), Principal(uid(), uid())
    settings = Settings(database_url=dsn, api_keys={
        hashlib.sha256(b"owner-one").hexdigest(): first,
        hashlib.sha256(b"owner-two").hexdigest(): second,
    })
    with TestClient(create_app(settings)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path,
                                      headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, content=response.content,
                                  headers=dict(response.headers))
        with MemoryClient("http://testserver", "owner-one", transport=httpx.MockTransport(forward)) as one:
            with MemoryClient("http://testserver", "owner-two", transport=httpx.MockTransport(forward)) as two:
                yield one, two, settings


def test_http_lifecycle_preserves_sources_and_small_model_context(clients):
    client, _, _ = clients
    client.brain_id = client.create_brain("HTTP lifecycle")['id']
    observation = client.observe(
        "备考期间，晚上先聊十分钟，再安排复习。",
        conversation_id="study", branch_id="main", source_event_key="agreement",
        interpretation_hint="real_world_report",
    )
    original_ids = observation["result"]["accepted_event_ids"]
    assert len(original_ids) == 1
    repeated = client.observe(
        "备考期间，晚上先聊十分钟，再安排复习。",
        conversation_id="study", branch_id="main", source_event_key="agreement",
        interpretation_hint="real_world_report",
    )
    assert repeated["result"]["accepted_event_ids"] == original_ids
    client.work()
    result = client.recall("备考", conversation_id="study", branch_id="main")["result"]
    assert any("十分钟" in item["memory"]["statement"] for item in result["items"])
    cue = client.observe(
        "今天备考，晚上怎么安排？", conversation_id="study", branch_id="main",
        source_event_key="cue", interpretation_hint="real_world_report",
    )
    prepared = client.prepare(
        "turn", conversation_id="study", branch_id="main",
        latest_input_event_ids=cue["result"]["accepted_event_ids"], budget=default_budget(2000),
    )
    context = prepared_context(prepared)
    assert "十分钟" in context
    assert len(context.encode()) <= 2000
    assert prepared["result"]["usage"]["context_tokens"] == len(context.encode())
    committed = client.commit("turn", conversation_id="study", branch_id="main",
                              expected_working_revision=0)
    assert committed["result"]["working_state"]["revision"] == 1
    checkpoint = client.checkpoint(conversation_id="study", branch_id="main",
                                   expected_working_revision=1, through_sequence=2)
    assert checkpoint["outcome"] == "completed"


def test_http_owner_isolation_and_reconnection(clients):
    one, two, settings = clients
    one.brain_id = one.create_brain("private")['id']
    one.observe("我的私密昵称是北斗七星", conversation_id="private", branch_id="main",
                source_event_key="private-note", interpretation_hint="real_world_report")
    one.work()
    assert two.brains()["brains"] == []
    two.brain_id = one.brain_id
    response = two.action("recall", {
        "query": "北斗七星", "intent": "explore", "cue_event_ids": [], "additional_cues": [],
        "budget": default_budget(16000),
        "read_fence": {"minimum_durable_sequence": 0, "consistency": "best_available", "snapshot_revision": None},
    }, raise_on_error=False)
    assert response["error"]["code"] == "not_found_or_forbidden"
    assert "北斗七星" not in str(response)
    with TestClient(create_app(settings)) as reopened:
        response = reopened.get(f"/v1/brains/{one.brain_id}/dashboard",
                                headers={"Authorization": "Bearer owner-one"})
        assert response.status_code == 200
        assert "北斗七星" in str(response.json()["memories"])
