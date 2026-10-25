import copy
import json
from dataclasses import replace

import pytest
from test_belief_semantic import UNBOUNDED
from test_bounded_learning_semantics import local_claim
from test_learning import learning as learning
from test_learning_neighborhood import publish, source
from test_learning_windows import claim, execute, jobs
from test_semantic import claim as proposed_claim
from test_semantic import encoded, proposals, reference, verifier

from agent_memory.helpers import uid
from agent_memory.jobs import JobStore
from agent_memory.learning_allocations import ENCODING_PHASE, phase
from agent_memory.learning_integration import reconsider
from agent_memory.semantic import SemanticProcessor

pytestmark = pytest.mark.integration


def setup_history(harness, monkeypatch, count=1):
    harness.service.semantic_processor = None
    memories = [publish(harness, source(harness, f"I prefer study conversation in context {index}."),
                        memory_kind="interaction_preference", representation="semantic_anchor",
                        claim_kind="reported_preference", conditions={"op": "always"},
                        valid_during=copy.deepcopy(UNBOUNDED), procedure=None)
                for index in range(count)]
    requests = []

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        requests.append((request.phase, copy.deepcopy(data)))
        if "integration_manifest" not in data:
            return encoded(proposals(local_claim(data["sources"][-1], statement="The user reported a study preference.")))
        manifest = data["integration_manifest"]
        targets = {(memory["memory_id"], memory["revision"]): memory for memory in data["existing_assertions"]}
        events = {(event["event_id"], event["revision"]): event for event in data["sources"]}
        new = events[(manifest["input_refs"][0]["event_id"], manifest["input_refs"][0]["revision"])]
        claims, links, decisions = [], [], []
        for index, objective in enumerate(manifest["objectives"]):
            version = objective["target_memory_version"]
            target = targets[(version["memory_id"], version["revision"])]
            claim_id, link_id = f"claim-{index}", f"link-{index}"
            candidate = proposed_claim(new, proposal_id=claim_id, statement=target["statement"],
                                       **{key: copy.deepcopy(target[key]) for key in (
                                           "memory_kind", "world", "claim_kind", "conditions", "valid_time", "valid_during", "procedure")})
            claims.append(candidate)
            links.append({"proposal_id": link_id, "kind": "equivalent", "claim_proposal_id": claim_id,
                          "target_memory_versions": [{"memory_id": version["memory_id"], "revision": version["revision"]}],
                          "evidence_refs": [*copy.deepcopy(target["evidence_refs"]), reference(new)],
                          "conditions": copy.deepcopy(target["conditions"]),
                          "valid_during": copy.deepcopy(target["valid_during"]), "world": target["world"]})
            decisions.append({"objective_id": objective["objective_id"], "outcome": "proposed", "reason": None,
                              "link_proposal_ids": [link_id]})
        return encoded({**proposals(*claims), "belief_links": links, "integration_decisions": decisions})

    def verify(request):
        requests.append((request.phase, json.loads(request.messages[-1]["content"])))
        checked = json.loads(verifier(request))
        for candidate, assessment in zip(requests[-1][1]["proposals"], checked["assessments"], strict=True):
            if candidate["group"] == "belief_links":
                assessment["link_verdict"] = "supports"
        return encoded(checked)

    harness.service.semantic_processor = SemanticProcessor(propose, verify)
    import agent_memory.neighborhood as neighborhood

    original = neighborhood.select_neighborhood

    def restricted(*args, **kwargs):
        selected = original(*args, **kwargs)
        return replace(selected, sampling={**selected.sampling, "publication_restricted": True})

    monkeypatch.setattr(neighborhood, "select_neighborhood", restricted)
    return memories, requests


def encode(harness):
    harness.observe("I prefer study conversation in these contexts.")
    current = [row for row in jobs(harness) if phase(row["payload"]) == ENCODING_PHASE and row["state"] == "queued"]
    assert len(current) == 1
    result = execute(harness, claim(harness, current[0]["id"]))
    assert result["state"] == "completed"
    assert result["result"]["proposal_mode"] == "local-observation-v1"
    return result


def reconsider_once(harness):
    with harness.database.transaction(harness.principal) as conn:
        brain = harness.service._brain(conn, harness.principal, harness.brain)
        reconsider(conn, harness.service, harness.principal, brain)


def test_local_encoding_queues_and_automatically_executes_exact_history_pair(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    child = learning.job(child_id)
    assert child["state"] == "queued"
    assert child["payload"]["authorization_root_job_id"] == parent["id"]
    assert child["source_event_ids"] and child["source_memory_ids"]
    frozen = copy.deepcopy(child["result"])
    completed = execute(learning, claim(learning, child_id))
    assert completed["state"] == "completed", (completed["last_error"], completed["result"])
    assert completed["model_calls"] == parent["model_calls"] == 2
    assert completed["result"]["history_integration"]["assessed_count"] == 1
    assert len(requests) == 4
    assert requests[2][1]["integration_manifest"] == frozen["integration_manifest"]
    assert "historical_context" in requests[3][1]
    assert learning.job(parent["id"])["payload"]["allocation_input_fingerprint"]
    assert JobStore(learning.database).claim(learning.principal, learning.brain, worker_id=uid(), job_id=child_id) is None


def test_unattempted_remainder_waits_without_paid_loop_then_new_input_funds_oldest_debt(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    first = execute(learning, claim(learning, parent["result"]["history_integration_job_id"]))
    assert first["state"] == "completed", (first["last_error"], first["result"])
    remainder_id = first["payload"]["integration_continuation_job_id"]
    remainder = learning.job(remainder_id)
    assert remainder["state"] == "deferred"
    assert remainder["result"]["history_integration_category"] == "shared_allocation_exhausted"
    for _ in range(3):
        reconsider_once(learning)
    assert len(requests) == 4
    donor = encode(learning)
    funded_id = learning.job(remainder_id)["payload"]["integration_continuation_job_id"]
    funded = learning.job(funded_id)
    assert funded["state"] == "queued"
    assert funded["payload"]["authorization_root_job_id"] == donor["id"]
    assert funded["payload"]["encoding_parent_job_id"] == parent["id"]
    assert learning.job(donor["result"]["history_integration_job_id"])["state"] == "deferred"
    done = execute(learning, claim(learning, funded_id))
    assert done["state"] == "completed" and len(requests) == 8


def test_provider_timeout_is_blocked_and_new_input_cannot_silently_retry(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    original = learning.service.semantic_processor.propose

    def timeout(request):
        if "integration_manifest" in json.loads(request.messages[-1]["content"]):
            raise TimeoutError("private timeout body")
        return original(request)

    learning.service.semantic_processor.propose = timeout
    failed = execute(learning, claim(learning, child_id))
    assert failed["state"] == "cancelled" and failed["model_calls"] == 1
    assert failed["last_error"] == "provider_failed"
    before = len(requests)
    reconsider_once(learning)
    assert len(requests) == before
    learning.service.semantic_processor.propose = original
    encode(learning)
    assert "integration_continuation_job_id" not in learning.job(child_id)["payload"]
    assert learning.job(child_id)["model_calls"] == 1


def test_budget_revision_wakes_unpaid_deferred_obligation_and_restart_keeps_plan(learning, monkeypatch):
    setup_history(learning, monkeypatch)
    store = JobStore(learning.database)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="limit", expected_revision=0,
                                     policy={"window_seconds": 3600, "max_model_calls": 2, "max_input_bytes": 2_000_000})
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    assert learning.job(child_id)["state"] == "deferred"
    reconsider_once(learning)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="allow", expected_revision=1,
                                     policy={"window_seconds": 3600, "max_model_calls": 10, "max_input_bytes": 2_000_000})
    reconsider_once(learning)
    ready = learning.job(child_id)
    assert ready["state"] == "queued"
    fingerprint = ready["payload"]["allocation_input_fingerprint"]
    completed = execute(learning, claim(learning, child_id))
    assert completed["state"] == "completed"
    assert learning.job(child_id)["payload"]["allocation_input_fingerprint"] == fingerprint


def test_source_erasure_retires_dormant_obligation_without_assessment(learning, monkeypatch):
    setup_history(learning, monkeypatch)
    store = JobStore(learning.database)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="limit", expected_revision=0,
                                     policy={"window_seconds": 3600, "max_model_calls": 2, "max_input_bytes": 2_000_000})
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    original = parent["result"]["learning_input_refs"][0]["event_id"]
    learning.memory.forget([original], kind="events", mode="erase_content", expected_scope_generation=1)
    child = learning.job(child_id)
    assert child["state"] == "superseded" and child["result"] is None
    reconsider_once(learning)
    assert learning.job(child_id)["model_calls"] == 0


def test_empty_finite_manifest_completes_without_model_and_records_discovery(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch, count=0)
    parent = encode(learning)
    child = learning.job(parent["result"]["history_integration_job_id"])
    assert child["state"] == "completed" and child["model_calls"] == 0
    assert child["result"]["bounded_discovery_assessed"] is True
    assert child["result"]["history_integration"]["assessed_count"] == 0
    assert len(requests) == 2


def test_rolling_budget_expiry_wakes_once_without_new_input_or_policy_revision(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch)
    store = JobStore(learning.database)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="limit", expected_revision=0,
                                     policy={"window_seconds": 3600, "max_model_calls": 2, "max_input_bytes": 2_000_000})
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    reconsider_once(learning)
    assert learning.job(child_id)["state"] == "deferred"
    with learning.database.transaction(learning.principal) as conn:
        conn.execute("UPDATE memory_cognitive_operations SET created_at=clock_timestamp()-interval '2 hours' "
                     "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s",
                     learning.service._params(learning.principal, learning.brain))
    reconsider_once(learning)
    ready = learning.job(child_id)
    assert ready["state"] == "queued" and ready["model_calls"] == 0
    for _ in range(3):
        reconsider_once(learning)
    assert learning.job(child_id)["payload"] == ready["payload"] and len(requests) == 2
    assert execute(learning, claim(learning, child_id))["state"] == "completed"
    assert len(requests) == 4


def test_budget_race_keeps_exact_unpaid_frozen_plan_until_original_allocation_can_run(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    frozen = learning.job(child_id)
    store = JobStore(learning.database)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="limit", expected_revision=0,
                                     policy={"window_seconds": 3600, "max_model_calls": 2, "max_input_bytes": 2_000_000})
    waiting = execute(learning, claim(learning, child_id))
    assert waiting["state"] == "deferred" and waiting["model_calls"] == 0
    assert waiting["payload"]["allocation_input_fingerprint"] == frozen["payload"]["allocation_input_fingerprint"]
    assert waiting["result"]["remaining_manifest"] == frozen["result"]["remaining_manifest"]
    reconsider_once(learning)
    store.configure_cognitive_budget(learning.principal, learning.brain, request_id="allow", expected_revision=1,
                                     policy={"window_seconds": 3600, "max_model_calls": 10, "max_input_bytes": 2_000_000})
    reconsider_once(learning)
    ready = learning.job(child_id)
    assert ready["state"] == "queued" and ready["result"] == frozen["result"]
    assert execute(learning, claim(learning, child_id))["state"] == "completed"
    assert len(requests) == 4


def test_truncated_discovery_advances_past_assessed_endpoints_under_finite_new_input_allocations(learning, monkeypatch):
    memories, requests = setup_history(learning, monkeypatch, count=5)
    learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy, max_assertions=2)
    original = encode(learning)
    child_id = original["result"]["history_integration_job_id"]
    seen = []
    for index in range(3):
        row = learning.job(child_id)
        seen.extend((entry["target_memory_version"]["memory_id"], entry["target_memory_version"]["revision"])
                    for entry in row["result"]["integration_manifest"]["objectives"])
        completed = execute(learning, claim(learning, child_id))
        assert completed["state"] == "completed", (completed["last_error"], completed["result"])
        if index < 2:
            pending_id = completed["payload"]["integration_continuation_job_id"]
            before = len(requests)
            reconsider_once(learning)
            assert len(requests) == before
            donor = encode(learning)
            leaf = learning.job(pending_id)
            while "integration_continuation_job_id" in leaf["payload"]:
                leaf = learning.job(leaf["payload"]["integration_continuation_job_id"])
            assert leaf["state"] == "queued", (
                index, leaf["result"].get("history_integration_category"),
                len(leaf["result"]["integration_manifest"]["objectives"]),
                leaf["payload"]["authorization_root_job_id"] == donor["id"],
            )
            assert leaf["payload"]["authorization_root_job_id"] == donor["id"]
            child_id = leaf["id"]
    assert set(seen) == {(item["memory_id"], item["revision"]) for item in memories}
    assert len(seen) == len(set(seen)) == 5
    assert len(requests) == 12


def test_partially_invalid_selected_work_does_not_block_unattempted_manifest_tail(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    frozen = learning.job(child_id)["result"]
    untouched = copy.deepcopy(frozen["remaining_manifest"]["objectives"])
    original = learning.service.semantic_processor.propose

    def partially_invalid(request):
        response = json.loads(original(request))
        if "integration_manifest" in json.loads(request.messages[-1]["content"]):
            response["claims"][0]["world"] = "hypothetical"
        return encoded(response)

    learning.service.semantic_processor.propose = partially_invalid
    result = execute(learning, claim(learning, child_id))
    assert result["state"] == "completed" and result["result"]["status"] == "partial"
    assert result["result"]["history_integration"]["assessed_count"] == 0
    assert result["result"]["publication"]["memory_versions"]
    tail_id = result["payload"]["integration_continuation_job_id"]
    tail = learning.job(tail_id)
    assert tail["state"] == "deferred"
    assert tail["result"]["integration_manifest"]["objectives"] == untouched
    learning.service.semantic_processor.propose = original
    encode(learning)
    funded_id = learning.job(tail_id)["payload"]["integration_continuation_job_id"]
    assert execute(learning, claim(learning, funded_id))["state"] == "completed"
    assert learning.job(child_id)["model_calls"] == 2 and len(requests) == 8


def test_unknown_selected_call_keeps_untouched_tail_runnable_under_later_input(learning, monkeypatch):
    _, requests = setup_history(learning, monkeypatch, count=4)
    parent = encode(learning)
    child_id = parent["result"]["history_integration_job_id"]
    frozen = learning.job(child_id)["result"]
    original = learning.service.semantic_processor.propose

    def unavailable(request):
        if "integration_manifest" in json.loads(request.messages[-1]["content"]):
            raise TimeoutError("Unknown remote outcome")
        return original(request)

    learning.service.semantic_processor.propose = unavailable
    failed = execute(learning, claim(learning, child_id))
    assert failed["state"] == "cancelled" and failed["model_calls"] == 1
    tail_id = failed["payload"]["integration_continuation_job_id"]
    assert learning.job(tail_id)["result"]["integration_manifest"] == frozen["remaining_manifest"]
    before = len(requests)
    for _ in range(3):
        reconsider_once(learning)
    assert len(requests) == before
    learning.service.semantic_processor.propose = original
    encode(learning)
    funded_id = learning.job(tail_id)["payload"]["integration_continuation_job_id"]
    assert execute(learning, claim(learning, funded_id))["state"] == "completed"
    assert learning.job(child_id)["model_calls"] == 1 and len(requests) == 6
    from agent_memory.helpers import DomainError

    with pytest.raises(DomainError) as error:
        JobStore(learning.database).retry_learning(learning.principal, learning.brain, child_id,
                                                  request_id="retry-selected", expected_fence=failed["fence"])
    assert error.value.code == "stage_conflict"
