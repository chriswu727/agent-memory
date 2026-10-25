import copy
import json

import pytest
from test_bounded_learning_semantics import local_claim
from test_learning import learning as learning
from test_learning_neighborhood import publish, source
from test_learning_windows import claim, execute, jobs
from test_semantic import encoded, proposals, verifier

from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import CONTRACT_VERSION, ENCODING_PHASE, phase
from agent_memory.learning_windows import recover_windows, select_window
from agent_memory.neighborhood import _Incomplete, _Selector
from agent_memory.semantic import SemanticProcessor

pytestmark = pytest.mark.integration
TARGET = {"conversation_ref": "chat", "branch_ref": "main"}


def install_provider(harness):
    requests = []

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        requests.append(copy.deepcopy(data))
        latest = max(data["sources"], key=lambda event: event["sequence"])
        return encoded(proposals(local_claim(latest, statement=latest["observed"]["content"]["text"])))

    harness.service.semantic_processor = SemanticProcessor(propose, verifier)
    return requests


def retract(harness, memory, correction):
    with harness.database.transaction(harness.principal) as connection:
        brain = harness.service._brain(connection, harness.principal, harness.brain)
    response = harness.memory.correct(
        memory["memory_id"], expected_revision=memory["revision"],
        expected_dependency_generation=brain["dependency_generation"],
        correction_event_ids=[correction["event_id"]], operation="retract",
        conversation_id="chat", branch_id="main",
    )
    assert response["outcome"] == "completed"
    return response["result"]


def test_corrected_revision_one_stays_excluded_but_user_revision_two_can_encode(learning):
    learning.service.semantic_processor = None
    original = source(learning, "I attended Cedar School.", source_key="school-report")
    memory = publish(learning, original)
    correction = source(learning, "Correction: that old school report is withdrawn.")
    retract(learning, memory, correction)
    requests = install_provider(learning)
    with learning.database.transaction(learning.principal) as connection:
        brain = learning.service._brain(connection, learning.principal, learning.brain)
        policy = learning.service.semantic_processor.policy
        refs, window = select_window(
            connection, learning.service, learning.principal, brain,
            {"target": TARGET, "from_sequence": original["sequence"], "through_sequence": original["sequence"]},
            policy=policy,
        )
        assert refs == [] and window["skipped_unavailable_count"] == 1
        selector = _Selector(connection, learning.service, learning.principal, brain, TARGET,
                             brain["durable_sequence"], policy)
        with pytest.raises(_Incomplete):
            selector.source_package([original["event_id"]])

    revised = source(learning, "Correction: I attended Maple School.", source_key="school-report", revision=2)
    assert revised["event_id"] == original["event_id"] and revised["revision"] == 2
    pending = [job for job in jobs(learning) if job["state"] == "queued"]
    assert len(pending) == 1
    assert pending[0]["payload"]["learning_contract_version"] == CONTRACT_VERSION
    completed = execute(learning, claim(learning, pending[0]["id"]))
    assert completed["state"] == "completed"
    assert completed["result"]["learning_window"]["processing_succeeded"]
    assert completed["result"]["learning_input_refs"] == [{"event_id": revised["event_id"], "revision": 2}]
    assert requests and all(event["revision"] == 2 for event in requests[0]["sources"]
                            if event["event_id"] == original["event_id"])
    assert completed["result"]["publication"]["memory_versions"]
    with learning.database.transaction(learning.principal) as connection:
        old = connection.execute(
            "SELECT invalidated,document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s "
            "AND brain_id=%s AND id=%s AND is_current",
            (learning.principal.tenant_id, learning.principal.principal_id, learning.brain, memory["memory_id"]),
        ).fetchone()
    assert old["invalidated"] and old["document"]["lifecycle"] == "retracted"


def test_unpaid_encoding_keeps_correction_input_after_optional_history_is_corrected(learning):
    learning.service.semantic_processor = None
    old_source = source(learning, "I prefer morning study conversation.")
    memory = publish(learning, old_source, statement="The user reported preferring morning study conversation.")
    requests = install_provider(learning)
    correction = source(learning, "Correction: I prefer evening study conversation.")
    pending = [job for job in jobs(learning) if phase(job["payload"]) == ENCODING_PHASE]
    assert len(pending) == 1
    running = claim(learning, pending[0]["id"])
    engine = LearningEngine(learning.service)
    _, snapshot = engine._snapshot(learning.principal, learning.brain, running)
    assert {ref["event_id"] for ref in snapshot["source_snapshot"]} >= {
        old_source["event_id"], correction["event_id"],
    }
    original_root = running["payload"]["authorization_root_job_id"]
    before = learning.job(running["id"])
    assert before["model_calls"] == 0 and before["payload"]["allocation_input_fingerprint"]
    retract(learning, memory, correction)
    assert requests == []
    with learning.database.transaction(learning.principal) as connection:
        brain = learning.service._brain(connection, learning.principal, learning.brain)
        current = learning.service._events(connection, learning.principal, brain,
                                           event_ids=[correction["event_id"]])
        recover_windows(connection, learning.service, learning.principal, brain)
    assert [(event["event_id"], event["revision"]) for event in current] == [(correction["event_id"], 1)]
    replacement = [job for job in jobs(learning) if job["state"] == "queued"
                   and phase(job["payload"]) == ENCODING_PHASE
                   and job["payload"]["from_sequence"] <= correction["sequence"] <= job["payload"]["through_sequence"]]
    assert replacement, "A valid correction input lost all autonomous encoding after optional history changed"
    assert all(job["payload"]["authorization_root_job_id"] == original_root for job in replacement)
    assert all(job["model_calls"] == 0 for job in jobs(learning))
    completed = execute(learning, claim(learning, replacement[0]["id"]))
    assert completed["state"] == "completed" and completed["model_calls"] == 2
    assert completed["result"]["learning_window"]["processing_succeeded"]
    assert {event["event_id"] for event in requests[0]["sources"]} == {correction["event_id"]}
    assert completed["result"]["publication"]["memory_versions"]
    assert learning.job(original_root)["model_calls"] == 0
