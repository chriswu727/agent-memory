import copy
import json
from collections import Counter
from dataclasses import replace

import pytest
from psycopg.types.json import Jsonb
from test_belief_recall import relation
from test_bounded_learning_semantics import FIXTURE
from test_learning import learning as learning
from test_learning_neighborhood import link_exception, publish, source, versions
from test_semantic import encoded, proposals, reference

from membrain import cognition, learning_packages
from membrain.belief_capabilities import RELATION_CAPABILITIES_INPUT_VERSION
from membrain.belief_store import _insert, _relation_id, assertion_fingerprint
from membrain.consolidation import publish_semantic
from membrain.contracts import validate
from membrain.helpers import DomainError, digest, uid
from membrain.history_semantics import LINK_DERIVED_INPUT_VERSION, MANIFEST_VERSION
from membrain.learning import LearningEngine
from membrain.learning_allocations import HISTORY_PHASE, allocation_metadata
from membrain.learning_integration import _snapshot as integration_snapshot
from membrain.learning_packages import (
    discover_manifest,
    empty_working,
    integration_package,
    local_neighborhood,
)
from membrain.local_semantics import (
    HISTORY_INTEGRATION_MODE,
    LOCAL_OBSERVATION_MODE,
    bounded_verification_context,
)
from membrain.neighborhood import _Selector
from membrain.semantic import (
    EPISODE_FIDELITY_INPUT_VERSION,
    VERIFICATION_SCHEMA,
    SemanticPolicy,
    SemanticProcessor,
    proposal_data,
    proposal_schema,
    request_payload,
)

pytestmark = pytest.mark.integration
TARGET = {"conversation_ref": "chat", "branch_ref": "main"}


def event_refs(events):
    return [{"event_id": item["event_id"], "revision": item["revision"]} for item in events]


def manifest_for(current, *memories):
    inputs = [{**event_refs([current])[0], "content_sha256": current["content_sha256"]}]
    fingerprint = digest(inputs)
    endpoints = [{"memory_id": memory["memory_id"], "revision": memory["revision"],
                  "content_sha256": assertion_fingerprint(memory)}
                 for memory in memories]
    return {"version": MANIFEST_VERSION, "input_fingerprint": fingerprint, "input_refs": inputs,
            "through_sequence": current["sequence"], "unresolved_discovery": True,
            "discovery_limitations": ["candidate_limit"], "objectives": [
                {"objective_id": "comparison-" + digest([MANIFEST_VERSION, fingerprint, endpoint]),
                 "target_memory_version": endpoint} for endpoint in endpoints]}


def integrate(learning, manifest, *, policy=None):
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        return integration_package(conn, learning.service, learning.principal, brain, TARGET, manifest=manifest,
                                   policy=policy or SemanticPolicy(), input_contract_version=EPISODE_FIDELITY_INPUT_VERSION)


def local(learning, events, *, policy=None, through=None):
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        return local_neighborhood(
            conn, learning.service, learning.principal, brain, TARGET,
            through_sequence=max(item["sequence"] for item in events) if through is None else through,
            required_event_refs=event_refs(events), policy=policy or SemanticPolicy(),
            input_contract_version=EPISODE_FIDELITY_INPUT_VERSION,
        )


def package_request(learning, package, *, manifest=None, refs=(), policy=None):
    policy = policy or SemanticPolicy()
    mode = HISTORY_INTEGRATION_MODE if manifest is not None else LOCAL_OBSERVATION_MODE
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        working = empty_working(learning.service._working(conn, learning.principal, brain, TARGET, None))
        assertions = [learning.service._rebind(item, learning.principal, brain) for item in package.assertions]
        data = proposal_data(package.sources, assertions, working, package.episodes, package.sampling,
                             package.belief_relations, proposal_mode=mode, learning_input_refs=refs,
                             scope=working["scope"], input_contract_version=EPISODE_FIDELITY_INPUT_VERSION,
                             integration_manifest=manifest)
    return request_payload("proposal", data, proposal_schema(mode), policy.max_output_tokens,
                           proposal_mode=mode, input_contract_version=EPISODE_FIDELITY_INPUT_VERSION)


def history_graph(learning, *, edge="both"):
    parent_source = source(learning, "The user reports talking before planning.", conversation="earlier")
    exception_source = source(learning, "An urgent deadline is an exception requiring silence.", conversation="earlier")
    derived_source = source(learning, "The reported routine refers to those planning sessions.", conversation="earlier")
    parent = publish(learning, parent_source)
    exception = publish(learning, exception_source)
    derived = publish(learning, derived_source, parents=[parent])
    if edge in {"legacy", "both"}:
        link_exception(learning, parent, exception, event_ids=[exception_source["event_id"]])
    saved_relation = None
    if edge in {"belief", "both"}:
        saved_relation = relation(exception, [parent], kind="exception")
        saved_relation["scope"] = copy.deepcopy(parent["scope"])
        saved_relation["relation_id"] = _relation_id(saved_relation)
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            _insert(conn, learning.service, learning.principal, brain, saved_relation,
                    {(item["memory_id"], item["revision"]): item for item in (parent, exception)})
    current = source(learning, "Does that reported planning routine still describe this case?")
    return current, (parent_source, exception_source, derived_source), (parent, exception, derived), saved_relation


@pytest.mark.parametrize("edge", ["legacy", "belief", "both"])
def test_history_closure_expands_exceptions_attached_only_to_a_derived_memory_parent(learning, edge):
    current, sources, memories, belief = history_graph(learning, edge=edge)
    manifest = manifest_for(current, memories[-1])
    package, selected, deferred = integrate(learning, manifest)
    assert versions(package) == {(item["memory_id"], item["revision"]) for item in memories}
    assert {item["event_id"] for item in package.sources} == {item["event_id"] for item in (*sources, current)}
    assert selected == manifest and deferred["objectives"] == []
    assert {item["relation_id"] for item in package.belief_relations} == ({belief["relation_id"]} if belief else set())
    messages, _ = package_request(learning, package, manifest=selected)
    body = json.loads(messages[-1]["content"])
    assert sources[1]["observed"]["content"]["text"] in messages[-1]["content"]
    assert len(body["existing_assertions"]) == 3 and len(body["sources"]) == 4
    assert body["integration_manifest"]["unresolved_discovery"] is True
    assert learning.calls == []


@pytest.mark.parametrize("edge", ["legacy", "belief", "both"])
def test_shared_history_expands_each_exact_memory_once_without_changing_complete_packages(learning, monkeypatch, edge):
    current, _, memories, _ = history_graph(learning, edge=edge)
    manifest = manifest_for(current, *reversed(memories))
    original_expand = _Selector.assertion_package
    original_complete = learning_packages.complete_history_package
    calls = []

    def expand(selector, root, **kwargs):
        calls.append((root["memory_id"], root["revision"]))
        return original_expand(selector, root, **kwargs)

    def uncached(selector, root, *, expansions=None):
        return original_complete(selector, root)

    monkeypatch.setattr(_Selector, "assertion_package", expand)
    with monkeypatch.context() as patch:
        patch.setattr(learning_packages, "complete_history_package", uncached)
        previous = integrate(learning, manifest)
    previous_calls = Counter(calls)
    calls.clear()
    current_result = integrate(learning, manifest)
    assert current_result == previous
    assert current_result[1] == manifest and not current_result[2]["objectives"]
    assert sum(previous_calls.values()) == 7
    assert Counter(calls) == {key: 1 for key in previous_calls}
    assert len(calls) == 3 and learning.calls == []


def test_deferred_shared_history_does_not_leak_unselected_source_or_dependents(learning):
    current, sources, memories, _ = history_graph(learning)
    parent, exception, derived = memories
    manifest = manifest_for(current, derived, parent, exception)
    package, selected, deferred = integrate(learning, manifest, policy=replace(SemanticPolicy(), max_sources=3))
    assert versions(package) == {(item["memory_id"], item["revision"]) for item in (parent, exception)}
    assert {item["event_id"] for item in package.sources} == {
        current["event_id"], sources[0]["event_id"], sources[1]["event_id"],
    }
    assert selected["objectives"] == manifest["objectives"][1:]
    assert deferred["objectives"] == manifest["objectives"][:1]
    assert len(package.belief_relations) == 1 and learning.calls == []


def test_history_expansion_reuse_ends_before_sources_can_be_deleted(learning):
    current, sources, memories, _ = history_graph(learning)
    manifest = manifest_for(current, *reversed(memories))
    assert integrate(learning, manifest)[1] == manifest
    learning.memory.forget([sources[1]["event_id"]], kind="events", mode="erase_content",
                           expected_scope_generation=1)
    updated_manifest = manifest_for(current, memories[-1])
    package, selected, deferred = integrate(learning, updated_manifest)
    assert {item["event_id"] for item in package.sources} == {
        current["event_id"], sources[0]["event_id"], sources[2]["event_id"],
    }
    assert versions(package) == {(item["memory_id"], item["revision"]) for item in (memories[0], memories[2])}
    assert all(item["scope"]["scope_generation"] == 2 for item in package.sources)
    assert package.belief_relations == () and selected == updated_manifest and deferred["objectives"] == []
    assert learning.calls == []


@pytest.mark.parametrize("limit", ["sources", "assertions", "request_bytes", "total_bytes"])
def test_incomplete_history_budget_defers_the_entire_objective_without_partial_evidence(learning, limit):
    current, _, memories, _ = history_graph(learning)
    manifest = manifest_for(current, memories[-1])
    full, selected, _ = integrate(learning, manifest)
    _, size = package_request(learning, full, manifest=selected)
    changes = {"sources": {"max_sources": 3}, "assertions": {"max_assertions": 2},
               "request_bytes": {"max_request_bytes": size + 2047},
               "total_bytes": {"max_input_bytes": size + 2047}}
    package, selected, deferred = integrate(learning, manifest, policy=replace(SemanticPolicy(), **changes[limit]))
    assert package.sources == (current,) and not package.assertions and not package.belief_relations
    assert selected["objectives"] == [] and deferred == manifest
    assert package.sampling["assertion_count"] == 0 and package.sampling["source_count"] == 1
    assert learning.calls == []


def test_known_complete_verifier_context_is_budgeted_before_any_history_call(learning):
    current, _, memories, _ = history_graph(learning)
    manifest = manifest_for(current, memories[-1])
    full, selected, _ = integrate(learning, manifest)
    _, proposal_size = package_request(learning, full, manifest=selected)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        assertions = [learning.service._rebind(item, learning.principal, brain) for item in full.assertions]
    verifier_context = bounded_verification_context(
        HISTORY_INTEGRATION_MODE, full.sources, assertions, full.belief_relations, manifest=selected)
    _, minimum_size = request_payload(
        "verification", {"proposals": [], **verifier_context}, VERIFICATION_SCHEMA,
        SemanticPolicy().max_output_tokens, proposal_mode=HISTORY_INTEGRATION_MODE,
        input_contract_version=EPISODE_FIDELITY_INPUT_VERSION)
    total = proposal_size + minimum_size + 2048
    assert proposal_size + 2048 < total - 1
    package, selected, deferred = integrate(
        learning, manifest, policy=replace(SemanticPolicy(), max_input_bytes=total - 1))
    assert package.sources == (current,) and not package.assertions and not package.belief_relations
    assert selected["objectives"] == [] and deferred == manifest and learning.calls == []
    package, selected, deferred = integrate(
        learning, manifest, policy=replace(SemanticPolicy(), max_input_bytes=total))
    assert selected == manifest and not deferred["objectives"]
    assert versions(package) == versions(full) and learning.calls == []


@pytest.mark.parametrize("mode", ["local", "history"])
@pytest.mark.parametrize("change", ["stale_revision", "deleted", "corrected", "scope", "future"])
def test_exact_required_inputs_cannot_be_substituted_or_omitted(learning, mode, change):
    old = publish(learning, source(learning, "An older independent report."))
    current = source(learning, "The currently bound input.", source_key="required-input")
    manifest = manifest_for(current, old)
    if change == "stale_revision":
        current["revision"] += 1
        manifest["input_refs"][0]["revision"] += 1
        manifest["input_fingerprint"] = digest(manifest["input_refs"])
        endpoint = manifest["objectives"][0]["target_memory_version"]
        manifest["objectives"][0]["objective_id"] = "comparison-" + digest([
            MANIFEST_VERSION, manifest["input_fingerprint"], endpoint])
    elif change == "deleted":
        learning.memory.forget([current["event_id"]], kind="events", mode="erase_content", expected_scope_generation=1)
    elif change == "corrected":
        learning.observe("A later correction must not replace the frozen input.", source_key="required-input", revision=2)
    elif change == "scope":
        current = source(learning, "This input belongs to another branch.", branch="fork")
        manifest = manifest_for(current, old)
    else:
        manifest["through_sequence"] = current["sequence"] - 1
    with pytest.raises(DomainError) as caught:
        if mode == "history":
            integrate(learning, manifest)
        else:
            local(learning, [current], through=manifest["through_sequence"])
    assert caught.value.code == "source_unavailable"
    assert learning.calls == []


def test_future_exception_on_parent_defers_old_frozen_comparison_without_leaking_future_content(learning):
    parent = publish(learning, source(learning, "The older planning routine.", conversation="earlier"))
    derived = publish(learning, source(learning, "A report derived from the older routine.", conversation="earlier"), parents=[parent])
    current = source(learning, "What was known at this point?")
    manifest = manifest_for(current, derived)
    later = source(learning, "The future exception that was not yet available.", conversation="earlier")
    exception = publish(learning, later)
    link_exception(learning, parent, exception, event_ids=[later["event_id"]])
    package, selected, deferred = integrate(learning, manifest)
    assert package.sources == (current,) and not package.assertions
    assert selected["objectives"] == [] and deferred == manifest
    assert all(item["sequence"] <= manifest["through_sequence"] for item in package.sources)
    assert learning.calls == []


def test_local_school_inputs_fit_independently_of_oversized_prior_history_and_working_text(learning):
    original = json.loads(FIXTURE.read_text())
    old = source(learning, "Unrelated prior history. " * 3000)
    publish(learning, old)
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        working = learning.service._working(conn, learning.principal, brain, TARGET, None)
        working["items"] = [{"key": "old-topic", "value": old["observed"]["content"]["text"],
                             "knowledge_state": "grounded", "evidence_refs": [reference(old)],
                             "expires_at": None, "updated_at": working["updated_at"]}]
        validate("WorkingState", working)
        learning.service._working_save(conn, learning.principal, brain, TARGET, working)
    events = []
    for item in original["local_sources"]:
        observed = item["observed"]
        receipt = learning.memory.observe(observed["content"]["text"], conversation_id="chat", branch_id="main",
                                          source_event_key=observed["source_event_key"], producer_ref="learning-test",
                                          role=observed["actor_role"], delivery_state=observed["delivery_state"])["result"]
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            events.extend(learning.service._events(conn, learning.principal, brain, event_ids=receipt["accepted_event_ids"]))
    package = local(learning, events)
    messages, size = package_request(learning, package, refs=event_refs(events))
    limited = replace(SemanticPolicy(), max_request_bytes=size + 1)
    selected = local(learning, events, policy=limited)
    assert selected.sources == tuple(events) and not selected.assertions and not selected.episodes
    body = json.loads(messages[-1]["content"])
    assert body["working_state"]["items"] == body["working_state"]["open_loops"] == []
    assert body["existing_assertions"] == [] and body["sources"] == events
    assert "Unrelated prior history." not in messages[-1]["content"]
    assert "我没有告诉你学校所在的城市。" in messages[-1]["content"]
    with pytest.raises(DomainError) as caught:
        local(learning, events, policy=replace(SemanticPolicy(), max_request_bytes=size - 1))
    assert caught.value.code == "budget_exceeded" and learning.calls == []


@pytest.mark.parametrize("change", ["deleted", "changed", "scope"])
def test_frozen_comparison_endpoint_cannot_silently_change_or_disappear(learning, change):
    root = publish(learning, source(learning, "An older independent report."))
    current = source(learning, "The current independent report.")
    manifest = manifest_for(current, root)
    with learning.database.transaction(learning.principal) as conn:
        if change == "deleted":
            conn.execute("UPDATE memory_assertions SET deleted=true WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (learning.principal.tenant_id, learning.brain, root["memory_id"]))
        else:
            altered = copy.deepcopy(root)
            if change == "changed":
                altered["statement"] = "Changed interpretation with the same identity."
            else:
                altered["scope"].update(visibility="conversation", conversation_id="unavailable")
            conn.execute("UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(altered), learning.principal.tenant_id, learning.brain, root["memory_id"]))
    with pytest.raises(DomainError) as caught:
        integrate(learning, manifest)
    assert caught.value.code == "context_stale" and learning.calls == []


def test_discovery_identity_does_not_reauthorize_same_comparison_after_unrelated_generation_changes(learning):
    root = publish(learning, source(learning, "A concise planning routine."))
    current = source(learning, "That concise planning routine.")

    def discover():
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            return discover_manifest(conn, learning.service, learning.principal, brain, TARGET,
                                     through_sequence=current["sequence"], required_event_refs=event_refs([current]),
                                     policy=SemanticPolicy(), input_contract_version=EPISODE_FIDELITY_INPUT_VERSION)

    first = discover()
    assert [item["target_memory_version"]["memory_id"] for item in first["objectives"]] == [root["memory_id"]]
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_brains SET dependency_generation=dependency_generation+5,"
                     "scope_generation=scope_generation+1 WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                     learning.service._params(learning.principal, learning.brain))
    assert discover() == first
    package, selected, deferred = integrate(learning, first)
    assert selected == first and not deferred["objectives"] and versions(package) == {(root["memory_id"], 1)}


def test_discovery_excludes_assessed_versions_before_candidate_limits_and_reaches_the_tail(learning):
    memories = [publish(learning, source(learning, "A concise planning routine.")) for _ in range(5)]
    current = source(learning, "That concise planning routine.")
    policy = replace(SemanticPolicy(), max_assertions=2)
    excluded = set()
    pages = []
    for count in (2, 2, 1, 0):
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            manifest = discover_manifest(conn, learning.service, learning.principal, brain, TARGET,
                                         through_sequence=current["sequence"], required_event_refs=event_refs([current]),
                                         policy=policy, input_contract_version=EPISODE_FIDELITY_INPUT_VERSION,
                                         excluded_versions=sorted(excluded))
        selected = {(item["target_memory_version"]["memory_id"], item["target_memory_version"]["revision"])
                    for item in manifest["objectives"]}
        assert len(selected) == count and not selected & excluded
        excluded.update(selected)
        pages.append(manifest)
    assert excluded == {(item["memory_id"], item["revision"]) for item in memories}
    assert [item["unresolved_discovery"] for item in pages] == [True, True, False, False]
    assert all("candidate_limit" in item["discovery_limitations"] for item in pages[:2])
    assert all(item["discovery_limitations"] == [] for item in pages[2:])
    assert learning.calls == []


def test_excluded_literal_still_provides_a_source_bridge_to_an_unassessed_derived_assertion(learning):
    original = source(learning, "备考时需要安静。")
    literal = publish(learning, original)
    derived = copy.deepcopy(literal)
    derived.update(memory_id=uid(), statement="The user reports needing a quiet environment while preparing.",
                   epistemic_status="derived_inference")
    derived["derivation"]["method"] = "aggregation"
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        derived = learning.service._publish(conn, learning.principal, brain, derived)
    current = source(learning, "备考")
    assert cognition._lexical_score("备考", derived["statement"]) == 0
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        manifest = discover_manifest(conn, learning.service, learning.principal, brain, TARGET,
                                     through_sequence=current["sequence"], required_event_refs=event_refs([current]),
                                     policy=replace(SemanticPolicy(), max_assertions=2),
                                     input_contract_version=EPISODE_FIDELITY_INPUT_VERSION,
                                     excluded_versions=[(literal["memory_id"], literal["revision"])])
    assert [item["target_memory_version"]["memory_id"] for item in manifest["objectives"]] == [derived["memory_id"]]
    assert learning.calls == []


@pytest.mark.parametrize("source_limit", [1, 3])
def test_discovery_advances_past_exhausted_bridge_sources_to_all_derived_tail_versions(learning, source_limit):
    memories = []
    for _ in range(3):
        literal = publish(learning, source(learning, "备考时需要安静。"))
        derived = copy.deepcopy(literal)
        derived.update(memory_id=uid(), statement="The user reports needing quiet while preparing.",
                       epistemic_status="derived_inference")
        derived["derivation"]["method"] = "aggregation"
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            derived = learning.service._publish(conn, learning.principal, brain, derived)
        assert cognition._lexical_score("备考", derived["statement"]) == 0
        memories.extend((literal, derived))
    current = source(learning, "备考")
    expected = {(item["memory_id"], item["revision"]) for item in memories}
    excluded = set()
    for _ in range(len(expected) + 1):
        with learning.database.transaction(learning.principal) as conn:
            brain = learning.service._brain(conn, learning.principal, learning.brain)
            manifest = discover_manifest(
                conn, learning.service, learning.principal, brain, TARGET,
                through_sequence=current["sequence"], required_event_refs=event_refs([current]),
                policy=replace(SemanticPolicy(), max_assertions=2, max_sources=source_limit),
                input_contract_version=EPISODE_FIDELITY_INPUT_VERSION, excluded_versions=sorted(excluded))
        selected = {(item["target_memory_version"]["memory_id"], item["target_memory_version"]["revision"])
                    for item in manifest["objectives"]}
        assert not selected & excluded and len(selected) <= 2
        excluded.update(selected)
        if excluded != expected:
            assert selected and manifest["unresolved_discovery"]
        if not selected:
            assert not manifest["unresolved_discovery"] and manifest["discovery_limitations"] == []
            break
    else:
        pytest.fail("Bounded discovery failed to terminate after all six exact versions")
    assert excluded == expected and learning.calls == []


@pytest.mark.parametrize("later_change", [None, "dependency", "scope"])
@pytest.mark.parametrize("input_version", [RELATION_CAPABILITIES_INPUT_VERSION, LINK_DERIVED_INPUT_VERSION])
def test_rebound_history_resumes_with_exact_raw_snapshot_and_separate_authority_fence(
    learning, later_change, input_version, monkeypatch,
):
    monkeypatch.setattr("membrain.learning_integration.LATEST_INPUT_CONTRACT_VERSION", input_version)
    root = publish(learning, source(learning, "A concise planning routine."))
    current = source(learning, "That concise planning routine.")
    manifest = manifest_for(current, root)
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_brains SET dependency_generation=dependency_generation+5,"
                     "scope_generation=scope_generation+1 WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                     learning.service._params(learning.principal, learning.brain))
    package, selected, remaining = integrate(learning, manifest)
    root_id, child_id = uid(), uid()
    base = {"target": TARGET, "from_sequence": current["sequence"], "through_sequence": current["sequence"]}
    root_payload = {**base, **allocation_metadata(root_id)}
    payload = {**base, **allocation_metadata(child_id, phase=HISTORY_PHASE, root_job_id=root_id)}
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        snapshot = integration_snapshot(conn, learning.service, learning.principal, brain,
                                        {"payload": payload}, package, selected, remaining, SemanticPolicy())
        for identifier, job_payload, state, result in ((root_id, root_payload, "completed", None),
                                                        (child_id, payload, "queued", snapshot)):
            conn.execute("INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,state,payload,result) "
                         "VALUES(%s,%s,%s,%s,'consolidate',%s,%s,%s)",
                         (*learning.service._params(learning.principal, learning.brain), identifier, state,
                          Jsonb(job_payload), Jsonb(result)))
    job = learning.job(child_id)
    batch, frozen = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, job)
    assert frozen == snapshot
    assert snapshot["memory_snapshot"][0]["content_sha256"] == digest(root)
    assert digest(batch.existing_assertions[0]) != digest(root)
    assert assertion_fingerprint(batch.existing_assertions[0]) == assertion_fingerprint(root)
    if later_change:
        column = "dependency_generation" if later_change == "dependency" else "scope_generation"
        with learning.database.transaction(learning.principal) as conn:
            conn.execute("UPDATE memory_brains SET " + column + "=" + column + "+1 "
                         "WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                         learning.service._params(learning.principal, learning.brain))
        with pytest.raises(DomainError) as caught:
            LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, learning.job(child_id))
        assert caught.value.code == "context_stale"
        return
    resumed, frozen_again = LearningEngine(learning.service)._snapshot(learning.principal, learning.brain, learning.job(child_id))
    assert resumed == batch and frozen_again == frozen
    calls = []

    def propose(request):
        calls.append(request)
        if "integration_abstentions" in request.response_schema["properties"]:
            response = {**proposals(), "integration_abstentions": [
                {"objective_id": item["objective_id"], "reason": "already_represented"}
                for item in selected["objectives"]]}
        else:
            response = {**proposals(), "integration_decisions": [
                {"objective_id": item["objective_id"], "outcome": "no_change", "reason": "already_represented",
                 "link_proposal_ids": []} for item in selected["objectives"]]}
        return encoded(response)

    result = SemanticProcessor(propose, lambda _: pytest.fail("No-change does not require verification")).process(
        resumed, reserve=lambda **_: None)
    assert result.status == "completed" and len(calls) == 1 and result.history_integration["assessed_count"] == 1
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        receipt = publish_semantic(conn, learning.service, learning.principal, brain, resumed, result, job_id=child_id)
    assert receipt["published_count"] == 0 and receipt["rejected_count"] == 0


def test_exhausted_visible_seeds_do_not_claim_completion_when_bridge_seed_scan_was_truncated(learning):
    literals = [publish(learning, source(learning, "A concise planning routine.")) for _ in range(5)]
    unseen_seed = max(literals, key=lambda item: item["memory_id"])
    derived = copy.deepcopy(unseen_seed)
    derived.update(memory_id=uid(), statement="User describes their personal workflow.",
                   epistemic_status="derived_inference")
    derived["derivation"]["method"] = "aggregation"
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        learning.service._publish(conn, learning.principal, brain, derived)
    current = source(learning, "A concise planning routine.")
    assert cognition._lexical_score(current["observed"]["content"]["text"], derived["statement"]) == 0
    with learning.database.transaction(learning.principal) as conn:
        brain = learning.service._brain(conn, learning.principal, learning.brain)
        manifest = discover_manifest(
            conn, learning.service, learning.principal, brain, TARGET,
            through_sequence=current["sequence"], required_event_refs=event_refs([current]),
            policy=replace(SemanticPolicy(), max_assertions=1),
            input_contract_version=EPISODE_FIDELITY_INPUT_VERSION,
            excluded_versions=[(item["memory_id"], item["revision"]) for item in literals])
    assert manifest["objectives"] == [] and manifest["unresolved_discovery"] is True
    assert "source_candidate_limit" in manifest["discovery_limitations"] and learning.calls == []
