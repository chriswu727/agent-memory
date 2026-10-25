from uuid import uuid4

import pytest
from test_cognition import event
from test_semantic import batch, claim, encoded, proposals, verifier

from agent_memory.provider_usage import reported_usage
from agent_memory.semantic import SemanticProcessor


class Provider:
    def __init__(self, call, usage):
        self.call, self.last_usage = call, usage
        self.last_call_id = None

    def __call__(self, request):
        self.last_call_id = uuid4().hex
        return self.call(request)


def test_completed_proposal_and_uncertain_verification_have_independent_usage():
    source = event("I prefer direct advice at work.")
    observations = []

    def fail(_):
        raise TimeoutError("Private provider response")

    processor = SemanticProcessor(
        Provider(lambda _: encoded(proposals(claim(source))),
                 {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}),
        Provider(fail, None),
    )
    result = processor.process(batch(source), reserve=lambda **_: None,
                               on_call_result=lambda **value: observations.append(value))
    assert result.status == "provider_unavailable"
    assert [row["outcome"] for row in observations] == ["completed", "uncertain"]
    assert observations[0]["usage"] == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    assert len(observations[0]["response_fingerprint"]) == 64
    assert observations[1]["usage"] is None
    assert observations[1]["response_fingerprint"] is None


def test_invalid_semantic_json_still_records_provider_reported_usage():
    observations = []
    processor = SemanticProcessor(Provider(lambda _: "invalid JSON", {
        "prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20,
    }), verifier)
    result = processor.process(batch(event("A statement.")), reserve=lambda **_: None,
                               on_call_result=lambda **value: observations.append(value))
    assert result.status == "invalid_response"
    assert len(observations) == 1 and observations[0]["outcome"] == "completed"
    assert observations[0]["usage"]["total_tokens"] == 20


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
                                   {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4},
                                   {"prompt_tokens": 1, "completion_tokens": None, "total_tokens": None}])
def test_invalid_usage_is_unknown(usage):
    provider = Provider(lambda _: "", usage)
    provider(None)
    assert reported_usage(provider, previous_call_id=None) is None


def test_unchanged_custom_provider_metadata_is_unknown():
    provider = Provider(lambda _: "", {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
    provider(None)
    assert reported_usage(provider, previous_call_id=provider.last_call_id) is None
    del provider.last_call_id
    assert reported_usage(provider, previous_call_id=None) is None


def test_oversized_token_counters_do_not_prevent_semantic_processing():
    observations = []
    processor = SemanticProcessor(Provider(lambda _: encoded(proposals()), {
        "prompt_tokens": 2**63, "completion_tokens": 0, "total_tokens": 2**63,
    }), verifier)
    result = processor.process(batch(event("A statement.")), reserve=lambda **_: None,
                               on_call_result=lambda **value: observations.append(value))
    assert result.status == "completed"
    assert observations[0]["usage"] is None


def test_usage_observer_failure_does_not_repeat_a_provider_call():
    calls = []

    def observe(**_):
        raise RuntimeError("Ledger unavailable")

    def propose(request):
        calls.append(request)
        return encoded(proposals())

    with pytest.raises(RuntimeError, match="Ledger unavailable"):
        SemanticProcessor(propose, verifier).process(batch(event("A statement.")),
                                                    reserve=lambda **_: None, on_call_result=observe)
    assert len(calls) == 1
