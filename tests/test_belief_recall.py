import copy
import json

import pytest
from test_belief_store import linked
from test_cognition import STAMP, event, prepare_payload, request, scope, state, working
from test_consolidation import memory as memory
from test_contextual_recall import CONDITION, UNBOUNDED, observation
from test_recall_control import decision, run
from test_recall_control import packet as control_packet

from agent_memory import cognition
from agent_memory.belief_resolution import _cyclic_nodes, frozen_relations, version
from agent_memory.belief_store import (
    VERIFICATION,
    _relation_id,
    assertion_fingerprint,
    relation_ref,
)
from agent_memory.contracts import validate
from agent_memory.helpers import DomainError
from agent_memory.ontology import CORE_REGISTRY
from agent_memory.recall_control import RecallPolicy


def ref(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def relation(source, targets, *, kind="supersedes", roots=(), **changes):
    endpoints = sorted([source, *targets], key=version)
    result = {
        "revision": 1, "scope": copy.deepcopy(scope()), "kind": kind,
        "source_memory_version": ref(source), "target_memory_versions": sorted([ref(value) for value in targets], key=version),
        "evidence_refs": copy.deepcopy(source["evidence_refs"]), "conditions": {"op": "always"},
        "valid_during": copy.deepcopy(UNBOUNDED), "world": "real_world",
        "condition_registry_fingerprint": CORE_REGISTRY.fingerprint,
        "verification": copy.deepcopy(VERIFICATION),
        "root_event_ids": sorted(set(roots) | {e["event_id"] for value in endpoints for e in value["evidence_refs"]}),
        "endpoint_snapshots": [{"memory_version": ref(value), "content_sha256": assertion_fingerprint(value)}
                               for value in endpoints], **changes,
    }
    result["relation_id"] = _relation_id(result)
    return result


def scenario(*, kind="supersedes", **changes):
    sources = [event("routine previously offered direct advice.", 1), event("routine now offers listening.", 2)]
    memories = [cognition.extract_event(value)[0] for value in sources]
    current = state(2, candidate_total=2, belief_relations=[relation(memories[1], memories[:1], kind=kind, **changes)],
                    belief_memories=memories, working_event_metadata={value["event_id"]: value for value in sources})
    return sources, memories, current


def recall(sources, memories, current, **changes):
    return cognition.recall(scope(), request("routine", **changes), memories, sources, current)


def states(result):
    return {version(item["memory"]): item.get("belief_resolution", {}).get("state", "unchanged")
            for item in result["items"]}


def blocks(packet):
    return [(block, json.loads(block["text"])) for block in packet["blocks"]
            if block["section"] in {"relevant_memory", "conditional_memory"}]


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: STAMP)


@pytest.mark.parametrize("kind,expected", [("supersedes", "superseded"), ("exception", "exception_applies"),
                                          ("equivalent", "unchanged"), ("contradicts", "conflicted")])
def test_current_resolution_preserves_original_versions_and_evidence(kind, expected):
    sources, memories, current = scenario(kind=kind)
    before = copy.deepcopy((memories, current))
    result = recall(sources, memories, current)
    assert states(result)[version(memories[0])] == expected
    assert states(result)[version(memories[1])] == ("conflicted" if kind == "contradicts" else "unchanged")
    assert all(item["belief_resolution"]["complete"] for item in result["items"])
    assert all(item["memory"] == before[0][memories.index(item["memory"])] for item in result["items"])
    assert (memories, current) == before
    validate("RecallResult", result)


def test_unknown_condition_keeps_old_rule_uncertain_and_expiry_restores_old_rule():
    sources, memories, current = scenario(kind="exception", conditions=copy.deepcopy(CONDITION))
    assert states(recall(sources, memories, current))[version(memories[0])] == "uncertain"
    current["context_observations"] = [observation(sources[1])]
    assert states(recall(sources, memories, current))[version(memories[0])] == "exception_applies"
    current["applicability_at"] = "2026-10-23T12:00:00Z"
    current["belief_relations"] = [relation(memories[1], memories[:1], kind="exception", valid_during={
        "kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "instant", "at": STAMP}})]
    assert set(states(recall(sources, memories, current)).values()) == {"unchanged"}


@pytest.mark.parametrize("change", ["disputed", "unknown", "contested"])
def test_unsettled_new_claim_cannot_unambiguously_override_old(change):
    sources, memories, current = scenario()
    if change == "contested":
        memories[1]["verification"] = {**VERIFICATION, "state": "contested"}
    else:
        memories[1]["epistemic_status"] = change
    current["belief_relations"] = [relation(memories[1], memories[:1])]
    result = recall(sources, memories, current)
    assert states(result)[version(memories[0])] in {"uncertain", "conflicted"}


def test_false_old_target_is_historical_and_not_a_current_conflict():
    sources, memories, current = scenario(kind="contradicts")
    memories[0].update(conditions={"op": "not", "arg": copy.deepcopy(CONDITION)}, valid_during=copy.deepcopy(UNBOUNDED))
    current["context_observations"] = [observation(sources[1])]
    current["belief_relations"] = [relation(memories[1], memories[:1], kind="contradicts")]
    result = recall(sources, memories, current)
    assert states(result)[version(memories[0])] == "unchanged"
    assert states(result)[version(memories[1])] == "unchanged"


def test_source_condition_observation_roles_survive_relation_assessment():
    sources, memories, current = scenario()
    memories[1].update(conditions=copy.deepcopy(CONDITION), valid_during=copy.deepcopy(UNBOUNDED))
    current["belief_relations"] = [relation(memories[1], memories[:1])]
    current["context_observations"] = [observation(sources[1])]
    result = recall(sources, memories, current)
    applicability = result["items"][0]["belief_resolution"]["relations"][0]["applicability"]
    assert applicability["supporting_observation_ids"]
    assert applicability["opposing_observation_ids"] == []
    current["context_observations"].append(observation(sources[0], polarity="negative"))
    result = recall(sources, memories, current)
    applicability = result["items"][0]["belief_resolution"]["relations"][0]["applicability"]
    assert applicability["supporting_observation_ids"] and applicability["opposing_observation_ids"]
    assert set(applicability["supporting_observation_ids"]).isdisjoint(applicability["opposing_observation_ids"])
    assert states(result)[version(memories[0])] == "conflicted"


def test_chain_and_cycles_do_not_resurrect_an_intermediate_old_rule():
    sources, memories, current = scenario()
    sources.append(event("routine changed again to silence.", 3))
    memories.append(cognition.extract_event(sources[-1])[0])
    current.update(durable_sequence=3, indexed_sequence=3)
    current["working_event_metadata"]["event-3"] = sources[-1]
    current["belief_relations"].append(relation(memories[2], [memories[1]]))
    result = recall(sources, memories, current)
    assert [states(result)[version(value)] for value in memories] == ["superseded", "superseded", "unchanged"]
    current["belief_relations"].append(relation(memories[0], [memories[2]]))
    assert set(states(recall(sources, memories, current)).values()) == {"conflicted"}
    sources.append(event("routine downstream conclusion", 4))
    memories.append(cognition.extract_event(sources[-1])[0])
    current.update(durable_sequence=4, indexed_sequence=4)
    current["working_event_metadata"]["event-4"] = sources[-1]
    current["belief_relations"].append(relation(memories[0], [memories[-1]]))
    assert states(recall(sources, memories, current))[version(memories[-1])] == "uncertain"


def test_dense_maximum_relation_dag_visits_edges_once_per_start_instead_of_enumerating_paths():
    traversed = []

    class Neighbors(list):
        def __iter__(self):
            traversed.extend(self[:])
            return super().__iter__()

    edges = {source: Neighbors(range(source + 1, source + 9)) for source in range(32)}
    assert _cyclic_nodes(edges) == set()
    assert len(traversed) <= 32 * 32 * 8
    edges[31][0] = 0
    assert _cyclic_nodes(edges) == set(range(32))


def test_cycle_conflict_does_not_label_noncyclic_downstream_target_as_in_cycle():
    assert _cyclic_nodes({"a": {"b"}, "b": {"a", "c"}, "c": {"d"}}) == {"a", "b"}


@pytest.mark.parametrize("kind", ["supersedes", "exception", "contradicts"])
def test_derived_understanding_inherits_uncertainty_from_necessary_parent_relation(kind):
    sources, memories, current = scenario(kind=kind)
    sources.append(event("routine derived understanding", 3))
    child = cognition.extract_event(sources[-1])[0]
    child["derivation"]["source_memory_versions"] = [ref(memories[0])]
    child["representation"] = "schema"
    memories.append(child)
    current.update(durable_sequence=3, indexed_sequence=3)
    current["working_event_metadata"]["event-3"] = sources[-1]
    result = recall(sources, memories, current)
    assert states(result)[version(child)] == "uncertain"
    child_item = next(item for item in result["items"] if version(item["memory"]) == version(child))
    assert frozen_relations([child_item]) == [relation_ref(current["belief_relations"][0])]
    assert child_item["memory"] == child
    packet = cognition.prepare(scope(), prepare_payload(), working(), result, current)
    assert next(block["section"] for block, content in blocks(packet)
                if content["statement"] == child["statement"]) == "conditional_memory"
    limited = cognition.prepare(scope(), prepare_payload(tokens=packet["usage"]["context_tokens"] - 1), working(), result, current)
    assert blocks(limited) == []


def test_parent_package_incomplete_or_expired_controls_child_without_changing_history():
    sources, memories, current = scenario(kind="exception")
    sources.append(event("routine derived understanding", 3))
    child = cognition.extract_event(sources[-1])[0]
    child["disclosure_basis"]["support_paths"][0]["source_memory_versions"] = [ref(memories[0])]
    memories.append(child)
    current.update(durable_sequence=3, indexed_sequence=3, belief_incomplete_versions=[ref(memories[0])])
    current["working_event_metadata"]["event-3"] = sources[-1]
    result = recall(sources, memories, current)
    assert states(result)[version(child)] == "uncertain"
    assert not next(item for item in result["items"] if version(item["memory"]) == version(child))["belief_resolution"]["complete"]
    current["belief_incomplete_versions"] = []
    current["belief_relations"] = [relation(memories[1], memories[:1], kind="exception", valid_during={
        "kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "instant", "at": STAMP}})]
    result = recall(sources, memories, current)
    assert states(result)[version(child)] == "unchanged"


@pytest.mark.parametrize("mode", ["cutoff", "cue_descendant"])
def test_future_relation_cannot_leak_into_historical_recall(mode):
    sources, memories, current = scenario()
    extra = event("unseen future interpretation", 3)
    sources.append(extra)
    current.update(durable_sequence=3, indexed_sequence=3)
    current["belief_relations"] = [relation(memories[1], memories[:1], roots=[extra["event_id"]])]
    changes = {"evidence_mode": "prior_record_check", "evidence_cutoff": {"mode": "through_sequence", "through_sequence": 2}}
    if mode == "cue_descendant":
        extra["observed"]["parent_source_event_keys"] = [sources[0]["observed"]["source_event_key"]]
        changes.update(query_event_ids=[sources[0]["event_id"]],
                       evidence_cutoff={"mode": "through_sequence", "through_sequence": 3})
    result = recall(sources, memories, current, **changes)
    assert all("belief_resolution" not in item for item in result["items"])
    assert "unseen future interpretation" not in json.dumps(result)
    assert current["belief_relations"][0]["relation_id"] not in json.dumps(result)


@pytest.mark.parametrize("mode", ["missing_source", "hash_change", "incomplete"])
def test_invalid_or_incomplete_relationship_cannot_silently_restore_current_rule(mode):
    sources, memories, current = scenario()
    if mode == "missing_source":
        current["belief_relations"] = [relation(memories[1], memories[:1], roots=["unavailable"])]
    elif mode == "hash_change":
        memories[1]["statement"] += " changed without revision"
    else:
        current.update(belief_relations=[], belief_incomplete_versions=[ref(value) for value in memories])
    result = recall(sources, memories, current)
    assert set(states(result).values()) == {"uncertain"}
    assert all(not item["belief_resolution"]["complete"] for item in result["items"])


def test_recall_budget_recomputes_completeness_and_prepare_budget_is_atomic():
    sources, memories, current = scenario()
    full = recall(sources, memories, current)
    limited_request = request("routine")
    limited_request["budget"]["max_context_tokens"] = full["usage"]["context_tokens"] - 1
    limited = cognition.recall(scope(), limited_request, memories, sources, current)
    assert len(limited["items"]) == 1
    assert limited["items"][0]["belief_resolution"]["state"] == "uncertain"
    packet = cognition.prepare(scope(), prepare_payload(), working(), full, current)
    assert len(blocks(packet)) == 2
    trimmed = cognition.prepare(scope(), prepare_payload(tokens=packet["usage"]["context_tokens"] - 1), working(), full, current)
    assert blocks(trimmed) == []
    assert trimmed["recall_traces"] == []
    assert trimmed["usage"]["context_tokens"] == cognition.estimate_tokens(cognition.render_context(trimmed))


def test_prepare_rechecks_expiry_and_binds_uncited_context_roots():
    sources, memories, current = scenario(kind="exception", valid_during={
        "kind": "interval", "start": {"kind": "unbounded"},
        "end": {"kind": "instant", "at": "2026-10-23T00:00:00Z"}})
    extra = event("uncited observed context", 3)
    sources.append(extra)
    current.update(durable_sequence=3, indexed_sequence=3)
    current["working_event_metadata"][extra["event_id"]] = extra
    current["belief_relations"][0]["root_event_ids"].append(extra["event_id"])
    current["belief_relations"][0]["relation_id"] = _relation_id(current["belief_relations"][0])
    recalled = recall(sources, memories, current)
    assert states(recalled)[version(memories[0])] == "exception_applies"
    current["applicability_at"] = "2026-10-24T00:00:00Z"
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
    assert all(content["belief_resolution"]["state"] == "unchanged" for _, content in blocks(packet))
    assert any(ref["event_id"] == extra["event_id"] and ref["relationship"] == "context_only"
               for block, _ in blocks(packet) for ref in block["evidence_refs"])


def test_controller_freezes_only_shown_relation_versions_and_all_observed_roots():
    sources, memories, current = scenario()
    recalled = recall(sources, memories, current)
    initial = control_packet(*memories)
    initial["recall_results"][0]["items"] = recalled["items"]
    seen, reserved = [], []

    def reason(request):
        shown = json.loads(request.messages[1]["content"])["candidates"]
        assert all("belief_resolution" in item for item in shown)
        seen.extend(shown)
        return decision(selected=[memories[0]["memory_id"]])

    plan = run(reason, initial=initial, reserve=lambda **values: reserved.append(values))
    assert plan["belief_relation_refs"] == [relation_ref(current["belief_relations"][0])]
    assert {ref["event_id"] for ref in reserved[0]["source_refs"]} == {"event-1", "event-2", "recent"}
    assert plan["observed_event_refs"] == reserved[0]["source_refs"]


def test_controller_trimming_an_endpoint_cannot_present_the_old_rule_as_settled():
    sources, memories, current = scenario()
    memories[1]["statement"] *= 100
    current["belief_relations"] = [relation(memories[1], memories[:1])]
    recalled = recall(sources, memories, current)
    initial = control_packet(*memories)
    initial["recall_results"][0]["items"] = recalled["items"]

    def reason(request):
        offered = json.loads(request.messages[1]["content"])["candidates"]
        assert [candidate["memory_id"] for candidate in offered] == [memories[0]["memory_id"]]
        assert offered[0]["belief_resolution"]["complete"] is False
        assert offered[0]["belief_resolution"]["state"] == "uncertain"
        return decision(selected=[memories[0]["memory_id"]], outcome="ambiguous")

    plan = run(reason, initial=initial, policy=RecallPolicy(max_request_bytes=10000))
    assert plan["belief_relation_refs"] == [relation_ref(current["belief_relations"][0])]
    assert plan["outcome"] == "ambiguous"


def test_equivalent_and_abstracted_views_do_not_transfer_instance_details_or_confidence():
    sources, memories, current = scenario(kind="equivalent")
    sources.append(event("routine general feedback schema", 3))
    memories.append(cognition.extract_event(sources[-1])[0])
    memories[2]["representation"] = "schema"
    current.update(durable_sequence=3, indexed_sequence=3)
    current["working_event_metadata"]["event-3"] = sources[-1]
    current["belief_relations"].append(relation(memories[2], memories[:2], kind="abstracts"))
    result = recall(sources, memories, current)
    assert set(states(result).values()) == {"unchanged"}
    assert {item["memory"]["statement"] for item in result["items"]} == {memory["statement"] for memory in memories}
    assert all(item["memory"]["epistemic_status"] == "supported_report" for item in result["items"])
    assert all(item["memory"]["evidence_refs"] == next(value for value in memories
               if version(value) == version(item["memory"]))["evidence_refs"] for item in result["items"])


@pytest.mark.integration
def test_service_routes_zero_lexical_endpoint_and_plan_replay_requires_frozen_relationship(memory, monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: "2026-10-24T12:00:00Z")
    sources, old, new, _, _, _, _ = linked(memory, hidden=True)
    current = memory.observe("请继续聊具体建议。")
    query = request("具体建议", evidence_mode="prior_record_check",
                    evidence_cutoff={"mode": "before_event", "event_id": current["event_id"]},
                    query_event_ids=[current["event_id"]], cue_event_ids=[current["event_id"]])
    assert cognition._lexical_score(query["query"], new["statement"]) == 0
    result = memory.action("recall", query)
    assert version(new) in states(result)
    assert states(result)[version(old)] == "exception_applies"
    plan = {
        "version": "evidence-recall-v1", "memory_versions": [ref(item["memory"]) for item in result["items"]],
        "belief_relation_refs": frozen_relations(result["items"]), "recent_event_refs": [],
        "observed_event_refs": [{"event_id": value["event_id"], "revision": value["revision"]} for value in sources],
        "outcome": "sufficient", "model_calls": 0, "search_calls": 0, "input_bytes": 0,
        "elapsed_ms": 0, "trace": [], "coverage_limitations": [],
    }
    payload = prepare_payload()
    payload.update(latest_input_event_ids=[current["event_id"]], recall_plan=plan,
                   expected_dependency_generation=result["retrieval_snapshot"]["dependency_generation"],
                   evidence_mode=query["evidence_mode"], evidence_cutoff=query["evidence_cutoff"])
    packet = memory.action("prepare", payload)
    assert any(content.get("belief_resolution", {}).get("state") == "exception_applies" for _, content in blocks(packet))
    original = copy.deepcopy(plan)
    plan["observed_event_refs"] = plan["observed_event_refs"][:2]
    with memory.database.transaction(memory.principal) as conn:
        brain = memory.service._brain(conn, memory.principal, memory.brain_id)
        with pytest.raises(DomainError) as failure:
            memory.service._prepare(conn, memory.principal, brain, {
                "target": {"conversation_ref": "test", "branch_ref": "main"}, "payload": payload})
        assert failure.value.code == "invalid_evidence"
    plan.update(original)
    plan["belief_relation_refs"][0]["content_sha256"] = "0" * 64
    with memory.database.transaction(memory.principal) as conn:
        brain = memory.service._brain(conn, memory.principal, memory.brain_id)
        with pytest.raises(DomainError) as failure:
            memory.service._prepare(conn, memory.principal, brain, {
                "target": {"conversation_ref": "test", "branch_ref": "main"}, "payload": payload})
        assert failure.value.code == "context_stale"
    del plan["belief_relation_refs"]
    with memory.database.transaction(memory.principal) as conn:
        brain = memory.service._brain(conn, memory.principal, memory.brain_id)
        with pytest.raises(DomainError, match="Belief relations changed") as failure:
            memory.service._prepare(conn, memory.principal, brain, {
                "target": {"conversation_ref": "test", "branch_ref": "main"}, "payload": payload})
        assert failure.value.code == "context_stale"


@pytest.mark.integration
def test_service_retrieves_parent_exception_for_a_differently_worded_derived_claim(memory, monkeypatch):
    from test_semantic import claim, proposals
    from test_semantic import run as semantic_run

    monkeypatch.setattr(cognition, "now", lambda: "2026-10-24T12:00:00Z")
    sources, old, new, _, _, _, _ = linked(memory)
    original = memory.observe("Family dinner coordination follows my prior interaction preference.")
    batch = memory.batch(*sources, original, existing_assertions=(old,))
    proposal = claim(original, statement="Family dinner coordination follows the prior preference.",
                     source_memory_versions=[ref(old)], representation="schema",
                     conditions={"op": "always"}, valid_during=copy.deepcopy(UNBOUNDED))
    proposal["evidence_refs"].extend(copy.deepcopy(old["evidence_refs"]))
    outcome = semantic_run(batch, proposals(proposal))
    assert outcome.claims, outcome.rejected
    published = memory.publish(batch, outcome)
    child = published["memory_versions"][0]
    assert cognition._lexical_score("Family dinner", old["statement"]) == 0
    assert cognition._lexical_score("Family dinner", new["statement"]) == 0
    result = memory.action("recall", request("Family dinner"))
    item = next(value for value in result["items"] if ref(value["memory"]) == child)
    assert item["belief_resolution"]["state"] == "uncertain"
    assert item["belief_resolution"]["complete"] is True
    assert {version(old), version(new)} <= states(result).keys()
    assert len(frozen_relations([item])) == 1
