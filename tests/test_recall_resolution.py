import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_bounded_learning_semantics import local_batch, local_claim, publication_snapshot
from test_cognition import STAMP, event, prepare_payload, request, scope, state, working
from test_semantic import encoded, proposals, verifier

from agent_memory import cognition
from agent_memory.consolidation import _assertion, _check_result
from agent_memory.context_views import recall_context_view
from agent_memory.contracts import validate
from agent_memory.helpers import DomainError
from agent_memory.recall_resolution import RESOLUTION_INPUT_VERSION
from agent_memory.semantic import SemanticProcessor, proposal_schema

TEXT = ("Grandma lit a lantern during power cuts and let me sort buttons into a tea tin beside her. "
        "She used the tin with the faded yellow lid and kept the lantern on the low shelf. "
        "That little ritual made the frightening dark house feel safe.")
SHORT = "The user reported that Grandma's power-cut ritual made the dark house feel safe."


def example(*, verdict="supports", version=RESOLUTION_INPUT_VERSION, source=None, **changes):
    source = event(TEXT) if source is None else source
    item = local_claim(source, statement="The user reported: " + TEXT,
                       representation="episode_outline", recall_summary={
                           "statement": SHORT, "detail_cues": ["lantern", "buttons", "tea tin", "yellow lid"]},
                       retention_priority=0.7, **changes)
    data = replace(local_batch(source, input_contract_version=version), scope=copy.deepcopy(source["scope"]))
    calls = []

    def propose(call):
        calls.append(call)
        return encoded(proposals(item))

    def verify(call):
        calls.append(call)
        result = json.loads(verifier(call))
        result["assessments"][0]["recall_summary_verdict"] = verdict
        return encoded(result)

    result = SemanticProcessor(propose, verify).process(data, reserve=lambda **_: None)
    return source, data, result, calls


def memory_case():
    source, data, result, calls = example()
    assert result.status == "completed" and len(calls) == 2
    snapshot = publication_snapshot(data)
    _check_result(data, result, snapshot)
    memory = _assertion(result.claims[0], data, {"dependency_generation": 0},
                        {"root_event_ids": [source["event_id"]]})
    memory["system_time"]["recorded_at"] = STAMP
    return source, memory


def read(source, memory, query="Grandma safe", *, at="2036-10-22T12:00:00Z", **options):
    return cognition.recall(scope(), request(query, **options),
                            [memory, *cognition.extract_event(source)], [source], state(), access_at=at)


def visible(result):
    return [item for item in result["items"] if item["display_mode"] != "do_not_surface"]


def test_summary_and_original_are_independently_checked_once_and_published_together():
    source, data, result, calls = example()
    assert len(calls) == result.model_calls == 2
    assert result.claims[0]["recall_summary"]["statement"] == SHORT
    _check_result(data, result, publication_snapshot(data))
    assert "recall_summary_verdict" in calls[1].response_schema["properties"]["assessments"]["items"]["properties"]
    assert json.loads(calls[1].messages[-1]["content"])["local_input_context"] == [source]


@pytest.mark.parametrize("verdict", ["unknown", "contradicts"])
def test_unfaithful_or_uncertain_summary_cannot_pass_with_a_supported_full_claim(verdict):
    _, _, result, calls = example(verdict=verdict)
    assert len(calls) == 2 and not result.claims
    assert result.rejected[0]["reason"] == "recall_summary_unconfirmed"


def test_summary_verdict_cannot_be_removed_at_publication():
    _, data, result, _ = example()
    assessments = copy.deepcopy(result.verification_assessments)
    assessments[0].pop("recall_summary_verdict")
    with pytest.raises(DomainError, match="summary fidelity"):
        _check_result(data, replace(result, verification_assessments=assessments), publication_snapshot(data))


@pytest.mark.parametrize("field,value", [
    ("supporting_evidence_indices", [0]), ("instance_verdicts", []), ("request_verdict", "supports"),
])
def test_local_verification_disallows_historical_only_fields_without_silently_stripping_them(field, value):
    source = event(TEXT)
    data = local_batch(source, input_contract_version=RESOLUTION_INPUT_VERSION)
    item = local_claim(source, statement="The user reported: " + TEXT)
    observed = []

    def verify(call):
        observed.append(call)
        response = json.loads(verifier(call))
        response["assessments"][0][field] = value
        return encoded(response)

    result = SemanticProcessor(lambda _: encoded(proposals(item)), verify).process(data, reserve=lambda **_: None)
    assert result.status == "invalid_response" and not result.claims
    assert field not in observed[0].response_schema["properties"]["assessments"]["items"]["properties"]


def test_old_contract_remains_fragment_only_and_does_not_accept_new_fields():
    from agent_memory.citation_coverage import SOURCE_ACT_INPUT_VERSION

    _, _, result, calls = example(version=SOURCE_ACT_INPUT_VERSION)
    assert len(calls) == 1 and not result.claims
    old = proposal_schema("local-observation-v1", input_contract_version=SOURCE_ACT_INPUT_VERSION)
    props = old["properties"]["claims"]["items"]["properties"]
    assert props["representation"] == {"const": "episode_fragment"}
    assert "recall_summary" not in props and "retention_priority" not in props


def test_old_memory_uses_short_view_without_erasing_full_record_or_duplicate_literal():
    source, memory = memory_case()
    original = copy.deepcopy((source, memory))
    recalled = read(source, memory)
    assert len(visible(recalled)) == 1
    item = visible(recalled)[0]
    assert item["memory"] == memory and item["presentation"]["level"] == "summary"
    assert recall_context_view(recalled)["memories"][0]["statement"] == SHORT
    assert "yellow lid" not in recalled["model_context"]
    assert any(item.get("deferred_by", {}).get("memory_id") == memory["memory_id"] for item in recalled["items"])
    assert (source, memory) == original
    validate("RecallResult", recalled)
    packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, state())
    assert "yellow lid" not in packet["model_context"] and SHORT in packet["model_context"]


@pytest.mark.parametrize("cue", ["Grandma lantern", "Grandma shelf", "Grandma yellow lid"])
def test_specific_cues_recover_detail_including_source_detail_not_in_cue_list(cue):
    source, memory = memory_case()
    recalled = read(source, memory, cue)
    assert any(item.get("presentation", {}).get("level") == "full" for item in visible(recalled))
    assert "yellow lid" in recalled["model_context"]
    assert all("deferred_by" not in item for item in recalled["items"])


@pytest.mark.parametrize("options", [
    {"intent": "episode"}, {"intent": "exact_quote"},
    {"intent": "cue_recovery", "evidence_cutoff": {"mode": "through_sequence", "through_sequence": 1}},
    {"detail_level": "full"},
])
def test_explicit_expansion_preserves_the_original_details_at_old_age(options):
    source, memory = memory_case()
    recalled = read(source, memory, **options)
    assert "yellow lid" in recalled["model_context"]
    assert next(item for item in recalled["items"] if item["memory"]["memory_id"] == memory["memory_id"])["presentation"]["level"] == "full"


def test_current_memory_remains_detailed_and_short_view_reduces_only_model_context():
    source, memory = memory_case()
    fresh, aged = read(source, memory, at=STAMP), read(source, memory)
    assert "yellow lid" in fresh["model_context"] and "yellow lid" not in aged["model_context"]
    assert aged["usage"]["context_tokens"] < fresh["usage"]["context_tokens"]
    assert aged["usage"]["model_calls"] == fresh["usage"]["model_calls"] == 0
    assert next(item["memory"] for item in aged["items"] if item["memory"]["memory_id"] == memory["memory_id"]) == memory


@pytest.mark.parametrize("broken", ["source_hash", "deleted", "superseded", "scope"])
def test_summary_cannot_bypass_the_original_evidence_guards(broken):
    source, memory = memory_case()
    current = state()
    sources = [source]
    if broken == "source_hash":
        source["content_sha256"] = "0" * 64
    elif broken == "deleted":
        sources = []
    elif broken == "superseded":
        current["superseded_evidence_refs"] = memory["evidence_refs"]
    else:
        memory["scope"]["brain_id"] = "other-brain"
    result = cognition.recall(scope(), request("Grandma safe"), [memory], sources, current,
                              access_at="2036-10-22T12:00:00Z")
    assert not result["items"] and SHORT not in result["model_context"]


def test_a_summary_for_another_source_cannot_hide_the_original_episode():
    source, memory = memory_case()
    other = event("Grandma's yellow lid belonged to a different episode.", 2)
    recalled = cognition.recall(scope(), request("Grandma safe"),
                                [memory, *cognition.extract_event(other)], [source, other], state(2),
                                access_at="2036-10-22T12:00:00Z")
    assert "different episode" in recalled["model_context"]


def test_pinned_detail_is_not_deferred_and_stable_budget_keeps_the_summary_with_its_raw_sources():
    source, memory = memory_case()
    memory["dynamics"]["user_pinned"] = True
    assert "yellow lid" in read(source, memory)["model_context"]
    memory["dynamics"]["user_pinned"] = False
    result = read(source, memory)
    tiny = read(source, memory, budget={**request()["budget"], "max_context_tokens": result["usage"]["context_tokens"] - 1})
    assert not any(item.get("deferred_by") for item in tiny["items"])
    assert tiny["usage"]["context_tokens"] <= result["usage"]["context_tokens"] - 1


def test_external_use_can_keep_old_details_active_without_rewriting_the_memory():
    source, memory = memory_case()
    original = copy.deepcopy(memory)
    current = state(access_usage={(memory["memory_id"], memory["revision"]): {
        "external_uses": 20, "successes": 20, "failures": 0,
        "last_reinforcement_at": "2036-10-22T12:00:00Z"}})
    result = cognition.recall(scope(), request("Grandma safe"), [memory], [source], current,
                              access_at="2036-10-22T12:00:00Z")
    assert result["items"][0]["presentation"]["level"] == "full"
    assert "yellow lid" in result["model_context"] and memory == original


def test_new_local_schema_contains_only_reachable_definitions_and_full_source_verification():
    from agent_memory.citation_coverage import SOURCE_ACT_INPUT_VERSION
    from agent_memory.local_semantics import LOCAL_OBSERVATION_MODE
    from agent_memory.semantic import proposal_data, request_payload

    source, data, _, calls = example()
    source_before = copy.deepcopy(source)
    sizes = {}
    for version in (SOURCE_ACT_INPUT_VERSION, RESOLUTION_INPUT_VERSION):
        schema = proposal_schema(LOCAL_OBSERVATION_MODE, input_contract_version=version)
        body = proposal_data(data.sources, (), data.working_state, (), data.sampling, (), scope=data.scope,
                             proposal_mode=LOCAL_OBSERVATION_MODE, learning_input_refs=data.learning_input_refs,
                             input_contract_version=version)
        _, sizes[version] = request_payload("proposal", body, schema, 8192,
                                           proposal_mode=LOCAL_OBSERVATION_MODE, input_contract_version=version)
        if version == RESOLUTION_INPUT_VERSION:
            assert "$defs" not in schema and "registry" not in body
        else:
            assert "$defs" in schema and "registry" in body
        assert body["sources"] == list(data.sources)
    assert sizes[RESOLUTION_INPUT_VERSION] < sizes[SOURCE_ACT_INPUT_VERSION] * 0.8
    assert json.loads(calls[1].messages[-1]["content"])["local_input_context"] == [source]
    assert source == source_before


@pytest.mark.parametrize("character", ['"', '\\', '\n', '🙂'])
def test_host_budget_accounts_for_instructions_and_escaped_memory_before_retrieval(character):
    from agent_memory.runtime import BrainRuntime, RuntimePolicy

    runtime = BrainRuntime(SimpleNamespace(brain_id="budget-test"), lambda _: pytest.fail("No model call"),
                           policy=RuntimePolicy(memory_context_tokens=16000, max_input_tokens=24000))
    budget = runtime._memory_budget("turn", "Continue the task")
    context = json.dumps({"text": character * (budget // 8)}, ensure_ascii=False)
    assert len(context.encode()) <= budget < 16000
    prompt = runtime._prompt("turn", "Continue the task", {"model_context": context})
    assert prompt.input_tokens_estimate <= 24000
