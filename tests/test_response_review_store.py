import hashlib
import json

import pytest
from psycopg.types.json import Jsonb
from test_turns import turns as turns

from agent_memory.helpers import DomainError, digest, uid

pytestmark = pytest.mark.integration
TEXT = "The latest remembered arrangement is therefore applicable now."
POLICY = digest("memory review policy")


def prepared_review(turns, *, policy=POLICY):
    turns.begin()
    turns.claim()
    turns.advance("captured", input_event_refs=[turns.observe(source_key="review-input")])
    generation = turns.store.status(turns.principal, turns.brain)["dependency_generation"]
    turns.advance("prepared", context_event_refs=[], context_fingerprint=digest("context"),
                  context_generation=generation, working_revision=0, context_tokens=50,
                  response_review_policy=policy)
    turns.advance("generating")
    turns.record_output(TEXT)
    return reservation(turns)


def reservation(turns):
    return {"lease_token": turns.token, "expected_revision": turns.row["revision"],
            "policy_fingerprint": POLICY, "request_fingerprint": digest("exact review request"),
            "draft_sha256": hashlib.sha256(TEXT.encode()).hexdigest()}


def reserve(turns):
    turns.row = turns.call("reserve_response_review", **reservation(turns))
    return turns.row


def completion(turns, status="approved"):
    return {"lease_token": turns.token, "expected_revision": turns.row["revision"],
            "request_fingerprint": digest("exact review request"), "status": status,
            "issue_codes": [] if status == "approved" else ["applicability_override"]}


def approve(turns):
    reserve(turns)
    turns.row = turns.call("complete_response_review", **completion(turns))
    return turns.row


def test_configured_policy_blocks_direct_delivery_without_review_and_is_immutable(turns):
    prepared_review(turns)
    with pytest.raises(DomainError) as error:
        turns.advance("delivering")
    assert error.value.code == "response_review_required"
    with pytest.raises(DomainError) as error:
        turns.advance("delivering", response_review_policy=digest("disabled replacement"))
    assert error.value.code == "validation_failed"
    assert turns.call("get")["response_review_policy"] == POLICY
    assert turns.call("sources")["output_events"][0]["observed"]["delivery_state"] == "draft"


def test_legacy_disabled_review_preserves_delivery_and_cannot_reserve(turns):
    params = prepared_review(turns, policy=None)
    assert turns.row["response_review_policy"] is None
    with pytest.raises(DomainError) as error:
        turns.call("reserve_response_review", **params)
    assert error.value.code == "invalid_evidence"
    assert turns.advance("delivering")["stage"] == "delivering"


def test_review_reservation_and_completion_are_exact_idempotent_and_content_free(turns):
    params = prepared_review(turns)
    first = turns.call("reserve_response_review", **params)
    assert first["response_review"]["status"] == "pending"
    assert first["response_review_calls"] == 1
    assert turns.call("reserve_response_review", **params) == first
    turns.row = first
    finished_params = completion(turns)
    completed = turns.call("complete_response_review", **finished_params)
    assert turns.call("complete_response_review", **finished_params) == completed
    assert completed["response_review"]["status"] == "approved"
    assert completed["response_review"]["not_a_truth_certificate"] is True
    assert TEXT not in json.dumps(completed)
    status = turns.store.status(turns.principal, turns.brain)
    assert status["response_review_counts"] == {"approved": 1}
    assert status["response_review_calls"] == 1
    turns.row = completed
    assert turns.advance("delivering")["stage"] == "delivering"


def test_pending_reservation_after_lease_recovery_cannot_be_repeated_or_delivered(turns):
    prepared_review(turns)
    reserve(turns)
    turns.expire()
    turns.claim()
    for method, params in (("reserve_response_review", reservation(turns)), ("advance", {
        "lease_token": turns.token, "expected_revision": turns.row["revision"], "stage": "delivering",
    })):
        with pytest.raises(DomainError) as error:
            turns.call(method, **params)
        assert error.value.code == "response_review_uncertain"
    assert turns.call("get")["response_review_calls"] == 1


@pytest.mark.parametrize("status", ["rejected", "inconclusive"])
def test_nonapproval_retains_draft_and_cannot_be_replaced_by_approval(turns, status):
    prepared_review(turns)
    reserve(turns)
    turns.row = turns.call("complete_response_review", **completion(turns, status))
    with pytest.raises(DomainError) as error:
        turns.advance("delivering")
    assert error.value.code == "response_review_blocked"
    with pytest.raises(DomainError) as error:
        turns.call("complete_response_review", **completion(turns))
    assert error.value.code == "response_review_blocked"
    source = turns.call("sources")["output_events"][0]["observed"]
    assert source["delivery_state"] == "draft" and source["content"]["text"] == TEXT


@pytest.mark.parametrize("field", ["policy_fingerprint", "draft_sha256"])
def test_reservation_must_match_prepared_policy_and_actual_draft_bytes(turns, field):
    params = prepared_review(turns)
    params[field] = digest("different")
    with pytest.raises(DomainError) as error:
        turns.call("reserve_response_review", **params)
    assert error.value.code == "invalid_evidence"
    assert turns.call("get")["response_review_calls"] == 0


def test_completion_requires_exact_reservation_and_bounded_codes(turns):
    prepared_review(turns)
    reserve(turns)
    wrong = {**completion(turns), "request_fingerprint": digest("different review")}
    with pytest.raises(DomainError) as error:
        turns.call("complete_response_review", **wrong)
    assert error.value.code == "invalid_evidence"
    for codes in (["private_source_text"], ["applicability_override"], ["review_unavailable"] * 2):
        with pytest.raises(DomainError) as error:
            turns.call("complete_response_review", **{**completion(turns), "issue_codes": codes})
        assert error.value.code == "validation_failed"
    assert turns.call("get")["response_review"]["status"] == "pending"


@pytest.mark.parametrize("change", ["generation", "working", "delete", "correct"])
@pytest.mark.parametrize("phase", ["reserve", "complete", "deliver"])
def test_changed_sources_or_context_block_each_review_boundary(turns, change, phase):
    prepared_review(turns)
    if phase in {"complete", "deliver"}:
        reserve(turns)
    if phase == "deliver":
        turns.row = turns.call("complete_response_review", **completion(turns))
    if change == "working":
        turns.action("commit", {"turn_id": uid(), "idempotency_key": uid(), "expected_working_revision": 0,
                                "delivered_event_ids": [], "proposed_items": [], "proposed_open_loops": [],
                                "assertion_proposals": []})
    elif change == "delete":
        source = turns.row["input_event_refs"][0]
        turns.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [source["event_id"]]},
                                "mode": "erase_content", "expected_scope_generation": 1})
    elif change == "correct":
        turns.observe("Corrected original input.", source_key="review-input", revision=2)
    else:
        with turns.database.transaction(turns.principal) as conn:
            conn.execute("UPDATE memory_brains SET dependency_generation=dependency_generation+1 "
                         "WHERE tenant_id=%s AND id=%s", (turns.principal.tenant_id, turns.brain))
    with pytest.raises(DomainError) as error:
        if phase == "reserve":
            reserve(turns)
        elif phase == "complete":
            turns.call("complete_response_review", **completion(turns))
        else:
            turns.advance("delivering")
    assert error.value.code in {"context_stale", "source_unavailable", "version_conflict"}


@pytest.mark.parametrize("field", ["draft_sha256", "context_fingerprint", "policy_fingerprint", "context_generation"])
def test_approved_certificate_cannot_authorize_different_draft_context_or_policy(turns, field):
    prepared_review(turns)
    approve(turns)
    damaged = {**turns.row["response_review"], field: 999 if field == "context_generation" else digest("different")}
    with turns.database.transaction(turns.principal) as conn:
        conn.execute("UPDATE memory_runtime_turns SET response_review=%s WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
                     (Jsonb(damaged), turns.principal.tenant_id, turns.brain, turns.turn_id))
    with pytest.raises(DomainError) as error:
        turns.advance("delivering")
    assert error.value.code == "context_stale"


def test_reservation_is_rolled_back_if_audit_journal_write_fails(turns, monkeypatch):
    prepared_review(turns)
    before = turns.call("get")

    def fail(*_):
        raise DomainError("dependency_unavailable", "Synthetic audit failure")

    monkeypatch.setattr(turns.store, "_record", fail)
    with pytest.raises(DomainError, match="Synthetic audit failure"):
        reserve(turns)
    assert turns.call("get") == before


def test_approved_review_survives_lease_recovery_without_another_reservation(turns):
    prepared_review(turns)
    original = approve(turns)["response_review"]
    turns.expire()
    turns.claim()
    result = turns.advance("delivering")
    assert result["response_review"] == original
    assert result["response_review_calls"] == 1
