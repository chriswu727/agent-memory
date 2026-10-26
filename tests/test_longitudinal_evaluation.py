import copy
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from test_learning import learning as learning
from test_semantic import encoded, proposals, verifier
from test_semantic_call_usage import Provider

from agent_memory.runtime import ModelRequest
from agent_memory.semantic import SemanticProcessor


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_longitudinal")


class Meter:
    def __init__(self):
        self.records = []
        self.unknown = 0

    def summary(self):
        return {"reserved_calls": len(self.records), "usage_unknown_calls": self.unknown,
                "reserved_output_tokens": len(self.records), "usage_totals": {}}


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected provider, environment or database access")


def test_plan_is_offline_and_includes_every_role_without_repetitive_turn_target(evaluation, monkeypatch, capsys):
    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    monkeypatch.setattr(evaluation, "Database", forbidden)
    monkeypatch.setattr(sys, "argv", ["eval", "--plan"])
    evaluation.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["runtime_policy"]["recent_context_events"] == 6
    assert planned["allocation"] == {"host": 33, "cognition": 198, "user": 17}
    assert planned["max_calls"] == 248
    assert planned["max_reserved_output_tokens"] == 198 * 16384 + 50 * 4096
    assert not planned["aging_assessed"]
    assert planned["time_mode"] == "narrative_order_only"
    assert all(path.endswith(".py") for path in planned["code_fingerprints"])


@pytest.mark.parametrize("damage", ["code_fingerprints", "runtime_policy", "scenario", "max_calls"])
def test_changed_frozen_plan_cannot_reuse_a_campaign(evaluation, damage):
    planned = evaluation.plan()
    planned[damage] = None
    with pytest.raises(ValueError, match="changed"):
        evaluation.validate_plan(planned)


@pytest.mark.parametrize("mode", ["live", "continue"])
def test_cli_rejects_invalid_session_limits_before_loading_credentials(evaluation, monkeypatch, mode):
    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    monkeypatch.setattr(sys, "argv", ["eval", "--" + mode, "--sessions", "0"])
    with pytest.raises(SystemExit):
        evaluation.main()


def test_unsafe_resume_refuses_before_credentials(evaluation, tmp_path, monkeypatch):
    path = tmp_path / "run"
    with evaluation.CampaignJournal(path, evaluation.plan()) as journal:
        journal.append("provider_started", {"role": "host"})
    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    with pytest.raises(RuntimeError, match="resumable"):
        evaluation.live(path, continuing=True)


def test_inspection_does_not_load_credentials_or_make_calls(evaluation, tmp_path, monkeypatch, capsys):
    path = tmp_path / "run"
    with evaluation.CampaignJournal(path, evaluation.plan()) as journal:
        journal.append("stopped", {"reason": "unknown_usage"})
    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    monkeypatch.setattr(evaluation, "Database", forbidden)
    monkeypatch.setattr(sys, "argv", ["eval", "--inspect", "--output", str(path)])
    evaluation.main()
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "stopped"
    assert result["semantic_acceptance"] == "pending_independent_review"
    assert {packet["status"] for packet in result["review_packets"]} == {"unexecuted"}


def test_real_meter_unknown_usage_stops_before_any_next_role(evaluation, tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "A reply without confirmed usage."}}]})

    with evaluation.CampaignJournal(tmp_path / "run", evaluation.plan()) as journal:
        meter = evaluation.MeteredTransport(journal.path / "calls.jsonl", transport=httpx.MockTransport(provider))
        with evaluation.ChatCompletionsHost("https://provider.test", "fake-key", "fake-model", transport=meter) as host:
            wrapped = evaluation.RecordedProvider(journal, meter, host, "host")
            request = ModelRequest("turn", "request", ({"role": "user", "content": "Hello"},), 5, "")
            with pytest.raises(evaluation.CampaignStopped, match="unknown_provider_usage"):
                wrapped(request)
            with pytest.raises(evaluation.CampaignStopped, match="unknown_provider_usage"):
                evaluation.RecordedProvider(journal, meter, forbidden, "user")(request)
            assert len(calls) == 1 and meter.summary()["reserved_calls"] == 1
            assert journal.events[-1]["kind"] == "provider_completed"
            assert journal.events[0]["payload"]["request"]["messages"] == list(request.messages)


def test_known_provider_failure_stops_even_when_usage_is_known(evaluation, tmp_path):
    with evaluation.CampaignJournal(tmp_path / "run", evaluation.plan()) as journal:
        meter = Meter()

        def fail(_):
            raise ValueError("Private provider text")

        request = ModelRequest("turn", "request", (), 1, "")
        with pytest.raises(ValueError):
            evaluation.RecordedProvider(journal, meter, fail, "cognition")(request)
        with pytest.raises(evaluation.CampaignStopped, match="provider_failure"):
            evaluation.RecordedProvider(journal, meter, forbidden, "host")(request)
        assert "Private provider text" not in json.dumps(journal.events)


def test_schema_failure_is_retained_and_stops_dependent_work(evaluation, tmp_path):
    from agent_memory.semantic import CognitiveRequest

    with evaluation.CampaignJournal(tmp_path / "run", evaluation.plan()) as journal:
        request = CognitiveRequest("request", "proposal", (), {"type": "object", "required": ["value"]}, 10, 1)
        meter = Meter()
        with pytest.raises(evaluation.CampaignStopped, match="provider_failure"):
            evaluation.RecordedProvider(journal, meter, lambda _: "{}", "cognition")(request)
        assert journal.events[-1]["payload"]["response"] == "{}"
        assert journal.events[-1]["payload"]["evaluation_stop"] == "invalid_response"


def add_transcript(evaluation, journal, session_index, turn_index, choice_id=None):
    session = journal.plan["scenario"]["sessions"][session_index]
    turn = session["turns"][turn_index]
    selection = next(item for item in turn["choices"] if item["id"] == choice_id) if choice_id else turn["choices"][0]
    value = {"transcript": {
        "session_id": session["id"], "turn_id": turn["id"], "choice_id": selection["id"],
        "user_text": selection["text"], "assistant_text": "What would you like to clarify?"},
        "runtime_turn_id": "runtime-" + turn["id"], "branch": selection["branch"],
        "sources": {"input_events": [{"event_id": turn["id"], "revision": 1}]}}
    journal.append("delivered_exchange", value)
    journal.append("turn_completed", value)


@pytest.mark.parametrize("output", ['{"choice_id":"invented"}', '{"choice_id":"continue","text":"spoiler"}',
                                     'not JSON', 'null', '{"choice_id":1}',
                                     '{"choice_id":"invented","choice_id":"clarify"}'])
def test_invalid_actor_output_never_becomes_a_user_message(evaluation, tmp_path, output):
    with evaluation.CampaignJournal(tmp_path / "run", evaluation.plan()) as journal:
        add_transcript(evaluation, journal, 0, 0)
        runner = evaluation.SessionRunner(journal, None, None, None, Meter(), forbidden, lambda _: output)
        with pytest.raises(evaluation.CampaignStopped, match="invalid_simulated_user"):
            runner.choose(0, 1)
        assert journal.events[-1]["kind"] == "user_started"
        assert len(evaluation.transcript(journal)) == 1


def test_actor_sees_actual_reply_but_host_receives_only_exact_utterance(evaluation, tmp_path):
    with evaluation.CampaignJournal(tmp_path / "run", evaluation.plan()) as journal:
        add_transcript(evaluation, journal, 0, 0)
        requests = []

        def select(request):
            requests.append(request)
            return '{"choice_id":"clarify"}'

        runner = evaluation.SessionRunner(journal, None, None, None, Meter(), forbidden, select)
        selected = runner.choose(0, 1)
        data = json.loads(requests[0].messages[-1]["content"])
        assert data["current_session_transcript"][-1]["content"] == "What would you like to clarify?"
        assert selected["branch"] == "assisted"
        assert "private_review" not in json.dumps(data) and "hidden_world_facts" not in json.dumps(data)
        assert data["released_user_knowledge"][0]["turn_id"] == "story-start"


def test_review_packets_never_fill_missing_or_future_evidence(evaluation, tmp_path):
    planned = evaluation.plan()
    first_case = copy.deepcopy(planned["scenario"]["private_review"]["cases"][0])
    first_case.update(at_turn="story-start", source_refs=[{"turn_id": "work-alex"}])
    planned["scenario"]["private_review"]["cases"] = [first_case]
    with evaluation.CampaignJournal(tmp_path / "run", planned) as journal:
        add_transcript(evaluation, journal, 0, 0)
        packet, = evaluation.review_packets(journal)
        assert packet["status"] == "required_evidence_not_delivered"
        assert packet["source_evidence"] == []
        assert packet["missing_source_refs"] == [{"turn_id": "work-alex"}]
        assert [item["transcript"]["turn_id"] for item in packet["actual_received_prefix"]] == ["story-start"]
        add_transcript(evaluation, journal, 0, 1)
        assert evaluation.review_packets(journal)[0] == packet


def configure_no_change(learning):
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}

    def propose(request):
        data = json.loads(request.messages[-1]["content"])
        result = proposals()
        if "integration_manifest" in data:
            result["integration_abstentions"] = [
                {"objective_id": item["objective_id"], "reason": "insufficient_shared_meaning"}
                for item in data["integration_manifest"]["objectives"]
            ]
        return encoded(result)

    provider = Provider(propose, usage)
    learning.service.semantic_processor = SemanticProcessor(provider, Provider(verifier, usage))


@pytest.mark.integration
def test_actual_http_sessions_preserve_context_and_resume_without_repeating_paid_work(
        evaluation, learning, tmp_path):
    configure_no_change(learning)
    planned, meter, requests = evaluation.plan(), Meter(), []

    def host(request):
        requests.append(request)
        return "Outside your class nights, would half-hour writing sessions work?"

    def actor(request):
        assert "half-hour writing sessions" in request.messages[-1]["content"]
        return json.dumps({"choice_id": json.loads(request.messages[-1]["content"])["choices"][0]["id"]})

    path = tmp_path / "run"
    with evaluation.CampaignJournal(path, planned) as journal:
        first = evaluation.SessionRunner(journal, learning.memory, learning.service, learning.principal,
                                         meter, host, actor).run(sessions=1)
        assert first["state"] == "paused" and first["turns_completed"] == 2
        rows = [event["payload"]["runtime"] for event in journal.events if event["kind"] == "runtime_completed"]
        assert len({row["conversation_id"] for row in rows}) == 1
        assert rows[1]["context_event_refs"]
        assert planned["scenario"]["sessions"][0]["turns"][0]["choices"][0]["text"] in json.dumps(requests[1].messages)
        paid_before = copy.deepcopy(first["last_event"]["payload"]["usage"])
        operations = evaluation.RuntimeClient(learning.memory).cognitive_usage()["reservation_count"]
    with evaluation.CampaignJournal(path, planned) as journal:
        second = evaluation.SessionRunner(journal, learning.memory, learning.service, learning.principal,
                                          meter, host, actor).run(sessions=1)
        assert second["turns_completed"] == 4 and len(requests) == 4
        assert second["sessions_completed"] == [session["id"] for session in planned["scenario"]["sessions"][:2]]
        assert any(event["kind"] == "checkpoint_verified" for event in journal.events)
        assert second["last_event"]["payload"]["usage"] == paid_before
        assert evaluation.RuntimeClient(learning.memory).cognitive_usage()["reservation_count"] > operations


@pytest.mark.integration
def test_actor_failure_stops_real_session_without_delivering_invalid_text(evaluation, learning, tmp_path):
    configure_no_change(learning)
    path = tmp_path / "run"
    with evaluation.CampaignJournal(path, evaluation.plan()) as journal:
        runner = evaluation.SessionRunner(journal, learning.memory, learning.service, learning.principal,
                                          Meter(), lambda _: "What would work for you?", lambda _: "invented")
        with pytest.raises(evaluation.CampaignStopped, match="invalid_simulated_user"):
            runner.run()
        assert journal.events[-1]["kind"] == "stopped"
        assert len(evaluation.transcript(journal)) == 1
        assert len([event for event in journal.events if event["kind"] == "delivered_exchange"]) == 1
        assert len(evaluation.RuntimeClient(learning.memory).list()) == 1
    with evaluation.CampaignJournal(path, evaluation.plan()) as journal:
        with pytest.raises(RuntimeError, match="resumable"):
            journal.assert_resumable()


@pytest.mark.integration
@pytest.mark.parametrize("failure_point", ["learning", "runtime_settlement"])
def test_delivered_answer_remains_reviewable_when_later_work_fails(
        evaluation, learning, tmp_path, monkeypatch, failure_point):
    configure_no_change(learning)
    planned = evaluation.plan()
    case = planned["scenario"]["private_review"]["cases"][0]
    case.update(at_turn="story-start", source_refs=[{"turn_id": "story-start"}])

    def fail(*args, **kwargs):
        raise TimeoutError("Injected failure after delivery")

    if failure_point == "learning":
        monkeypatch.setattr(evaluation, "process_learning", fail)
    else:
        monkeypatch.setattr(evaluation.RuntimeClient, "finish", fail)
    with evaluation.CampaignJournal(tmp_path / "run", planned) as journal:
        runner = evaluation.SessionRunner(journal, learning.memory, learning.service, learning.principal,
                                          Meter(), lambda _: "A concrete delivered answer.", forbidden)
        with pytest.raises(TimeoutError):
            runner.run()
        observed = evaluation.report(journal)
        assert observed["turns_delivered"] == 1 and observed["turns_completed"] == 0
        assert observed["unsettled_deliveries"] == (failure_point == "runtime_settlement")
        packet = observed["review_packets"][0]
        assert packet["status"] == "pending_independent_review"
        assert packet["answer"] == "A concrete delivered answer."
        assert packet["source_evidence"][0]["input_events"][0]["observed"]["delivery_state"] == "received"
        assert evaluation.transcript(journal) == []


@pytest.mark.integration
@pytest.mark.parametrize("changed", ["database", "ledger"])
def test_changed_checkpoint_stops_before_new_model_work(evaluation, learning, tmp_path, changed):
    configure_no_change(learning)
    planned, meter = evaluation.plan(), Meter()

    def choose(request):
        return json.dumps({"choice_id": json.loads(request.messages[-1]["content"])["choices"][0]["id"]})

    path = tmp_path / "run"
    with evaluation.CampaignJournal(path, planned) as journal:
        evaluation.SessionRunner(journal, learning.memory, learning.service, learning.principal,
                                 meter, lambda _: "A practical response.", choose).run()
    if changed == "database":
        learning.observe("An external input changed the frozen checkpoint.", conversation="outside")
    else:
        meter.records.append({"unexpected": "external usage"})
    with evaluation.CampaignJournal(path, planned) as journal:
        runner = evaluation.SessionRunner(journal, learning.memory, learning.service, learning.principal,
                                          meter, forbidden, forbidden)
        with pytest.raises(evaluation.CampaignStopped, match="checkpoint_" + changed + "_changed"):
            runner.run()
        assert evaluation.report(journal)["turns_delivered"] == 2


@pytest.mark.integration
def test_complete_scenario_through_real_loopback_http_has_no_automatic_semantic_pass(
        evaluation, learning, tmp_path):
    configure_no_change(learning)
    evaluation.RuntimeClient(learning.memory).configure_cognitive_budget(
        request_id="frozen-test-budget", expected_revision=0, **evaluation.plan()["brain_budget"])
    settings = evaluation.Settings(database_url=learning.database.dsn, api_keys={
        evaluation.hashlib.sha256(b"loopback-test").hexdigest(): learning.principal,
    })
    requests = []

    def host(request):
        requests.append(request)
        return "Let's work through that together."

    def choose(request):
        return json.dumps({"choice_id": json.loads(request.messages[-1]["content"])["choices"][0]["id"]})

    with evaluation.loopback_server(evaluation.create_app(settings, service=learning.service)) as url:
        with evaluation.MemoryClient(url, "loopback-test", learning.brain) as memory:
            with evaluation.CampaignJournal(tmp_path / "run", evaluation.plan()) as journal:
                result = evaluation.SessionRunner(journal, memory, learning.service, learning.principal,
                                                   Meter(), host, choose).run(sessions=16)
                assert result["state"] == "completed"
                assert result["turns_completed"] == result["turns_delivered"] == len(requests) == 33
                assert len(result["sessions_completed"]) == 16
                assert result["semantic_acceptance"] == "pending_independent_review"
                assert not result["aging_assessed"]
                statuses = {packet["status"] for packet in result["review_packets"]}
                assert statuses <= {"pending_independent_review", "branch_not_exercised"}
                with pytest.raises(RuntimeError, match="resumable"):
                    journal.assert_resumable()


@pytest.mark.integration
def test_live_entrypoint_reopens_brain_and_cumulative_ledger_in_a_fresh_process(evaluation, tmp_path):
    script = tmp_path / "local_campaign.py"
    script.write_text('''
import json, os, sys
from pathlib import Path
import agent_memory
import httpx
sys.path.insert(0, sys.argv[1])
from examples import evaluate_longitudinal as evaluation

def respond(request):
    body = json.loads(request.content)
    if body["messages"][0]["content"] == evaluation.ACTOR_INSTRUCTIONS:
        value = json.loads(body["messages"][-1]["content"])
        answer = json.dumps({"choice_id": value["choices"][0]["id"]})
    elif "response_format" in body:
        value = json.loads(body["messages"][-1]["content"])
        response = {"claims": [], "situation_updates": [], "episode_actions": []}
        if "integration_manifest" in value:
            response["integration_abstentions"] = [
                {"objective_id": item["objective_id"], "reason": "insufficient_shared_meaning"}
                for item in value["integration_manifest"]["objectives"]]
        answer = json.dumps(response)
    else:
        answer = "Would a short writing session fit around your classes?"
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": answer}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

class LocalMeter(evaluation.MeteredTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, transport=httpx.MockTransport(respond), **kwargs)

evaluation.MeteredTransport = LocalMeter
result = evaluation.live(Path(sys.argv[2]), continuing=sys.argv[3] == "continue")
print(json.dumps({"pid": os.getpid(), "turns": result["turns_completed"],
                  "state": result["state"], "usage": result["last_event"]["payload"]["usage"]}))
''')
    env = {key: value for key, value in os.environ.items() if "API_KEY" not in key and "API_TOKEN" not in key}
    env.update(DEEPSEEK_API_KEY="local-fake-key", PYTHON_DOTENV_DISABLED="1")
    path = tmp_path / "campaign"
    command = [sys.executable, "-I", str(script), str(evaluation.ROOT), str(path)]
    first = subprocess.run([*command, "live"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert first.returncode == 0, first.stderr
    ledger_before = (path / "calls.jsonl").read_text()
    second = subprocess.run([*command, "continue"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert second.returncode == 0, second.stderr
    first_result, second_result = json.loads(first.stdout), json.loads(second.stdout)
    assert first_result["pid"] != second_result["pid"]
    assert first_result["turns"] == 2 and second_result["turns"] == 4
    assert first_result["state"] == second_result["state"] == "paused"
    assert second_result["usage"]["reserved_calls"] > first_result["usage"]["reserved_calls"] > 0
    assert second_result["usage"]["usage_unknown_calls"] == 0
    assert (path / "calls.jsonl").read_text().startswith(ledger_before)
    with evaluation.CampaignJournal(path, evaluation.plan()) as journal:
        assert sum(event["kind"] == "initialized" for event in journal.events) == 1
        assert sum(event["kind"] == "checkpoint_verified" for event in journal.events) == 1
        hosts = [event for event in journal.events
                 if event["kind"] == "provider_started" and event["payload"]["role"] == "host"]
        assert len(hosts) == 4
