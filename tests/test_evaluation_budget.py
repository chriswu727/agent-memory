import copy
import hashlib
import json
from dataclasses import asdict

import httpx
import pytest
from test_learning import learning as learning
from test_memory_lifecycle_evaluation import evaluation as evaluation
from test_semantic_call_usage import Provider
from test_semantic_provider import completion

from membrain.api import create_app
from membrain.client import MemoryClient
from membrain.config import Settings
from membrain.helpers import digest
from membrain.runtime import BrainRuntime, DeliveryReceipt
from membrain.runtime_client import RuntimeClient
from membrain.semantic import SemanticProcessor

pytestmark = pytest.mark.integration


def trial(evaluation):
    value = object.__new__(evaluation.LifecycleTrial)
    value.arm = "memory"
    value.report = {"plan": evaluation.plan("long")}
    value.report["plan_sha256"] = digest(value.report["plan"])
    value.current = {}
    value.saved = []
    value.save = lambda: value.saved.append(copy.deepcopy(value.report))
    return value


def test_long_evaluation_records_public_budget_once_before_any_paid_call(learning, evaluation):
    value = trial(evaluation)
    value.configure_runtime(learning.memory)
    saved = value.report["server_budget_configurations"][learning.brain]
    assert saved["state"] == "finished"
    assert saved["receipt"]["configuration"]["policy"] == value.report["plan"]["server_budget_policy"]
    assert value.saved[0]["server_budget_configurations"][learning.brain]["state"] == "started"
    value.configure_runtime(learning.memory)
    assert RuntimeClient(learning.memory).cognitive_budget()["revision"] == 1
    assert value.current["server_budget_before"]["used"]["model_calls"] == 0
    assert learning.calls == []
    configuration = RuntimeClient(learning.memory).configure_cognitive_budget
    configuration(request_id="outside-change", expected_revision=1,
                  window_seconds=3600, max_model_calls=100, max_input_bytes=2_000_000)
    with pytest.raises(RuntimeError, match="differs from its frozen"):
        value.configure_runtime(learning.memory)
    assert RuntimeClient(learning.memory).cognitive_budget()["revision"] == 2


def test_evaluation_never_replaces_existing_allowance_or_retries_uncertain_configuration(learning, evaluation):
    client = RuntimeClient(learning.memory)
    client.configure_cognitive_budget(request_id="existing", expected_revision=0,
                                      window_seconds=3600, max_model_calls=1, max_input_bytes=1000)
    with pytest.raises(RuntimeError, match="refuses to replace"):
        trial(evaluation).configure_runtime(learning.memory)
    value = trial(evaluation)
    value.report["server_budget_configurations"] = {learning.brain: {"state": "started"}}
    with pytest.raises(RuntimeError, match="differs from its frozen"):
        value.configure_runtime(learning.memory)
    assert client.cognitive_budget()["revision"] == 1
    assert learning.calls == []


def test_evaluation_recording_preserves_provider_usage_in_durable_ledger(learning, evaluation):
    value = trial(evaluation)
    value.provider = Provider(lambda request: learning.propose(request) if request.phase == "proposal"
                              else learning.verify(request),
                              {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20})
    value.cognition = lambda request: value.provider(request)
    provider = evaluation.RecordedCognition(value)
    learning.service.semantic_processor = SemanticProcessor(provider, provider)
    client, row, token = learning.capture()
    client.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    result = client.cognitive_usage()
    assert result["reservation_count"] == result["usage_known_count"] == 2
    assert result["usage_unknown_count"] == 0
    assert result["reported_tokens"]["total_tokens"] == 40


def test_live_evaluation_uses_configured_service_and_reopens_recorded_provider_per_stage(learning, evaluation,
                                                                                    tmp_path):
    calls, transports, providers = [], [], []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={**completion('{"situation_updates":[]}'),
                                        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}})

    def meter_factory():
        meter = evaluation.MeteredTransport(tmp_path / "calls.jsonl", max_calls=2, max_output_tokens=32768,
                                            transport=httpx.MockTransport(handle), capture_content=False)
        transports.append(meter)
        return meter

    planned = evaluation.plan()
    report = {"arm": "memory", "plan": planned, "plan_sha256": digest(planned),
              "principal": asdict(learning.principal), "brains": {}, "stages": []}
    settings = Settings(database_url=learning.database.dsn,
                        api_keys={hashlib.sha256(b"configured-test").hexdigest(): learning.principal},
                        cognition_base_url=planned["provider_base_url"], cognition_api_key="private-test-provider-key",
                        cognition_model=planned["model"], cognition_thinking=planned["thinking"],
                        cognition_max_wall_time_ms=240000, cognition_max_output_tokens=16384,
                        cognition_max_response_bytes=65536)
    meter = meter_factory()
    value = evaluation.LifecycleTrial(tmp_path, report, learning.database, None, None, meter, None,
                                      cognition_settings=settings, meter_factory=meter_factory)
    try:
        for index in range(2):
            value.current = {"name": str(index)}
            report["stages"].append(value.current)
            with value.stage_service():
                providers.append(value.provider)
                service = value.make_service(learning.database)
                assert service.database is not learning.database
                with evaluation.loopback_server(create_app(settings, service=service)) as url:
                    with MemoryClient(url, "configured-test", learning.brain) as memory:
                        runtime = BrainRuntime(memory, lambda _: "Understood.",
                                               deliver=lambda _: DeliveryReceipt("delivered"))
                        result = runtime.respond("I use a blue notebook.", message_id=str(index), conversation_id=str(index))
                        assert result.stage == "completed"
                        job = learning.job(runtime.journal.get(result.turn_id)["orientation_job_id"])
                        assert job["result"]["semantic_policy"]["limits"] == planned["semantic_policy"]
                        assert job["result"]["status"] == "completed"
                        assert runtime.journal.cognitive_usage()["usage_known_count"] == index + 1
            assert value.provider is None and value._configured_service is None
            assert providers[-1]._http.is_closed
        assert providers[0] is not providers[1]
        assert meter.summary()["reserved_calls"] == meter.summary()["usage_confirmed_calls"] == 2
        assert meter.summary()["usage_unknown_calls"] == 0
        for request in calls:
            body = json.loads(request.content)
            assert body["max_tokens"] == 16384 and body["model"] == planned["model"]
            assert body["thinking"] == {"type": "enabled"}
            assert 239 < request.extensions["timeout"]["read"] <= 240
        assert "private-test-provider-key" not in (tmp_path / "report.json").read_text()
    finally:
        for transport in transports:
            transport.close()


def test_evaluation_rejects_configured_policy_drift_before_any_provider_call(learning, evaluation, tmp_path):
    planned = evaluation.plan()
    settings = Settings(database_url=learning.database.dsn, cognition_base_url=planned["provider_base_url"],
                        cognition_api_key="private-test-key", cognition_model=planned["model"],
                        cognition_thinking=planned["thinking"])
    report = {"arm": "memory", "plan": planned, "principal": asdict(learning.principal)}
    value = evaluation.LifecycleTrial(tmp_path, report, learning.database, None, None, None, None,
                                      cognition_settings=settings,
                                      meter_factory=lambda: httpx.MockTransport(lambda _: pytest.fail("No call authorized")))
    with pytest.raises(RuntimeError, match="differs from the frozen"):
        with value.stage_service():
            pytest.fail("Mismatched policy must not become active")
    assert value._configured_service is None and value.provider is None


@pytest.mark.parametrize("change", [{"cognition_model": "different-model"}, {"cognition_thinking": "disabled"},
                                   {"cognition_base_url": "https://different.test/v1"}])
def test_evaluation_rejects_provider_drift_before_database_or_transport(evaluation, tmp_path, monkeypatch, change):
    planned = evaluation.plan()
    settings = Settings(**{"database_url": "unused", "cognition_base_url": planned["provider_base_url"],
                           "cognition_api_key": "private-test-key", "cognition_model": planned["model"],
                           "cognition_thinking": planned["thinking"], **change})
    report = {"arm": "memory", "plan": planned, "principal": {"tenant_id": "tenant", "principal_id": "owner"}}
    value = evaluation.LifecycleTrial(tmp_path, report, None, None, None, None, None, cognition_settings=settings)
    monkeypatch.setattr(evaluation, "configured_service", lambda _: pytest.fail("No service construction authorized"))
    with pytest.raises(RuntimeError, match="Configured provider differs"):
        with value.stage_service():
            pytest.fail("Mismatched provider must not become active")
