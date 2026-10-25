import copy
import json

import pytest
from psycopg.types.json import Jsonb
from test_candidates import candidates as candidates
from test_candidates import key

from agent_memory.lexical_candidates import MAX_QUERY_TERMS, lexical_rows
from agent_memory.service import _vector


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return []


def test_word_channel_is_bounded_and_uses_bound_parameters():
    conn = RecordingConnection()
    query = " ".join(f"word{number:04d}" for number in range(MAX_QUERY_TERMS + 5))
    rows, truncated = lexical_rows(conn, ("tenant", "owner", "brain"), {}, query, limit=4)
    assert not rows and truncated and len(conn.calls) == 2
    sql, params = conn.calls[-1]
    assert "word0000" not in sql
    assert params[-3].count(" | ") == MAX_QUERY_TERMS - 1
    assert params[-1] == 5


@pytest.mark.parametrize("text,expected_calls,truncated", [
    ("银杏树旁的小学", 1, False), ("x" * 257, 1, True),
    ("label' | !('private'); --", 2, False),
])
def test_unusable_or_non_word_queries_retain_bounded_hash_fallback(text, expected_calls, truncated):
    conn = RecordingConnection()
    assert lexical_rows(conn, ("tenant", "owner", "brain"), {}, text, limit=4) == ([], truncated)
    assert len(conn.calls) == expected_calls
    if expected_calls == 2:
        assert conn.calls[-1][1][-3] == "'label' | 'private'"


@pytest.mark.integration
def test_exact_label_survives_real_hash_collisions_beyond_candidate_limit(candidates):
    query = "zeta00940"
    wanted = candidates.add("My copper inventory label zeta00940 refers to shelf 6.")
    vector = json.loads(_vector(query))

    def similarity(statement):
        return sum(a * b for a, b in zip(vector, json.loads(_vector(statement)), strict=True))

    threshold = similarity(wanted["statement"])
    distractors = []
    for number in range(10000):
        text = f"My copper inventory label unrelated{number:05d} refers to shelf {number % 17 + 1}."
        if similarity("user user (direct_statement): " + text) > threshold + 1e-6:
            distractors.append(text)
            if len(distractors) == 20:
                break
    assert len(distractors) == 20
    for text in distractors:
        candidates.add(text)
    _, state = candidates.query(query, budget=2048)
    assert state["candidate_versions"] == [key(wanted)]
    assert state["source_truncated"]


@pytest.mark.integration
def test_scope_projections_follow_document_changes_and_do_not_grant_visibility(candidates):
    wanted = candidates.add("The amber notebook is on shelf four.")
    changed = copy.deepcopy(wanted)
    changed["scope"].update(visibility="conversation", conversation_id="different", branch_id="private")
    with candidates.database.transaction(candidates.principal) as conn:
        row = conn.execute(
            "UPDATE memory_assertions SET document=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s RETURNING scope_visibility,scope_conversation_id,scope_branch_id",
            (Jsonb(changed), *candidates.params, wanted["memory_id"]),
        ).fetchone()
    assert row == {"scope_visibility": "conversation", "scope_conversation_id": "different", "scope_branch_id": "private"}
    assert candidates.query("amber notebook")[1]["candidate_versions"] == []
    with candidates.database.transaction(candidates.principal) as conn:
        row = conn.execute(
            "UPDATE memory_assertions SET document=NULL WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s RETURNING scope_visibility,scope_conversation_id,scope_branch_id",
            (*candidates.params, wanted["memory_id"]),
        ).fetchone()
    assert all(value is None for value in row.values())


@pytest.mark.integration
def test_learning_word_channel_preserves_frozen_source_sequence(candidates):
    older = candidates.add("The amber notebook is on shelf four.")
    candidates.add("The turquoise lantern belongs in the attic.")
    with candidates.database.transaction(candidates.principal) as conn:
        rows, _ = lexical_rows(conn, candidates.params, candidates.target, "amber turquoise", limit=16,
                               through_sequence=1)
    assert [key(row["document"]) for row in rows] == [key(older)]
