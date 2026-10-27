import copy
import json
from types import SimpleNamespace

import pytest
from test_cognition import event

from membrain import cognition
from membrain.belief_store import assertion_fingerprint
from membrain.helpers import DomainError, digest
from membrain.history_attention import (
    POLICY,
    admit,
    attention_summary,
    model_manifest,
    sanitize_attention_summary,
)
from membrain.history_semantics import MANIFEST_VERSION, validate_manifest
from membrain.learning_packages import discover_manifest
from membrain.local_semantics import HISTORY_INTEGRATION_MODE, bounded_verification_context
from membrain.semantic import EPISODE_SOURCE_VIEW_INPUT_VERSION, SemanticPolicy


def memory(text, sequence, **changes):
    value = cognition.extract_event(event(text, sequence))[0]
    value.update(changes)
    return value


def manifest(current, memories, *, truncated=False):
    refs = [{"event_id": current["event_id"], "revision": current["revision"],
             "content_sha256": current["content_sha256"]}]
    fingerprint = digest(refs)
    endpoints = [{"memory_id": item["memory_id"], "revision": item["revision"],
                  "content_sha256": assertion_fingerprint(item)} for item in memories]
    return {"version": MANIFEST_VERSION, "input_fingerprint": fingerprint, "input_refs": refs,
            "through_sequence": current["sequence"], "unresolved_discovery": truncated,
            "discovery_limitations": ["candidate_scan_limit"] if truncated else [], "objectives": [
                {"objective_id": "comparison-" + digest([MANIFEST_VERSION, fingerprint, endpoint]),
                 "target_memory_version": endpoint} for endpoint in endpoints]}


def selected_ids(value):
    return {item["target_memory_version"]["memory_id"] for item in value["objectives"]}


def attention(current, memories, **changes):
    result = admit(manifest(current, memories, **changes), memories, [current])
    validate_manifest(result)
    return result


def test_one_hundred_templated_origins_create_only_three_admitted_objectives():
    memories = [memory(f"Daily note {index}: sketch label Cloud{index} is on page {index}.", index)
                for index in range(1, 101)]
    current = event("Daily note 101: music label Cloud101 is on page 101.", 101)
    original = copy.deepcopy(memories)
    result = attention(current, memories)
    assert len(result["objectives"]) == 3
    assert result["unresolved_discovery"] is False
    summary = attention_summary(result)
    assert summary == {
        "version": POLICY, "limit": 3, "candidate_count": 100, "admitted_count": 3,
        "not_selected_count": 97, "focus_count": 2, "exploration_count": 1,
        "source_origin_count": 100, "retrieval_truncated": False, "search_exhaustive": False,
        "discovery_limitations": [],
    }
    assert len(result["attention"]["candidates"]) == 100
    assert memories == original
    assert result == attention(current, list(reversed(memories)))
    assert len({item["support_fingerprint"] for item in result["attention"]["candidates"]
                if item["basis"] != "not_selected"}) == 3


@pytest.mark.parametrize("text,cue", [
    ("The Zephyrglass access code is stored in the blue notebook.", "Where is the Zephyrglass access code?"),
    ("小时候就读的学校叫纸湾小学。", "纸湾小学的学校信息。"),
    ("Project Quasar has a Friday release date.", "Correction: Project Quasar releases Monday, replacing Friday."),
])
def test_rare_cues_and_meaningful_corrections_remain_focus_candidates(text, cue):
    relevant = memory(text, 1)
    distractions = [memory(f"Daily note {index}: sketch label Cloud{index} is on page {index}.", index)
                    for index in range(2, 102)]
    result = attention(event(cue, 200), [*distractions, relevant])
    assert relevant["memory_id"] in selected_ids(result)
    selected = next(item for item in result["attention"]["candidates"]
                    if item["target_memory_version"]["memory_id"] == relevant["memory_id"])
    assert selected["basis"] == "focus"
    assert selected["target_memory_version"]["content_sha256"] == assertion_fingerprint(relevant)


def test_literal_semantic_duplicates_do_not_count_as_independent_origin_frequency():
    original = memory("The Quasar launch is Friday.", 1)
    duplicate = copy.deepcopy(original)
    duplicate.update(memory_id="semantic-duplicate", derivation={**duplicate["derivation"], "method": "aggregation"})
    other = memory("The Quasar review is Thursday.", 2)
    current = event("The Quasar launch and review dates changed.", 4)
    plain = attention(current, [original, other])
    repeated = attention(current, [original, duplicate, other])
    assert repeated["attention"]["source_origin_count"] == 2
    plain_scores = {item["target_memory_version"]["memory_id"]: item["score"]
                    for item in plain["attention"]["candidates"]}
    repeated_scores = {item["target_memory_version"]["memory_id"]: item["score"]
                       for item in repeated["attention"]["candidates"]}
    assert all(repeated_scores[key] == score for key, score in plain_scores.items())
    focused = [item for item in repeated["attention"]["candidates"] if item["basis"] == "focus"]
    assert len({item["support_fingerprint"] for item in focused}) == 2
    assert len(repeated["objectives"]) == 3


def test_assistant_added_template_matches_are_not_focus_cues_but_remain_explorable():
    memories = [memory("The school is Paper Bay Primary.", 1), memory("Ornamental courtesy protocol.", 2)]
    user = event("Paper Bay Primary is my school.", 3)
    assistant = event("Ornamental courtesy protocol.", 4, "assistant")
    value = manifest(user, memories)
    value["input_refs"].append({"event_id": assistant["event_id"], "revision": assistant["revision"],
                                "content_sha256": assistant["content_sha256"]})
    value["input_fingerprint"] = digest(value["input_refs"])
    value["through_sequence"] = 4
    for objective in value["objectives"]:
        objective["objective_id"] = "comparison-" + digest([
            MANIFEST_VERSION, value["input_fingerprint"], objective["target_memory_version"]])
    result = admit(value, memories, [user, assistant])
    validate_manifest(result)
    chosen = {item["target_memory_version"]["memory_id"]: item for item in result["attention"]["candidates"]}
    assert chosen[memories[0]["memory_id"]]["basis"] == "focus"
    assert chosen[memories[1]["memory_id"]]["basis"] == "exploration"
    assert chosen[memories[1]["memory_id"]]["primary_shared_term_count"] == 0
    assert chosen[memories[1]["memory_id"]]["shared_term_count"] > 0
    assert result["input_refs"] == value["input_refs"]


def test_historical_assistant_promise_can_focus_when_the_user_names_it():
    promise = cognition.extract_event(event("I will review Project Quasar tomorrow.", 1, "assistant"))[0]
    current = event("What did you promise about reviewing Project Quasar?", 5)
    result = attention(current, [promise, memory("The weather is sunny.", 2)])
    candidate = next(item for item in result["attention"]["candidates"]
                     if item["target_memory_version"]["memory_id"] == promise["memory_id"])
    assert candidate["basis"] == "focus"
    assert candidate["target_memory_version"]["content_sha256"] == assertion_fingerprint(promise)


def test_distinct_claims_sharing_the_exact_source_are_not_merged_or_declared_assessed():
    base = memory("Quasar launch is Friday and Quasar review is Thursday.", 1)
    launch = copy.deepcopy(base)
    launch.update(memory_id="launch", statement="Quasar launch is Friday.")
    review = copy.deepcopy(base)
    review.update(memory_id="review", statement="Quasar review is Thursday.")
    result = attention(event("The Quasar launch and review dates changed.", 3), [launch, review])
    assert selected_ids(result) == {"launch", "review"}
    assert result["attention"]["admitted_count"] == 2
    assert len({item["support_fingerprint"] for item in result["attention"]["candidates"]}) == 1
    assert all("assessed" not in item and "no_change" not in item.values()
               for item in result["attention"]["candidates"])


@pytest.mark.parametrize("text,cue", [("红色的桥。", "红桥"),
                                     ("The user requires a quiet study environment.", "备考")])
def test_character_fallback_and_source_bridged_candidates_keep_an_exploration_route(text, cue):
    target = memory(text, 1)
    result = attention(event(cue, 2), [target])
    assert selected_ids(result) == {target["memory_id"]}
    assert result["attention"]["focus_count"] == 0
    assert result["attention"]["exploration_count"] == 1


def test_exploration_changes_with_fresh_input_identity_without_changing_a_frozen_choice():
    memories = [memory(f"Daily sketch note number {index}.", index) for index in range(1, 51)]
    explored = set()
    for sequence in range(100, 110):
        current = event("Daily sketch note.", sequence)
        result = attention(current, memories)
        assert result == attention(current, memories)
        explored.update(item["target_memory_version"]["memory_id"] for item in result["attention"]["candidates"]
                        if item["basis"] == "exploration")
    assert len(explored) > 1


def test_retrieval_truncation_stays_visible_without_authorizing_automatic_rediscovery():
    memories = [memory(f"A planning note number {index}.", index) for index in range(1, 8)]
    result = attention(event("A planning note.", 20), memories, truncated=True)
    assert result["unresolved_discovery"] is False
    assert attention_summary(result)["retrieval_truncated"] is True
    assert attention_summary(result)["search_exhaustive"] is False
    assert result["discovery_limitations"] == ["candidate_scan_limit"]


def test_model_projection_omits_nonadmitted_endpoints_and_keeps_exact_selected_sources():
    memories = [memory(f"A private planning note number {index}.", index) for index in range(1, 30)]
    current = event("A private planning note.", 100)
    result = attention(current, memories)
    projected = model_manifest(result)
    assert projected["objectives"] == result["objectives"]
    assert projected["input_refs"] == result["input_refs"]
    assert projected["attention"] == attention_summary(result)
    assert "candidates" not in projected["attention"] and "selection_seed" not in projected["attention"]
    serialized = json.dumps(projected)
    assert all(item["memory_id"] not in serialized for item in memories if item["memory_id"] not in selected_ids(result))
    assert all(item["statement"] not in json.dumps(result["attention"]) for item in memories)
    context = bounded_verification_context(HISTORY_INTEGRATION_MODE, [current], memories[:1], manifest=result)
    assert context["integration_manifest"] == projected
    assert context["historical_context"]["sources"] == [current]
    assert context["historical_context"]["assertions"] == memories[:1]
    assert "candidates" in result["attention"]


def test_legacy_manifest_and_verifier_wire_remain_identical():
    current, memories = event("A planning note.", 3), [memory("An earlier planning note.", 1)]
    value = manifest(current, memories, truncated=True)
    validate_manifest(value)
    assert attention_summary(value) is None
    assert model_manifest(value) == value
    assert model_manifest(value) is not value
    assert bounded_verification_context(HISTORY_INTEGRATION_MODE, [current], memories, manifest=value) == {
        "integration_manifest": value,
        "historical_context": {"sources": [current], "assertions": memories, "belief_relations": []},
        "local_input_refs": value["input_refs"],
    }


@pytest.mark.parametrize("change", ["bool", "negative", "extra", "unknown", "null", "count", "coverage", "nested"])
def test_public_summary_revalidation_rejects_malformed_or_private_fields(change):
    result = attention(event("A planning note.", 3), [memory("An earlier planning note.", 1)])
    summary = attention_summary(result)
    assert sanitize_attention_summary(summary) == summary
    if change == "bool":
        summary["candidate_count"] = True
    elif change == "negative":
        summary["not_selected_count"] = -1
    elif change == "extra":
        summary["candidates"] = result["attention"]["candidates"]
    elif change == "unknown":
        summary["version"] = "future"
    elif change == "null":
        summary = None
    elif change == "count":
        summary["admitted_count"] = 3
    elif change == "coverage":
        summary["search_exhaustive"] = True
    else:
        summary["discovery_limitations"] = [{"private": "source text"}]
    assert sanitize_attention_summary(summary) is None


@pytest.mark.parametrize("change", [
    "unknown_version", "bool_count", "oversized_count", "bad_selected_count", "bad_seed", "hidden_candidate",
    "selected_omission", "unselected_admitted", "bad_fingerprint", "unknown_field", "search_complete",
    "rediscovery", "duplicate_candidate", "control_identifier", "zero_revision",
])
def test_corrupt_attention_audit_fails_closed(change):
    result = attention(event("Planning notes.", 10), [memory(f"Planning note {index}.", index) for index in range(1, 7)])
    audit = result["attention"]
    if change == "unknown_version":
        audit["version"] = "unknown"
    elif change == "bool_count":
        audit["candidate_count"] = True
    elif change == "oversized_count":
        audit["candidate_count"] = 129
    elif change == "bad_selected_count":
        audit["admitted_count"] = 0
    elif change == "bad_seed":
        audit["selection_seed"] = "0" * 64
    elif change == "hidden_candidate":
        audit["candidates"].pop()
    elif change == "selected_omission":
        next(item for item in audit["candidates"] if item["basis"] == "focus")["basis"] = "not_selected"
    elif change == "unselected_admitted":
        candidate = next(item for item in audit["candidates"] if item["basis"] == "not_selected")
        result["objectives"][0] = {key: copy.deepcopy(candidate[key]) for key in ("objective_id", "target_memory_version")}
    elif change == "bad_fingerprint":
        audit["candidates"][0]["support_fingerprint"] = "x"
    elif change == "unknown_field":
        audit["extra"] = "private content"
    elif change == "search_complete":
        audit["search_exhaustive"] = True
    elif change == "rediscovery":
        result["unresolved_discovery"] = True
    elif change == "duplicate_candidate":
        audit["candidates"][1] = copy.deepcopy(audit["candidates"][0])
    elif change == "control_identifier":
        audit["candidates"][0]["target_memory_version"]["memory_id"] = "bad\nidentifier"
    else:
        audit["candidates"][0]["target_memory_version"]["revision"] = 0
    with pytest.raises((ValueError, DomainError)):
        validate_manifest(result)


def test_split_package_keeps_every_admitted_objective_without_promoting_unselected_candidates():
    result = attention(event("Planning notes.", 10), [memory(f"Planning note {index}.", index) for index in range(1, 7)])
    selected = {**copy.deepcopy(result), "objectives": result["objectives"][:1]}
    remaining = {**copy.deepcopy(result), "objectives": result["objectives"][1:]}
    validate_manifest(selected)
    validate_manifest(remaining)
    assert selected_ids(selected) | selected_ids(remaining) == selected_ids(result)
    assert attention_summary(selected) == attention_summary(remaining) == attention_summary(result)


def test_discovery_applies_attention_only_with_an_explicit_policy_and_leaves_broad_candidates_unchanged(monkeypatch):
    current = event("Daily planning notes.", 200)
    memories = [memory(f"Daily planning note {index}.", index) for index in range(1, 101)]
    calls = []

    def candidates(cue, **kwargs):
        calls.append((cue, kwargs))
        return memories

    selector = SimpleNamespace(scope=current["scope"], reasons=set(), candidates=candidates,
                               source_package=lambda ids: {current["event_id"]: current}, bind=lambda value: value)
    monkeypatch.setattr("membrain.learning_packages._Selector", lambda *args, **kwargs: selector)
    kwargs = {"through_sequence": 200, "required_event_refs": [{"event_id": current["event_id"], "revision": 1}],
              "policy": SemanticPolicy(max_assertions=128), "input_contract_version": EPISODE_SOURCE_VIEW_INPUT_VERSION}
    old = discover_manifest(None, None, None, None, {}, **kwargs)
    new = discover_manifest(None, None, None, None, {}, **kwargs, attention_policy=POLICY)
    assert len(old["objectives"]) == 32 and old["unresolved_discovery"] is True and "attention" not in old
    assert len(new["objectives"]) == 3 and new["attention"]["candidate_count"] == 100
    assert new["attention"]["retrieval_truncated"] is False
    assert calls[0] == calls[1]
    assert calls[1][1]["independent_of_event_ids"] == (current["event_id"],)
    validate_manifest(new)
    with pytest.raises(DomainError, match="Historical attention policy"):
        discover_manifest(None, None, None, None, {}, **kwargs, attention_policy="unknown")
    assert len(calls) == 2
