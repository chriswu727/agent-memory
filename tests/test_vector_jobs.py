import copy
import json

import httpx
import pytest
from test_turns import turns as turns

from membrain.client import default_budget, default_read_fence
from membrain.embeddings import ChatCompletionsEmbeddingProvider
from membrain.helpers import DomainError, uid

pytestmark = pytest.mark.integration


def provider(turns, calls, mutate=None):
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        with turns.database.transaction(turns.principal) as conn:
            conn.execute("SET LOCAL lock_timeout='200ms'")
            conn.execute("SELECT id FROM memory_brains WHERE id=%s FOR UPDATE", (turns.brain,)).fetchone()
        if mutate:
            mutate()
        return httpx.Response(200, json={"model": "fake-vector", "data": [
            {"index": index, "embedding": [1, 0, 0]} for index, _ in enumerate(body["input"])
        ]})
    return ChatCompletionsEmbeddingProvider("http://fake.test/v1", "fake-key", "fake-vector",
                                           model_version="unit-v1", dimensions=3, transport=httpx.MockTransport(respond))


def recall_request(turns, text="meeting spot", embedding_calls=1):
    return {"contract_version": "1.0.0-draft", "message_type": "request", "request_id": uid(),
            "action": "recall", "target": {"brain_ref": turns.brain, "conversation_ref": turns.conversation,
            "branch_ref": turns.branch}, "payload": {"query": text, "intent": "explore", "cue_event_ids": [],
            "additional_cues": [], "read_fence": default_read_fence(),
            "budget": {**default_budget(16000), "max_embedding_calls": embedding_calls, "max_cost_microunits": 1000}}}


def drain(turns):
    for _ in range(20):
        if not turns.service.work_once(turns.principal, turns.brain):
            return
    pytest.fail("Unexpected endless jobs")


def test_embedding_jobs_and_paraphrase_recall_execute_outside_database_lock(turns):
    calls = []
    with provider(turns, calls) as vectors:
        turns.service.embedding_provider = vectors
        turns.observe("The rendezvous is beneath the crimson canopy.")
        drain(turns)
        assert len(calls) == 1
        result = turns.service.handle(turns.principal, recall_request(turns))
        assert result["outcome"] == "completed", result
        assert any("crimson canopy" in item["memory"]["statement"] for item in result["result"]["items"])
        assert any(signal["method"] == "vector" for item in result["result"]["items"] for signal in item["rank_signals"])
        assert result["result"]["usage"]["embedding_calls"] == 1
        assert result["result"]["usage"]["actual_cost_microunits"] is None
        assert len(calls) == 2
    with turns.database.transaction(turns.principal) as conn:
        assert conn.execute("SELECT count(*) AS n FROM memory_embeddings WHERE brain_id=%s", (turns.brain,)).fetchone()["n"] == 1
        assert conn.execute("SELECT result FROM memory_jobs WHERE brain_id=%s AND kind='query_embedding'", (turns.brain,)).fetchone()["result"] is None


def test_query_idempotency_and_zero_embedding_budget(turns):
    calls = []
    with provider(turns, calls) as vectors:
        turns.service.embedding_provider = vectors
        turns.observe("My favourite tree is a willow.")
        drain(turns)
        count = len(calls)
        zero = turns.service.handle(turns.principal, recall_request(turns, "willow", 0))
        assert zero["outcome"] == "completed" and len(calls) == count
        request = recall_request(turns)
        first = turns.service.handle(turns.principal, request)
        repeated = turns.service.handle(turns.principal, request)
        assert first == repeated and len(calls) == count + 1
        changed = copy.deepcopy(request)
        changed["payload"]["query"] = "another question"
        assert turns.service.handle(turns.principal, changed)["error"]["code"] == "idempotency_conflict"
        assert len(calls) == count + 1


def test_embedding_response_cannot_republish_a_deleted_source(turns):
    calls = []
    ref = turns.observe("Private forgotten phrase")
    with provider(turns, calls) as vectors:
        turns.service.embedding_provider = vectors
        assert turns.service.work_once(turns.principal, turns.brain) == 1
    def erase():
        turns.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [ref["event_id"]]},
                                "mode": "erase_content", "expected_scope_generation": 1})
    with provider(turns, calls, erase) as vectors:
        turns.service.embedding_provider = vectors
        with pytest.raises(DomainError):
            turns.service.work_once(turns.principal, turns.brain)
    with turns.database.transaction(turns.principal) as conn:
        assert not conn.execute("SELECT 1 FROM memory_embeddings WHERE brain_id=%s", (turns.brain,)).fetchone()
        source = conn.execute("SELECT deleted,document FROM memory_events WHERE brain_id=%s AND id=%s",
                              (turns.brain, ref["event_id"])).fetchone()
        assert source["deleted"] and source["document"] is None


def test_failed_embedding_reservation_is_charged_and_retry_does_not_call_again(turns):
    calls = []
    def failure(request):
        calls.append(request)
        raise httpx.ReadTimeout("private provider message")
    with ChatCompletionsEmbeddingProvider("http://fake.test/v1", "fake-key", "fake-vector", model_version="v1",
                                          dimensions=3, transport=httpx.MockTransport(failure)) as vectors:
        turns.service.embedding_provider = vectors
        request = recall_request(turns)
        first = turns.service.handle(turns.principal, request)
        repeated = turns.service.handle(turns.principal, request)
        assert first["outcome"] in {"failed", "rejected"} and repeated["outcome"] in {"failed", "rejected"}
        assert "private provider" not in str(first) + str(repeated)
        assert len(calls) == 1
    with turns.database.transaction(turns.principal) as conn:
        job = conn.execute("SELECT model_calls FROM memory_jobs WHERE brain_id=%s AND kind='query_embedding'", (turns.brain,)).fetchone()
        assert job["model_calls"] == 1


def test_memories_written_before_a_provider_are_backfilled_once(turns):
    from membrain import vector_jobs

    turns.observe("The spare key is under the blue flowerpot.")
    turns.observe("My dentist appointment moved to Thursday.")
    drain(turns)
    calls, now = [], [1000.0]
    with provider(turns, calls) as vectors:
        turns.service.embedding_provider = vectors
        assert vector_jobs.backfill_embeddings(turns.service, turns.principal, turns.brain, clock=lambda: now[0]) >= 2
        drain(turns)
        with turns.database.transaction(turns.principal) as conn:
            embedded = conn.execute("SELECT count(*) AS n FROM memory_embeddings WHERE brain_id=%s",
                                    (turns.brain,)).fetchone()["n"]
            current = conn.execute("SELECT count(*) AS n FROM memory_assertions WHERE brain_id=%s AND is_current "
                                   "AND NOT deleted AND NOT suppressed AND NOT invalidated AND document IS NOT NULL",
                                   (turns.brain,)).fetchone()["n"]
        assert embedded == current >= 2
        count = len(calls)
        assert vector_jobs.backfill_embeddings(turns.service, turns.principal, turns.brain, clock=lambda: now[0]) == 0
        now[0] += vector_jobs.BACKFILL_RESCAN_SECONDS + 1
        assert vector_jobs.backfill_embeddings(turns.service, turns.principal, turns.brain, clock=lambda: now[0]) == 0
        assert len(calls) == count
        result = turns.service.handle(turns.principal, recall_request(turns, "where is the house key hidden"))
        assert any("flowerpot" in item["memory"]["statement"] for item in result["result"]["items"])


def test_unmetered_local_models_do_not_consume_the_background_budget(turns):
    from membrain.embeddings import EmbeddingResult, EmbeddingSpace

    class Local:
        metered = False
        space = EmbeddingSpace("local-test", "v1", 3)

        def __init__(self):
            self.calls = 0

        def request_metadata(self, request):
            raise AssertionError("unmetered models are not reserved")

        def __call__(self, request):
            self.calls += 1
            return EmbeddingResult(self.space, tuple((1.0, 0.0, 0.0) for _ in request.texts), None, "local", 0)

    local = Local()
    turns.service.embedding_provider = local
    for index in range(3):
        turns.observe(f"Local memory number {index} about the canal boat.")
    drain(turns)
    result = turns.service.handle(turns.principal, recall_request(turns, "canal boat"))
    assert result["outcome"] == "completed" and local.calls == 2
    with turns.database.transaction(turns.principal) as conn:
        assert conn.execute("SELECT coalesce(sum(model_calls),0) AS n FROM memory_jobs WHERE brain_id=%s "
                            "AND kind IN ('embed','query_embedding')", (turns.brain,)).fetchone()["n"] == 0


def test_other_script_neighbours_are_interleaved_without_duplicates():
    from membrain.candidates import _interleave

    def row(name):
        return {"document": {"memory_id": name, "revision": 1}}

    primary = [row(f"en{index}") for index in range(7)]
    merged = _interleave(primary, [row("zh0"), row("en1"), row("zh1"), row("zh2"), row("zh3")])
    assert [item["document"]["memory_id"] for item in merged] == [
        "en0", "en1", "en2", "zh0", "en3", "en4", "en5", "zh1", "en6", "zh2", "zh3"]


def test_local_indexing_batches_inference_without_consuming_paid_budgets(turns):
    from membrain.embeddings import EmbeddingResult, EmbeddingSpace
    from membrain.helpers import digest

    class Local:
        metered = False
        space = EmbeddingSpace("local-batch-test", "v1", 3)

        def __init__(self):
            self.calls = []

        def __call__(self, request):
            self.calls.append(request.texts)
            with turns.database.transaction(turns.principal) as conn:
                conn.execute("SET LOCAL lock_timeout='200ms'")
                conn.execute("SELECT id FROM memory_brains WHERE id=%s FOR UPDATE", (turns.brain,))
            return EmbeddingResult(self.space, tuple((1.0, 0.0, 0.0) for _ in request.texts), {},
                                   digest(request.texts), sum(len(text.encode()) for text in request.texts))

    vectors = Local()
    turns.service.embedding_provider = vectors
    for text in ("The appointment is on Monday.", "The meeting is at the blue cafe.", "My sister lives in Lisbon."):
        turns.observe(text)
    drain(turns)
    assert any(len(batch) > 1 for batch in vectors.calls)
    with turns.database.transaction(turns.principal) as conn:
        assert conn.execute("SELECT count(*) AS n FROM memory_embeddings WHERE brain_id=%s", (turns.brain,)).fetchone()["n"] >= 3
        assert conn.execute("SELECT sum(model_calls) AS n FROM memory_jobs WHERE brain_id=%s AND kind='embed'",
                            (turns.brain,)).fetchone()["n"] == 0


def test_erasure_during_a_local_batch_does_not_publish_erased_content_or_cancel_other_sources(turns):
    from membrain.embeddings import EmbeddingResult, EmbeddingSpace

    first = turns.observe("A private appointment is on Monday.")
    turns.observe("The meeting is at the blue cafe.")
    drain(turns)

    class Local:
        metered = False
        space = EmbeddingSpace("local-erasure-batch", "v1", 3)

        def __call__(self, request):
            turns.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [first["event_id"]]},
                                    "mode": "erase_content", "expected_scope_generation": 1})
            return EmbeddingResult(self.space, tuple((1.0, 0.0, 0.0) for _ in request.texts), None, "local", 0)

    turns.service.embedding_provider = Local()
    with pytest.raises(DomainError):
        turns.service.work_once(turns.principal, turns.brain)
    with turns.database.transaction(turns.principal) as conn:
        rows = conn.execute("SELECT a.document FROM memory_embeddings v JOIN memory_assertions a ON "
                            "a.tenant_id=v.tenant_id AND a.owner_id=v.owner_id AND a.brain_id=v.brain_id "
                            "AND a.id=v.memory_id AND a.revision=v.memory_revision WHERE v.brain_id=%s",
                            (turns.brain,)).fetchall()
        assert rows and all("private appointment" not in row["document"]["statement"] for row in rows)
