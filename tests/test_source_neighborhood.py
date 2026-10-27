import copy

import pytest
from psycopg.types.json import Jsonb
from test_candidates import Candidates, key
from test_candidates import candidates as candidates
from test_source_candidates import derived

from membrain import cognition
from membrain.helpers import digest
from membrain.source_neighborhood import source_neighborhood_candidates

pytestmark = pytest.mark.integration


def event_id(memory):
    return memory["evidence_refs"][0]["event_id"]


def source(candidates, memory):
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        return candidates.service._events(conn, candidates.principal, brain, event_ids=[event_id(memory)])[0]


def change(candidates, table, identifier, **changes):
    assert table in {"memory_assertions", "memory_events"}
    assert set(changes) <= {"document", "is_current", "deleted", "suppressed", "invalidated", "retired"}
    values = [Jsonb(value) if name == "document" else value for name, value in changes.items()]
    with candidates.database.transaction(candidates.principal) as conn:
        conn.execute("UPDATE " + table + " SET " + ",".join(name + "=%s" for name in changes)
                     + " WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                     (*values, *candidates.params, identifier))


def route(candidates, seeds, **options):
    with candidates.database.transaction(candidates.principal) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        brain = conn.execute("SELECT * FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                             candidates.params).fetchone()
        options.setdefault("through_sequence", brain["durable_sequence"])
        ranked, truncated = source_neighborhood_candidates(
            conn, candidates.service, candidates.principal, brain, candidates.target, seeds, **options)
    return {key(row["document"]): score for score, row in ranked}, truncated


def test_one_hop_recovers_zero_keyword_reply_without_mutation_or_recursive_expansion(candidates):
    anchor = candidates.add("The writing class schedule has changed.")
    reply = candidates.add("Twenty minutes works.")
    farther = candidates.add("The green notebook is on the shelf.")
    assert cognition._lexical_score("writing class", reply["statement"]) == 0
    found, truncated = route(candidates, [(0.8, anchor)])
    assert found == {key(reply): 0.8}
    assert key(anchor) not in found and key(farther) not in found
    assert not truncated


def test_previous_and_next_are_exact_conversation_and_branch_neighbors(candidates):
    before = candidates.add("Twenty minutes works.", conversation="earlier", branch="fork")
    candidates.add("Other conversation.", conversation="elsewhere", branch="fork")
    anchor = candidates.add("The writing class schedule.", conversation="earlier", branch="fork")
    candidates.add("Other branch.", conversation="earlier", branch="other")
    after = candidates.add("After lunch is better.", conversation="earlier", branch="fork", lifecycle="dormant")
    candidates.add("Not adjacent.", conversation="earlier", branch="fork")
    assert route(candidates, [(0.75, anchor)])[0] == {key(before): 0.75, key(after): 0.75}


def test_null_conversation_does_not_create_an_accidental_global_chain(candidates):
    anchor = candidates.add("The writing class schedule.")
    reply = candidates.add("Twenty minutes works.")
    for memory in (anchor, reply):
        event = source(candidates, memory)
        event["scope"].update(conversation_id=None, branch_id=None)
        memory["scope"].update(conversation_id=None, branch_id=None)
        with candidates.database.transaction(candidates.principal) as conn:
            conn.execute("UPDATE memory_events SET conversation_id=NULL,branch_id=NULL,document=%s "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(event), *candidates.params, event["event_id"]))
        change(candidates, "memory_assertions", memory["memory_id"], document=memory)
    assert route(candidates, [(1, anchor)]) == ({}, False)


@pytest.mark.parametrize("damage", ["deleted", "suppressed", "retired", "document", "draft", "scope"])
def test_hidden_nearest_source_is_a_barrier_not_a_bridge_to_a_remote_event(candidates, damage):
    anchor = candidates.add("The writing class schedule.")
    neighbor = candidates.add("Twenty minutes works.")
    farther = candidates.add("A distant detail.")
    if damage in {"deleted", "suppressed", "retired", "document"}:
        change(candidates, "memory_events", event_id(neighbor), **{damage: None if damage == "document" else True})
    else:
        event = source(candidates, neighbor)
        if damage == "draft":
            event["observed"]["delivery_state"] = "draft"
        else:
            event["scope"]["branch_id"] = "hidden"
        change(candidates, "memory_events", event_id(neighbor), document=event)
    found, _ = route(candidates, [(1, anchor)])
    assert found == {}
    assert key(farther) not in found


@pytest.mark.parametrize("damage", ["revision", "hash", "span", "context_only", "contradicts", "retracted",
                                     "invalidated", "deleted", "suppressed", "is_current", "scope"])
def test_neighbor_assertion_requires_current_valid_positive_bound_evidence(candidates, damage):
    anchor = candidates.add("The writing class schedule.")
    neighbor = candidates.add("Twenty minutes works.")
    memory = derived(candidates, neighbor)
    if damage in {"invalidated", "deleted", "suppressed", "is_current"}:
        change(candidates, "memory_assertions", memory["memory_id"], **{damage: damage != "is_current"})
    else:
        ref = memory["evidence_refs"][0]
        if damage == "revision":
            ref["event_revision"] += 1
        elif damage == "hash":
            ref["source_content_sha256"] = digest("another source")
        elif damage == "span":
            ref["span"]["end"] = 99999
        elif damage in {"context_only", "contradicts"}:
            ref["relationship"] = damage
        elif damage == "retracted":
            memory["lifecycle"] = "retracted"
        else:
            memory["scope"].update(conversation_id="hidden", visibility="conversation")
        change(candidates, "memory_assertions", memory["memory_id"], document=memory)
    found, _ = route(candidates, [(1, anchor)])
    assert key(neighbor) in found
    assert key(memory) not in found


@pytest.mark.parametrize("damage", ["source_deleted", "source_retired", "source_revision", "source_hash",
                                     "memory_suppressed", "memory_context", "memory_retracted"])
def test_passed_seed_document_cannot_bypass_current_database_authority(candidates, damage):
    anchor = candidates.add("The writing class schedule.")
    candidates.add("Twenty minutes works.")
    if damage.startswith("source_"):
        if damage in {"source_deleted", "source_retired"}:
            change(candidates, "memory_events", event_id(anchor), **{damage.removeprefix("source_"): True})
        else:
            event = source(candidates, anchor)
            if damage == "source_revision":
                event["revision"] += 1
            else:
                event["observed"]["content"]["text"] = "The content was changed."
            change(candidates, "memory_events", event_id(anchor), document=event)
    elif damage == "memory_suppressed":
        change(candidates, "memory_assertions", anchor["memory_id"], suppressed=True)
    else:
        changed = copy.deepcopy(anchor)
        if damage == "memory_context":
            changed["evidence_refs"][0]["relationship"] = "context_only"
        else:
            changed["lifecycle"] = "retracted"
        change(candidates, "memory_assertions", anchor["memory_id"], document=changed)
    assert route(candidates, [(1, anchor)])[0] == {}


def test_root_context_does_not_supply_positive_seed_or_neighbor_evidence(candidates):
    anchor = candidates.add("The writing class schedule.")
    neighbor = candidates.add("Twenty minutes works.")
    unrelated = candidates.add("An unrelated conversation.", conversation="other")
    false_seed = derived(candidates, unrelated, root_event_ids=[event_id(anchor)])
    false_neighbor = derived(candidates, unrelated, root_event_ids=[event_id(neighbor)])
    assert route(candidates, [(1, false_seed)])[0] == {}
    found, _ = route(candidates, [(1, anchor)])
    assert key(neighbor) in found and key(false_neighbor) not in found


def test_cutoff_cues_exclusions_and_foreign_principals_remain_barriers(candidates):
    anchor = candidates.add("The writing class schedule.")
    neighbor = candidates.add("Twenty minutes works.")
    later = candidates.add("A future detail.")
    compound = derived(candidates, neighbor, evidence_refs=neighbor["evidence_refs"] + later["evidence_refs"])
    assert route(candidates, [(1, anchor)], through_sequence=source(candidates, anchor)["sequence"])[0] == {}
    found, _ = route(candidates, [(1, anchor)], through_sequence=source(candidates, neighbor)["sequence"])
    assert key(neighbor) in found and key(compound) not in found
    assert route(candidates, [(1, anchor)], independent_of_event_ids=[event_id(anchor)])[0] == {}
    assert route(candidates, [(1, anchor)], independent_of_event_ids=[event_id(neighbor)])[0] == {}
    assert route(candidates, [(1, anchor)], excluded_versions=[key(neighbor), key(compound)])[0] == {}
    foreign = Candidates(candidates.database)
    assert route(foreign, [(1, anchor)])[0] == {}


def test_superseded_seed_and_neighbor_references_are_not_retrieval_bridges(candidates):
    anchor = candidates.add("The writing class schedule.")
    neighbor = candidates.add("Twenty minutes works.")
    audit = candidates.add("The earlier evidence is withdrawn.", conversation="audit")
    event = source(candidates, audit)
    event["observed"].update(event_kind="source_correction", producer_ref="memory-service")
    for memory in (neighbor, anchor):
        event["observed"]["content"]["structured"] = {"superseded_evidence_refs": memory["evidence_refs"]}
        with candidates.database.transaction(candidates.principal) as conn:
            conn.execute("UPDATE memory_events SET producer_ref='memory-service',document=%s "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(event), *candidates.params, event["event_id"]))
        assert route(candidates, [(1, anchor)])[0] == {}


def test_neighbor_event_cap_preserves_the_stronger_seed_before_older_sources(candidates):
    weak = candidates.add("An older topic anchor.", conversation="weak")
    candidates.add("An older adjacent reply.", conversation="weak")
    before = candidates.add("Twenty minutes works.", conversation="strong")
    strong = candidates.add("The writing class schedule.", conversation="strong")
    after = candidates.add("After lunch is better.", conversation="strong")
    found, truncated = route(candidates, [(0.9, strong), (0.2, weak)], event_limit=2)
    assert found == {key(before): 0.9, key(after): 0.9}
    assert truncated


def test_assertion_scan_limit_prefers_strong_seed_over_lower_identifier_weak_assertions(candidates):
    weak = candidates.add("An older topic anchor.", conversation="weak")
    weak_reply = candidates.add("An older adjacent reply.", conversation="weak")
    for index in range(5):
        derived(candidates, weak_reply, memory_id=f"aaa-weak-{index}")
    strong = candidates.add("The writing class schedule.", conversation="strong")
    reply = candidates.add("Twenty minutes works.", conversation="strong")
    found, truncated = route(candidates, [(0.9, strong), (0.2, weak)], scan_limit=2)
    assert key(reply) in found and found[key(reply)] == 0.9
    assert len(found) == 2 and truncated


def test_seed_cap_deduplicates_versions_and_source_cap_deduplicates_references(candidates):
    strong = candidates.add("The writing class schedule.", conversation="strong")
    reply = candidates.add("Twenty minutes works.", conversation="strong")
    same_source = derived(candidates, strong)
    weak = candidates.add("Another topic.", conversation="weak")
    candidates.add("Another reply.", conversation="weak")
    found, truncated = route(candidates, [(0.9, strong), (0.8, strong), (0.7, same_source)], event_limit=1)
    assert found == {key(reply): 0.9} and not truncated
    found, truncated = route(candidates, [(0.9, strong), (0.8, strong), (0.2, weak)], seed_limit=1)
    assert found == {key(reply): 0.9} and truncated


def test_capped_direct_seeds_cannot_self_boost_or_consume_neighbor_capacity(candidates):
    anchor = candidates.add("The writing class schedule.")
    direct = candidates.add("Tuesday works.")
    contextual = derived(candidates, direct, memory_id="zzz-contextual-detail")
    found, truncated = route(candidates, [(0.9, anchor), (0.2, direct)], seed_limit=1, scan_limit=1)
    assert found == {key(contextual): 0.9}
    assert key(direct) not in found
    assert truncated
