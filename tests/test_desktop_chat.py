import argparse
import io
import json
from contextlib import contextmanager
from unittest.mock import Mock

import httpx
import pytest

from membrain import desktop_chat, desktop_cli
from membrain.local_profile import ensure_profile, load_profile
from membrain.runtime import DeliveryRequest, RuntimeBlocked, TurnResult


def arguments(*values):
    parser = argparse.ArgumentParser()
    desktop_cli.add_commands(parser.add_subparsers())
    return parser.parse_args(["desktop", *values])


def test_configuration_is_separate_and_never_prints_secrets(tmp_path, monkeypatch, capsys):
    from membrain.chat_config import load_chat_configuration

    profile = ensure_profile(tmp_path / "profile")
    before = (profile.directory / "profile.json").read_bytes()
    monkeypatch.setenv("CHAT_TEST_KEY", "test-secret-chat-credential")
    desktop_cli.dispatch(arguments(
        "configure-chat", "--directory", str(profile.directory),
        "--base-url", "https://models.example/v1", "--model", "chat-model",
        "--api-key-env", "CHAT_TEST_KEY", "--non-interactive",
    ))
    result = capsys.readouterr()
    assert json.loads(result.out) == {"chat": "configured", "cognition": "not_configured",
                                      "service_restart_required": False}
    assert "test-secret-chat-credential" not in result.out + result.err
    assert (profile.directory / "profile.json").read_bytes() == before
    saved = load_chat_configuration(profile.directory)
    assert saved.model == "chat-model"
    assert saved.thinking is None
    assert load_profile(profile.directory).brain_id == profile.brain_id


def test_configure_interactively_uses_hidden_key_input(tmp_path, monkeypatch, capsys):
    from membrain.chat_config import load_chat_configuration

    monkeypatch.setattr(desktop_chat.sys, "stdin", Mock(isatty=lambda: True))
    answers = iter(["https://models.example/v1", "configured-model"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    hidden = Mock(return_value="hidden-provider-key")
    monkeypatch.setattr(desktop_chat.getpass, "getpass", hidden)
    args = arguments("configure-chat", "--directory", str(tmp_path / "new-profile"))
    desktop_cli.dispatch(args)
    hidden.assert_called_once()
    output = capsys.readouterr()
    assert "hidden-provider-key" not in output.out + output.err
    assert load_chat_configuration(args.directory).api_key == "hidden-provider-key"


@pytest.mark.parametrize("flags", [
    ["--non-interactive"], ["--model", "missing-endpoint"],
    ["--use-cognition", "--model", "conflict"],
    ["--base-url", "https://models.example", "--model", "model", "--api-key-env", "MISSING_CHAT_KEY"],
])
def test_invalid_configuration_does_not_create_profile(tmp_path, monkeypatch, flags):
    monkeypatch.delenv("MISSING_CHAT_KEY", raising=False)
    args = arguments("configure-chat", "--directory", str(tmp_path / "new-profile"), *flags)
    with pytest.raises(ValueError):
        desktop_chat.configure(args)
    assert not args.directory.exists()


@pytest.fixture
def fake_session(tmp_path, monkeypatch):
    from membrain import agent_session

    profile = ensure_profile(tmp_path / "profile")
    session = Mock(pending=None)
    session.respond.return_value = TurnResult("turn-test", "completed", "response")
    session.resume.return_value = TurnResult("turn-test", "completed", "response", replayed=True)

    @contextmanager
    def connect(*args, **kwargs):
        session.connection = (args, kwargs)
        yield session

    factory = Mock(side_effect=connect)
    monkeypatch.setattr(agent_session, "open_agent", factory)
    session.factory = factory
    session.profile = profile
    return session


def test_one_shot_preserves_message_identity_and_terminal_delivery(fake_session, capsys):
    args = arguments("chat", "--directory", str(fake_session.profile.directory),
                     "--conversation", "room", "--branch", "branch", "--message", "Hello",
                     "--message-id", "event-17", "--orientation", "on_demand")
    desktop_chat.run(args)
    fake_session.respond.assert_called_once_with("Hello", message_id="event-17")
    bound = fake_session.connection[1]
    assert (bound["conversation_id"], bound["branch_id"]) == ("room", "branch")
    assert bound["policy"].orientation_policy == "on_demand"
    assert capsys.readouterr().out == ""
    receipt = bound["deliver"](DeliveryRequest("turn", "delivery", "Actual response"))
    assert receipt.state == "delivered"
    assert capsys.readouterr().out == "Actual response\n"


def test_terminal_flush_failure_does_not_acknowledge_delivery(monkeypatch):
    stream = Mock()
    stream.write.return_value = len("answer\n")
    stream.flush.side_effect = BrokenPipeError("private output target")
    monkeypatch.setattr(desktop_chat.sys, "stdout", stream)
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        desktop_chat._deliver(DeliveryRequest("turn", "delivery", "answer"))
    stream.write.assert_called_once_with("answer\n")


def test_terminal_short_write_does_not_acknowledge_delivery(monkeypatch):
    stream = Mock()
    stream.write.return_value = 2
    monkeypatch.setattr(desktop_chat.sys, "stdout", stream)
    with pytest.raises(RuntimeBlocked, match="delivery_uncertain"):
        desktop_chat._deliver(DeliveryRequest("turn", "delivery", "answer"))
    stream.flush.assert_not_called()


@pytest.mark.parametrize("flags", [
    ["--message", "Hello"], ["--message-id", "event"], [],
    ["--message", "   ", "--message-id", "event"],
    ["--message", "Hello", "--message-id", "invalid id"],
    ["--message", "Hello", "--message-id", "event", "--acknowledge-uncertain"],
    ["--cancel", ""], ["--cancel", "invalid turn"],
])
def test_invalid_chat_mode_never_opens_session(fake_session, monkeypatch, flags):
    monkeypatch.setattr(desktop_chat.sys, "stdin", io.StringIO())
    with pytest.raises(ValueError):
        desktop_chat.run(arguments("chat", *flags))
    fake_session.factory.assert_not_called()


def test_resume_does_not_generate_new_message_or_reprint_completed_answer(fake_session, capsys):
    desktop_chat.run(arguments("chat", "--directory", str(fake_session.profile.directory), "--resume"))
    fake_session.resume.assert_called_once_with()
    fake_session.respond.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert "already completed" in output.err


def test_interactive_resume_and_retry_keep_original_identity(fake_session, monkeypatch, capsys):
    fake_session.pending = {"turn_id": "turn-pending", "message_id": "original-message"}
    fake_session.resume.side_effect = RuntimeBlocked("input_not_captured_retry_original_message", "turn-pending")
    answers = iter(["/resume", "/retry Original input", "/quit"])
    monkeypatch.setattr(desktop_chat.sys, "stdin", Mock(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    desktop_chat.run(arguments("chat", "--directory", str(fake_session.profile.directory)))
    fake_session.respond.assert_called_once_with("Original input", message_id="original-message")
    fake_session.resume.assert_called_once_with()
    output = capsys.readouterr()
    assert "turn-pending" in output.err
    assert output.out == ""


def test_incomplete_commands_and_whitespace_do_not_become_model_input(fake_session, monkeypatch):
    answers = iter(["/retry", "/resume ", "/resume invalid", "/retry\ttext", " /quit "])
    monkeypatch.setattr(desktop_chat.sys, "stdin", Mock(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    desktop_chat.run(arguments("chat", "--directory", str(fake_session.profile.directory)))
    fake_session.respond.assert_not_called()
    fake_session.resume.assert_called_once_with()


def test_service_failure_is_redacted_and_one_shot_fails(fake_session, capsys):
    fake_session.respond.side_effect = httpx.ConnectError("https://secret:password@private.example")
    with pytest.raises(SystemExit) as error:
        desktop_chat.run(arguments("chat", "--directory", str(fake_session.profile.directory),
                                   "--message", "Hi", "--message-id", "original"))
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "memory_service_unavailable" in output.err
    assert "password" not in output.err
    assert output.out == ""


@pytest.mark.parametrize("acknowledged", [False, True])
def test_cancellation_is_explicit_and_does_not_generate_or_deliver(fake_session, capsys, acknowledged):
    turn_id = "turn-" + "a" * 40
    fake_session.cancel.return_value = {
        "turn_id": turn_id, "stage": "failed",
        "failure_code": "generation_uncertain" if acknowledged else "cancelled",
    }
    flags = ["--acknowledge-uncertain"] if acknowledged else []
    desktop_chat.run(arguments("chat", "--directory", str(fake_session.profile.directory),
                               "--cancel", turn_id, *flags))
    fake_session.cancel.assert_called_once_with(turn_id=turn_id, acknowledge_uncertain=acknowledged)
    fake_session.respond.assert_not_called()
    fake_session.resume.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert ("remains unknown" in output.err) is acknowledged


@pytest.mark.parametrize("contents", [b"x" * 64001, b"\xff"])
def test_invalid_instruction_file_fails_before_opening_session(fake_session, tmp_path, contents):
    path = tmp_path / "instructions.txt"
    path.write_bytes(contents)
    with pytest.raises(ValueError):
        desktop_chat.run(arguments("chat", "--instructions-file", str(path),
                                   "--message", "Hello", "--message-id", "id"))
    fake_session.factory.assert_not_called()
