import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_belief_semantic import UNBOUNDED, link_verifier, version
from test_cognitive_archives import archive as archive
from test_semantic import claim, encoded, episode, proposals, reference, run, situation, verifier

from agent_memory import candidates, cognition, consolidation
from agent_memory import service as service_module
from agent_memory.api import create_app
from agent_memory.client import MemoryClient, default_budget
from agent_memory.config import Settings
from agent_memory.consolidation_planning import plan_consolidation
from agent_memory.dynamics import access_state
from agent_memory.helpers import digest, uid
from agent_memory.runtime import BrainRuntime, DeliveryReceipt, RuntimePolicy
from agent_memory.runtime_client import RuntimeClient
from agent_memory.semantic import SemanticProcessor
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration
POLICY = RuntimePolicy(memory_context_tokens=60000, max_input_tokens=90000, recent_context_events=0,
                       orientation_policy="on_demand")
WORK = {"op": "holds", "subject_ref": "user", "predicate_ref": {"id": "core.activity", "version": 1},
        "value": {"type": "concept", "id": "activity:work"}}
WORKING_QUERY = ("From 2020-01-01T00:00:00Z onward with no scheduled end I am working; "
                 "describe my recorded walking pattern.")
SHARED = "In the two recorded working instances, walking preceded study."


@pytest.fixture
def acceptance(archive):
    with client_for(archive, archive.service) as memory:
        yield SimpleNamespace(store=archive, memory=memory, requests=[], deliveries=[])


@contextmanager
def client_for(store, service):
    settings = Settings(database_url=store.database.dsn, api_keys={
        hashlib.sha256(b"v1-acceptance").hexdigest(): store.principal,
    })
    with TestClient(create_app(settings, service=service)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path,
                                      headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        with MemoryClient("http://testserver", "v1-acceptance", store.brain_id,
                          transport=httpx.MockTransport(forward)) as memory:
            yield memory


def observe(harness, text, *, key=None, revision=1, conversation="test"):
    response = harness.memory.observe(text, conversation_id=conversation, branch_id="main",
                                      producer_ref="v1-acceptance", source_event_key=key or uid(),
                                      source_revision=revision, interpretation_hint="real_world_report")
    identifier, = response["result"]["accepted_event_ids"]
    store = harness.store
    with store.database.transaction(store.principal) as conn:
        brain = store.service._brain(conn, store.principal, harness.memory.brain_id)
        return store.service._events(conn, store.principal, brain, event_ids=[identifier])[0]


def deliver(harness, text, render, *, conversation=None, message_id=None):
    def generate(request):
        harness.requests.append(request)
        return render(json.loads(request.memory_context))

    def sent(request):
        harness.deliveries.append(request)
        return DeliveryReceipt("delivered")

    runtime = BrainRuntime(harness.memory, generate, deliver=sent, policy=POLICY)
    result = runtime.respond(text, message_id=message_id or uid(), conversation_id=conversation or uid())
    assert result.stage == "completed" and result.text == harness.deliveries[-1].text
    row = runtime.journal.get(result.turn_id)
    assert row["delivery_receipt"]["content_sha256"] == hashlib.sha256(result.text.encode()).hexdigest()
    assert row["prepared_memory_selection"]["state"] == "complete"
    return result, json.loads(harness.requests[-1].memory_context), row


def blocks(context):
    return [(context["memory_versions"][block["content"]["memory_ref"]], block["content"])
            for block in context["blocks"] if isinstance(block.get("content"), dict)
            and "memory_ref" in block["content"]]


def selected(context, memory):
    return next(content for ref, content in blocks(context) if ref == version(memory))


def access(harness, memory):
    store = harness.store
    with store.database.transaction(store.principal) as conn:
        return conn.execute("SELECT * FROM memory_access_state WHERE tenant_id=%s AND owner_id=%s "
                            "AND brain_id=%s AND memory_id=%s AND memory_revision=%s",
                            (*store.service._params(store.principal, harness.memory.brain_id),
                             memory["memory_id"], memory["revision"])).fetchone()


def publish(harness, sources, proposed, *, parents=()):
    batch = harness.store.batch(*sources, existing_assertions=tuple(parents))
    proposed = copy.deepcopy(proposed)
    planned = plan_consolidation(batch.sources, batch.existing_assertions)
    proposed["consolidation_decisions"] = []
    for objective in planned["objectives"]:
        links = [link["proposal_id"] for link in proposed.get("belief_links", [])
                 if link["kind"] == "abstracts" and sorted(link["target_memory_versions"], key=digest)
                 == sorted(objective["instance_memory_versions"], key=digest)]
        proposed["consolidation_decisions"].append({
            "objective_id": objective["objective_id"], "outcome": "proposed" if links else "no_change",
            "reason": None if links else "insufficient_shared_meaning", "link_proposal_ids": links,
        })
    result = run(batch, proposed, verify=link_verifier)
    assert result.status == "completed", result.rejected
    metadata = harness.store.publish(batch, result)
    versions = metadata["memory_versions"]
    return [row["document"] for row in harness.store.rows("memory_assertions") if version(row["document"]) in versions]


def exact_score(response, memory):
    item = next(item for item in response["result"]["items"] if version(item["memory"]) == version(memory))
    return sum(signal["score"] for signal in item["rank_signals"])


def test_synthetic_age_cues_and_two_hundred_internal_selections_do_not_create_external_credit(
        acceptance, monkeypatch):
    harness = acceptance
    clock = SimpleNamespace(at=datetime(2020, 1, 1, tzinfo=UTC))
    for module in (service_module, cognition, candidates, consolidation):
        monkeypatch.setattr(module, "now", lambda: clock.at.isoformat())
    original = observe(harness, "My childhood keepsake is a quartz whistle.")
    memory, = publish(harness, [original], proposals(claim(
        original, statement="The user reported a quartz whistle as their childhood keepsake.",
        memory_kind="episodic", representation="episode_fragment", claim_kind="reported_fact",
        conditions={"op": "always"}, valid_during=UNBOUNDED)))
    frozen = copy.deepcopy(memory)
    scores = []
    for days in (0, 365, 3650):
        clock.at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=days)
        ordinary = harness.memory.recall("quartz keepsake", conversation_id="age", branch_id="main")
        cue_input = observe(harness, "What was my quartz keepsake?", conversation="age")
        cue = harness.memory.recall("quartz keepsake", conversation_id="age", branch_id="main", intent="cue_recovery",
                                    cue_event_ids=[cue_input["event_id"]])
        scores.append(exact_score(ordinary, memory))
        assert exact_score(cue, memory) >= scores[-1] > 0
        result, context, _ = deliver(harness, "What was my quartz keepsake?",
                                     lambda context: selected(context, memory)["statement"])
        assert "whistle" in result.text and "whistle" not in harness.requests[-1].messages[-1]["content"]
        assert original["event_id"] in {ref["event_id"] for ref in context["sources"].values()}
    assert scores[0] > scores[1] > scores[2]

    interference = observe(harness, "My current keepsake is a quartz keyring.")
    fresh, = publish(harness, [interference], proposals(claim(
        interference, statement="The user reported a quartz keyring as their current keepsake.",
        memory_kind="episodic", representation="episode_fragment", claim_kind="reported_fact",
        conditions={"op": "always"}, valid_during=UNBOUNDED)))
    competition = harness.memory.recall("quartz keepsake", conversation_id="age", branch_id="main")
    assert exact_score(competition, fresh) > exact_score(competition, memory)
    question = observe(harness, "What was my quartz childhood keepsake?", conversation="internal")
    for index in range(200):
        packet = harness.memory.prepare(f"internal-{index}", conversation_id="internal", branch_id="main",
                                        latest_input_event_ids=[question["event_id"]], recent_event_limit=0,
                                        budget=default_budget(60000))["result"]
        assert memory["memory_id"] in {trace["memory_id"] for trace in packet["recall_traces"]}
    assert access(harness, memory) is None and access(harness, fresh) is None

    _, context, _ = deliver(harness, "What was my quartz childhood keepsake?",
                            lambda context: selected(context, memory)["statement"], conversation="feedback")
    assert "whistle" in selected(context, memory)["statement"]
    deliver(harness, "yes", lambda _: "Acknowledged.", conversation="feedback", message_id="confirmation")
    positive = access(harness, memory)
    assert (positive["external_uses"], positive["successes"], positive["failures"]) == (1, 1, 0)
    assert access_state(memory, positive, at=clock.at)["activation"] > access_state(memory, None, at=clock.at)["activation"]

    deliver(harness, "What was my quartz childhood keepsake?",
            lambda context: selected(context, memory)["statement"], conversation="feedback")
    deliver(harness, "wrong", lambda _: "Acknowledged.", conversation="feedback", message_id="correction")
    corrected = access(harness, memory)
    assert (corrected["external_uses"], corrected["successes"], corrected["failures"]) == (2, 1, 1)
    assert corrected["last_reinforcement_at"] == positive["last_reinforcement_at"]
    assert access_state(memory, corrected, at=clock.at)["activation"] == access_state(memory, positive, at=clock.at)["activation"]
    assert access_state(memory, corrected, at=clock.at)["utility"] < access_state(memory, positive, at=clock.at)["utility"]
    stored = next(row["document"] for row in harness.store.rows("memory_assertions") if row["id"] == memory["memory_id"])
    assert stored == frozen
    assert RuntimeClient(harness.memory).cognitive_usage()["reservation_count"] == 0
    fresh_service = MemoryService(Database(harness.store.database.dsn))
    with client_for(harness.store, fresh_service) as reopened:
        restarted = SimpleNamespace(store=harness.store, memory=reopened, requests=[], deliveries=[])
        result, context, _ = deliver(restarted, "What was my quartz childhood keepsake?",
                                     lambda context: selected(context, memory)["statement"])
        assert "whistle" in result.text
        assert selected(context, memory)["use"] == "historical_report_not_current_rule"
        assert access(restarted, memory) == corrected


def graph(harness):
    originals = [observe(harness, text, key=key) for key, text in (
        ("first", "While working I walked before studying algebra and wore a cobalt coat."),
        ("second", "While working I walked before studying biology and wore an ochre hat."),
    )]
    parents = [publish(harness, [source], proposals(claim(
        source, statement=source["observed"]["content"]["text"], memory_kind="episodic",
        representation="episode_outline", claim_kind="reported_fact", conditions=WORK, valid_during=UNBOUNDED),
        episodes=[episode(source, organization_statement="The user reports one working study instance.")]))[0]
        for source in originals]
    refs = [reference(source) for source in originals]
    shared = claim(originals[0], proposal_id="shared", statement=SHARED, memory_kind="semantic",
                   representation="schema", claim_kind="inferred_pattern", conditions=WORK,
                   valid_during=UNBOUNDED, evidence_refs=refs, source_memory_versions=[version(parent) for parent in parents])
    link = {"proposal_id": "abstracts", "kind": "abstracts", "claim_proposal_id": "shared",
            "target_memory_versions": [version(parent) for parent in parents], "evidence_refs": refs,
            "world": "real_world", "conditions": WORK, "valid_during": UNBOUNDED}
    schema, = publish(harness, originals, {**proposals(shared), "belief_links": [link]}, parents=parents)
    return originals, parents, schema


def current_work(harness):
    calls = []

    def propose(request):
        calls.append(request)
        data = json.loads(request.messages[-1]["content"])
        assert data["proposal_mode"] == "orient-v1"
        source = next(source for source in data["sources"] if source["observed"]["content"]["text"] == WORKING_QUERY)
        observation = {"observation_id": "observed-work", "subject_ref": "user",
                       "predicate_ref": WORK["predicate_ref"], "value": WORK["value"], "polarity": "positive",
                       "valid_during": {"kind": "interval", "start": {"kind": "instant", "at": "2020-01-01T00:00:00Z"},
                                        "end": {"kind": "unbounded"}}, "evidence_refs": [reference(source)]}
        return encoded({"situation_updates": [situation(source, kind="context", key="activity", value="working",
                                                         evidence_use="source_content", observation=observation)]})

    harness.store.service.semantic_processor = SemanticProcessor(propose, verifier)
    return calls


def test_sampled_schema_reaches_delivered_context_without_residual_details_and_parent_conflict_stays_uncertain(acceptance):
    harness = acceptance
    originals, parents, schema = graph(harness)
    frozen = copy.deepcopy((originals, parents, schema))
    current_work(harness)

    def answer(context):
        item = selected(context, schema)
        if item["use"] == "source_attributed_applicable_memory":
            return item["statement"]
        return "The recorded pattern has unresolved evidence; I cannot treat it as a settled current rule."

    result, context, _ = deliver(harness, WORKING_QUERY, answer)
    assert result.text == SHARED
    assert selected(context, schema)["conditions"] == WORK
    assert selected(context, schema)["applicability"]["value"] == "true"
    assert "cobalt" not in result.text and "ochre" not in result.text and "algebra" not in result.text
    assert {ref["event_id"] for ref in context["sources"].values()} >= {source["event_id"] for source in originals}
    assert all(selected(context, parent)["statement"] == parent["statement"] for parent in parents)

    counter = observe(harness, "I correct the algebra account: while working I studied before walking.")
    changed = claim(counter, proposal_id="counter", statement=counter["observed"]["content"]["text"],
                    conditions=WORK, valid_during=UNBOUNDED)
    relation = {"proposal_id": "conflict", "kind": "contradicts", "claim_proposal_id": "counter",
                "target_memory_versions": [version(parents[0])], "evidence_refs": [reference(originals[0]), reference(counter)],
                "world": "real_world", "conditions": WORK, "valid_during": UNBOUNDED}
    publish(harness, [originals[0], counter], {**proposals(changed), "belief_links": [relation]}, parents=[parents[0]])
    result, context, _ = deliver(harness, WORKING_QUERY, answer)
    assert "unresolved evidence" in result.text
    changed_context = selected(context, schema)
    assert changed_context["use"] == "belief_not_settled_current_rule"
    assert changed_context["applicability"]["value"] == "true"
    assert changed_context["belief_resolution"]["complete"] is True
    assert changed_context["belief_resolution"]["state"] == "uncertain"
    assert selected(context, parents[0])["belief_resolution"]["state"] == "conflicted"
    assert counter["event_id"] in {ref["event_id"] for ref in context["sources"].values()}
    assert (originals, parents, schema) == frozen


def test_erased_ancestor_cannot_return_through_schema_output_or_restored_archive_but_independent_memory_survives(acceptance):
    harness = acceptance
    originals, parents, schema = graph(harness)
    independent = observe(harness, "The emergency locker label is larch-4902.")
    survivor, = publish(harness, [independent], proposals(claim(
        independent, statement=independent["observed"]["content"]["text"], conditions={"op": "always"},
        valid_during=UNBOUNDED)))
    question = "Recall the walking study accounts and the emergency locker label."

    def answer(context):
        return "\n".join(content["statement"] for _, content in blocks(context)) or "No supporting record was found."

    first, context, row = deliver(harness, question, answer)
    assert "cobalt" in first.text and "larch-4902" in first.text
    assert selected(context, schema)["statement"] == SHARED
    exported = harness.memory.export(budget=default_budget(64000))["result"]
    original_archive = harness.memory.download_archive(exported["archive_id"])
    assert original_archive["document"]["belief_relations"]
    assert original_archive["document"]["cognitive_views"]["associations"]
    assert original_archive["document"]["event_dependencies"]
    harness.memory.forget([originals[0]["event_id"]], kind="events", mode="erase_content",
                          expected_scope_generation=RuntimeClient(harness.memory).status()["scope_generation"])
    sanitized = harness.memory.download_archive(exported["archive_id"])
    assert "cobalt" not in json.dumps(sanitized)
    assert schema["memory_id"] not in {item["memory_id"] for item in sanitized["document"]["memories"]}
    assert not RuntimeClient(harness.memory).get(row["turn_id"])["sources_available"]

    after, context, _ = deliver(harness, question, answer)
    assert "cobalt" not in harness.requests[-1].memory_context and "cobalt" not in after.text
    assert "larch-4902" in after.text and selected(context, survivor)["statement"] == survivor["statement"]
    assert schema["memory_id"] not in {ref["memory_id"] for ref, _ in blocks(context)}
    harness.memory.brain_id = harness.memory.create_brain("Restored deterministic acceptance")['id']
    restored = harness.memory.restore(exported["archive_id"], expected_manifest_sha256=sanitized["manifest_sha256"],
                                      expected_scope_generation=1, mode="new_brain", budget=default_budget(64000))
    assert restored["result"]["read_enabled"]
    recovered, context, _ = deliver(harness, question, answer)
    assert "cobalt" not in harness.requests[-1].memory_context and "cobalt" not in recovered.text
    assert "larch-4902" in recovered.text and selected(context, survivor)["statement"] == survivor["statement"]
    assert digest(original_archive["document"]) == original_archive["manifest_sha256"]
