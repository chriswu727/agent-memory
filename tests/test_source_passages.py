import copy

import pytest
from psycopg.types.json import Jsonb
from test_candidates import candidates as candidates
from test_candidates import key
from test_longitudinal_repairs import BLACKOUT

from membrain import cognition
from membrain.source_passages import SOURCE_PASSAGE_VERSION, source_passage_candidates

pytestmark = pytest.mark.integration


def captures(candidates, text):
    first = candidates.add(text)
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        source = candidates.service._events(
            conn, candidates.principal, brain, event_ids=[first["evidence_refs"][0]["event_id"]]
        )[0]
        memories = cognition.extract_event(source)
        for memory in memories[1:]:
            candidates.service._publish(conn, candidates.principal, brain, memory)
    return source, memories


def test_source_adjacent_meaning_is_recovered_without_inventing_a_semantic_anchor(candidates):
    _, memories = captures(
        candidates, BLACKOUT + " It made a scary dark house feel safe. A separate topic follows."
    )
    assert cognition._lexical_score("Grandma lantern", memories[1]["statement"]) == 0
    _, state = candidates.query("Grandma lantern")
    assert key(memories[0]) in state["candidate_versions"]
    assert key(memories[1]) in state["candidate_versions"]
    assert key(memories[2]) not in state["candidate_versions"]
    assert any(
        signal["method"] == "association"
        and signal["model_or_index_version"] == SOURCE_PASSAGE_VERSION
        for signal in state["rank_signals"][key(memories[1])]
    )


@pytest.mark.parametrize(
    "damage", ["source_hash", "seed_span", "future", "scope", "suppressed", "deleted"]
)
def test_context_expansion_retains_source_identity_scope_cutoff_and_deletion_guards(
    candidates, damage
):
    source, memories = captures(candidates, BLACKOUT + " It made a scary dark house feel safe.")
    seed = copy.deepcopy(memories[0])
    through = source["sequence"]
    if damage == "source_hash":
        seed["evidence_refs"][0]["source_content_sha256"] = "0" * 64
    elif damage == "seed_span":
        seed["evidence_refs"][0]["span"]["end"] -= 1
    elif damage == "future":
        through -= 1
    with candidates.database.transaction(candidates.principal) as conn:
        brain = candidates.service._brain(conn, candidates.principal, candidates.brain_id)
        if damage == "scope":
            damaged = copy.deepcopy(source)
            damaged["scope"].update(visibility="conversation", conversation_id="hidden")
            conn.execute(
                "UPDATE memory_events SET document=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb(damaged), *candidates.params, source["event_id"]),
            )
        elif damage in {"suppressed", "deleted"}:
            conn.execute(
                "UPDATE memory_assertions SET "
                + damage
                + "=true WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (*candidates.params, memories[1]["memory_id"]),
            )
        found, _ = source_passage_candidates(
            conn,
            candidates.service,
            candidates.principal,
            brain,
            candidates.target,
            [(1, seed)],
            through_sequence=through,
        )
    assert found == []


def test_long_source_finds_the_exact_adjacent_unit_without_scanning_all_of_its_captures(candidates):
    prefix = " ".join(f"Unrelated observation number {i}." for i in range(70))
    _, memories = captures(
        candidates, prefix + " " + BLACKOUT + " It made a scary dark house feel safe."
    )
    _, state = candidates.query("Grandma lantern")
    assert key(memories[-1]) in state["candidate_versions"]
    assert not state["source_truncated"]
