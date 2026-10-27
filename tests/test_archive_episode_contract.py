import copy

import pytest
from test_cognition import event

from membrain.archive_views import validate_extensions
from membrain.helpers import DomainError


def archive_episode(**changes):
    source = event("开始讨论复习计划。")
    document = {
        "episode_id": "episode", "revision": 2, "scope": source["scope"], "state": "open",
        "member_refs": [{"event_id": source["event_id"], "revision": source["revision"]}],
        "boundary_ref": None, "organization_status": "inferred", "job_id": "job",
        "proposal_id": "proposal", "root_event_ids": [source["event_id"]],
        "organization_statement": "These attributed messages form an ongoing discussion about study plans.",
        "organization_scope": "episode_transition", "organization_action": "continue",
        "organization_source_version": {"episode_id": "episode", "revision": 1},
    }
    document.update(changes)
    return {"events": [source], "memories": [], "cognitive_views": {
        "version": 1, "episodes": [{"document": document, "event_ids": [source["event_id"]]}],
        "members": [], "associations": [],
    }}


@pytest.mark.parametrize("operation", ["create", "continue", "close", "split", "reopen"])
def test_episode_archive_accepts_complete_typed_organization_metadata_without_rewriting(operation):
    data = archive_episode(organization_action=operation,
                           organization_source_version=None if operation == "create" else {
                               "episode_id": "prior-episode", "revision": 7})
    original = copy.deepcopy(data)
    validate_extensions(data)
    assert data == original


def test_legacy_episode_archive_remains_valid_without_fabricated_metadata():
    data = archive_episode()
    document = data["cognitive_views"]["episodes"][0]["document"]
    for field in ("organization_statement", "organization_scope", "organization_action", "organization_source_version"):
        document.pop(field)
    original = copy.deepcopy(data)
    validate_extensions(data)
    assert data == original


@pytest.mark.parametrize("missing", [
    "organization_statement", "organization_scope", "organization_action", "organization_source_version",
])
def test_episode_archive_rejects_partial_new_metadata(missing):
    data = archive_episode()
    data["cognitive_views"]["episodes"][0]["document"].pop(missing)
    with pytest.raises(DomainError, match="Cognitive archive"):
        validate_extensions(data)


@pytest.mark.parametrize("changes", [
    {"organization_statement": ""}, {"organization_statement": " \n"},
    {"organization_statement": "x" * 2049}, {"organization_statement": None},
    {"organization_statement": {"text": "untracked"}},
    {"organization_scope": "historical_fact"}, {"organization_scope": None},
    {"organization_action": "verified_event"},
    {"organization_source_version": None},
    {"organization_action": "create", "organization_source_version": {"episode_id": "prior", "revision": 1}},
    {"organization_source_version": {"episode_id": "prior", "revision": True}},
    {"organization_source_version": {"episode_id": "prior", "revision": 0}},
    {"organization_source_version": {"episode_id": "prior", "revision": 2**63}},
    {"organization_source_version": {"episode_id": "", "revision": 1}},
    {"organization_source_version": {"episode_id": "x" * 257, "revision": 1}},
    {"organization_source_version": {"event_id": "prior", "revision": 1}},
    {"organization_source_version": {"episode_id": "prior", "revision": 1, "hidden_text": "untracked"}},
    {"hidden_text": "untracked"},
])
def test_episode_archive_metadata_is_strict_bounded_and_cannot_claim_extra_trust(changes):
    with pytest.raises(DomainError, match="Cognitive archive"):
        validate_extensions(archive_episode(**changes))
