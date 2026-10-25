import copy

import pytest
from jsonschema import Draft202012Validator

from agent_memory.history_semantics import (
    HISTORY_ABSTENTIONS,
    LINK_DERIVED_INPUT_VERSION,
    derive_link_decisions,
    integration_coverage,
    validate_decisions,
)
from agent_memory.local_semantics import (
    HISTORY_INTEGRATION_MODE,
    LINK_DERIVED_INSTRUCTIONS,
    LOCAL_OBSERVATION_MODE,
    bounded_proposal_schema,
)
from agent_memory.semantic import PROPOSAL_SCHEMA


def manifest(count=3):
    return {"objectives": [{"objective_id": f"objective-{index}", "target_memory_version": {
        "memory_id": f"memory-{index}", "revision": 1, "content_sha256": "a" * 64,
    }} for index in range(count)]}


def link(identifier, *indices):
    return {"proposal_id": identifier,
            "target_memory_versions": [{"memory_id": f"memory-{index}", "revision": 1} for index in indices]}


def abstention(index, reason="insufficient_shared_meaning"):
    return {"objective_id": f"objective-{index}", "reason": reason}


def wire(*links, abstentions=()):
    return {"belief_links": list(links), "integration_abstentions": list(abstentions)}


@pytest.mark.parametrize("count", [2, 3])
def test_one_shared_relation_covers_every_exact_manifest_endpoint(count):
    data = manifest(count)
    proposed = wire(link("shared-link", *range(count)))
    decisions = derive_link_decisions(data, proposed)
    assert decisions == [{"objective_id": f"objective-{index}", "outcome": "proposed", "reason": None,
                          "link_proposal_ids": ["shared-link"]} for index in range(count)]
    assert decisions == validate_decisions(data, {"belief_links": proposed["belief_links"],
                                                 "integration_decisions": decisions})
    coverage = integration_coverage(data, decisions, [], assessed=True)
    assert coverage["assessed_count"] == count and coverage["published_count"] == 0
    assert all(item["status"] == "assessed_no_publication" for item in coverage["objectives"])


def test_overlapping_links_have_complete_deterministic_incidence_lists():
    data = manifest()
    proposed = wire(link("z-link", 0, 1), link("a-link", 1, 2))
    decisions = derive_link_decisions(data, proposed)
    assert [item["link_proposal_ids"] for item in decisions] == [["z-link"], ["a-link", "z-link"], ["a-link"]]
    assert derive_link_decisions(data, wire(*reversed(proposed["belief_links"]))) == decisions


def test_uncovered_endpoint_has_an_explicit_no_change_judgment():
    proposed = wire(link("shared-link", 0, 1), abstentions=[abstention(2, "incompatible_qualifiers")])
    decisions = derive_link_decisions(manifest(), proposed)
    assert decisions[2] == {"objective_id": "objective-2", "outcome": "no_change",
                            "reason": "incompatible_qualifiers", "link_proposal_ids": []}


@pytest.mark.parametrize("reason", HISTORY_ABSTENTIONS["items"]["properties"]["reason"]["enum"])
def test_no_links_preserves_each_explicit_non_null_reason(reason):
    decisions = derive_link_decisions(manifest(1), wire(abstentions=[abstention(0, reason)]))
    assert decisions == [{"objective_id": "objective-0", "outcome": "no_change", "reason": reason,
                          "link_proposal_ids": []}]


def test_derived_accounting_does_not_modify_model_claims_links_or_manifest():
    data = manifest(2)
    proposed = wire(link("shared-link", 0, 1))
    proposed["claims"] = [{"statement": "Unverified model assertion.", "evidence_refs": [{"unverified": True}]}]
    before = copy.deepcopy((data, proposed))
    decisions = derive_link_decisions(data, proposed)
    decisions[0]["link_proposal_ids"].append("mutated-return")
    assert (data, proposed) == before


@pytest.mark.parametrize("failure", ["covered", "missing", "duplicate", "unknown", "missing_field"])
def test_abstention_cannot_contradict_or_omit_manifest_coverage(failure):
    proposed = wire(link("shared-link", 0, 1), abstentions=[abstention(2)])
    if failure == "covered":
        proposed["integration_abstentions"].append(abstention(0, "already_represented"))
    elif failure == "missing":
        proposed["integration_abstentions"] = []
    elif failure == "duplicate":
        proposed["integration_abstentions"].append(abstention(2))
    elif failure == "unknown":
        proposed["integration_abstentions"].append(abstention(3))
    else:
        del proposed["integration_abstentions"]
    with pytest.raises(ValueError):
        derive_link_decisions(manifest(), proposed)


@pytest.mark.parametrize("value", [None, {}, "skip", [None], [abstention(0, None)],
                                   [abstention(0, "invented")], [{"objective_id": "objective-0"}],
                                   [{**abstention(0), "outcome": "no_change"}], [abstention(0)] * 4])
def test_malformed_abstention_metadata_is_rejected(value):
    with pytest.raises(ValueError):
        derive_link_decisions(manifest(1), {"belief_links": [], "integration_abstentions": value})


@pytest.mark.parametrize("failure", ["duplicate_link", "unrelated_link", "wrong_revision", "extra_target_fields",
                                     "duplicate_target", "missing_id", "missing_targets", "non_list", "boolean_revision"])
def test_invalid_or_unrelated_link_accounting_is_rejected(failure):
    proposed = wire(link("shared-link", 0, 1), abstentions=[abstention(2)])
    links = proposed["belief_links"]
    if failure == "duplicate_link":
        links.append(copy.deepcopy(links[0]))
    elif failure == "unrelated_link":
        links.append(link("unrelated", 8))
    elif failure == "wrong_revision":
        for target in links[0]["target_memory_versions"]:
            target["revision"] = 2
        proposed["integration_abstentions"] = [abstention(index) for index in range(3)]
    elif failure == "extra_target_fields":
        links[0]["target_memory_versions"][0]["unexpected"] = True
    elif failure == "duplicate_target":
        links[0]["target_memory_versions"].append(copy.deepcopy(links[0]["target_memory_versions"][0]))
    elif failure == "missing_id":
        del links[0]["proposal_id"]
    elif failure == "missing_targets":
        del links[0]["target_memory_versions"]
    elif failure == "non_list":
        proposed["belief_links"] = {}
    else:
        links[0]["target_memory_versions"][0]["revision"] = True
    with pytest.raises(ValueError):
        derive_link_decisions(manifest(), proposed)


@pytest.mark.parametrize("legacy", [[], None, [{"objective_id": "objective-0", "outcome": "proposed"}]])
def test_new_wire_rejects_mixed_legacy_decision_metadata_even_when_empty(legacy):
    proposed = wire(link("shared-link", 0, 1))
    proposed["integration_decisions"] = legacy
    with pytest.raises(ValueError):
        derive_link_decisions(manifest(2), proposed)


def test_frozen_legacy_contradiction_remains_rejected():
    data = manifest(2)
    proposed = {"belief_links": [link("shared-link", 0, 1)], "integration_decisions": [
        {"objective_id": "objective-0", "outcome": "proposed", "reason": None, "link_proposal_ids": ["shared-link"]},
        {"objective_id": "objective-1", "outcome": "no_change", "reason": "already_represented", "link_proposal_ids": []},
    ]}
    with pytest.raises(ValueError, match="outcome differs from its links"):
        validate_decisions(data, proposed)
    proposed["integration_decisions"][1].update(outcome="proposed", reason=None, link_proposal_ids=["shared-link"])
    assert validate_decisions(data, proposed) == proposed["integration_decisions"]


def test_only_new_history_wire_uses_required_abstentions_and_forbids_legacy_decisions():
    before = copy.deepcopy(PROPOSAL_SCHEMA)
    new = bounded_proposal_schema(HISTORY_INTEGRATION_MODE, PROPOSAL_SCHEMA,
                                  input_contract_version=LINK_DERIVED_INPUT_VERSION)
    old = bounded_proposal_schema(HISTORY_INTEGRATION_MODE, PROPOSAL_SCHEMA)
    local = bounded_proposal_schema(LOCAL_OBSERVATION_MODE, PROPOSAL_SCHEMA,
                                    input_contract_version=LINK_DERIVED_INPUT_VERSION)
    assert PROPOSAL_SCHEMA == before
    assert "integration_abstentions" in new["required"] and "integration_decisions" not in new["properties"]
    assert "integration_decisions" in old["required"] and "integration_abstentions" not in old["properties"]
    assert "integration_abstentions" not in local["properties"] and "integration_decisions" not in local["properties"]
    empty = {"claims": [], "situation_updates": [], "episode_actions": [], "belief_links": [],
             "consolidation_decisions": []}
    assert Draft202012Validator(new).is_valid({**empty, "integration_abstentions": []})
    assert not Draft202012Validator(new).is_valid({**empty, "integration_decisions": []})
    assert not Draft202012Validator(new).is_valid({**empty, "integration_abstentions": [], "integration_decisions": []})
    assert Draft202012Validator(old).is_valid({**empty, "integration_decisions": []})
    assert not Draft202012Validator(old).is_valid({**empty, "integration_abstentions": []})


def test_new_instruction_separates_proposed_coverage_from_evidence_approval():
    assert LINK_DERIVED_INPUT_VERSION in LINK_DERIVED_INSTRUCTIONS
    assert "integration_abstentions" in LINK_DERIVED_INSTRUCTIONS
    assert "explicit no_change judgment" in LINK_DERIVED_INSTRUCTIONS
    assert "does not authorize evidence" in LINK_DERIVED_INSTRUCTIONS
