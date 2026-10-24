import copy

import pytest
from test_cognition import scope

from agent_memory.helpers import DomainError
from agent_memory.learning import _current_input_authorization


def authorization():
    return {"scope": scope(), "current_input_refs": [{"event_id": "event-1", "revision": 1}],
            "current_input_binding": {"turn_id": "turn-1", "job_id": "job-1"}}


def damaged_authorization(result, mutation):
    result = copy.deepcopy(result)
    if mutation == "bool_revision":
        result["current_input_refs"][0]["revision"] = True
    elif mutation == "float_revision":
        result["current_input_refs"][0]["revision"] = 1.0
    elif mutation == "missing_revision":
        result["current_input_refs"][0].pop("revision")
    elif mutation == "extra_ref_field":
        result["current_input_refs"][0]["trusted"] = True
    elif mutation == "empty_identifier":
        result["current_input_refs"][0]["event_id"] = ""
    elif mutation == "duplicate_refs":
        result["current_input_refs"] *= 2
    elif mutation == "null_refs":
        result["current_input_refs"] = None
    elif mutation == "empty_refs":
        result["current_input_refs"] = []
    elif mutation == "bool_binding":
        result["current_input_binding"]["turn_id"] = True
    elif mutation == "missing_job":
        result["current_input_binding"].pop("job_id")
    elif mutation == "extra_binding":
        result["current_input_binding"]["trusted"] = True
    elif mutation == "null_scope":
        result["scope"] = None
    elif mutation == "bool_scope_generation":
        result["scope"]["scope_generation"] = True
    elif mutation == "bool_conversation":
        result["scope"]["conversation_id"] = True
    elif mutation == "missing_scope_binding":
        result["scope"].pop("principal_binding_id")
    else:
        raise AssertionError(mutation)
    return result


MUTATIONS = (
    "bool_revision", "float_revision", "missing_revision", "extra_ref_field", "empty_identifier",
    "duplicate_refs", "null_refs", "empty_refs", "bool_binding", "missing_job", "extra_binding",
    "null_scope", "bool_scope_generation", "bool_conversation", "missing_scope_binding",
)


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_malformed_authorization_is_rejected_before_database_comparison(mutation):
    with pytest.raises(DomainError) as error:
        _current_input_authorization(damaged_authorization(authorization(), mutation))
    assert error.value.code == "context_stale"


def test_valid_and_legacy_authority_keep_distinct_meanings():
    result = authorization()
    assert _current_input_authorization(result) == (result["current_input_refs"], result["current_input_binding"])
    assert _current_input_authorization({}) == ([], None)
    assert _current_input_authorization({"current_input_refs": []}) == ([], None)
    with pytest.raises(DomainError):
        _current_input_authorization({"current_input_refs": None})
