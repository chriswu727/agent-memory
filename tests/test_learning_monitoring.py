import copy
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.config import Settings
from agent_memory.helpers import Principal
from agent_memory.jobs import JobStore, sanitize_learning_summary, summarize_learning
from agent_memory.semantic import SemanticResult


def job_row(result, *, kind="consolidate", state="completed"):
    instant = datetime(2026, 10, 24, tzinfo=UTC)
    return {
        "id": "job", "brain_id": "brain", "kind": kind, "state": state,
        "attempts": 1, "fence": 1, "lease_expires_at": None, "available_at": instant,
        "model_calls": 2, "input_bytes": 200, "max_model_calls": 2,
        "max_input_bytes": 196608, "last_error": None, "created_at": instant,
        "updated_at": instant, "completed_at": instant, "db_now": instant,
        "result": result, "payload": {"input": "private-input"},
        "lease_token_digest": "private-lease",
    }


@pytest.mark.parametrize("status,outcome,claims,rejected", [
    ("completed", "no_proposals", (), ()),
    ("partial", "no_accepted_proposals", (), ({"reason": "invalid_proposal"},)),
    ("completed", "accepted_proposals", ({"statement": "private-accepted"},), ()),
    ("partial", "accepted_proposals", ({"statement": "private-accepted"},),
     ({"reason": "semantic_support_unconfirmed"},)),
    ("budget_exhausted", "not_evaluated", (), ()),
    ("provider_unavailable", "not_evaluated", (), ()),
    ("verification_unavailable", "not_evaluated", (), ({"reason": "verification_unavailable"},)),
    ("invalid_response", "not_evaluated", (), ()),
    ("no_sources", "not_evaluated", (), ()),
])
def test_job_processing_and_learning_outcomes_remain_distinct(status, outcome, claims, rejected):
    result = asdict(SemanticResult(status, claims=claims, rejected=rejected, learning_outcome=outcome))
    public = JobStore(None)._public(job_row(result))
    summary = public["learning_summary"]
    assert public["state"] == "completed"
    assert summary["processing_status"] == status
    assert summary["learning_outcome"] == outcome
    assert summary["accepted_counts"] == {
        "claims": len(claims), "situation_updates": 0, "episode_actions": 0,
    }
    assert summary["published_counts"] == {"claims": 0, "episode_versions": 0}
    assert "private-" not in json.dumps(public)


def test_published_counts_use_actual_versions_instead_of_accepted_actions_or_claimed_count():
    result = {
        "status": "partial", "learning_outcome": "accepted_proposals",
        "claims": [{"statement": "private-claim"}],
        "situation_updates": [{"value": "private-situation"}],
        "episode_actions": [{"action": "split", "episode_id": "private-episode"}],
        "publication": {
            "published_count": 99,
            "memory_versions": [{"memory_id": "private-memory", "revision": 2}],
            "episode_versions": [{"episode_id": "private-left"}, {"episode_id": "private-right"}],
            "source_event_ids": ["private-source"],
        },
    }
    summary = JobStore(None)._public(job_row(result))["learning_summary"]
    assert summary["accepted_counts"] == {"claims": 1, "situation_updates": 1, "episode_actions": 1}
    assert summary["published_counts"] == {"claims": 1, "episode_versions": 2}
    assert "private-" not in json.dumps(summary)
    result["publication"] = {}
    oriented = JobStore(None)._public(job_row(result, kind="orient"))["learning_summary"]
    assert oriented["accepted_counts"]["situation_updates"] == 1
    assert oriented["published_counts"] == {"claims": 0, "episode_versions": 0}


def test_snapshot_before_processing_is_not_reported_as_successful_learning():
    snapshot = {"sources": [{"content": "private-source"}], "sampling": {"source_count": 1}}
    public = JobStore(None)._public(job_row(snapshot, state="running"))
    assert public["learning_summary"] == {
        "processing_status": "not_evaluated", "learning_outcome": "not_evaluated",
        "accepted_counts": {"claims": 0, "situation_updates": 0, "episode_actions": 0},
        "published_counts": {"claims": 0, "episode_versions": 0},
        "sampling": {"source_count": 1}, "rejection_reason_counts": {},
    }
    assert "private-" not in json.dumps(public)


@pytest.mark.parametrize("views,expected", [
    (["private-pattern-a", "private-pattern-b"], 2), ([], 0), (None, 0),
    ("private-pattern-id", 0), ({"private-pattern-id": "private-text"}, 0),
])
def test_pattern_publication_exposes_only_an_optional_count(views, expected):
    result = {
        "status": "completed", "learning_outcome": "accepted_proposals",
        "publication": {"pattern_view_ids": views, "pattern_views": 999, "text": "private-summary"},
    }
    public = JobStore(None)._public(job_row(result))
    assert public["learning_summary"]["published_counts"] == {
        "claims": 0, "episode_versions": 0, "pattern_views": expected,
    }
    assert "private-" not in json.dumps(public)
    del result["publication"]["pattern_view_ids"]
    summary = summarize_learning(result)
    assert summary["published_counts"] == {"claims": 0, "episode_versions": 0}


@pytest.mark.parametrize("value,expected", [
    (0, 0), (12, 12), (1_000_000, 1_000_000), (1_000_001, 0), (-1, 0), (True, 0),
    (float("inf"), 0), (float("nan"), 0), ("private-count", 0),
    (["private-pattern-id"], 0), ({"text": "private-meaning"}, 0), (None, 0),
])
def test_pattern_publication_count_is_an_allowlisted_bounded_integer(value, expected):
    summary = sanitize_learning_summary({"published_counts": {
        "pattern_views": value, "pattern_view_ids": ["private-id"], "text": "private-summary",
    }})
    assert summary["published_counts"] == {"claims": 0, "episode_versions": 0, "pattern_views": expected}
    assert "private-" not in json.dumps(summary)


@pytest.mark.parametrize("kind,state,result", [
    ("orient", "queued", None),
    ("consolidate", "completed", None),
    ("consolidate", "superseded", None),
    ("extract", "completed", {"status": "completed"}),
    ("rebuild", "completed", {"status": "completed"}),
    ("consolidate", "failed", "private-malformed-result"),
])
def test_absent_cleared_or_unrelated_results_have_no_learning_summary(kind, state, result):
    row = job_row(result, kind=kind, state=state)
    row["learning_summary"] = {"text": "private-stale-summary"}
    public = JobStore(None)._public(row)
    assert "learning_summary" not in public
    assert "private-" not in json.dumps(public)


def test_sampling_and_rejections_are_bounded_fixed_metadata_without_proposal_ids():
    result = {
        "status": "partial", "learning_outcome": "no_accepted_proposals",
        "sampling": {
            "strategy": "private-strategy", "through_sequence": 200, "indexed_through_sequence": 199,
            "selected_min_sequence": 2, "selected_max_sequence": 200, "source_count": 32,
            "assertion_count": 8, "episode_count": 2, "candidate_count": 12, "omitted_packages": 1,
            "search_exhaustive": False, "publication_restricted": True,
            "limits": {"sources": 32, "assertions": 32, "episodes": 16, "private-limit": 1},
            "limitations": ["source_budget", "index_lag", "private-limitation", "source_budget", {}],
            "sources": [{"text": "private-source"}], "source_event_ids": ["private-id"],
        },
        "rejected": [
            {"proposal_id": "private-proposal", "reason": "incomplete_history_package"},
            {"proposal_id": "private-other", "reason": "incomplete_history_package"},
            {"proposal_id": "private-unsupported", "reason": "semantic_support_unconfirmed"},
            {"reason": "private-exception"}, {"reason": {}}, "private-error",
        ],
        "exception": "private-exception",
    }
    original = copy.deepcopy(result)
    summary = summarize_learning(result)
    assert summary["sampling"] == {
        "through_sequence": 200, "indexed_through_sequence": 199,
        "selected_min_sequence": 2, "selected_max_sequence": 200,
        "source_count": 32, "assertion_count": 8, "episode_count": 2,
        "candidate_count": 12, "omitted_packages": 1,
        "search_exhaustive": False, "publication_restricted": True,
        "limits": {"sources": 32, "assertions": 32, "episodes": 16},
        "limitations": ["index_lag", "source_budget"],
    }
    assert summary["rejection_reason_counts"] == {
        "incomplete_history_package": 2, "semantic_support_unconfirmed": 1,
    }
    assert result == original
    assert sanitize_learning_summary(summary) == summary
    assert "private-" not in json.dumps(summary)


def test_monitoring_rejects_nested_text_invalid_types_and_unbounded_numbers():
    summary = sanitize_learning_summary({
        "processing_status": {"text": "private-status"}, "learning_outcome": "private-outcome",
        "accepted_counts": {"claims": True, "situation_updates": -1, "episode_actions": "private-count"},
        "published_counts": {"claims": 10**30, "episode_versions": float("inf")},
        "sampling": {
            "source_count": True, "candidate_count": -2, "episode_count": float("nan"),
            "through_sequence": 2**63, "selected_min_sequence": None,
            "publication_restricted": "private-boolean", "search_exhaustive": 1,
            "limits": {"sources": {"text": "private-source"}},
            "limitations": [{"text": "private-limit"}, "private-limit"],
        },
        "rejection_reason_counts": {"invalid_proposal": False, "private-reason": 2},
        "sources": ["private-source"],
    })
    assert summary == {
        "processing_status": "not_evaluated", "learning_outcome": "not_evaluated",
        "accepted_counts": {"claims": 0, "situation_updates": 0, "episode_actions": 0},
        "published_counts": {"claims": 0, "episode_versions": 0},
        "sampling": {"limits": {}, "limitations": []}, "rejection_reason_counts": {},
    }
    assert sanitize_learning_summary(None) is None
    assert sanitize_learning_summary(["private-summary"]) is None


@pytest.mark.parametrize("kind,has_summary", [("orient", True), ("consolidate", True), ("extract", False)])
def test_api_refilters_injected_job_summaries_on_list_and_detail(kind, has_summary):
    principal = Principal("tenant", "owner")
    summary = {
        "processing_status": "partial", "learning_outcome": "accepted_proposals",
        "accepted_counts": {"claims": 1, "situation_updates": 2, "episode_actions": 0,
                            "private-claim": "private-statement"},
        "published_counts": {"claims": 1, "episode_versions": 0, "pattern_views": 2,
                             "ids": ["private-id"], "pattern_view_ids": ["private-pattern"]},
        "sampling": {"source_count": 3, "search_exhaustive": False,
                     "limitations": ["index_lag", "private-limit"], "query": "private-query"},
        "rejection_reason_counts": {"invalid_proposal": 1, "private-exception": 1},
        "sources": [{"content": "private-source"}], "statement": "private-statement",
    }
    record = {"id": "job", "kind": kind, "state": "completed", "last_error": "private-error",
              "learning_summary": summary, "result": {"text": "private-result"},
              "payload": {"text": "private-input"}, "lease_token": "private-token"}
    jobs = SimpleNamespace(get=lambda *_: record, list=lambda *_, **__: [record])
    settings = Settings(database_url="unused", api_keys={hashlib.sha256(b"key").hexdigest(): principal})
    with TestClient(create_app(settings, service=SimpleNamespace(), jobs=jobs)) as client:
        for path in ("/jobs/job", "/jobs"):
            response = client.get("/v1/brains/brain/runtime" + path,
                                  headers={"Authorization": "Bearer key"})
            assert response.status_code == 200
            assert "private-" not in response.text
            body = response.json()
            public = body["jobs"][0] if path == "/jobs" else body
            assert public["last_error"] == "internal_error"
            assert ("learning_summary" in public) == has_summary
            if has_summary:
                assert public["learning_summary"] == sanitize_learning_summary(summary)
