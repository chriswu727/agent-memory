import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from agent_memory import agent_session
from agent_memory.agent_session import SessionError, open_agent
from agent_memory.api import create_app
from agent_memory.client import MemoryClient
from agent_memory.config import Settings
from agent_memory.helpers import Principal, digest
from agent_memory.local_profile import _save, ensure_profile
from agent_memory.runtime import DeliveryReceipt, RuntimeBlocked, RuntimePolicy, TurnResult


def turn_id(profile, message_id="one", conversation_id="conversation", branch_id="main"):
    return "turn-" + digest([profile.brain_id, conversation_id, branch_id, message_id])[:40]


@pytest.fixture
def mocked(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "profile")
    state = SimpleNamespace(profile=profile, closed=0, stage="generated", generate=Mock(), rows={})

    @contextmanager
    def connect(*args, **kwargs):
        try:
            yield SimpleNamespace(brain_id=state.profile.brain_id)
        finally:
            state.closed += 1

    state.connect = Mock(side_effect=connect)
    def get(identifier):
        if identifier not in state.rows:
            response = httpx.Response(404, request=httpx.Request("GET", "http://test/turn"))
            response.raise_for_status()
        return state.rows[identifier] | {"stage": state.stage}

    def begin(**kwargs):
        state.rows.setdefault(kwargs["turn_id"], kwargs | {
            "brain_id": profile.brain_id, "revision": 1, "failure_code": None,
        })
        return get(kwargs["turn_id"])

    def respond(text, **kwargs):
        identifier = turn_id(profile, kwargs["message_id"], kwargs["conversation_id"], kwargs["branch_id"])
        begin(turn_id=identifier, conversation_id=kwargs["conversation_id"], branch_id=kwargs["branch_id"],
              input_fingerprint=digest({"message_id": kwargs["message_id"], "text": text}))
        return TurnResult(identifier, state.stage, "private-response")

    state.runtime = SimpleNamespace(
        respond=Mock(side_effect=respond),
        resume=Mock(side_effect=lambda identifier: TurnResult(identifier, state.stage, "private-response")),
        journal=SimpleNamespace(get=Mock(side_effect=get), begin=Mock(side_effect=begin), claim=Mock(), fail=Mock()),
        worker_id="test-worker", policy=RuntimePolicy(),
    )
    state.factory = Mock(return_value=state.runtime)
    monkeypatch.setattr(agent_session.desktop_connection, "open_client", state.connect)
    monkeypatch.setattr(agent_session, "BrainRuntime", state.factory)
    return state


def session(mocked, **kwargs):
    defaults = {
        "conversation_id": "conversation", "generate": mocked.generate,
        "generator_fingerprint": "model-v1", "start_service": False,
    }
    return open_agent(mocked.profile.directory, **(defaults | kwargs))


def pending_path(profile):
    paths = list((profile.directory / "sessions").glob("*/pending.json"))
    assert len(paths) == 1
    return paths[0]


def test_direct_construction_does_not_bypass_session_lock():
    with pytest.raises(SessionError, match="invalid_configuration"):
        agent_session.AgentSession(profile=None, identity=None, runtime=None, path=None,
                                   generator_fingerprint=None, runtime_fingerprint=None)


def test_pending_is_durable_before_runtime_and_contains_no_message_or_response(mocked):
    original = mocked.runtime.respond.side_effect

    def respond(text, **kwargs):
        saved = json.loads(pending_path(mocked.profile).read_text())
        assert saved["input_fingerprint"] == digest({"message_id": "one", "text": text})
        assert saved["turn_id"] == turn_id(mocked.profile)
        return original(text, **kwargs)

    mocked.runtime.respond.side_effect = respond
    with session(mocked, instructions="private-host-instruction") as agent:
        result = agent.respond("private-user-message", message_id="one")
        assert result.stage == "generated"
        assert agent.pending == {"message_id": "one", "turn_id": result.turn_id}
        content = pending_path(mocked.profile).read_text()
        for forbidden in ("private-user-message", "private-response", "private-host-instruction",
                          "model-v1", mocked.profile.api_key):
            assert forbidden not in content
    assert mocked.closed == 1
    mocked.generate.close.assert_not_called()
    arguments = mocked.factory.call_args.kwargs
    assert "generation_is_idempotent" not in arguments
    assert "delivery_is_idempotent" not in arguments


def test_same_message_retry_and_completed_turn_clear_pending(mocked):
    with session(mocked) as agent:
        first = agent.respond("message", message_id="one")
        assert agent.respond("message", message_id="one") == first
        for text, message_id in (("changed", "one"), ("message", "two")):
            with pytest.raises(SessionError) as error:
                agent.respond(text, message_id=message_id)
            assert error.value.code == "pending_turn" and error.value.turn_id == first.turn_id
        assert mocked.runtime.respond.call_count == 2
        mocked.stage = "completed"
        assert agent.resume().stage == "completed"
        assert agent.pending is None
        with pytest.raises(SessionError, match="no_pending_turn"):
            agent.resume()
        assert agent.respond("next message", message_id="two").stage == "completed"


def test_draft_reopens_with_delivery_callback_and_no_extra_generation(mocked):
    with session(mocked) as agent:
        result = agent.respond("message", message_id="one")
    mocked.stage = "completed"
    send = Mock()
    with session(mocked, deliver=send) as reopened:
        assert reopened.pending["turn_id"] == result.turn_id
        assert reopened.resume().stage == "completed"
        assert reopened.pending is None
    assert mocked.runtime.respond.call_count == 1
    assert mocked.runtime.resume.call_count == 1
    assert mocked.factory.call_args.kwargs["deliver"] is send


@pytest.mark.parametrize("change", [
    {"generator_fingerprint": "model-v2"}, {"instructions": "changed"},
    {"policy": RuntimePolicy(memory_context_tokens=5000)},
])
def test_changed_configuration_blocks_retry_and_resume_but_pending_remains_readable(mocked, change):
    with session(mocked) as agent:
        agent.respond("message", message_id="one")
    with session(mocked, **change) as reopened:
        assert reopened.pending["message_id"] == "one"
        with pytest.raises(SessionError, match="configuration_changed"):
            reopened.resume()
        with pytest.raises(SessionError, match="configuration_changed"):
            reopened.respond("message", message_id="one")
    assert mocked.runtime.respond.call_count == 1
    mocked.runtime.resume.assert_not_called()
    with session(mocked) as original:
        assert original.resume().stage == "generated"


def test_uncaptured_pending_still_requires_original_configuration(mocked):
    mocked.runtime.respond.side_effect = httpx.ConnectError("offline")
    with session(mocked) as agent:
        with pytest.raises(httpx.ConnectError):
            agent.respond("message", message_id="one")
    with session(mocked, instructions="changed") as reopened:
        for operation in (reopened.resume, lambda: reopened.respond("message", message_id="one")):
            with pytest.raises(SessionError, match="configuration_changed"):
                operation()
        assert reopened.pending["message_id"] == "one"
    assert mocked.runtime.respond.call_count == 1


def test_abnormal_committed_stage_requires_explicit_diagnosis(mocked):
    with session(mocked) as agent:
        first = agent.respond("message", message_id="one")
        mocked.stage = "committed"
        for operation in (agent.resume, lambda: agent.respond("message", message_id="one")):
            with pytest.raises(SessionError, match="commit_recovery_required"):
                operation()
        assert agent.pending["turn_id"] == first.turn_id
    assert mocked.runtime.respond.call_count == 1
    mocked.runtime.resume.assert_not_called()


@pytest.mark.parametrize("failure", [TimeoutError("transport failed"), KeyboardInterrupt()])
def test_failed_call_keeps_pending_and_preserves_stable_retry(mocked, failure):
    mocked.runtime.respond.side_effect = failure
    with session(mocked) as agent:
        with pytest.raises(type(failure)):
            agent.respond("message", message_id="one")
        assert agent.pending["message_id"] == "one"
    mocked.runtime.resume.side_effect = RuntimeBlocked(
        "input_not_captured_retry_original_message", turn_id(mocked.profile),
    )
    mocked.runtime.journal.begin(
        turn_id=turn_id(mocked.profile), conversation_id="conversation", branch_id="main",
        input_fingerprint=digest({"message_id": "one", "text": "message"}),
    )
    with session(mocked) as reopened:
        with pytest.raises(RuntimeBlocked, match="input_not_captured"):
            reopened.resume()
        assert reopened.pending["turn_id"] == turn_id(mocked.profile)


@pytest.mark.parametrize("scope", ["", " ", "contains space", "a\n", "a\x00", "a" * 257, None, 1])
@pytest.mark.parametrize("field", ["conversation_id", "branch_id"])
def test_invalid_scope_has_no_filesystem_or_service_effects(tmp_path, monkeypatch, scope, field):
    load = Mock(side_effect=AssertionError("Must not load profile"))
    monkeypatch.setattr(agent_session, "load_profile", load)
    with pytest.raises(SessionError, match="invalid_scope"):
        with open_agent(tmp_path / "new", **({"conversation_id": "valid"} | {field: scope})):
            pytest.fail("Invalid scope accepted")
    load.assert_not_called()
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("overrides", [
    {"generator_fingerprint": None}, {"generator_fingerprint": ""}, {"generate": "not-callable"},
    {"generate": None}, {"instructions": None}, {"instructions": "\ud800"},
    {"policy": {}}, {"deliver": True}, {"start_service": 1},
])
def test_invalid_configuration_precedes_service_start(mocked, overrides):
    with pytest.raises(SessionError, match="invalid_configuration"):
        with session(mocked, **overrides):
            pytest.fail("Invalid configuration accepted")
    mocked.connect.assert_not_called()


@pytest.mark.parametrize("text,message_id", [
    ("", "one"), (" ", "one"), (None, "one"), ("\ud800", "one"),
    ("text", ""), ("text", "a b"), ("text", "\x00"), ("text", "a" * 257),
])
def test_invalid_message_never_creates_pending(mocked, text, message_id):
    with session(mocked) as agent:
        with pytest.raises(SessionError, match="invalid_message"):
            agent.respond(text, message_id=message_id)
        assert agent.pending is None
    mocked.runtime.respond.assert_not_called()


def test_same_scope_nested_session_is_busy_and_other_scopes_are_independent(mocked):
    with session(mocked):
        with pytest.raises(SessionError, match="session_busy"):
            with session(mocked):
                pytest.fail("Nested same-scope session opened")
        with session(mocked, conversation_id="other") as other:
            assert other.pending is None
        with session(mocked, branch_id="other") as other:
            assert other.pending is None
    with session(mocked) as reopened:
        assert reopened.pending is None


def test_cross_process_lock_blocks_same_scope_but_allows_other_scope(mocked):
    script = """
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from agent_memory import agent_session
from agent_memory.agent_session import open_agent, SessionError
from agent_memory.local_profile import load_profile
profile = load_profile(Path(sys.argv[1]))
@contextmanager
def connect(*args, **kwargs):
    yield SimpleNamespace(brain_id=profile.brain_id)
agent_session.desktop_connection.open_client = connect
agent_session.BrainRuntime = lambda *args, **kwargs: object()
try:
    with open_agent(profile.directory, conversation_id=sys.argv[2], start_service=False,
                    generate=lambda _: 'unused', generator_fingerprint='model-v1'):
        print('opened')
except SessionError as error:
    print(error.code)
"""
    with session(mocked):
        for conversation, expected in (("conversation", "session_busy"), ("other", "opened")):
            result = subprocess.run(
                [sys.executable, "-c", script, str(mocked.profile.directory), conversation],
                check=True, text=True, capture_output=True, timeout=10,
            )
            assert result.stdout.strip() == expected and not result.stderr


def test_same_session_operations_do_not_overlap_and_closed_session_rejects_calls(mocked):
    started, release = threading.Event(), threading.Event()
    original = mocked.runtime.respond.side_effect

    def blocking(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    mocked.runtime.respond.side_effect = blocking
    with session(mocked) as agent:
        thread = threading.Thread(target=lambda: agent.respond("message", message_id="one"))
        thread.start()
        assert started.wait(5)
        try:
            with pytest.raises(SessionError, match="session_busy"):
                agent.respond("message", message_id="one")
        finally:
            release.set()
            thread.join(5)
        assert not thread.is_alive()
    for call in (lambda: agent.pending, agent.resume, lambda: agent.respond("message", message_id="one")):
        with pytest.raises(SessionError, match="session_closed"):
            call()


def test_profile_identity_change_is_blocked_and_memory_client_is_closed(mocked, monkeypatch):
    real_load = agent_session.load_profile
    calls = 0

    def changed(directory):
        nonlocal calls
        calls += 1
        current = real_load(directory)
        return replace(current, brain_id="changed") if calls > 1 else current

    monkeypatch.setattr(agent_session, "load_profile", changed)
    with pytest.raises(SessionError, match="profile_identity_changed"):
        with session(mocked):
            pytest.fail("Changed identity accepted")
    assert mocked.closed == 1


def test_profile_identity_is_rechecked_before_operations(mocked, monkeypatch):
    with session(mocked) as agent:
        monkeypatch.setattr(agent_session, "load_profile", lambda _: replace(mocked.profile, tenant_id="other"))
        with pytest.raises(SessionError, match="profile_identity_changed"):
            agent.respond("message", message_id="one")
    mocked.runtime.respond.assert_not_called()


@pytest.mark.parametrize("damage", ["invalid_json", "duplicate", "oversized", "extra", "identity", "turn", "hash"])
def test_corrupt_pending_is_preserved_and_blocks_open_before_service_start(mocked, damage):
    with session(mocked) as agent:
        agent.respond("private message", message_id="one")
    path = pending_path(mocked.profile)
    value = json.loads(path.read_text())
    if damage == "invalid_json":
        raw = b"private message"
    elif damage == "duplicate":
        raw = b'{"version":1,"version":1}'
    elif damage == "oversized":
        raw = b" " * 8193
    else:
        if damage == "extra":
            value["text"] = "private message"
        elif damage == "identity":
            value["identity"]["brain_id"] = "other"
        elif damage == "turn":
            value["turn_id"] = "turn-" + "0" * 40
        else:
            value["input_fingerprint"] = "private message"
        raw = json.dumps(value).encode()
    path.write_bytes(raw)
    mocked.connect.reset_mock()
    with pytest.raises(SessionError) as error:
        with session(mocked):
            pytest.fail("Corrupt state accepted")
    assert error.value.code == "session_state_invalid"
    assert "private message" not in str(error.value)
    assert path.read_bytes() == raw
    mocked.connect.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="POSIX special files and permission bits")
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_pending_files_are_rejected_without_reading_targets(mocked, tmp_path, kind):
    with session(mocked) as agent:
        agent.respond("message", message_id="one")
    path = pending_path(mocked.profile)
    path.unlink()
    target = tmp_path / "private-target"
    target.write_text("private contents")
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    else:
        os.mkfifo(path)
    with pytest.raises(SessionError):
        with session(mocked):
            pytest.fail("Unsafe state accepted")
    assert target.read_text() == "private contents"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_pending_and_session_directories_are_private(mocked):
    with session(mocked) as agent:
        agent.respond("message", message_id="one")
    path = pending_path(mocked.profile)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert not list(path.parent.glob(".pending-*.tmp"))


def test_failed_pending_persistence_prevents_runtime_call(mocked, monkeypatch):
    monkeypatch.setattr(agent_session.os, "replace", Mock(side_effect=OSError("private filename")))
    with session(mocked) as agent:
        with pytest.raises(SessionError, match="session_state_unavailable") as error:
            agent.respond("message", message_id="one")
        assert "private filename" not in str(error.value)
        assert agent.pending is None
    mocked.runtime.respond.assert_not_called()


def test_default_generator_owned_by_session_and_key_rotation_preserves_resume(mocked, monkeypatch):
    from agent_memory import chat_config

    calls, generators = [], []

    def configured(directory):
        calls.append(directory)
        return chat_config.ChatConfiguration("https://provider.example/v1", "model", f"key-{len(calls)}")

    def make_generator(configuration):
        generator = Mock()
        generators.append(generator)
        return generator

    monkeypatch.setattr(chat_config, "load_chat_configuration", configured)
    monkeypatch.setattr(chat_config.ChatConfiguration, "generator", make_generator)
    with open_agent(mocked.profile.directory, conversation_id="conversation", start_service=False) as agent:
        agent.respond("message", message_id="one")
    with open_agent(mocked.profile.directory, conversation_id="conversation", start_service=False) as reopened:
        assert reopened.resume().stage == "generated"
    assert len(generators) == 2
    for generator in generators:
        generator.close.assert_called_once_with()
    assert all(path == mocked.profile.directory for path in calls)


@pytest.mark.parametrize("field,value", [
    ("input_fingerprint", "0" * 64), ("brain_id", "different"),
    ("conversation_id", "different"), ("branch_id", "different"), ("turn_id", "turn-" + "0" * 40),
])
def test_resume_and_cancel_validate_database_binding(mocked, field, value):
    with session(mocked) as agent:
        first = agent.respond("message", message_id="one")
        mocked.rows[first.turn_id][field] = value
        for operation in (agent.resume, lambda: agent.cancel(turn_id=first.turn_id)):
            with pytest.raises(SessionError, match="session_state_invalid"):
                operation()
        assert agent.pending["turn_id"] == first.turn_id
    mocked.runtime.resume.assert_not_called()
    mocked.runtime.journal.claim.assert_not_called()
    mocked.runtime.journal.fail.assert_not_called()


def test_cancel_requires_exact_turn_id_and_valid_acknowledgement(mocked):
    with session(mocked) as agent:
        result = agent.respond("message", message_id="one")
        mocked.runtime.journal.get.reset_mock()
        for identifier, acknowledgement, code in (
            ("turn-" + "0" * 40, False, "pending_turn"), ("invalid", False, "invalid_turn_id"),
            (None, False, "invalid_turn_id"), (result.turn_id, 1, "invalid_configuration"),
        ):
            with pytest.raises(SessionError, match=code):
                agent.cancel(turn_id=identifier, acknowledge_uncertain=acknowledgement)
        mocked.runtime.journal.get.assert_not_called()
        assert agent.pending["turn_id"] == result.turn_id
    with pytest.raises(SessionError, match="session_closed"):
        agent.cancel(turn_id=result.turn_id)


@pytest.mark.parametrize("stage", ["delivered", "committed"])
def test_cancel_cannot_fail_confirmed_delivery(mocked, stage):
    with session(mocked) as agent:
        result = agent.respond("message", message_id="one")
        mocked.stage = stage
        with pytest.raises(SessionError, match="delivery_already_recorded"):
            agent.cancel(turn_id=result.turn_id, acknowledge_uncertain=True)
        assert agent.pending["turn_id"] == result.turn_id
    mocked.runtime.journal.claim.assert_not_called()
    mocked.runtime.journal.fail.assert_not_called()


@pytest.mark.parametrize("stage,code", [("completed", None), ("failed", "generation_uncertain")])
def test_cancel_clears_already_terminal_turn_without_new_side_effects(mocked, stage, code):
    with session(mocked) as agent:
        result = agent.respond("message", message_id="one")
    mocked.stage = stage
    mocked.rows[result.turn_id]["failure_code"] = code
    with session(mocked, instructions="Changed instructions") as reopened:
        assert reopened.cancel(turn_id=result.turn_id) == {
            "turn_id": result.turn_id, "stage": stage, "failure_code": code,
        }
        assert reopened.pending is None
    mocked.runtime.journal.claim.assert_not_called()
    mocked.runtime.journal.fail.assert_not_called()


def test_new_message_database_preflight_failure_leaves_no_pending(mocked):
    mocked.runtime.journal.get.side_effect = httpx.ConnectError("offline")
    with session(mocked) as agent:
        with pytest.raises(httpx.ConnectError):
            agent.respond("message", message_id="one")
        assert agent.pending is None
    mocked.runtime.respond.assert_not_called()


@pytest.fixture
def live_local(tmp_path, monkeypatch):
    load_dotenv()
    dsn = os.environ.get("MEMORY_DATABASE_URL")
    if not dsn:
        pytest.skip("A dedicated PostgreSQL database is required")
    profile = ensure_profile(tmp_path / "local-profile")
    principal = Principal(profile.tenant_id, profile.principal_id)
    settings = Settings(database_url=dsn, api_keys={hashlib.sha256(profile.api_key.encode()).hexdigest(): principal})
    with TestClient(create_app(settings)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path, headers=dict(request.headers),
                                      content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        def connect(directory, **kwargs):
            return MemoryClient("http://testserver", profile.api_key, profile.brain_id,
                                transport=httpx.MockTransport(forward), trust_env=False)

        with connect(profile.directory) as client:
            profile = replace(profile, brain_id=client.create_brain("Local session integration")["id"])
            _save(profile)
        monkeypatch.setattr(agent_session.desktop_connection, "open_client", connect)
        yield profile


@pytest.mark.integration
def test_real_runtime_restart_idempotency_and_cross_conversation_memory(live_local):
    prompts, deliveries = [], []

    def generate(request):
        prompts.append(request)
        return "You told me your school was Willow Primary."

    def deliver(request):
        deliveries.append(request)
        return DeliveryReceipt("delivered")

    options = {"generate": generate, "generator_fingerprint": "deterministic-v1",
               "deliver": deliver, "start_service": False}
    with open_agent(live_local.directory, conversation_id="school", **options) as agent:
        first = agent.respond("I attended Willow Primary School.", message_id="one")
        assert first.stage == "completed" and agent.pending is None
    with open_agent(live_local.directory, conversation_id="school", **options) as restarted:
        assert restarted.respond("I attended Willow Primary School.", message_id="one").replayed
    assert len(prompts) == len(deliveries) == 1
    with open_agent(live_local.directory, conversation_id="new-conversation", **options) as fresh:
        assert fresh.respond("Which school did I attend?", message_id="two").stage == "completed"
    assert "Willow" in prompts[-1].memory_context


@pytest.mark.integration
def test_real_runtime_reused_id_with_different_text_never_recovers_the_old_answer(live_local):
    generate, deliver = Mock(return_value="Original answer"), Mock(return_value=DeliveryReceipt("delivered"))
    options = {"generate": generate, "generator_fingerprint": "model-v1", "deliver": deliver,
               "start_service": False, "conversation_id": "reuse"}
    with open_agent(live_local.directory, **options) as agent:
        original = agent.respond("Original input", message_id="one")
        with pytest.raises(SessionError, match="message_identity_conflict"):
            agent.respond("Different input", message_id="one")
        assert agent.pending is None
        assert agent.respond("Original input", message_id="one").replayed
        assert generate.call_count == deliver.call_count == 1
        identity = agent_session._identity(live_local, "reuse", "main")
        forged = {
            "version": 1, "identity": identity, "message_id": "one", "turn_id": original.turn_id,
            "input_fingerprint": digest({"message_id": "one", "text": "Different input"}),
            "generator_fingerprint": agent._generator_fingerprint,
            "runtime_fingerprint": agent._runtime_fingerprint,
        }
        agent_session._save_pending(agent._path, forged)
        for operation in (agent.resume, lambda: agent.cancel(turn_id=original.turn_id)):
            with pytest.raises(SessionError, match="session_state_invalid"):
                operation()
        assert agent.pending["turn_id"] == original.turn_id
    assert generate.call_count == deliver.call_count == 1


@pytest.mark.integration
def test_real_runtime_budget_block_can_be_explicitly_cancelled_after_configuration_change(live_local):
    generate, deliver = Mock(return_value="A new answer"), Mock(return_value=DeliveryReceipt("delivered"))
    options = {"generate": generate, "generator_fingerprint": "model-v1", "deliver": deliver,
               "start_service": False, "conversation_id": "budget"}
    with open_agent(live_local.directory, policy=RuntimePolicy(max_input_tokens=1), **options) as agent:
        with pytest.raises(RuntimeBlocked, match="input_budget_exceeded"):
            agent.respond("First input", message_id="one")
        pending = agent.pending
    generate.assert_not_called()
    with open_agent(live_local.directory, **options) as reopened:
        with pytest.raises(SessionError, match="configuration_changed"):
            reopened.resume()
        assert reopened.cancel(turn_id=pending["turn_id"]) == {
            "turn_id": pending["turn_id"], "stage": "failed", "failure_code": "cancelled",
        }
        assert reopened.pending is None
        assert reopened.respond("Second input", message_id="two").stage == "completed"
    assert generate.call_count == deliver.call_count == 1


@pytest.mark.integration
def test_real_runtime_draft_recovers_without_regenerating(live_local):
    generate = Mock(return_value="A saved draft.")
    options = {"generate": generate, "generator_fingerprint": "deterministic-v1", "start_service": False}
    with open_agent(live_local.directory, conversation_id="draft", **options) as agent:
        first = agent.respond("Hello", message_id="one")
        assert first.stage == "generated" and agent.pending
    deliveries = []
    with open_agent(live_local.directory, conversation_id="draft", deliver=lambda request: (
        deliveries.append(request) or DeliveryReceipt("delivered")
    ), **options) as recovered:
        assert recovered.resume().stage == "completed" and recovered.pending is None
    assert generate.call_count == len(deliveries) == 1


@pytest.mark.integration
def test_real_runtime_original_input_recovers_failure_before_journal_begin(live_local, monkeypatch):
    generate = Mock(return_value="A recovered reply.")
    deliver = Mock(return_value=DeliveryReceipt("delivered"))
    options = {"generate": generate, "generator_fingerprint": "deterministic-v1",
               "deliver": deliver, "start_service": False, "conversation_id": "before-begin"}
    original = agent_session.BrainRuntime.respond
    with open_agent(live_local.directory, **options) as agent:
        with monkeypatch.context() as patch:
            patch.setattr(agent_session.BrainRuntime, "respond", Mock(side_effect=httpx.ConnectError("offline")))
            with pytest.raises(httpx.ConnectError):
                agent.respond("Original message", message_id="one")
        pending = agent.pending
    assert agent_session.BrainRuntime.respond is original
    with open_agent(live_local.directory, **options) as reopened:
        assert reopened.pending == pending
        with pytest.raises(httpx.HTTPStatusError) as error:
            reopened.resume()
        assert error.value.response.status_code == 404
        assert reopened.pending == pending
        assert reopened.respond("Original message", message_id="one").stage == "completed"
        assert reopened.pending is None
    assert generate.call_count == deliver.call_count == 1


@pytest.mark.integration
def test_real_runtime_cancel_before_begin_creates_a_durable_cancellation_tombstone(live_local, monkeypatch):
    generate, deliver = Mock(return_value="A new reply"), Mock(return_value=DeliveryReceipt("delivered"))
    options = {"generate": generate, "generator_fingerprint": "model-v1", "deliver": deliver,
               "start_service": False, "conversation_id": "cancel-before-begin"}
    with open_agent(live_local.directory, **options) as agent:
        with monkeypatch.context() as patch:
            patch.setattr(agent_session.BrainRuntime, "respond", Mock(side_effect=httpx.ConnectError("offline")))
            with pytest.raises(httpx.ConnectError):
                agent.respond("First input", message_id="one")
        pending = agent.pending
    with open_agent(live_local.directory, **options) as reopened:
        assert reopened.cancel(turn_id=pending["turn_id"])["failure_code"] == "cancelled"
        row = reopened._runtime.journal.get(pending["turn_id"])
        assert row["stage"] == "failed" and row["input_event_refs"] == []
        assert row["input_fingerprint"] == digest({"message_id": "one", "text": "First input"})
        assert reopened.pending is None
        with pytest.raises(RuntimeBlocked, match="cancelled"):
            reopened.respond("First input", message_id="one")
        assert reopened.cancel(turn_id=pending["turn_id"])["failure_code"] == "cancelled"
        assert reopened.respond("Second input", message_id="two").stage == "completed"
    assert generate.call_count == deliver.call_count == 1


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["claim", "fail"])
def test_real_runtime_failed_cancel_request_preserves_pending(live_local, monkeypatch, operation):
    generate = Mock(return_value="A draft")
    with open_agent(live_local.directory, conversation_id="cancel-offline", generate=generate,
                    generator_fingerprint="model-v1", start_service=False) as agent:
        agent.respond("Hello", message_id="one")
        pending = agent.pending
        monkeypatch.setattr(agent._runtime.journal, operation, Mock(side_effect=httpx.ReadTimeout("uncertain")))
        with pytest.raises(httpx.ReadTimeout):
            agent.cancel(turn_id=pending["turn_id"])
        assert agent.pending == pending
        assert agent._runtime.journal.get(pending["turn_id"])["stage"] == "generated"
    generate.assert_called_once()


@pytest.mark.integration
def test_real_runtime_cancel_cannot_overwrite_confirmed_delivery(live_local, monkeypatch):
    generate, deliver = Mock(return_value="Delivered reply"), Mock(return_value=DeliveryReceipt("delivered"))
    options = {"generate": generate, "generator_fingerprint": "model-v1", "deliver": deliver,
               "start_service": False, "conversation_id": "delivered"}
    with open_agent(live_local.directory, **options) as agent:
        with monkeypatch.context() as patch:
            patch.setattr(agent._runtime.journal, "finish", Mock(side_effect=httpx.ConnectError("offline")))
            with pytest.raises(httpx.ConnectError):
                agent.respond("Hello", message_id="one")
        pending = agent.pending
        assert agent._runtime.journal.get(pending["turn_id"])["stage"] == "delivered"
        with pytest.raises(SessionError, match="delivery_already_recorded"):
            agent.cancel(turn_id=pending["turn_id"], acknowledge_uncertain=True)
        assert agent.pending == pending
        assert agent.resume().stage == "completed" and agent.pending is None
    assert generate.call_count == deliver.call_count == 1


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["delivered", "completed"])
@pytest.mark.parametrize("recovery", ["respond", "resume"])
def test_real_runtime_confirmed_delivery_recovery_ignores_changed_generation_config(
        live_local, monkeypatch, stage, recovery):
    generate, deliver = Mock(return_value="Delivered reply"), Mock(return_value=DeliveryReceipt("delivered"))
    options = {"generate": generate, "generator_fingerprint": "model-v1", "deliver": deliver,
               "start_service": False, "conversation_id": "delivered-config-change"}
    with open_agent(live_local.directory, **options) as agent:
        with monkeypatch.context() as patch:
            if stage == "delivered":
                patch.setattr(agent._runtime.journal, "finish", Mock(side_effect=httpx.ConnectError("offline")))
            else:
                patch.setattr(agent, "_clear", Mock(side_effect=SessionError("session_state_unavailable")))
            with pytest.raises((httpx.ConnectError, SessionError)):
                agent.respond("Hello", message_id="one")
        pending = agent.pending
        assert agent._runtime.journal.get(pending["turn_id"])["stage"] == stage
    changed_generate, changed_deliver = Mock(), Mock()
    changed = options | {"generate": changed_generate, "generator_fingerprint": "model-v2",
                         "deliver": changed_deliver, "instructions": "Different host instructions",
                         "policy": RuntimePolicy(memory_context_tokens=4500)}
    with open_agent(live_local.directory, **changed) as reopened:
        result = reopened.resume() if recovery == "resume" else reopened.respond("Hello", message_id="one")
        assert result.stage == "completed" and result.text == "Delivered reply"
        assert reopened.pending is None
    changed_generate.assert_not_called()
    changed_deliver.assert_not_called()
    assert generate.call_count == deliver.call_count == 1


@pytest.mark.integration
def test_real_runtime_cancel_recovers_lost_terminal_response_without_repeating_work(live_local, monkeypatch):
    generate = Mock(return_value="A saved draft")
    with open_agent(live_local.directory, conversation_id="cancel-lost-response", generate=generate,
                    generator_fingerprint="model-v1", start_service=False) as agent:
        agent.respond("Hello", message_id="one")
        pending = agent.pending
        fail = agent._runtime.journal.fail

        def lose_response(*args, **kwargs):
            fail(*args, **kwargs)
            raise httpx.ReadTimeout("outcome unknown")

        with monkeypatch.context() as patch:
            patch.setattr(agent._runtime.journal, "fail", lose_response)
            with pytest.raises(httpx.ReadTimeout):
                agent.cancel(turn_id=pending["turn_id"])
        assert agent.pending == pending
        row = agent._runtime.journal.get(pending["turn_id"])
        assert row["stage"] == "failed" and row["failure_code"] == "cancelled"
        assert agent.cancel(turn_id=pending["turn_id"])["failure_code"] == "cancelled"
        assert agent.pending is None
        assert agent._runtime.journal.get(pending["turn_id"])["revision"] == row["revision"]
    generate.assert_called_once()


@pytest.mark.integration
def test_real_runtime_cancel_does_not_steal_another_workers_active_lease(live_local):
    generate = Mock(return_value="A saved draft")
    with open_agent(live_local.directory, conversation_id="cancel-lease", generate=generate,
                    generator_fingerprint="model-v1", start_service=False) as agent:
        agent.respond("Hello", message_id="one")
        pending = agent.pending
        journal = agent._runtime.journal
        row = journal.get(pending["turn_id"])
        held = journal.claim(pending["turn_id"], worker_id="another-host", expected_revision=row["revision"])
        try:
            with pytest.raises(httpx.HTTPStatusError) as error:
                agent.cancel(turn_id=pending["turn_id"])
            assert error.value.response.status_code == 409
            assert agent.pending == pending
            assert journal.get(pending["turn_id"])["revision"] == held["revision"]
        finally:
            journal.release(pending["turn_id"], lease_token=held["lease_token"], expected_revision=held["revision"])
        assert agent.cancel(turn_id=pending["turn_id"])["failure_code"] == "cancelled"
        assert agent.pending is None
    generate.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize("uncertain", ["generation", "delivery"])
def test_real_runtime_unknown_outcomes_are_never_retried(live_local, uncertain):
    generate = Mock(return_value="A reply")
    deliver = Mock(return_value=DeliveryReceipt("unknown"))
    if uncertain == "generation":
        generate.side_effect = TimeoutError("unknown provider outcome")
    options = {"generate": generate, "generator_fingerprint": "model-v1", "deliver": deliver,
               "start_service": False, "conversation_id": "unknown"}
    with open_agent(live_local.directory, **options) as agent:
        with pytest.raises((RuntimeBlocked, TimeoutError)):
            agent.respond("Hello", message_id="one")
        expected = agent.pending
    with open_agent(live_local.directory, **options) as reopened:
        with pytest.raises(RuntimeBlocked, match=uncertain + "_uncertain"):
            reopened.resume()
        with pytest.raises(SessionError, match="pending_turn"):
            reopened.respond("Next input", message_id="two")
        assert reopened.pending == expected
        with pytest.raises(SessionError, match="acknowledgement_required"):
            reopened.cancel(turn_id=expected["turn_id"])
        assert reopened.pending == expected
        assert reopened.cancel(turn_id=expected["turn_id"], acknowledge_uncertain=True) == {
            "turn_id": expected["turn_id"], "stage": "failed", "failure_code": uncertain + "_uncertain",
        }
        assert reopened.pending is None
    assert generate.call_count == 1
    assert deliver.call_count == (1 if uncertain == "delivery" else 0)
    generate.side_effect = None
    deliver.return_value = DeliveryReceipt("delivered")
    with open_agent(live_local.directory, **options) as next_session:
        assert next_session.respond("Next input", message_id="two").stage == "completed"
    assert generate.call_count == 2
    assert deliver.call_count == (2 if uncertain == "delivery" else 1)
