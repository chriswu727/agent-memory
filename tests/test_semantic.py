import copy
import json
from dataclasses import replace

import pytest
from test_cognition import UNKNOWN_TIME, event, scope

from agent_memory import cognition
from agent_memory.helpers import DomainError
from agent_memory.semantic import (
    CognitiveBatch,
    SemanticPolicy,
    SemanticProcessor,
    evaluate_condition,
    expand_schema_residual,
)


def reference(source, relationship="supports"):
    return {"event_id": source["event_id"], "event_revision": source["revision"],
            "source_content_sha256": source["content_sha256"], "relationship": relationship,
            "span": {"start": 0, "end": len(cognition._content_text(source["observed"]["content"])),
                     "unit": "unicode_code_point"}}


def claim(source, **changes):
    value = {"proposal_id": "claim-1", "statement": "用户报告在工作时希望直接获得建议。",
             "memory_kind": "interaction_preference", "representation": "semantic_anchor",
             "claim_kind": "reported_preference", "world": "real_world",
             "conditions": {"op": "holds", "subject_ref": "user",
                            "predicate_ref": {"id": "core.activity", "version": 1},
                            "value": {"type": "concept", "id": "activity:work"}},
             "evidence_refs": [reference(source)], "counterexamples": [],
             "source_memory_versions": [], "valid_time": copy.deepcopy(UNKNOWN_TIME),
             "valid_during": {"kind": "unknown"}, "procedure": None}
    value.update(changes)
    return value


def situation(source, **changes):
    value = {"proposal_id": "situation-1", "kind": "goal", "key": "current_goal",
             "value": "制定复习计划", "state": "active", "world": "real_world",
             "conditions": {"op": "always"}, "evidence_refs": [reference(source)]}
    value.update(changes)
    return value


def episode(source, **changes):
    value = {"proposal_id": "episode-1", "action": "create", "episode_id": None,
             "organization_statement": "These observed messages form an interaction about the user's request.",
             "expected_revision": None,
             "member_refs": [{"event_id": source["event_id"], "revision": source["revision"]}],
             "boundary_ref": None}
    value.update(changes)
    return value


def proposals(*claims, updates=(), episodes=()):
    return {"claims": list(claims), "situation_updates": list(updates), "episode_actions": list(episodes)}


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def batch(*sources, **changes):
    return CognitiveBatch("batch-1", scope(), tuple(sources), **changes)


def verifier(request):
    data = json.loads(request.messages[-1]["content"])
    return encoded({"assessments": [{
        "proposal_id": candidate["proposal"]["proposal_id"],
        "proposal_verdict": "supports",
        "link_verdict": None,
        "relations": [{"evidence_index": evidence["evidence_index"],
                       "verdict": evidence["reference"]["relationship"]}
                      for evidence in candidate["evidence"]],
        **({"citation_coverage_verdict": "supports"} if "citation_scope" in candidate else {}),
        **({"organization_fidelity": dict.fromkeys(("attribution", "modality", "specificity", "boundary"), "supports")}
           if candidate["group"] == "episode_actions" and "organization_fidelity" in
           request.response_schema["properties"]["assessments"]["items"]["properties"] else {}),
        **({"request_verdict": "supports"}
           if candidate["proposal"].get("evidence_use") == "current_request" else {}),
        **({"instance_verdicts": [{"memory_version": item["memory_version"], "verdict": "supports"}
                                  for item in candidate["instance_support"]]}
           if "instance_support" in candidate else {}),
        **({"supporting_evidence_indices": [item["evidence_index"] for item in candidate["evidence"]
                                            if item["reference"]["relationship"] == "supports"]}
           if candidate.get("evidence_selection") == "minimal_shared_support" else {}),
    } for candidate in data["proposals"]]})


def run(data, proposed, verify=verifier, **kwargs):
    return SemanticProcessor(lambda request: encoded(proposed), verify, kwargs.pop("policy", None)).process(
        data, reserve=kwargs.pop("reserve", lambda **_: None), **kwargs,
    )


@pytest.mark.parametrize("expression,context,expected", [
    ({"op": "always"}, {}, "true"),
    ({"op": "eq", "key": "task", "value": "exam"}, {"task": "exam"}, "true"),
    ({"op": "eq", "key": "task", "value": "exam"}, {"task": "work"}, "false"),
    ({"op": "eq", "key": "task", "value": "exam"}, {}, "unknown"),
    ({"op": "ne", "key": "task", "value": "exam"}, {}, "unknown"),
    ({"op": "eq", "key": "task", "value": None}, {"task": "exam"}, "unknown"),
    ({"op": "eq", "key": "task", "value": True}, {"task": 1}, "false"),
    ({"op": "not", "arg": {"op": "eq", "key": "task", "value": "exam"}}, {}, "unknown"),
    ({"op": "all", "args": [{"op": "always"}, {"op": "eq", "key": "task", "value": "exam"}]}, {}, "unknown"),
    ({"op": "any", "args": [{"op": "always"}, {"op": "eq", "key": "task", "value": "exam"}]}, {}, "true"),
    ({"op": "all", "args": [{"op": "eq", "key": "task", "value": "exam"},
                              {"op": "eq", "key": "mood", "value": "sad"}]}, {"task": "work"}, "false"),
])
def test_conditions_have_three_valued_semantics(expression, context, expected):
    assert evaluate_condition(expression, context) == expected


@pytest.mark.parametrize("expression", [
    {"op": "eval", "code": "print('unsafe')"}, {"op": "always", "injected": True},
    {"op": "eq", "key": "__class__", "value": "x"}, {"op": "all", "args": []},
    {"op": "eq", "key": "task", "value": float("nan")},
])
def test_invalid_condition_language_is_rejected(expression):
    with pytest.raises(ValueError):
        evaluate_condition(expression, {})


def test_condition_depth_is_bounded_even_when_result_could_short_circuit():
    expression = {"op": "always"}
    for _ in range(10):
        expression = {"op": "not", "arg": expression}
    with pytest.raises(ValueError):
        evaluate_condition(expression, {})


def test_batch_proposal_and_verification_are_separate_reserved_calls():
    source = event("工作时请直接给我建议。")
    response = proposals(claim(source), updates=[situation(source)], episodes=[episode(source)])
    calls, reserved = [], []

    def propose(request):
        assert len(reserved) == 1
        assert request.phase == "proposal"
        assert "untrusted data" in request.messages[0]["content"]
        assert "private reasoning" in request.messages[0]["content"]
        calls.append(request)
        return encoded(response)

    def verify(request):
        assert len(reserved) == 2 and request.phase == "verification"
        calls.append(request)
        return verifier(request)

    result = SemanticProcessor(propose, verify).process(batch(source), reserve=lambda **data: reserved.append(data))
    assert result.status == "completed"
    assert result.claims[0]["epistemic_status"] == "derived_inference"
    assert result.claims[0]["verification"]["not_a_truth_certificate"] is True
    assert result.situation_updates[0]["current_only"] is True
    assert result.episode_actions[0]["action"] == "create"
    assert result.model_calls == 2 and result.input_bytes == sum(row["input_bytes"] for row in reserved)
    assert result.observed_event_refs == ({"event_id": "event-1", "revision": 1},)
    assert {row["phase"] for row in reserved} == {"proposal", "verification"}
    assert all(row["source_refs"] == list(result.observed_event_refs) for row in reserved)
    assert "直接给我建议" not in repr(result)
    assert "直接给我建议" not in repr(calls[0])


def test_missing_verifier_never_publishes_claims_state_or_event_boundaries():
    source = event("工作时我希望直接获得建议。")
    result = run(batch(source), proposals(claim(source), updates=[situation(source)],
                                         episodes=[episode(source)]), verify=None)
    assert result.status == "verification_unavailable" and result.model_calls == 1
    assert result.claims == result.situation_updates == result.episode_actions == ()
    assert {row["reason"] for row in result.rejected} == {"verification_unavailable"}


def test_completed_empty_processing_does_not_report_learning():
    source = event("工作时我希望直接获得建议。")
    result = run(batch(source), proposals())
    assert result.status == "completed" and result.learning_outcome == "no_proposals"
    result = run(batch(source), proposals(claim(source)))
    assert result.learning_outcome == "accepted_proposals"
    result = run(batch(source), proposals(claim(source)), verify=None)
    assert result.learning_outcome == "not_evaluated"


def test_model_token_budget_is_independent_of_response_byte_budget():
    requests = []

    def propose(request):
        requests.append(request)
        return encoded(proposals())

    policy = SemanticPolicy(max_response_bytes=1024, max_output_tokens=333)
    result = SemanticProcessor(propose, verifier, policy).process(
        batch(event("今天继续复习。")), reserve=lambda **_: None,
    )
    assert result.status == "completed" and requests[0].max_output_tokens == 333
    with pytest.raises(ValueError):
        SemanticPolicy(max_output_tokens=True)


@pytest.mark.parametrize("field,value", [
    ("event_id", "unknown"), ("event_revision", 9), ("source_content_sha256", "0" * 64),
    ("relationship", "contradicts"),
])
def test_unknown_or_changed_source_cannot_reach_verification(field, value):
    source = event("工作时我希望直接获得建议。")
    proposal = claim(source)
    proposal["evidence_refs"][0][field] = value
    result = run(batch(source), proposals(proposal), verify=lambda _: pytest.fail("No verification expected"))
    assert result.claims == () and result.rejected[0]["reason"] == "invalid_proposal"


def test_narrow_span_cannot_strip_negation_or_condition_before_verification():
    source = event("只有工作时我才喜欢直接的建议。")
    proposal = claim(source, statement="用户喜欢直接建议。", conditions={"op": "always"})
    proposal["evidence_refs"][0]["span"]["start"] = 7
    result = run(batch(source), proposals(proposal))
    assert result.claims == () and result.model_calls == 1


@pytest.mark.parametrize("text,world,role,kind", [
    ("你记得我喜欢直接建议吗？", "real_world", "user", "reported_preference"),
    ("梦见你直接给我建议。", "real_world", "user", "reported_preference"),
    ("我完成了任务。", "real_world", "assistant", "task_completed"),
    ("用户喜欢直接建议。", "real_world", "assistant", "inferred_pattern"),
    ("我说过我会完成任务。", "real_world", "user", "agent_said"),
])
def test_source_role_world_and_speech_act_cannot_be_promoted(text, world, role, kind):
    source = event(text, role=role)
    result = run(batch(source), proposals(claim(source, world=world, claim_kind=kind)))
    assert result.claims == () and result.model_calls == 1


def test_delivered_assistant_supports_speech_but_not_real_world_facts():
    source = event("我答应先听你说。", role="assistant")
    delivered = run(batch(source), proposals(claim(source, claim_kind="commitment_expressed")))
    assert delivered.claims
    draft = copy.deepcopy(source)
    draft["observed"]["delivery_state"] = "draft"
    assert not run(batch(draft), proposals(claim(draft, claim_kind="commitment_expressed"))).claims


def test_verifier_unknown_cannot_be_upgraded_by_valid_source_identifiers():
    source = event("我不喜欢咖啡。")
    proposal = claim(source, statement="用户喜欢咖啡。", claim_kind="reported_fact")

    def unknown(request):
        result = json.loads(verifier(request))
        result["assessments"][0]["relations"][0]["verdict"] = "unknown"
        return encoded(result)

    result = run(batch(source), proposals(proposal), verify=unknown)
    assert result.claims == () and result.rejected[0]["reason"] == "semantic_support_unconfirmed"


def test_verified_counterexample_is_retained_and_result_is_contested():
    source = event("工作时我希望直接获得建议。")
    exception = event("今天工作时我不想听建议。", sequence=2)
    proposed = claim(source, counterexamples=[reference(exception, "contradicts")])
    result = run(batch(source, exception), proposals(proposed))
    assert result.claims[0]["counterexamples"] == proposed["counterexamples"]
    assert result.claims[0]["verification"]["state"] == "contested"
    assert result.claims[0]["conditions"] == proposed["conditions"]


def test_claim_cannot_depend_on_unobserved_parent_sources():
    source = event("工作时我希望直接获得建议。")
    parent_source = event("没有提供给处理器的另一项信息。", sequence=2)
    parent = cognition.extract_event(parent_source)[0]
    proposal = claim(source, source_memory_versions=[{"memory_id": parent["memory_id"], "revision": 1}])
    result = run(batch(source, existing_assertions=(parent,)), proposals(proposal))
    assert result.claims == () and result.model_calls == 1
    assert {"event_id": "event-2", "revision": 1} in result.observed_event_refs


def test_working_state_sources_are_included_in_reservation_and_invalidation_dependencies():
    source, old = event("继续制定计划。"), event("上一次的目标。", sequence=2)
    saved = {"items": [{"evidence_refs": [reference(old)]}], "open_loops": []}
    reservations = []
    result = run(batch(source, working_state=saved), proposals(), reserve=lambda **r: reservations.append(r))
    expected = {"event_id": old["event_id"], "revision": 1}
    assert expected in result.observed_event_refs
    assert expected in reservations[0]["source_refs"]


def test_duplicate_proposal_ids_and_extra_response_fields_are_rejected():
    source = event("工作时我希望直接获得建议。")
    duplicate = proposals(claim(source), updates=[situation(source, proposal_id="claim-1")])
    assert run(batch(source), duplicate).status == "invalid_response"
    extra = {**proposals(claim(source)), "reasoning": "private reasoning"}
    assert run(batch(source), extra).status == "invalid_response"


@pytest.mark.parametrize("invalid", [
    '{"claims":[],"claims":[],"situation_updates":[],"episode_actions":[]}',
    "[" * 1000 + "]" * 1000, "{}", "secret details", "x" * 40000,
])
def test_malformed_proposals_are_bounded_and_redacted(invalid):
    source = event("工作时我希望直接获得建议。")
    result = SemanticProcessor(lambda _: invalid, verifier).process(batch(source), reserve=lambda **_: None)
    assert result.status == "invalid_response" and not result.claims
    assert "secret details" not in repr(result)


def test_verifier_cannot_invent_ids_or_omit_source_relations():
    source = event("工作时我希望直接获得建议。")
    invalid = {"assessments": [{
        "proposal_id": "invented", "proposal_verdict": "supports", "link_verdict": None,
        "relations": [{"evidence_index": 0, "verdict": "supports"}],
    }]}
    result = run(batch(source), proposals(claim(source)), verify=lambda _: encoded(invalid))
    assert result.status == "invalid_response" and not result.claims


def test_failed_budget_reservation_never_calls_provider():
    source = event("工作时我希望直接获得建议。")

    def reject(**_):
        raise DomainError("budget_exceeded", "Budget is exhausted")

    with pytest.raises(DomainError, match="Budget is exhausted"):
        SemanticProcessor(lambda _: pytest.fail("No provider expected"), verifier).process(
            batch(source), reserve=reject,
        )


def test_prior_usage_and_request_size_stop_before_network_calls():
    source = event("工作时我希望直接获得建议。")
    processor = SemanticProcessor(lambda _: pytest.fail("No provider expected"), verifier)
    assert processor.process(batch(source), reserve=lambda **_: None, prior_model_calls=2).status == "budget_exhausted"
    assert processor.process(batch(source), reserve=lambda **_: None, prior_input_bytes=196608).status == "budget_exhausted"
    result = run(batch(source), proposals(claim(source)), policy=SemanticPolicy(max_request_bytes=100))
    assert result.status == "budget_exhausted" and result.model_calls == 0


def test_verification_budget_exhaustion_does_not_publish_unverified_proposals():
    source = event("工作时我希望直接获得建议。")
    result = run(batch(source), proposals(claim(source)), policy=SemanticPolicy(max_model_calls=1))
    assert result.status == "budget_exhausted" and result.model_calls == 1 and result.claims == ()


def test_provider_failure_is_degraded_but_lease_failure_still_propagates():
    source = event("工作时我希望直接获得建议。")

    def fail(_):
        raise RuntimeError("secret provider contents")

    result = SemanticProcessor(fail, verifier).process(batch(source), reserve=lambda **_: None)
    assert result.status == "provider_unavailable" and result.model_calls == 1
    assert "secret provider contents" not in repr(result)
    calls = []

    def lease():
        calls.append(1)
        if len(calls) >= 3:
            raise DomainError("lease_expired", "Lease lost")

    with pytest.raises(DomainError, match="Lease lost"):
        SemanticProcessor(fail, verifier).process(batch(source), reserve=lambda **_: None, check=lease)


def test_reservation_latency_cannot_start_a_call_after_deadline(monkeypatch):
    source = event("工作时我希望直接获得建议。")
    clock = [0.0]
    monkeypatch.setattr("agent_memory.semantic.time.monotonic", lambda: clock[0])

    def reserve(**_):
        clock[0] = 31

    result = SemanticProcessor(lambda _: pytest.fail("No provider expected"), verifier).process(
        batch(source), reserve=reserve,
    )
    assert result.status == "budget_exhausted" and result.model_calls == 1


def test_late_provider_result_is_not_published(monkeypatch):
    source = event("工作时我希望直接获得建议。")
    clock = [0.0]
    monkeypatch.setattr("agent_memory.semantic.time.monotonic", lambda: clock[0])

    def propose(_):
        clock[0] = 31
        return encoded(proposals(claim(source)))

    result = SemanticProcessor(propose, verifier).process(batch(source), reserve=lambda **_: None)
    assert result.status == "budget_exhausted" and result.claims == ()


def test_episode_members_are_ordered_scoped_and_versioned():
    first, second = event("开始制定计划。"), event("计划完成。", sequence=2)
    existing = {"episode_id": "episode-existing", "revision": 2, "scope": scope(), "state": "open",
                "member_refs": [{"event_id": first["event_id"], "revision": 1}], "boundary_ref": None}
    proposed = episode(first, action="close", episode_id="episode-existing", expected_revision=2,
                       member_refs=[{"event_id": first["event_id"], "revision": 1},
                                    {"event_id": second["event_id"], "revision": 1}],
                       boundary_ref={"event_id": second["event_id"], "revision": 1})
    assert run(batch(first, second, episodes=(existing,)), proposals(episodes=[proposed])).episode_actions
    proposed["member_refs"].reverse()
    assert not run(batch(first, second, episodes=(existing,)), proposals(episodes=[proposed])).episode_actions
    proposed["member_refs"].reverse()
    proposed["expected_revision"] = 1
    assert not run(batch(first, second, episodes=(existing,)), proposals(episodes=[proposed])).episode_actions
    proposed["expected_revision"] = 2
    second["scope"]["conversation_id"] = "different"
    assert not run(batch(first, second, episodes=(existing,)), proposals(episodes=[proposed])).episode_actions


def test_procedure_requires_verified_actual_outcome_and_attempt_reference():
    source = event("执行步骤后，任务检查成功。", role="tool")
    method = {"steps": ["执行步骤"],
              "exit_conditions": {"op": "holds", "subject_ref": "agent",
                                  "predicate_ref": {"id": "core.task_state", "version": 1},
                                  "value": {"type": "string", "value": "completed"}},
              "failure_conditions": {"op": "unresolved", "label": "任务检查失败"},
              "environment": "test-env-v1", "attempt_event_ids": [source["event_id"]]}
    proposed = claim(source, memory_kind="procedural", representation="procedure",
                     claim_kind="procedure", procedure=method)
    result = run(batch(source), proposals(proposed))
    assert result.claims[0]["epistemic_status"] == "derived_inference"
    untrusted = copy.deepcopy(source)
    untrusted["origin_verification"] = "unknown"
    assert not run(batch(untrusted), proposals(proposed)).claims
    proposed["procedure"]["attempt_event_ids"] = ["invented-attempt"]
    assert not run(batch(source), proposals(proposed)).claims


def test_cross_scope_batch_is_rejected_before_model_can_read_it():
    source = event("private source")
    source["scope"]["tenant_id"] = "other-tenant"
    with pytest.raises(DomainError, match="out of scope"):
        run(batch(source), proposals())


def compressed_example():
    source = event("先聊一会，再安排复习。")
    common = {"claim_id": "shared-pattern", "statement": "先交流，再安排复习。",
              "conditions": {"op": "always"}, "world": "real_world",
              "evidence_refs": [reference(source)]}
    observed = {**copy.deepcopy(common), "claim_id": "observed-order"}
    residual = {"claim_id": "observed-exception", "statement": "没有记录交流时长。",
                "conditions": {"op": "always"}, "world": "real_world",
                "evidence_refs": [reference(source)]}
    schema = {"schema_id": "pattern-1", "revision": 1, "claims": [common]}
    instance = {"schema_ref": {"schema_id": "pattern-1", "revision": 1},
                "shared_bindings": [{"schema_claim_id": "shared-pattern", "observed_claim_id": "observed-order"}],
                "residual_claim_ids": ["observed-exception"], "observed_claims": [observed, residual]}
    return schema, instance


def test_schema_expansion_returns_only_observed_claims_with_their_own_evidence():
    schema, instance = compressed_example()
    expanded = expand_schema_residual(schema, instance)
    assert expanded == tuple(instance["observed_claims"])
    expanded[0]["statement"] = "mutated"
    assert instance["observed_claims"][0]["statement"] != "mutated"


@pytest.mark.parametrize("change", ["common_detail", "unobserved", "omit_exception", "version", "negation"])
def test_schema_cannot_fill_unobserved_details_or_change_meaning(change):
    schema, instance = compressed_example()
    if change == "common_detail":
        schema["claims"][0]["statement"] = "先聊十分钟，再安排复习。"
    elif change == "unobserved":
        instance["shared_bindings"][0]["observed_claim_id"] = "nonexistent-observation"
    elif change == "omit_exception":
        instance["residual_claim_ids"] = []
    elif change == "version":
        schema["revision"] = 2
    else:
        schema["claims"][0]["conditions"] = {"op": "not", "arg": {"op": "always"}}
    with pytest.raises(ValueError):
        expand_schema_residual(schema, instance)


def test_request_fingerprint_does_not_depend_on_changing_time_budget(monkeypatch):
    source = event("工作时我希望直接获得建议。")
    reserved = []
    clock = [0.0]
    monkeypatch.setattr("agent_memory.semantic.time.monotonic", lambda: clock[0])
    proposed = proposals(claim(source))
    run(batch(source), proposed, verify=None, reserve=lambda **r: reserved.append(r))
    clock[0] = 99
    run(batch(source), proposed, verify=None, reserve=lambda **r: reserved.append(r))
    assert reserved[0]["request_fingerprint"] == reserved[1]["request_fingerprint"]


@pytest.mark.parametrize("value", [-1, True, float("inf"), "2"])
def test_invalid_policy_limits_are_rejected(value):
    with pytest.raises(ValueError):
        replace(SemanticPolicy(), max_model_calls=value)
