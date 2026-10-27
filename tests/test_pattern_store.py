import copy
import json
from dataclasses import replace

import pytest
from psycopg.types.json import Jsonb
from test_cognitive_archives import Archives
from test_consolidation import memory as memory
from test_semantic import claim, episode, proposals, run

from membrain.helpers import Principal, uid
from membrain.pattern_store import invalidate_patterns, load_patterns, refresh_patterns

pytestmark = pytest.mark.integration


@pytest.fixture
def store(memory):
    return Archives(memory.database)


def rows(store):
    with store.database.transaction(store.principal) as conn:
        return conn.execute(
            "SELECT * FROM memory_pattern_views WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s ORDER BY id",
            store.service._params(store.principal, store.brain_id),
        ).fetchall()


def populated(store, *, hidden_context=False):
    sources = []
    for detail in ("今天复习代数。", "今天复习几何。"):
        common = store.observe("先交流，再制定计划。")
        specific = store.observe(detail)
        inputs = [common, specific]
        if hidden_context and not sources:
            inputs.append(store.observe("本轮另有未引用的上下文。"))
        batch = store.batch(*inputs)
        action = episode(common, member_refs=[{"event_id": source["event_id"], "revision": source["revision"]}
                                             for source in (common, specific)])
        store.publish(batch, run(batch, proposals(episodes=[action])))
        sources.extend(inputs)
    memories = [row["document"] for row in store.rows("memory_assertions")]
    episodes = [row["document"] for row in store.rows("memory_episodes")]
    batch = store.batch(*sources, existing_assertions=tuple(memories), episodes=tuple(episodes))
    result = run(batch, proposals())
    metadata = store.publish(batch, result)
    assert metadata["pattern_view_ids"]
    assert len(rows(store)) == len(metadata["pattern_view_ids"])
    return sources, memories, episodes, batch, result


def load(store, memories, **scope_changes):
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        scope = store.service._scope(store.principal, brain, {"conversation_ref": "test", "branch_ref": "main"})
        scope.update(scope_changes)
        return load_patterns(conn, store.service, store.principal, brain, scope=scope,
                             memory_versions=[{"memory_id": memory["memory_id"], "revision": memory["revision"]}
                                              for memory in memories])


def test_verified_episode_publication_builds_an_idempotent_pattern_without_changing_facts(store):
    _, memories, _, batch, result = populated(store)
    original = copy.deepcopy(store.rows("memory_assertions"))
    saved = rows(store)
    assert load(store, memories) == [row["document"] for row in saved]
    again = store.publish(batch, result)
    assert set(again["pattern_view_ids"]) == {row["id"] for row in saved}
    assert rows(store) == saved
    assert store.rows("memory_assertions") == original
    assert result.model_calls == 1


def test_pattern_requires_every_exact_selected_memory_version_and_matching_scope(store):
    _, memories, _, _, _ = populated(store)
    view = rows(store)[0]["document"]
    omitted = view["source_memory_versions"][0]
    assert load(store, [memory for memory in memories if memory["memory_id"] != omitted["memory_id"]]) == []
    wrong = copy.deepcopy(memories)
    next(memory for memory in wrong if memory["memory_id"] == omitted["memory_id"])["revision"] += 1
    assert load(store, wrong) == []
    assert load(store, memories, branch_id="another-branch") == []


@pytest.mark.parametrize("mode", ["suppress_recall", "erase_content"])
def test_forget_clears_pattern_content_and_leaves_unrelated_instance_facts_independent(store, mode):
    sources, memories, _, _, _ = populated(store)
    store.action("forget", {"selector": {"kind": "events", "ids": [sources[0]["event_id"]]},
                            "mode": mode, "expected_scope_generation": 1, "idempotency_key": uid()})
    assert all(row["invalidated"] and row["document"] is None for row in rows(store))
    assert all(row["deleted"] is (mode == "erase_content") for row in rows(store))
    assert load(store, memories) == []
    other_ids = {source["event_id"] for source in sources[2:]}
    other = [row for row in store.rows("memory_assertions") if set(row["event_ids"]) <= other_ids]
    assert other and all(not row["invalidated"] and not row["deleted"] for row in other)


def test_source_edit_clears_pattern_and_cannot_republish_from_stale_batch(store):
    sources, _, _, batch, _ = populated(store)
    source = sources[0]
    store.observe("今天不交流，直接制定计划。", key=source["observed"]["source_event_key"], revision=2)
    assert all(row["document"] is None and row["invalidated"] for row in rows(store))
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        assert refresh_patterns(conn, store.service, store.principal, brain, batch, [], []) == []


def test_unselected_episode_root_is_an_invalidation_dependency(store):
    sources, _, _, _, _ = populated(store, hidden_context=True)
    hidden = sources[2]
    assert all(hidden["event_id"] in row["event_ids"] for row in rows(store))
    store.action("forget", {"selector": {"kind": "events", "ids": [hidden["event_id"]]},
                            "mode": "erase_content", "expected_scope_generation": 1, "idempotency_key": uid()})
    assert all(row["document"] is None for row in rows(store))


def test_episode_revision_invalidates_only_the_derived_view(store):
    sources, memories, episodes, _, _ = populated(store)
    original_views = rows(store)
    previous = episodes[0]
    refs = previous["member_refs"]
    selected = {ref["event_id"] for ref in refs}
    inputs = [source for source in sources if source["event_id"] in selected]
    batch = store.batch(*inputs, episodes=(previous,))
    action = episode(inputs[0], action="close", episode_id=previous["episode_id"], expected_revision=previous["revision"],
                     member_refs=refs, boundary_ref=refs[-1])
    store.publish(batch, run(batch, proposals(episodes=[action])))
    assert all(row["document"] is None and row["invalidated"] for row in rows(store))
    assert all(not row["invalidated"] for row in store.rows("memory_assertions"))
    assert load(store, memories) == []
    current = [row["document"] for row in store.rows("memory_episodes") if row["is_current"]]
    refreshed = store.batch(*sources, existing_assertions=tuple(memories), episodes=tuple(current))
    store.publish(refreshed, run(refreshed, proposals()))
    rebuilt = load(store, memories)
    assert rebuilt and all(view["view_id"] != original_views[0]["id"] for view in rebuilt)
    assert rebuilt[0]["definition"] == original_views[0]["document"]["definition"]
    assert any(instance["episode_ref"] == {"episode_id": previous["episode_id"], "revision": previous["revision"] + 1}
               for instance in rebuilt[0]["instances"])


@pytest.mark.parametrize("change", ["missing_source", "missing_parent", "cycle", "same_version_meaning"])
def test_reader_rechecks_current_source_and_parent_closure_instead_of_trusting_cached_view(store, change):
    sources, memories, _, _, _ = populated(store)
    with store.database.transaction(store.principal) as conn:
        params = store.service._params(store.principal, store.brain_id)
        if change == "missing_source":
            conn.execute("UPDATE memory_events SET retired=true WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (*params, sources[0]["event_id"]))
        else:
            changed = copy.deepcopy(memories[0])
            if change == "same_version_meaning":
                changed["statement"] = "A changed interpretation under an unchanged identity."
            else:
                parent = changed["memory_id"] if change == "cycle" else "unavailable-parent"
                changed["derivation"]["source_memory_versions"] = [{"memory_id": parent, "revision": 1}]
            conn.execute("UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(changed), *params, changed["memory_id"]))
    assert rows(store)[0]["document"] is not None
    assert load(store, memories) == []


@pytest.mark.parametrize("bound,value", [("MAX_MEMORIES", 1), ("MAX_EPISODES", 1), ("MAX_SOURCES", 1)])
def test_reader_omits_the_entire_view_when_its_validation_budget_is_exceeded(store, monkeypatch, bound, value):
    _, memories, _, _, _ = populated(store)
    monkeypatch.setattr("membrain.pattern_store." + bound, value)
    assert load(store, memories) == []


def test_pattern_table_enforces_principal_isolation(store):
    populated(store)
    with store.database.transaction(Principal(uid(), uid())) as conn:
        assert conn.execute("SELECT count(*) AS value FROM memory_pattern_views WHERE brain_id=%s",
                            (store.brain_id,)).fetchone()["value"] == 0


def test_local_archive_rebuilds_patterns_from_restored_exact_sources_without_exporting_the_cache(store):
    populated(store)
    exported = store.export()
    assert "schema-residual-v1" not in json.dumps(exported["document"])
    result = store.restore(exported)
    assert result["quarantined_event_count"] == 0
    assert rows(store)
    memories = [row["document"] for row in store.rows("memory_assertions")]
    assert load(store, memories)


def test_uploaded_archive_cannot_restore_pattern_verification(store):
    populated(store)
    exported = store.export()
    result = store.restore(exported, upload=True)
    assert not rows(store)
    assert any('"patterns_rebuilt": 0' in detail for detail in result["job"]["details"])


def oversized_episode(store):
    sources, memories, episodes, _, _ = populated(store)
    extras = [store.observe(f"Additional observed member {index}.") for index in range(127)]
    previous = episodes[0]
    updated = copy.deepcopy(previous)
    updated["revision"] += 1
    updated["member_refs"].extend({"event_id": source["event_id"], "revision": source["revision"]} for source in extras)
    updated["root_event_ids"] = sorted(set(updated["root_event_ids"]) | {source["event_id"] for source in extras})
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, store.brain_id)
        params = store.service._params(store.principal, store.brain_id)
        invalidate_patterns(conn, store.service, store.principal, brain, episode_ids=[previous["episode_id"]])
        conn.execute("UPDATE memory_episodes SET is_current=false WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*params, previous["episode_id"]))
        conn.execute(
            "INSERT INTO memory_episodes(tenant_id,owner_id,brain_id,id,revision,conversation_id,branch_id,state,document,event_ids) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*params, updated["episode_id"], updated["revision"], updated["scope"]["conversation_id"],
             updated["scope"]["branch_id"], updated["state"], Jsonb(updated), updated["root_event_ids"]),
        )
        conn.execute(
            "INSERT INTO memory_episode_members(tenant_id,owner_id,brain_id,episode_id,episode_revision,memory_id,memory_revision,role,position) "
            "SELECT tenant_id,owner_id,brain_id,episode_id,%s,memory_id,memory_revision,role,position "
            "FROM memory_episode_members WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND episode_id=%s AND episode_revision=%s",
            (updated["revision"], *params, previous["episode_id"], previous["revision"]),
        )
    assert len(updated["member_refs"]) == 129
    return sources + extras, memories, [updated, episodes[1]]


def test_pattern_kernel_budget_rejection_does_not_roll_back_valid_fact_publication(store):
    sources, memories, episodes = oversized_episode(store)
    observation = sources[3]
    proposal = claim(observation, statement=observation["observed"]["content"]["text"],
                     claim_kind="reported_fact", conditions={"op": "always"})
    verified = run(store.batch(observation), proposals(proposal))
    result = replace(verified, observed_event_refs=tuple(
        {"event_id": source["event_id"], "revision": source["revision"]} for source in sources))
    batch = store.batch(*sources, existing_assertions=tuple(memories), episodes=tuple(episodes))
    published = store.publish(batch, result)
    assert published["published_count"] == 1
    assert published["pattern_view_ids"] == []
    assert published["memory_versions"][0]["memory_id"] in {row["id"] for row in store.rows("memory_assertions")}
    assert len(store.rows("memory_assertions")) == len(memories) + 1


def test_valid_large_local_episode_restores_facts_when_pattern_kernel_rejects_organization(store):
    _, memories, _ = oversized_episode(store)
    exported = store.export()
    result = store.restore(exported)
    assert result["quarantined_event_count"] == 0
    assert len(store.rows("memory_assertions")) == len(memories)
    assert len(store.rows("memory_episodes")) == 2
    assert not rows(store)
    assert any('"patterns_rebuilt": 0' in detail for detail in result["job"]["details"])
