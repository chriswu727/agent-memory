import pytest
from jsonschema import Draft202012Validator
from test_cognition import event
from test_semantic import batch, episode, proposals, run

from agent_memory.semantic import PROPOSAL_SCHEMA, SemanticProcessor


@pytest.mark.parametrize("identifier,revision", [("model-invented-id", None), (None, 1), ("model-invented-id", 1)])
def test_create_episode_schema_matches_runtime_allocated_identity(identifier, revision):
    source = event("I started planning a garden.")
    proposed = episode(source, episode_id=identifier, expected_revision=revision)
    response = proposals(episodes=[proposed])
    assert not Draft202012Validator(PROPOSAL_SCHEMA).is_valid(response)
    result = run(batch(source), response)
    assert result.status == "invalid_response" and result.model_calls == 1
    with pytest.raises(ValueError, match="Existing episode on create"):
        SemanticProcessor._validate_proposal("episode_actions", proposed, source["scope"],
                                            {(source["event_id"], 1): source}, {}, {})


@pytest.mark.parametrize("action", ["continue", "close", "split", "reopen"])
def test_existing_episode_actions_require_existing_identity_and_version_in_schema(action):
    source = event("I continued planning a garden.")
    response = proposals(episodes=[episode(source, action=action)])
    assert not Draft202012Validator(PROPOSAL_SCHEMA).is_valid(response)


@pytest.mark.parametrize("action", ["close", "split"])
def test_boundary_episode_actions_require_nonnull_source_boundary(action):
    source = event("I completed planning a garden.")
    proposed = episode(source, action=action, episode_id="existing", expected_revision=1)
    assert not Draft202012Validator(PROPOSAL_SCHEMA).is_valid(proposals(episodes=[proposed]))
    proposed["boundary_ref"] = {"event_id": source["event_id"], "revision": 1}
    assert Draft202012Validator(PROPOSAL_SCHEMA).is_valid(proposals(episodes=[proposed]))


def test_valid_new_episode_identity_is_assigned_only_after_verified_proposal():
    source = event("I started planning a garden.")
    result = run(batch(source), proposals(episodes=[episode(source)]))
    assert result.status == "completed" and result.model_calls == 2
    assert result.episode_actions[0]["episode_id"] is None
    assert result.episode_actions[0]["expected_revision"] is None
