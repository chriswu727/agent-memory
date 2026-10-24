import copy
import json
from dataclasses import replace

import pytest
from psycopg.types.json import Jsonb
from test_cognitive_archives import Archives
from test_consolidation import memory as memory
from test_semantic import claim, proposals, reference, run, verifier

from agent_memory import cognition
from agent_memory.belief_store import (
    assertion_fingerprint,
    load_belief_relations,
    validate_relation,
)
from agent_memory.helpers import DomainError, Principal, uid
from agent_memory.neighborhood import _Selector
from agent_memory.semantic import PROPOSAL_SCHEMA, SemanticPolicy

pytestmark = pytest.mark.integration
UNBOUNDED = {"kind": "interval", "start": {"kind": "unbounded"}, "end": {"kind": "unbounded"}}
ONE_DAY = {"kind": "interval", "start": {"kind": "instant", "at": "2026-10-24T00:00:00Z"},
           "end": {"kind": "instant", "at": "2026-10-25T00:00:00Z"}}


@pytest.fixture
def store(memory):
    return Archives(memory.database)


def verified(request):
    result = json.loads(verifier(request))
    for item in result["assessments"]:
        item["link_verdict"] = "supports"
    return json.dumps(result)


def rows(store):
    with store.database.transaction(store.principal) as conn:
        return conn.execute(
            "SELECT * FROM memory_belief_relations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s ORDER BY id",
            store.service._params(store.principal, store.brain_id),
        ).fetchall()


def version(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def linked(store, *, source_revision=1, hidden=False, independent_endpoints=False):
    old_source = store.observe("平时我希望直接获得建议。", key="old-preference")
    if source_revision == 2:
        old_source = store.observe("平时我希望直接获得具体建议。", key="old-preference", revision=2)
    first = store.batch(old_source)
    old_claim = claim(old_source, conditions={"op": "always"}, valid_during=UNBOUNDED)
    metadata = store.publish(first, run(first, proposals(old_claim)))
    old = next(row["document"] for row in store.rows("memory_assertions")
               if version(row["document"]) == metadata["memory_versions"][0])
    new_source = store.observe("2026年10月24日只需要先听我说，不要马上给建议。")
    sources = [old_source, new_source]
    if hidden:
        sources.append(store.observe("没有引用但被模型读到的上下文。"))
    new_claim = claim(new_source, statement="2026年10月24日用户希望先被倾听。",
                      conditions={"op": "always"}, valid_during=ONE_DAY)
    if independent_endpoints:
        independent = store.batch(new_source)
        store.publish(independent, run(independent, proposals(new_claim)))
    batch = store.batch(*sources, existing_assertions=(old,))
    link = {"proposal_id": "relation-1", "kind": "exception", "claim_proposal_id": new_claim["proposal_id"],
            "target_memory_versions": [version(old)], "evidence_refs": [reference(old_source), reference(new_source)],
            "conditions": {"op": "always"}, "valid_during": ONE_DAY, "world": "real_world"}
    proposed = proposals(new_claim) | {"belief_links": [link]}
    result = run(batch, proposed, verify=verified)
    assert len(result.belief_links) == 1, result.rejected
    before = store.service.dashboard(store.principal, store.brain_id)["brain"]["dependency_generation"]
    metadata = store.publish(batch, result)
    new = next(row["document"] for row in store.rows("memory_assertions")
               if version(row["document"]) == metadata["memory_versions"][0])
    return sources, old, new, batch, result, before, metadata


def load(store, memories, **changes):
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        scope = store.service._scope(store.principal, brain, {"conversation_ref": "test", "branch_ref": "main"})
        scope.update(changes)
        return load_belief_relations(conn, store.service, store.principal, brain, scope,
                                     [version(memory) for memory in memories])


def test_published_relation_preserves_old_fact_exact_versions_and_invalidates_prepared_generation_once(store):
    sources, old, new, batch, result, before, metadata = linked(store)
    saved = rows(store)
    assert len(saved) == 1 and metadata["belief_relation_versions"] == [{"relation_id": saved[0]["id"], "revision": 1}]
    document = saved[0]["document"]
    validate_relation(document)
    assert document["target_memory_versions"] == [version(old)]
    assert document["source_memory_version"] == version(new)
    assert document["valid_during"] == ONE_DAY
    assert set(document["root_event_ids"]) == {source["event_id"] for source in sources}
    original = next(row for row in store.rows("memory_assertions") if row["id"] == old["memory_id"])
    assert original["document"] == old and original["is_current"] and not original["invalidated"]
    assert new["derivation"]["source_memory_versions"] == []
    assert store.service.dashboard(store.principal, store.brain_id)["brain"]["dependency_generation"] == before + 1
    assert store.publish(batch, result) == metadata
    assert store.service.dashboard(store.principal, store.brain_id)["brain"]["dependency_generation"] == before + 1
    loaded = load(store, [old])
    assert len(loaded["relations"]) == 1 and loaded["incomplete_versions"] == []
    assert {tuple(version(memory).values()) for memory in loaded["memories"]} == {tuple(version(memory).values()) for memory in (old, new)}


@pytest.mark.parametrize("mode", ["suppress_recall", "erase_content"])
def test_forgetting_new_evidence_clears_relation_without_revoking_independent_old_fact(store, mode):
    sources, old, _, _, _, _, _ = linked(store)
    store.action("forget", {"selector": {"kind": "events", "ids": [sources[1]["event_id"]]},
                            "mode": mode, "expected_scope_generation": 1, "idempotency_key": uid()})
    relation = rows(store)[0]
    assert relation["document"] is None and relation["invalidated"]
    assert relation["deleted"] is (mode == "erase_content")
    old_row = next(row for row in store.rows("memory_assertions") if row["id"] == old["memory_id"])
    assert not old_row["invalidated"] and not old_row["suppressed"]
    assert load(store, [old])["relations"] == []


def test_source_edit_during_or_after_publication_revokes_relation_and_stale_writeback(store):
    sources, _, _, batch, result, _, _ = linked(store)
    store.observe("这一天仍然需要直接建议。", key=sources[1]["observed"]["source_event_key"], revision=2)
    assert rows(store)[0]["document"] is None
    with pytest.raises(DomainError):
        store.publish(batch, result)


@pytest.mark.parametrize("change", ["retired_source", "changed_endpoint", "missing_parent", "scope"])
def test_incomplete_active_relation_marks_known_endpoints_incomplete_instead_of_reviving_old_rule(store, change):
    sources, old, new, _, _, _, _ = linked(store)
    with store.database.transaction(store.principal) as conn:
        params = store.service._params(store.principal, store.brain_id)
        if change == "retired_source":
            conn.execute("UPDATE memory_events SET retired=true WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (*params, sources[1]["event_id"]))
        else:
            altered = copy.deepcopy(new)
            if change == "changed_endpoint":
                altered["statement"] = "Same identity, unrelated assertion."
            elif change == "scope":
                altered["scope"]["visibility"] = "conversation"
                altered["scope"]["conversation_id"] = "unavailable-conversation"
            else:
                altered["derivation"]["source_memory_versions"] = [{"memory_id": "missing-parent", "revision": 1}]
            conn.execute("UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(altered), *params, new["memory_id"]))
    result = load(store, [old])
    assert result["relations"] == []
    expected = {tuple(version(memory).values()) for memory in (old, new)}
    if change == "missing_parent":
        expected.add(("missing-parent", 1))
    assert {tuple(ref.values()) for ref in result["incomplete_versions"]} == expected


def test_full_observed_context_is_a_relation_dependency_even_without_a_selected_span(store):
    sources, old, _, _, _, _, _ = linked(store, hidden=True)
    assert sources[2]["event_id"] in rows(store)[0]["event_ids"]
    store.action("forget", {"selector": {"kind": "events", "ids": [sources[2]["event_id"]]},
                            "mode": "erase_content", "expected_scope_generation": 1, "idempotency_key": uid()})
    assert rows(store)[0]["document"] is None
    assert not next(row for row in store.rows("memory_assertions") if row["id"] == old["memory_id"])["invalidated"]


def test_source_budget_cannot_drop_the_exception_and_return_an_unqualified_old_rule(store, monkeypatch):
    _, old, _, _, _, _, _ = linked(store)
    monkeypatch.setattr("agent_memory.belief_store.MAX_SOURCES", 1)
    result = load(store, [old])
    assert result["relations"] == [] and version(old) in result["incomplete_versions"]


def test_endpoint_fingerprint_ignores_access_and_generation_but_freezes_meaning(store):
    _, old, _, _, _, _, _ = linked(store)
    changed = copy.deepcopy(old)
    changed["dynamics"]["activation"] = 0.8
    changed["system_time"]["recorded_at"] = "2026-10-01T00:00:00Z"
    changed["scope"]["scope_generation"] += 1
    changed["disclosure_basis"]["dependency_generation"] += 1
    for path in changed["disclosure_basis"]["support_paths"]:
        path["dependency_generation"] += 1
        path["permitted_scope"]["scope_generation"] += 1
    assert assertion_fingerprint(changed) == assertion_fingerprint(old)
    changed["conditions"] = {"op": "not", "arg": {"op": "always"}}
    assert assertion_fingerprint(changed) != assertion_fingerprint(old)


def test_local_restore_rebinds_relations_and_exact_event_revision_snapshots(store):
    sources, old, _, _, _, _, _ = linked(store, source_revision=2)
    original = rows(store)[0]["document"]
    exported = store.export()
    assert exported["document"]["belief_relations"] == [original]
    result = store.restore(exported)
    assert result["quarantined_event_count"] == 0
    restored = rows(store)[0]["document"]
    assert restored["relation_id"] != original["relation_id"]
    assert restored["target_memory_versions"] == original["target_memory_versions"]
    assert restored["scope"]["brain_id"] == store.brain_id
    assert next(ref for ref in restored["evidence_refs"] if ref["event_id"] == sources[0]["event_id"])["event_revision"] == 1
    assert load(store, [old])["relations"] == [restored]


def test_uploaded_archive_cannot_restore_verification_and_old_archives_remain_supported(store):
    linked(store)
    exported = store.export()
    result = store.restore(exported, upload=True)
    assert rows(store) == []
    assert any('"quarantined_belief_relations": 1' in detail for detail in result["job"]["details"])
    old_format = copy.deepcopy(exported)
    old_format["document"].pop("belief_relations")
    store.service._validate_archive(old_format["document"])


def test_erasure_scrubs_relation_content_from_stored_archives_and_blocks_restore(store):
    sources, _, _, _, _, _, _ = linked(store)
    exported = store.export()
    store.action("forget", {"selector": {"kind": "events", "ids": [sources[1]["event_id"]]},
                            "mode": "erase_content", "expected_scope_generation": 1, "idempotency_key": uid()})
    sanitized = store.service.download_archive(store.principal, exported["archive_id"])
    assert sanitized["document"]["belief_relations"] == []
    store.restore(sanitized)
    assert rows(store) == []


def test_belief_table_enforces_principal_isolation(store):
    linked(store)
    with store.database.transaction(Principal(uid(), uid())) as conn:
        assert conn.execute("SELECT count(*) AS value FROM memory_belief_relations WHERE brain_id=%s",
                            (store.brain_id,)).fetchone()["value"] == 0


def test_loader_keeps_complete_connected_change_chain_and_marks_the_whole_package_on_budget_failure(store):
    sources, old, new, _, _, _, _ = linked(store)
    last_source = store.observe("我又犹豫了，也许现在还是需要直接建议。")
    batch = store.batch(*sources, last_source, existing_assertions=(new,))
    last_claim = claim(last_source, statement="用户又表示可能希望直接获得建议。",
                       conditions={"op": "always"}, valid_during=ONE_DAY)
    link = {"proposal_id": "later-relation", "kind": "contradicts", "claim_proposal_id": last_claim["proposal_id"],
            "target_memory_versions": [version(new)], "evidence_refs": [reference(sources[1]), reference(last_source)],
            "conditions": {"op": "always"}, "valid_during": ONE_DAY, "world": "real_world"}
    result = run(batch, proposals(last_claim) | {"belief_links": [link]}, verify=verified)
    assert len(result.belief_links) == 1, result.rejected
    store.publish(batch, result)
    loaded = load(store, [old])
    assert len(loaded["relations"]) == 2 and len(loaded["memories"]) == 3
    assert loaded["incomplete_versions"] == []
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        scope = store.service._scope(store.principal, brain, {"conversation_ref": "test", "branch_ref": "main"})
        limited = load_belief_relations(conn, store.service, store.principal, brain, scope,
                                        [version(old)], max_relations=1)
    assert limited["relations"] == [] and version(old) in limited["incomplete_versions"]
    assert version(new) in limited["incomplete_versions"]


def test_restore_does_not_resurrect_a_revoked_relation_in_the_same_brain(store):
    sources, _, _, _, _, _, _ = linked(store)
    exported = store.export()
    store.action("forget", {"selector": {"kind": "events", "ids": [sources[1]["event_id"]]},
                            "mode": "suppress_recall", "expected_scope_generation": 1, "idempotency_key": uid()})
    refreshed = store.service.download_archive(store.principal, exported["archive_id"])
    result = store.restore(refreshed, new_brain=False)
    assert all(row["document"] is None for row in rows(store))
    assert any('"quarantined_belief_relations": 1' in detail for detail in result["job"]["details"])


def proposed_again(result):
    return proposals(*[{key: value for key, value in item.items()
                        if key in PROPOSAL_SCHEMA["properties"]["claims"]["items"]["properties"]}
                       for item in result.claims]) | {
        "belief_links": [{key: value for key, value in item.items()
                          if key in PROPOSAL_SCHEMA["properties"]["belief_links"]["items"]["properties"]}
                         for item in result.belief_links]}


def test_repeated_relation_with_unrelated_context_reuses_the_first_complete_proof_without_advancing_generation(store):
    _, old, _, batch, result, _, metadata = linked(store)
    original = copy.deepcopy(rows(store)[0])
    generation = store.service.dashboard(store.principal, store.brain_id)["brain"]["dependency_generation"]
    unrelated = store.observe("顺便问一下明天的天气。")
    later = replace(batch, batch_id=uid(), sources=(*batch.sources, unrelated))
    again = run(later, proposed_again(result), verify=verified)
    repeated = store.publish(later, again)
    assert repeated["belief_relation_versions"] == metadata["belief_relation_versions"]
    assert rows(store) == [original]
    assert store.service.dashboard(store.principal, store.brain_id)["brain"]["dependency_generation"] == generation
    store.action("forget", {"selector": {"kind": "events", "ids": [unrelated["event_id"]]},
                            "mode": "erase_content", "expected_scope_generation": 1, "idempotency_key": uid()})
    assert rows(store) == [original]
    assert len(load(store, [old])["relations"]) == 1


@pytest.mark.parametrize("change", ["endpoint", "meaning"])
def test_new_endpoint_or_relationship_meaning_has_a_distinct_identity(store, change):
    _, _, _, batch, result, _, metadata = linked(store)
    proposed = proposed_again(result)
    if change == "endpoint":
        proposed["claims"][0]["statement"] = "2026年10月24日用户希望被充分倾听再回应。"
    else:
        proposed["belief_links"][0]["kind"] = "contradicts"
    updated = store.publish(batch, run(batch, proposed, verify=verified))
    assert updated["belief_relation_versions"] != metadata["belief_relation_versions"]
    assert len(rows(store)) == 2


def derived_from_changed_parent(store):
    sources, old, new, _, _, _, _ = linked(store)
    batch = store.batch(sources[0], existing_assertions=(old,))
    proposed = claim(sources[0], statement="A derived rule for giving direct advice.",
                     memory_kind="semantic", representation="schema", claim_kind="inferred_pattern",
                     conditions={"op": "always"}, valid_during=UNBOUNDED,
                     source_memory_versions=[version(old)])
    metadata = store.publish(batch, run(batch, proposals(proposed)))
    child = next(row for row in store.rows("memory_assertions") if version(row["document"]) == metadata["memory_versions"][0])
    return sources, old, new, child


def test_relation_loading_and_learning_neighborhood_include_a_derived_parents_exception(store):
    sources, old, new, child = derived_from_changed_parent(store)
    loaded = load(store, [child["document"]])
    assert loaded["incomplete_versions"] == [] and len(loaded["relations"]) == 1
    assert {tuple(version(memory).values()) for memory in loaded["memories"]} == {
        tuple(version(memory).values()) for memory in (old, new, child["document"])}
    assert sources[1]["event_id"] in {event["event_id"] for event in loaded["events"]}
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        selector = _Selector(conn, store.service, store.principal, brain,
                             {"conversation_ref": "test", "branch_ref": "main"}, brain["durable_sequence"], SemanticPolicy())
        selector.remember([child])
        events, memories = selector.assertion_package(child["document"])
    assert len(memories) == 3 and len(selector.belief_relations) == 1
    assert sources[1]["event_id"] in events


@pytest.mark.parametrize("failure", ["budget", "source"])
def test_missing_or_oversized_parent_relation_package_marks_the_original_derived_candidate_incomplete(store, monkeypatch, failure):
    sources, _, _, child = derived_from_changed_parent(store)
    if failure == "budget":
        monkeypatch.setattr("agent_memory.belief_store.MAX_MEMORIES", 2)
    else:
        with store.database.transaction(store.principal) as conn:
            conn.execute("UPDATE memory_events SET retired=true WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (*store.service._params(store.principal, store.brain_id), sources[1]["event_id"]))
    loaded = load(store, [child["document"]])
    assert loaded["relations"] == [] and version(child["document"]) in loaded["incomplete_versions"]


@pytest.mark.parametrize("depth", [8, 9])
def test_alternating_closure_respects_the_existing_eight_level_parent_limit(store, depth):
    _, old, _, _, _, _, _ = linked(store)
    current = old
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        for index in range(depth):
            parent = version(current)
            current = copy.deepcopy(old)
            current["memory_id"] = uid()
            current["statement"] = f"Derived observation at level {index + 1}."
            current["derivation"]["source_memory_versions"] = [parent]
            for path in current["disclosure_basis"]["support_paths"]:
                path["source_memory_versions"] = [parent]
            current = store.service._publish(conn, store.principal, brain, current)
    loaded = load(store, [current])
    if depth == 8:
        assert loaded["incomplete_versions"] == [] and len(loaded["relations"]) == 1
    else:
        assert loaded["relations"] == [] and version(current) in loaded["incomplete_versions"]


def test_correcting_only_hidden_relation_context_clears_relation_without_revoking_independent_endpoints(store):
    sources, old, new, _, _, _, _ = linked(store, hidden=True, independent_endpoints=True)
    hidden = sources[2]
    assert hidden["event_id"] in rows(store)[0]["event_ids"]
    assert all(hidden["event_id"] not in memory["root_event_ids"] for memory in (old, new))
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        hidden_memory = store.service._publish(conn, store.principal, brain, cognition.extract_event(hidden)[0])
    correction = store.observe("之前那段背景有误，请撤回。")
    generation = store.service.dashboard(store.principal, store.brain_id)["brain"]["dependency_generation"]
    store.action("correct", {"memory_id": hidden_memory["memory_id"], "expected_revision": hidden_memory["revision"],
                             "expected_dependency_generation": generation, "correction_event_ids": [correction["event_id"]],
                             "operation": "retract", "idempotency_key": uid()})
    assert rows(store)[0]["invalidated"] and rows(store)[0]["document"] is None
    endpoints = {old["memory_id"], new["memory_id"]}
    endpoint_rows = [row for row in store.rows("memory_assertions") if row["id"] in endpoints]
    assert len(endpoint_rows) == 2 and all(not row["invalidated"] for row in endpoint_rows)
    assert load(store, [old])["relations"] == []
