import json

import httpx
import pytest
from test_learning import learning as learning
from test_learning_recovery import operation_rows, stopped_window
from test_learning_windows import drain

from agent_memory.runtime_client import RuntimeClient
from agent_memory.semantic import SemanticProcessor

pytestmark = pytest.mark.integration


def test_http_recovery_closes_an_old_gap_after_newer_learning_succeeds(learning):
    parent, _ = stopped_window(learning)
    old_operations = operation_rows(learning)
    runtime = RuntimeClient(learning.memory)
    before = runtime.learning_progress()
    assert before["blocked_count"] == 1
    assert before["blocked_examples"][0]["job_id"] == parent["id"]
    assert learning.calls == ["proposal"]

    learning.service.semantic_processor = SemanticProcessor(learning.propose, learning.verify)
    learning.observe("A later input must not hide the unfinished earlier window.")
    drain(learning)
    status = runtime.status()
    assert status["indexed_sequence"] == status["durable_sequence"]
    assert status["learning_progress"]["blocked_count"] == 1
    calls_before_retry = list(learning.calls)
    arguments = {"request_id": "http-recovery", "expected_fence": parent["fence"]}
    recovery = runtime.retry_learning(parent["id"], **arguments)
    assert recovery["state"] == "queued"
    assert recovery["max_model_calls"] == 2
    assert recovery["max_input_bytes"] == 196608
    assert learning.calls == calls_before_retry
    assert runtime.retry_learning(parent["id"], **arguments)["id"] == recovery["id"]
    assert runtime.learning_progress()["blocked_count"] == 0
    assert runtime.learning_progress()["open_window_count"] == 1

    with pytest.raises(httpx.HTTPStatusError) as caught:
        runtime.retry_learning(parent["id"], request_id="different-request", expected_fence=parent["fence"])
    assert caught.value.response.status_code == 409
    drain(learning)
    after = runtime.learning_progress()
    assert after["open_window_count"] == 0
    assert after["blocked_count"] == 0
    completed = runtime.job(recovery["id"])
    assert completed["state"] == "completed"
    assert completed["learning_summary"]["learning_window"]["processing_succeeded"] is True
    assert runtime.retry_learning(parent["id"], **arguments)["state"] == "completed"
    assert learning.job(parent["id"])["model_calls"] == parent["model_calls"]
    assert [row for row in operation_rows(learning) if row["job_id"] == parent["id"]] == old_operations
    assert len(learning.calls) == len(calls_before_retry) + 2
    assert "A later input" not in json.dumps(after)


def test_http_recovery_requires_configuration_without_hiding_the_gap(learning):
    parent, _ = stopped_window(learning)
    runtime = RuntimeClient(learning.memory)
    learning.service.semantic_processor = None
    before = runtime.learning_progress()
    assert before["provider_state"] == "not_configured"
    assert before["blocked_count"] == 1
    with pytest.raises(httpx.HTTPStatusError) as caught:
        runtime.retry_learning(parent["id"], request_id="wait-for-configuration", expected_fence=parent["fence"])
    assert caught.value.response.status_code == 503
    assert "recovery_job_id" not in learning.job(parent["id"])["payload"]
    assert learning.calls == ["proposal"]
