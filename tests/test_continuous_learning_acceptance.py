import copy
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_bounded_learning_semantics import local_claim
from test_learning import learning as learning
from test_learning_windows import jobs
from test_semantic import claim, encoded, proposals, reference, verifier

from agent_memory.api import create_app
from agent_memory.client import MemoryClient
from agent_memory.config import Settings
from agent_memory.history_attention import POLICY as ATTENTION_POLICY
from agent_memory.history_attention import model_manifest
from agent_memory.learning import LearningEngine
from agent_memory.learning_allocations import ENCODING_PHASE, HISTORY_PHASE, phase
from agent_memory.runtime import BrainRuntime, DeliveryReceipt, RuntimePolicy
from agent_memory.runtime_client import RuntimeClient
from agent_memory.semantic import SemanticPolicy, SemanticProcessor
from agent_memory.service import MemoryService

pytestmark = pytest.mark.integration

ROUTINES = (
    "I reread my study notes in the library.",
    "I write a study outline at home.",
    "I use study flashcards on the train.",
    "I review my study mistakes on weekends.",
    "I summarize study chapters aloud.",
)
NOTEBOOK = "I keep my study notebook in a blue folder."
FIRST = " ".join(ROUTINES)
SECOND = FIRST + " These are reported routines; my present activity is unspecified."
POLICY = RuntimePolicy(memory_context_tokens=60000, max_input_tokens=90000,
                       recent_context_events=0, orientation_policy="on_demand")


def statement(fact):
    return "The user reported: " + fact


class GroundedCognition:
    def __init__(self):
        self.requests = []
        self.history_failure = None

    def propose(self, request):
        data = json.loads(request.messages[-1]["content"])
        self.requests.append((request.phase, copy.deepcopy(data)))
        if data.get("proposal_mode") == "orient-v1":
            return encoded(proposals())
        if "integration_manifest" in data:
            return self.history(data)
        current = {(item["event_id"], item["revision"]) for item in data["learning_input_refs"]}
        selected = [source for source in data["sources"]
                    if (source["event_id"], source["revision"]) in current
                    and source["observed"]["actor_role"] == "user"]
        output = []
        for source in selected:
            for fact in (*ROUTINES, NOTEBOOK):
                if fact in source["observed"]["content"]["text"]:
                    output.append(local_claim(source, proposal_id=f"report-{len(output)}",
                                              statement=statement(fact)))
        return encoded(proposals(*output))

    def history(self, data):
        manifest = data["integration_manifest"]
        if self.history_failure is not None:
            failure, self.history_failure = self.history_failure, None
            if failure == "failed":
                return "invalid provider JSON"
            raise TimeoutError("Synthetic provider outcome is unknown")
        inputs = {(item["event_id"], item["revision"]) for item in manifest["input_refs"]}
        users = [source for source in data["sources"]
                 if (source["event_id"], source["revision"]) in inputs
                 and source["observed"]["actor_role"] == "user"]
        targets = {(item["memory_id"], item["revision"]): item for item in data["existing_assertions"]}
        claims, links, decisions = [], [], []
        for index, objective in enumerate(manifest["objectives"]):
            version = objective["target_memory_version"]
            target = targets[(version["memory_id"], version["revision"])]
            match = next(((source, fact) for source in users for fact in ROUTINES
                          if target["statement"] == statement(fact)
                          and fact in source["observed"]["content"]["text"]), None)
            identifiers = []
            if match:
                source, _ = match
                candidate_id, link_id = f"repeat-{index}", f"equivalent-{index}"
                claims.append(claim(source, proposal_id=candidate_id, statement=target["statement"],
                                    **{key: copy.deepcopy(target[key]) for key in (
                                        "memory_kind", "representation", "world", "claim_kind", "conditions",
                                        "valid_time", "valid_during", "procedure")}))
                links.append({"proposal_id": link_id, "kind": "equivalent", "claim_proposal_id": candidate_id,
                              "target_memory_versions": [{"memory_id": version["memory_id"],
                                                          "revision": version["revision"]}],
                              "evidence_refs": [*copy.deepcopy(target["evidence_refs"]), reference(source)],
                              "conditions": copy.deepcopy(target["conditions"]),
                              "valid_during": copy.deepcopy(target["valid_during"]), "world": target["world"]})
                identifiers.append(link_id)
            decisions.append({"objective_id": objective["objective_id"],
                              "outcome": "proposed" if identifiers else "no_change",
                              "reason": None if identifiers else "insufficient_shared_meaning",
                              "link_proposal_ids": identifiers})
        return encoded({**proposals(*claims), "belief_links": links, "integration_decisions": decisions})

    def verify(self, request):
        data = json.loads(request.messages[-1]["content"])
        self.requests.append((request.phase, copy.deepcopy(data)))
        result = json.loads(verifier(request))
        for item, assessment in zip(data["proposals"], result["assessments"], strict=True):
            if item["group"] == "belief_links":
                assessment["link_verdict"] = "supports"
        return encoded(result)


@contextmanager
def restarted_service(harness, cognition):
    service = MemoryService(harness.database, semantic_processor=SemanticProcessor(
        cognition.propose, cognition.verify, SemanticPolicy(max_assertions=4, max_request_bytes=60000),
    ))
    harness.service = service
    settings = Settings(database_url=harness.database.dsn, api_keys={
        hashlib.sha256(b"continuous-test").hexdigest(): harness.principal,
    })
    with TestClient(create_app(settings, service=service)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path, headers=dict(request.headers),
                                      content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        with MemoryClient("http://testserver", "continuous-test", harness.brain,
                          transport=httpx.MockTransport(forward)) as memory:
            yield memory


def turn(memory, text, identifier, *, generate=lambda _: "I received your report."):
    delivered = []

    def deliver(request):
        delivered.append(request)
        return DeliveryReceipt("delivered", request.text)

    runtime = BrainRuntime(memory, generate, deliver=deliver, policy=POLICY)
    result = runtime.respond(text, message_id=identifier, conversation_id="continuous", branch_id="main")
    assert result.stage == "completed" and len(delivered) == 1
    sources = runtime.journal.sources(result.turn_id)
    assert sources["input_events"][0]["observed"]["content"]["text"] == text
    assert sources["output_events"][0]["observed"]["content"]["text"] == delivered[0].text
    assert sources["output_events"][0]["observed"]["delivery_state"] == "delivered"
    return result, sources


def drain(harness):
    for _ in range(40):
        if not LearningEngine(harness.service).work_once(harness.principal, harness.brain):
            return
    pytest.fail("The bounded worker did not quiesce")


def operations(harness, job_id=None):
    with harness.database.transaction(harness.principal) as conn:
        return conn.execute(
            "SELECT job_id,operation_id,request_fingerprint,input_bytes,outcome FROM memory_cognitive_operations "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND (%s::text IS NULL OR job_id=%s) ORDER BY job_id,operation_id",
            (*harness.service._params(harness.principal, harness.brain), job_id, job_id),
        ).fetchall()


def diagnostic(harness):
    return [{"id": row["id"], "phase": phase(row["payload"]), "state": row["state"],
             "calls": row["model_calls"], "mode": (row["result"] or {}).get("proposal_mode"),
             "category": (row["result"] or {}).get("history_integration_category"),
             "status": (row["result"] or {}).get("status"), "error": row["last_error"],
             "rejected": (row["result"] or {}).get("rejected"),
             "objectives": len((row["result"] or {}).get("integration_manifest", {}).get("objectives", [])),
             "remaining": len((row["result"] or {}).get("remaining_manifest", {}).get("objectives", []))}
            for row in jobs(harness)]


def objective_ids(manifest):
    return {item["objective_id"] for item in manifest["objectives"]}


def encoding_for(harness, sources):
    inputs = {(item["event_id"], item["revision"]) for item in sources["input_events"]}
    matching = [row for row in jobs(harness) if phase(row["payload"]) == ENCODING_PHASE
                and inputs <= {(item["event_id"], item["revision"])
                               for item in (row["result"] or {}).get("learning_input_refs", [])}]
    assert len(matching) == 1, diagnostic(harness)
    return matching[0]


def selected_reports(request):
    view = json.loads(request.memory_context)
    return view, [block for block in view["blocks"] if block["section"] in {"relevant_memory", "conditional_memory"}
                  and block["content"].get("representation") != "literal"]


def assert_exact_delivery_context(memory, earlier_sources, requests, *, routines=ROUTINES):
    def generate(request):
        requests.append(request)
        view, reports = selected_reports(request)
        expected = {statement(fact) for fact in routines}
        chosen = [block for block in reports if block["content"].get("statement") in expected]
        assert {block["content"]["statement"] for block in chosen} == expected
        prior_users = {(event["event_id"], event["revision"]): event
                       for sources in earlier_sources for event in sources["input_events"]}
        for block in chosen:
            content = block["content"]
            assert content["use"] == "historical_report_not_current_rule"
            assert content["valid_during"] == {"kind": "unknown"}
            assert content["memory_ref"] in view["memory_versions"]
            cited = [view["sources"][key] for key in block["sources"]
                     if view["sources"][key]["relationship"] == "supports"]
            assert cited
            for evidence in cited:
                source = prior_users[(evidence["event_id"], evidence["event_revision"])]
                span = evidence["span"]
                excerpt = source["observed"]["content"]["text"][span["start"]:span["end"]]
                assert content["statement"].removeprefix("The user reported: ") in excerpt
        return "\n".join(sorted(expected)) + "\nYour present activity is not established by these historical reports."

    result, sources = turn(
        memory, "Which study routines did I report for the library, home, train, weekends and aloud? "
        "Is my present activity known?", "recall", generate=generate,
    )
    assert result.text == sources["output_events"][0]["observed"]["content"]["text"]
    assert len(requests) == 1


def test_same_conversation_repays_exact_unattempted_history_after_service_restart(learning):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                              max_sources=4, max_assertions=8)
        earlier_sources = []
        for index, fact in enumerate(ROUTINES[:3]):
            _, sources = turn(memory, fact, f"first-{index}")
            earlier_sources.append(sources)
            drain(learning)
        second_sources = earlier_sources[-1]
        second = encoding_for(learning, second_sources)
        assert second["payload"]["history_attention_policy"] == ATTENTION_POLICY
        assert second["result"]["proposal_mode"] == "local-observation-v1"
        assert second["result"]["history_sampling"]["publication_restricted"] is True
        previous = learning.job(second["result"]["history_integration_job_id"])
        assert previous["state"] == "completed" and previous["model_calls"] == 1
        assert previous["result"]["history_integration"]["assessed_count"] == len(
            previous["result"]["integration_manifest"]["objectives"])
        assert previous["result"]["history_integration"]["published_count"] == 0
        deferred = [row for row in jobs(learning) if phase(row["payload"]) == HISTORY_PHASE
                    and row["state"] == "deferred"]
        assert len(deferred) == 1, diagnostic(learning)
        old = deferred[0]
        assert old["model_calls"] == old["input_bytes"] == 0 and not operations(learning, old["id"])
        frozen = copy.deepcopy(old["result"]["integration_manifest"])
        assert 1 <= len(frozen["objectives"]) < 3
        assert frozen["attention"]["admitted_count"] == 3
        assert not objective_ids(frozen) & objective_ids(previous["result"]["integration_manifest"])
        old_ledger = operations(learning)
        original_service = learning.service
    with restarted_service(learning, cognition) as memory:
        learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                              max_sources=8, max_assertions=8)
        assert learning.service is not original_service
        before = len(cognition.requests)
        for _ in range(3):
            drain(learning)
        assert len(cognition.requests) == before
        assert operations(learning) == old_ledger

        def forbidden(_):
            pytest.fail("A completed turn must replay without generation or delivery")

        runtime = BrainRuntime(memory, forbidden, deliver=forbidden, policy=POLICY)
        replay = runtime.respond(ROUTINES[2], message_id="first-2", conversation_id="continuous")
        assert replay.replayed and replay.stage == "completed"
        assert len(cognition.requests) == before
        _, donor_sources = turn(memory, NOTEBOOK, "notebook")
        drain(learning)
        donor = encoding_for(learning, donor_sources)
        old = learning.job(old["id"])
        assert old["payload"].get("integration_continuation_job_id"), diagnostic(learning)
        paid = learning.job(old["payload"]["integration_continuation_job_id"])
        assert paid["state"] == "completed", diagnostic(learning)
        assert paid["result"]["integration_manifest"] == frozen
        assert paid["payload"]["authorization_root_job_id"] == donor["id"] != second["id"]
        assert paid["payload"]["encoding_parent_job_id"] == old["payload"]["encoding_parent_job_id"]
        assert paid["result"]["history_integration"]["assessed_count"] == len(frozen["objectives"])
        assert paid["result"]["history_integration"]["published_count"] == 0
        assert paid["model_calls"] == 1 and len(operations(learning, paid["id"])) == 1
        assert all(operation["outcome"] == "completed" for operation in operations(learning, paid["id"]))
        assert all(operation in operations(learning) for operation in old_ledger)
        assert not operations(learning, old["id"])
        assert {row["payload"]["target"]["conversation_ref"] for row in jobs(learning)} == {"continuous"}
        assert {row["payload"]["target"]["branch_ref"] for row in jobs(learning)} == {"main"}
        own_history = learning.job(donor["result"]["history_integration_job_id"])
        assert own_history["state"] == "deferred" and own_history["model_calls"] == 0
        assert RuntimeClient(memory).learning_progress()["learning_phases"]["history_integration_open_count"] > 0
        matching = [data["integration_manifest"] for phase_name, data in cognition.requests
                    if phase_name == "proposal" and "integration_manifest" in data
                    and objective_ids(data["integration_manifest"]) & objective_ids(frozen)]
        assert matching == [model_manifest(frozen)]
        delivered_requests = []
        assert_exact_delivery_context(memory, earlier_sources, delivered_requests, routines=ROUTINES[:3])


@pytest.mark.parametrize("failure,outcome", [("failed", "completed"), ("unknown", "uncertain")])
def test_later_conversation_capacity_never_retries_paid_failed_or_unknown_comparisons(learning, failure, outcome):
    cognition = GroundedCognition()
    with restarted_service(learning, cognition) as memory:
        learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                              max_sources=4, max_assertions=8)
        for index, fact in enumerate(ROUTINES[:2]):
            turn(memory, fact, f"first-{index}")
            drain(learning)
        cognition.history_failure = failure
        _, second_sources = turn(memory, ROUTINES[2], "first-2")
        drain(learning)
        second = encoding_for(learning, second_sources)
        failed = learning.job(second["result"]["history_integration_job_id"])
        assert failed["state"] == "cancelled" and failed["model_calls"] == 1
        selected = objective_ids(failed["result"]["integration_manifest"])
        assert 1 <= len(selected) < 3
        assert failed["payload"]["history_attention_policy"] == ATTENTION_POLICY
        ledger = operations(learning, failed["id"])
        assert len(ledger) == 1 and ledger[0]["outcome"] == outcome
        remainder = learning.job(failed["payload"]["integration_continuation_job_id"])
        assert remainder["state"] == "deferred" and remainder["model_calls"] == 0
        untouched = copy.deepcopy(remainder["result"]["integration_manifest"])
        assert 1 <= len(objective_ids(untouched)) < 3 and not selected & objective_ids(untouched)
        assert selected | objective_ids(untouched) == {
            item["objective_id"] for item in untouched["attention"]["candidates"] if item["basis"] != "not_selected"}
        assert not operations(learning, remainder["id"])
        calls = len(cognition.requests)
    with restarted_service(learning, cognition) as memory:
        learning.service.semantic_processor.policy = replace(learning.service.semantic_processor.policy,
                                                              max_sources=8, max_assertions=8)
        for _ in range(3):
            drain(learning)
        assert len(cognition.requests) == calls and operations(learning, failed["id"]) == ledger
        _, donor_sources = turn(memory, NOTEBOOK, "notebook")
        drain(learning)
        donor = encoding_for(learning, donor_sources)
        delegated = learning.job(learning.job(remainder["id"])["payload"]["integration_continuation_job_id"])
        assert delegated["state"] == "completed"
        assert delegated["payload"]["authorization_root_job_id"] == donor["id"]
        assert delegated["payload"]["encoding_parent_job_id"] == second["id"]
        assert delegated["result"]["integration_manifest"] == untouched
        assert delegated["result"]["history_integration"]["assessed_count"] == len(untouched["objectives"])
        for _ in range(3):
            drain(learning)
        retained = learning.job(failed["id"])
        assert retained["state"] == "cancelled" and retained["model_calls"] == 1
        assert retained["result"]["history_integration"]["assessed_count"] == 0
        assert operations(learning, failed["id"]) == ledger
        later = [row for row in jobs(learning) if phase(row["payload"]) == HISTORY_PHASE
                 and row["id"] != failed["id"] and row["payload"].get("encoding_parent_job_id") == second["id"]]
        for row in later:
            for key in ("integration_manifest", "remaining_manifest"):
                if key in (row["result"] or {}):
                    assert not selected & objective_ids(row["result"][key])
        retried = [data for phase_name, data in cognition.requests[calls:]
                   if phase_name == "proposal" and "integration_manifest" in data
                   and selected & objective_ids(data["integration_manifest"])]
        assert retried == []
        summary = RuntimeClient(memory).learning_progress()["learning_phases"]
        assert summary["history_integration_open_count"] > 0
        assert summary["history_integration_unassessed_objective_count"] >= len(selected)
