import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_desktop_chat import arguments
from test_learning import learning as learning
from test_semantic import claim as proposed_claim
from test_semantic import encoded, proposals, verifier
from test_semantic_provider import completion

from membrain import desktop_cli, semantic_provider
from membrain.api import create_app
from membrain.client import MemoryClient
from membrain.cognition_config import CognitionPolicyConfiguration, save_cognition_policy
from membrain.config import configured_service
from membrain.helpers import uid
from membrain.jobs import JobStore
from membrain.learning import LearningEngine
from membrain.local_profile import configure_cognition, ensure_profile, load_profile
from membrain.runtime_client import RuntimeClient

pytestmark = pytest.mark.integration


@contextmanager
def restarted(profile, harness):
    settings = replace(profile.settings(), database_url=harness.database.dsn, migration_database_url=None,
                       allowed_hosts=("testserver",),
                       api_keys={key: harness.principal for key in profile.settings().api_keys})
    with configured_service(settings) as service, TestClient(create_app(settings, service=service)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path, headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        with MemoryClient("http://testserver", profile.api_key, harness.brain,
                          transport=httpx.MockTransport(forward)) as memory:
            yield service, memory


def execute(service, harness, job_id=None):
    job = JobStore(service.database).claim(harness.principal, harness.brain, worker_id=uid(),
                                           kinds=["consolidate"], job_id=job_id)
    assert job is not None
    LearningEngine(service)._execute(harness.principal, harness.brain, job)
    return job["id"]


@pytest.mark.parametrize("restart_after_freeze", [False, True])
def test_public_policy_restart_and_bounded_recovery_repairs_timeout_without_database_edits(
    learning, tmp_path, monkeypatch, capsys, restart_after_freeze,
):
    requests = []
    original = semantic_provider.ChatCompletionsSemanticProvider

    def handle(request):
        body = json.loads(request.content)
        data = json.loads(body["messages"][-1]["content"])
        requests.append(request)
        if request.extensions["timeout"]["read"] < 31:
            raise httpx.ReadTimeout("private-provider-timeout", request=request)
        if "proposals" in data:
            schema = json.loads(body["messages"][0]["content"].split("Response JSON schema:\n", 1)[1])
            content = verifier(SimpleNamespace(messages=body["messages"], response_schema=schema))
        else:
            source = next(item for item in data["sources"] if item["observed"]["actor_role"] == "user")
            content = encoded(proposals(proposed_claim(
                source, statement="The user reports preferring a blue mug.", conditions={"op": "always"},
            )))
        return httpx.Response(200, json=completion(content))

    monkeypatch.setattr(semantic_provider, "ChatCompletionsSemanticProvider", lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(handle)))
    profile = configure_cognition(ensure_profile(tmp_path / "profile"), "https://provider.test/v1",
                                  "explicit-model", "private-provider-key", thinking="enabled")
    with restarted(profile, learning) as (service, memory):
        memory.observe("I prefer a blue mug.", conversation_id="chat", branch_id="main", source_event_key="mug-1")
        parent_id = execute(service, learning)
        runtime = RuntimeClient(memory)
        parent = runtime.job(parent_id)
        assert parent["state"] == "cancelled" and parent["model_calls"] == 1
        before = runtime.learning_progress()
        assert before["blocked_count"] == 1
        assert before["blocked_examples"][0]["job_id"] == parent_id
        assert before["blocked_examples"][0]["reason"] == "provider_failed"
        assert parent["learning_summary"]["processing_status"] == "provider_unavailable"
        prior_usage = runtime.cognitive_usage()
        assert len(requests) == 1
        frozen = learning.job(parent_id)["result"]["semantic_policy"]
        assert frozen["limits"]["max_wall_time_ms"] == 30000

    desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory),
                                   "--preset", "reasoning"))
    assert json.loads(capsys.readouterr().out)["restart_required_if_running"] is True
    with restarted(load_profile(profile.directory), learning) as (service, memory):
        assert service.semantic_processor.policy.max_wall_time_ms == 240000
        runtime = RuntimeClient(memory)
        assert runtime.learning_progress()["blocked_count"] == 1
        assert runtime.cognitive_usage() == prior_usage
        assert len(requests) == 1
        child = runtime.retry_learning(parent_id, request_id="explicit-timeout-recovery", expected_fence=parent["fence"])
        assert (child["max_model_calls"], child["max_input_bytes"]) == (2, 196608)
        assert len(requests) == 1
        if restart_after_freeze:
            selected = JobStore(service.database).claim(learning.principal, learning.brain, worker_id=uid(),
                                                         kinds=["consolidate"], job_id=child["id"])
            _, frozen_child = LearningEngine(service)._snapshot(learning.principal, learning.brain, selected)
            saved = learning.job(child["id"])
            assert saved["model_calls"] == 0 and len(requests) == 1
            assert frozen_child["source_snapshot"] == learning.job(parent_id)["result"]["source_snapshot"]
            assert saved["payload"]["allocation_input_fingerprint"] != learning.job(parent_id)["payload"]["allocation_input_fingerprint"]
            save_cognition_policy(CognitionPolicyConfiguration(), profile.directory)
            with restarted(load_profile(profile.directory), learning) as (new_service, _):
                assert new_service.semantic_processor.policy.max_wall_time_ms == 30000
                resumed, resumed_snapshot = LearningEngine(new_service)._snapshot(learning.principal, learning.brain, selected)
                assert resumed_snapshot == frozen_child
                assert resumed.sources
                LearningEngine(new_service)._execute(learning.principal, learning.brain, selected)
        else:
            assert execute(service, learning, child["id"]) == child["id"]
        completed = runtime.job(child["id"])
        assert completed["state"] == "completed" and completed["model_calls"] == 2
        assert completed["learning_summary"]["published_counts"]["claims"] == 1
        assert runtime.learning_progress()["open_window_count"] == 0
        replay = runtime.retry_learning(parent_id, request_id="explicit-timeout-recovery", expected_fence=parent["fence"])
        assert replay["id"] == child["id"] and replay["state"] == "completed"
        retained = runtime.job(parent_id)
        for key in ("state", "model_calls", "input_bytes", "fence", "learning_summary"):
            assert retained[key] == parent[key]
        assert learning.job(parent_id)["result"]["semantic_policy"] == frozen
        assert learning.job(child["id"])["result"]["semantic_policy"]["limits"]["max_wall_time_ms"] == 240000
        assert len(requests) == 3
        assert all(json.loads(request.content)["max_tokens"] == 16384 for request in requests[1:])
        assert all(230 < request.extensions["timeout"]["read"] <= 240 for request in requests[1:])
