import json

import httpx
import pytest
from psycopg.types.json import Jsonb
from test_learning import learning as learning
from test_semantic_call_usage import Provider

from membrain.helpers import DomainError, Principal, uid
from membrain.jobs import JobStore
from membrain.runtime_client import RuntimeClient
from membrain.semantic import SemanticProcessor
from membrain.storage import Database

pytestmark = pytest.mark.integration


def test_usage_survives_reopen_and_keeps_each_phase_outcome(learning):
    def fail(_):
        with learning.database.transaction(learning.principal) as conn:
            proposal = conn.execute(
                "SELECT outcome,usage FROM memory_cognitive_operations WHERE tenant_id=%s AND brain_id=%s",
                (learning.principal.tenant_id, learning.brain),
            ).fetchall()
        assert any(row["outcome"] == "completed" and row["usage"]["total_tokens"] == 20 for row in proposal)
        raise TimeoutError("Private response content")

    learning.service.semantic_processor = SemanticProcessor(
        Provider(learning.propose, {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}),
        Provider(fail, None),
    )
    journal, row, token = learning.capture()
    journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    result = RuntimeClient(learning.memory).cognitive_usage()
    assert result["reservation_count"] == 2
    assert result["usage_known_count"] == result["usage_unknown_count"] == 1
    assert result["outcome_counts"] == {"reserved": 0, "completed": 1, "failed": 0, "uncertain": 1}
    assert result["reported_tokens"] == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    assert result["counts_complete"] is False
    assert result["scope"] == "cognition_and_embeddings"
    fresh = JobStore(Database(learning.database.dsn))
    assert fresh.cognitive_usage(learning.principal, learning.brain) == result
    text = json.dumps(result)
    assert "Private" not in text and row["turn_id"] not in text
    with pytest.raises(DomainError):
        fresh.cognitive_usage(Principal(uid(), uid()), learning.brain)


@pytest.mark.parametrize("legacy_usage", [
    None, {}, {"input_tokens": 10, "output_tokens": None},
    {"input_tokens": 10, "output_tokens": 3, "total_tokens": 20},
    {"input_tokens": True, "output_tokens": 3, "total_tokens": 4},
    {"input_tokens": "sensitive-untrusted", "output_tokens": 3, "total_tokens": 4},
    {"input_tokens": 2**63, "output_tokens": 0, "total_tokens": 2**63},
])
def test_missing_or_invalid_legacy_usage_is_not_counted_as_zero(learning, legacy_usage):
    journal, row, token = learning.capture()
    journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_cognitive_operations SET usage=%s WHERE tenant_id=%s AND brain_id=%s",
                     (Jsonb(legacy_usage) if legacy_usage is not None else None,
                      learning.principal.tenant_id, learning.brain))
    result = journal.cognitive_usage()
    assert result["reservation_count"] == result["usage_unknown_count"] == 2
    assert result["usage_known_count"] == 0 and not result["counts_complete"]
    assert result["reported_tokens"]["total_tokens"] == 0
    assert "sensitive-untrusted" not in json.dumps(result)


def test_empty_usage_summary_is_authenticated_and_read_only(learning):
    journal = RuntimeClient(learning.memory)
    result = journal.cognitive_usage()
    assert result["reservation_count"] == 0 and result["counts_complete"]
    assert result["phases"] == []
    assert journal.cognitive_usage() == result


def test_settlement_failure_cannot_automatically_repeat_paid_proposal(learning, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Private database error")

    monkeypatch.setattr(JobStore, "settle_operation", fail)
    journal, row, token = learning.capture()
    with pytest.raises(httpx.HTTPStatusError) as uncertain:
        journal.orient(row["turn_id"], lease_token=token, expected_revision=row["revision"])
    assert uncertain.value.response.status_code == 409
    assert uncertain.value.response.json()["error"]["code"] == "provider_uncertain"
    current = journal.get(row["turn_id"])
    job = learning.job(current["orientation_job_id"])
    assert job["state"] == "cancelled" and job["last_error"] == "provider_uncertain"
    assert job["model_calls"] == 1 and learning.calls == ["proposal"]
    store = JobStore(learning.database)
    assert store.claim(learning.principal, learning.brain, worker_id=uid(), job_id=job["id"]) is None
    with pytest.raises(httpx.HTTPStatusError) as blocked:
        journal.orient(row["turn_id"], lease_token=token, expected_revision=current["revision"])
    assert blocked.value.response.status_code == 409
    assert learning.calls == ["proposal"]
    usage = journal.cognitive_usage()
    assert usage["outcome_counts"]["uncertain"] == 1
