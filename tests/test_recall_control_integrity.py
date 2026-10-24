import copy
import json

import pytest
from test_belief_recall import recall, scenario
from test_recall_control import decision, packet, run

from agent_memory.belief_store import relation_ref
from agent_memory.helpers import DomainError


def related_packet():
    sources, memories, current = scenario()
    recalled = recall(sources, memories, current)
    initial = packet(*memories)
    initial["recall_results"][0]["items"] = recalled["items"]
    initial["recall_results"][0]["belief_relations"] = recalled["belief_relations"]
    return initial, memories


@pytest.mark.parametrize("damage,code", [
    ("missing_table", "dependency_unavailable"),
    ("missing_version", "dependency_unavailable"),
    ("changed_digest", "context_stale"),
    ("changed_document", "context_stale"),
    ("conflicting_document", "context_stale"),
    ("conflicting_assessment", "context_stale"),
    ("conflicting_inline", "context_stale"),
])
def test_incomplete_or_inconsistent_relation_certificates_stop_before_model_reservation(damage, code):
    initial, _ = related_packet()
    result = initial["recall_results"][0]
    assessment = result["belief_relations"][0]
    resolution = result["items"][0]["belief_resolution"]
    if damage == "missing_table":
        result.pop("belief_relations")
    elif damage == "missing_version":
        resolution["relation_refs"][0]["revision"] += 1
    elif damage == "changed_digest":
        resolution["relation_refs"][0]["content_sha256"] = "0" * 64
    elif damage == "changed_document":
        assessment["relation"]["kind"] = "contradicts"
    else:
        changed = copy.deepcopy(assessment)
        if damage == "conflicting_assessment":
            changed["applicability"]["value"] = "false"
        else:
            changed["relation"]["kind"] = "contradicts"
        if damage == "conflicting_inline":
            resolution["relations"] = [changed]
        else:
            result["belief_relations"].append(changed)

    def unexpected_call(*_, **__):
        pytest.fail("Invalid relation certificates cannot reach or reserve a model call")

    with pytest.raises(DomainError) as error:
        run(unexpected_call, initial=initial, reserve=unexpected_call)
    assert error.value.code == code


@pytest.mark.parametrize("form", ["shared", "identical_duplicate", "historical_inline"])
def test_exact_relation_certificate_remains_visible_frozen_and_source_bound(form):
    initial, memories = related_packet()
    result = initial["recall_results"][0]
    assessment = copy.deepcopy(result["belief_relations"][0])
    if form == "identical_duplicate":
        result["belief_relations"].append(copy.deepcopy(assessment))
    elif form == "historical_inline":
        for item in result["items"]:
            item["belief_resolution"]["relations"] = [copy.deepcopy(assessment)]
            item["belief_resolution"].pop("relation_refs")
        result.pop("belief_relations")
    reservations = []

    def reason(request):
        shown = json.loads(request.messages[1]["content"])
        assert len(shown["relations"]) == 1
        assert all(item["belief_resolution"]["complete"] for item in shown["candidates"])
        assert all(len(item["belief_resolution"]["relation_refs"]) == 1 for item in shown["candidates"])
        return decision(selected=[memories[0]["memory_id"]])

    plan = run(reason, initial=initial, reserve=lambda **values: reservations.append(values))
    assert plan["outcome"] == "sufficient"
    assert plan["belief_relation_refs"] == [relation_ref(assessment["relation"])]
    assert {value["event_id"] for value in reservations[0]["source_refs"]} == {"recent", "event-1", "event-2"}
    assert plan["observed_event_refs"] == reservations[0]["source_refs"]
