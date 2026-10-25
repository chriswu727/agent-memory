import copy
from dataclasses import replace

import pytest
from test_continuous_learning_acceptance import FIRST, SECOND, GroundedCognition, drain, turn
from test_learning import learning as learning
from test_learning_packages import history_graph, integrate, manifest_for, versions
from test_learning_windows import jobs

from agent_memory.history_attention import POLICY, admit, attention_summary
from agent_memory.history_semantics import validate_manifest
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import ENCODING_PHASE, HISTORY_PHASE, phase
from agent_memory.learning_integration import _refresh_manifest
from agent_memory.semantic import SemanticPolicy, SemanticProcessor
from agent_memory.service import MemoryService

pytestmark = pytest.mark.integration


def test_admitted_history_keeps_complete_parent_exception_and_source_closure(learning):
    current, sources, memories, belief = history_graph(learning)
    frozen = admit(manifest_for(current, memories[-1]), [memories[-1]], [current])
    package, selected, remaining = integrate(learning, frozen)
    assert selected == frozen and not remaining["objectives"]
    assert versions(package) == {(memory["memory_id"], memory["revision"]) for memory in memories}
    assert {source["event_id"] for source in package.sources} == {source["event_id"] for source in (*sources, current)}
    assert {relation["relation_id"] for relation in package.belief_relations} == {belief["relation_id"]}
    assert attention_summary(selected) == attention_summary(remaining)
    assert learning.calls == []


def test_incomplete_admitted_package_remains_exact_debt_and_is_never_rediscovered(learning, monkeypatch):
    current, _, memories, _ = history_graph(learning)
    frozen = admit(manifest_for(current, memories[-1]), [memories[-1]], [current])
    package, selected, remaining = integrate(learning, frozen, policy=replace(SemanticPolicy(), max_sources=3))
    assert package.sources == (current,) and not selected["objectives"]
    assert remaining == frozen
    assert remaining["attention"]["retrieval_truncated"] is True
    assert remaining["unresolved_discovery"] is False
    validate_manifest(selected)
    validate_manifest(remaining)

    def unexpected_discovery(*args, **kwargs):
        pytest.fail("A fixed attention authorization must not rediscover omitted candidates.")

    monkeypatch.setattr("agent_memory.learning_integration.discover_manifest", unexpected_discovery)
    assert _refresh_manifest(None, None, None, None, None, remaining, SemanticPolicy()) == frozen
    exhausted = {**copy.deepcopy(remaining), "objectives": []}
    assert _refresh_manifest(None, None, None, None, None, exhausted, SemanticPolicy()) == exhausted
    assert learning.calls == []


def test_latest_delivered_lifecycle_bounds_attention_without_legacy_fixture_or_selection_override(learning):
    provider = GroundedCognition()
    policy = SemanticPolicy(max_assertions=4, max_request_bytes=60000)
    learning.service.semantic_processor = SemanticProcessor(provider.propose, provider.verify, policy)
    turn(learning.memory, FIRST, "attention-first")
    drain(learning)
    turn(learning.memory, SECOND, "attention-second")
    drain(learning)
    rows = jobs(learning)
    encoding = [row for row in rows if phase(row["payload"]) == ENCODING_PHASE]
    history = [row for row in rows if phase(row["payload"]) == HISTORY_PHASE]
    assert encoding and history
    assert all(row["payload"]["history_attention_policy"] == POLICY for row in encoding)
    audited = []
    admitted_by_input = {}
    for row in history:
        assert row["payload"]["history_attention_policy"] == POLICY
        value = row["payload"]["history_obligation_manifest"]
        validate_manifest(value)
        summary = attention_summary(value)
        assert summary["admitted_count"] <= 3
        assert summary["search_exhaustive"] is False
        assert value["unresolved_discovery"] is False
        admitted = {item["objective_id"] for item in value["attention"]["candidates"]
                    if item["basis"] != "not_selected"}
        key = row["payload"]["encoding_input_fingerprint"]
        assert admitted_by_input.setdefault(key, admitted) == admitted
        assert {item["objective_id"] for item in value["objectives"]} <= admitted
        assert row["state"] in {"completed", "deferred"}, (row["state"], row["last_error"], row["result"])
        if row["state"] == "deferred":
            assert value["objectives"] and row["model_calls"] == 0
        public = JobStore(learning.database).get(learning.principal, learning.brain, row["id"])["learning_summary"]
        assert public["history_attention"] == summary
        audited.append(summary)
    assert any(value["not_selected_count"] > 0 and value["retrieval_truncated"] for value in audited)
    before = len(provider.requests)
    restarted = MemoryService(learning.database, semantic_processor=SemanticProcessor(
        provider.propose, provider.verify, policy,
    ))
    for _ in range(3):
        assert LearningEngine(restarted).work_once(learning.principal, learning.brain) == 0
    assert len(provider.requests) == before
    assert sum(row["model_calls"] for row in rows if phase(row["payload"]) is not None) <= 4 * len(encoding)
    progress = JobStore(learning.database).learning_progress(learning.principal, learning.brain, configured=True)
    assert progress["learning_phases"]["counts_complete"] is True
    assert bool(progress["learning_phases"]["history_integration_open_count"]) == any(
        row["state"] == "deferred" for row in history)
