import copy
import hashlib
import importlib
import json
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_evaluation_archive import rebound, sample_archive
from test_learning import learning as learning

from agent_memory.belief_store import assertion_fingerprint
from agent_memory.helpers import digest
from agent_memory.runtime import ModelRequest, TurnResult


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.longitudinal_cue_diagnostics")


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected database, environment or provider access")


def test_plan_is_offline_bounded_and_withholds_target_details(diagnostic, monkeypatch, capsys):
    monkeypatch.setattr(diagnostic.Settings, "from_env", forbidden)
    monkeypatch.setattr(diagnostic, "Database", forbidden)
    monkeypatch.setattr(sys, "argv", ["diagnostic", "--plan"])
    diagnostic.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["max_calls"] == 15
    assert planned["max_reserved_output_tokens"] == 5 * (4096 + 2 * 16384)
    assert planned["per_copy_allocation"] == {"host": 1, "cognition": 2}
    assert planned["provider"]["thinking"] == "enabled"
    assert planned["provider"]["reasoning_effort"] == "high"
    assert planned["cognitive_reasoning_effort"] == "low"
    assert planned["provider_envelope_bytes"] == 262144
    assert planned["semantic_policy"]["max_response_bytes"] == 65536
    assert len(planned["cases"]) == len({case["id"] for case in planned["cases"]}) == 5
    for case in planned["cases"][:3]:
        assert "lantern" not in case["text"] and "button" not in case["text"]
        assert "safe" not in case["text"]
    assert "power cuts" not in planned["cases"][0]["text"]
    assert "tea tin" not in planned["cases"][1]["text"]
    assert "tea tin" in planned["cases"][2]["text"]


@contextmanager
def source_journal(diagnostic, path, *, session_count=11):
    planned = diagnostic.longitudinal.plan()
    with diagnostic.CampaignJournal(path, planned) as journal:
        (path / "calls.jsonl").write_text("")
        journal.append("initialized", {"principal": {"tenant_id": "tenant", "principal_id": "owner"},
                                       "brain_id": "brain"})
        for session in planned["scenario"]["sessions"][:session_count]:
            for turn in session["turns"]:
                choice = turn["choices"][0]
                content = {"text": choice["text"]}
                source = {"event_id": turn["id"], "revision": 1, "content_sha256": digest(content),
                          "observed": {"actor_role": "user", "content": content}}
                value = {"runtime_turn_id": turn["id"], "sources": {"input_events": [source]},
                         "transcript": {"turn_id": turn["id"], "user_text": choice["text"],
                                        "assistant_text": "An actual prior reply."}}
                journal.append("delivered_exchange", value)
                journal.append("runtime_completed", {"result": {"turn_id": turn["id"], "stage": "completed"},
                                                     "sources": value["sources"]})
            journal.append("session_completed", {"session_id": session["id"], "snapshot_sha256": digest({}),
                                                  "ledger_sha256": digest([]),
                                                  "usage": {"usage_unknown_calls": 0}})
        journal.append("paused", {})
        yield journal


def test_source_binding_keeps_actual_prefix_and_does_not_release_future_information(diagnostic, tmp_path):
    with source_journal(diagnostic, tmp_path / "source") as source:
        before = source.events
        binding = diagnostic.source_boundary(source)
        turns = [item["transcript"]["turn_id"] for item in binding["actual_received_prefix"]]
        assert "blackout-memory" in turns and "grandma-card" not in turns and "grandma-cue" not in turns
        assert binding["source_evidence"]["sources"]["input_events"][0]["event_id"] == "blackout-memory"
        assert source.events == before


@pytest.mark.parametrize("session_count", [10, 12])
def test_wrong_source_boundary_is_rejected_before_opening_environment(diagnostic, tmp_path, monkeypatch, session_count):
    monkeypatch.setattr(diagnostic.Settings, "from_env", forbidden)
    with source_journal(diagnostic, tmp_path / "source", session_count=session_count):
        pass
    with pytest.raises(ValueError, match="eleven sessions"):
        diagnostic.live(tmp_path / "source", tmp_path / "diagnostic")
    assert not (tmp_path / "diagnostic").exists()


def test_source_ledger_change_and_lock_are_rejected(diagnostic, tmp_path):
    path = tmp_path / "source"
    with source_journal(diagnostic, path) as source:
        with pytest.raises(RuntimeError, match="locked"):
            with diagnostic.source_campaign(path):
                pass
        (path / "calls.jsonl").write_text('{"extra":"call"}\n')
        with pytest.raises(ValueError, match="ledger"):
            diagnostic.source_boundary(source)


class ArchiveMemory:
    def __init__(self):
        self.brain_id = "brain"
        self.documents = {"brain": sample_archive()}
        self.archives = {}
        self.restores = []

    def create_brain(self, name):
        identifier = "copy-" + str(len(self.documents))
        self.documents[identifier] = None
        return {"id": identifier}

    def export(self, **kwargs):
        document = copy.deepcopy(self.documents[self.brain_id])
        identifier = "archive-" + str(len(self.archives))
        value = {"archive_id": identifier, "manifest_sha256": digest(document), "document": document}
        self.archives[identifier] = value
        return {"action": "export", "outcome": "completed", "result": {
            "archive_id": identifier, "manifest_sha256": value["manifest_sha256"]}}

    def download_archive(self, identifier):
        return copy.deepcopy(self.archives[identifier])

    def restore(self, identifier, **kwargs):
        self.restores.append((self.brain_id, identifier, kwargs))
        restored = rebound(self.archives[identifier]["document"])

        def rebind(value):
            if isinstance(value, dict):
                if "access_policy_id" in value:
                    value.update(brain_id=self.brain_id, access_policy_id="owner:" + self.brain_id)
                for item in value.values():
                    rebind(item)
            elif isinstance(value, list):
                for item in value:
                    rebind(item)

        rebind(restored)
        restored["source_brain_id"] = self.brain_id
        restored["belief_relations"][0]["endpoint_snapshots"][0]["content_sha256"] = assertion_fingerprint(
            restored["memories"][0])
        self.documents[self.brain_id] = restored
        return {"action": "restore", "outcome": "completed", "result": {
            "read_enabled": True, "quarantined_event_count": 0, "excluded_deleted_count": 0}}


def diagnostic_plan(diagnostic):
    return diagnostic.plan({"brain_id": "brain", "checkpoint": {"snapshot_sha256": digest({})}})


def test_copies_use_identical_trusted_archive_without_cross_probe_teaching(diagnostic, tmp_path):
    memory = ArchiveMemory()
    original = copy.deepcopy(memory.documents["brain"])
    with diagnostic.CampaignJournal(tmp_path / "diagnostic", diagnostic_plan(diagnostic)) as journal:
        archive, evidence = diagnostic.freeze_archive(memory, journal)
        for case in diagnostic.CASES:
            diagnostic.restore_copy(memory, journal, case, archive, evidence)
            assert memory.documents[memory.brain_id]["memories"][0]["statement"] == original["memories"][0]["statement"]
            memory.documents[memory.brain_id]["memories"][0]["statement"] = "Probe answer must never teach later copies."
        assert memory.documents["brain"] == original
        assert len({brain for brain, _, _ in memory.restores}) == 5
        assert {identifier for _, identifier, _ in memory.restores} == {archive["archive_id"]}
        assert all(options["mode"] == "new_brain" for _, _, options in memory.restores)
        assert all(event["payload"]["equal_durable_graph"] for event in journal.events
                   if event["kind"] == "copy_graph_checked")


def test_graph_loss_blocks_probe_before_any_provider(diagnostic, tmp_path, monkeypatch):
    memory = ArchiveMemory()
    restore = memory.restore

    def damaged(*args, **kwargs):
        result = restore(*args, **kwargs)
        memory.documents[memory.brain_id]["cognitive_views"]["associations"] = []
        return result

    monkeypatch.setattr(memory, "restore", damaged)
    with diagnostic.CampaignJournal(tmp_path / "diagnostic", diagnostic_plan(diagnostic)) as journal:
        archive, evidence = diagnostic.freeze_archive(memory, journal)
        with pytest.raises(diagnostic.longitudinal.CampaignStopped, match="copy_graph_changed"):
            diagnostic.restore_copy(memory, journal, diagnostic.CASES[0], archive, evidence)
        assert not any(event["kind"] == "provider_started" for event in journal.events)


def test_provider_unknown_usage_stops_next_role_and_records_actual_prompt(diagnostic, tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "A reply without usage."}}]})

    with diagnostic.CampaignJournal(tmp_path / "diagnostic", diagnostic_plan(diagnostic)) as journal:
        journal.append("probe_started", {"brain_id": "copy", "case_id": "unaided"})
        with diagnostic.MeteredTransport(journal.path / "calls.jsonl", transport=httpx.MockTransport(respond)) as meter:
            with diagnostic.ChatCompletionsHost("https://provider.test", "fake-key", "test-model", transport=meter) as host:
                request = ModelRequest("turn", "request", ({"role": "user", "content": diagnostic.CASES[0]["text"]},), 100, "")
                with pytest.raises(diagnostic.longitudinal.CampaignStopped, match="unknown_provider_usage"):
                    diagnostic.BoundedProvider(journal, meter, host, "host")(request)
                with pytest.raises(diagnostic.longitudinal.CampaignStopped, match="unknown_provider_usage"):
                    diagnostic.BoundedProvider(journal, meter, forbidden, "cognition")(request)
                assert len(requests) == 1 and meter.summary()["usage_unknown_calls"] == 1
                assert journal.events[1]["payload"]["request"]["messages"] == list(request.messages)


class EmptyMeter:
    def summary(self):
        return {"usage_unknown_calls": 0, "reserved_calls": 0}


def test_per_copy_limit_is_enforced_before_a_second_host_call(diagnostic, tmp_path):
    with diagnostic.CampaignJournal(tmp_path / "diagnostic", diagnostic_plan(diagnostic)) as journal:
        request = ModelRequest("turn", "request", (), 1, "")
        with pytest.raises(diagnostic.longitudinal.CampaignStopped, match="without_probe"):
            diagnostic.BoundedProvider(journal, EmptyMeter(), forbidden, "host")(request)
        journal.append("probe_started", {"brain_id": "copy", "case_id": "unaided"})
        assert diagnostic.BoundedProvider(journal, EmptyMeter(), lambda _: "First", "host")(request) == "First"
        with pytest.raises(diagnostic.longitudinal.CampaignStopped, match="probe_call_limit"):
            diagnostic.BoundedProvider(journal, EmptyMeter(), forbidden, "host")(request)


@pytest.mark.parametrize("changed", [False, True])
def test_failure_still_checks_source_and_never_starts_another_probe(diagnostic, tmp_path, monkeypatch, changed):
    memory, seen = ArchiveMemory(), []

    @contextmanager
    def client():
        yield memory

    def fail(memory, journal, meter, case, host):
        seen.append(case["id"])
        raise diagnostic.longitudinal.CampaignStopped("probe_failure")

    snapshots = iter([{}, {"changed": True} if changed else {}])
    monkeypatch.setattr(diagnostic, "run_probe", fail)
    with diagnostic.CampaignJournal(tmp_path / "diagnostic", diagnostic_plan(diagnostic)) as journal:
        with pytest.raises(diagnostic.longitudinal.CampaignStopped, match="source_brain_changed" if changed else "probe_failure"):
            diagnostic.run_diagnostics(journal, EmptyMeter(), memory, lambda: next(snapshots), client, forbidden)
        assert seen == ["unaided"]
        assert journal.events[-1]["kind"] == "stopped"
        assert diagnostic.report(journal)["source_unchanged"] is not changed


def test_inspection_is_offline_and_does_not_imply_semantic_acceptance(diagnostic, tmp_path, monkeypatch):
    path = tmp_path / "diagnostic"
    with diagnostic.CampaignJournal(path, diagnostic_plan(diagnostic)) as journal:
        journal.append("completed", {"usage": {"reserved_calls": 0}})
    monkeypatch.setattr(diagnostic.Settings, "from_env", forbidden)
    monkeypatch.setattr(diagnostic, "Database", forbidden)
    result = diagnostic.inspect(path)
    assert result["semantic_acceptance"] == "pending_independent_review"
    assert result["source_unchanged"] is None


def test_run_probe_passes_only_case_and_normal_instructions_in_a_fresh_conversation(diagnostic, tmp_path, monkeypatch):
    requests = []

    class Runtime:
        def __init__(self, memory, generate, **kwargs):
            requests.append(kwargs)
            self.journal = SimpleNamespace(get=lambda _: {}, sources=lambda _: {})

        def respond(self, text, **kwargs):
            requests.append({"text": text, **kwargs})
            return TurnResult("turn", "completed", "Answer")

    monkeypatch.setattr(diagnostic, "BrainRuntime", Runtime)
    monkeypatch.setattr(diagnostic, "RuntimeClient", lambda _: SimpleNamespace(configure_cognitive_budget=lambda **_: {}))
    with diagnostic.CampaignJournal(tmp_path / "diagnostic", diagnostic_plan(diagnostic)) as journal:
        for case in diagnostic.CASES:
            diagnostic.run_probe(SimpleNamespace(brain_id="copy"), journal, EmptyMeter(), case, forbidden)
        utterances = requests[1::2]
        assert len({item["conversation_id"] for item in utterances}) == 5
        assert [item["text"] for item in utterances] == [case["text"] for case in diagnostic.CASES]
        for options in requests[::2]:
            assert options["instructions"] == journal.plan["host_instructions"]
            assert asdict(options["policy"]) == journal.plan["runtime_policy"]


@pytest.mark.parametrize("status,restricted", [("provider_unavailable", False), ("completed", True)])
def test_failed_orientation_cannot_spend_host_call(diagnostic, tmp_path, monkeypatch, status, restricted):
    state = SimpleNamespace(get=lambda _: {"orientation_job_id": "orientation"},
                            job=lambda _: {"state": "completed", "learning_summary": {
                                "processing_status": status,
                                "sampling": {"publication_restricted": restricted}}})
    monkeypatch.setattr(diagnostic, "RuntimeClient", lambda _: state)
    with diagnostic.CampaignJournal(tmp_path / "diagnostic", diagnostic_plan(diagnostic)) as journal:
        with pytest.raises(diagnostic.longitudinal.CampaignStopped, match="orientation_processing_incomplete"):
            diagnostic.checked_host(None, journal, forbidden)(ModelRequest("turn", "request", (), 1, ""))


@pytest.mark.integration
def test_real_http_archive_copies_preserve_source_and_isolate_all_five_probes(diagnostic, learning, tmp_path):
    learning.observe("During power cuts Grandma lit a lantern and let us sort buttons in a tea tin. "
                     "It made the dark house feel safe.", conversation="original-history")
    original_runner = diagnostic.longitudinal.SessionRunner(
        SimpleNamespace(plan=diagnostic.longitudinal.plan()), learning.memory, learning.service,
        learning.principal, None, None, None)
    before = original_runner.snapshot()
    planned = diagnostic.plan({"brain_id": learning.brain, "checkpoint": {"snapshot_sha256": digest(before)}})
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "enabled"}
        answer = (json.dumps({"claims": [], "situation_updates": [], "episode_actions": []})
                  if "response_format" in body else "A small kindness is a good starting point.")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    with diagnostic.CampaignJournal(tmp_path / "diagnostic", planned) as journal:
        with diagnostic.MeteredTransport(
                journal.path / "calls.jsonl", max_calls=15, max_output_tokens=184320,
                max_response_bytes=262144, transport=httpx.MockTransport(respond)) as meter:
            with diagnostic.ChatCompletionsSemanticProvider(
                    "https://provider.test", "fake-key", "test-model", transport=meter,
                    max_response_bytes=262144) as cognitive:
                recorded = diagnostic.BoundedProvider(journal, meter, cognitive, "cognition")
                learning.service.semantic_processor = diagnostic.SemanticProcessor(
                    recorded, recorded, diagnostic.SemanticPolicy(**planned["semantic_policy"]))
                settings = diagnostic.Settings(database_url=learning.database.dsn, api_keys={
                    hashlib.sha256(b"cue-test").hexdigest(): learning.principal})
                with diagnostic.loopback_server(diagnostic.create_app(settings, service=learning.service)) as url:
                    with diagnostic.ChatCompletionsHost(
                            "https://provider.test", "fake-key", "test-model", transport=meter,
                            max_response_bytes=262144) as host:
                        with diagnostic.MemoryClient(url, "cue-test", learning.brain) as source:
                            result = diagnostic.run_diagnostics(
                                journal, meter, source, original_runner.snapshot,
                                lambda: diagnostic.MemoryClient(url, "cue-test"), host)
            assert result["completed_cases"] == [case["id"] for case in diagnostic.CASES]
            assert result["source_unchanged"] is True
            assert original_runner.snapshot() == before
            assert result["semantic_acceptance"] == "pending_independent_review"
            starts = [event["payload"] for event in journal.events if event["kind"] == "probe_started"]
            assert len({item["brain_id"] for item in starts}) == 5
            assert len({item["conversation_id"] for item in starts}) == 5
            assert learning.brain not in {item["brain_id"] for item in starts}
            assert all(event["payload"]["equal_durable_graph"] for event in journal.events
                       if event["kind"] == "copy_graph_checked")
            hosts = [request for request in requests if "response_format" not in request]
            assert len(hosts) == 5
            assert [request["messages"][-1]["content"] for request in hosts] == [case["text"] for case in diagnostic.CASES]
            assert meter.summary()["usage_unknown_calls"] == 0
            assert 5 <= meter.summary()["reserved_calls"] <= 15
