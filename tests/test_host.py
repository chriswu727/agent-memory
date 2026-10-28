import json
import os
import time

import pytest

from membrain import desktop, embedded_database, host


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")


class Client:
    brain_id = "brain"
    def __init__(self):
        self.observed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def observe(self, **record):
        self.observed.append(record)

    def recall(self, query, **options):
        return {"result": {"items": []}}


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-" + "a" * 40, "sk-proj-" + "b" * 30, "ghp_" + "c" * 36, "github_pat_" + "d" * 50,
    "AKIA" + "E" * 16, "xoxb-1234567890-abcdefghij", "AIza" + "f" * 35,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
])
def test_credentials_are_scrubbed(secret):
    assert host.scrub(f"my key is {secret} ok") == f"my key is {host.SECRET_MARK} ok"


def test_assignments_and_private_spans_are_scrubbed_but_ordinary_text_is_kept():
    text = ('password: hunter2hunter2 and api_key="abc123def456" then '
            "<private>my diagnosis</private> visible <PRIVATE>until the end")
    assert host.scrub(text) == (f"password: {host.SECRET_MARK} and api_key={host.SECRET_MARK} then "
                                f"{host.PRIVATE_MARK} visible {host.PRIVATE_MARK}")
    ordinary = "My sister's password manager is 1Password; the token ring game is fun. 我对虾过敏。"
    assert host.scrub(ordinary) == ordinary
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    assert host.scrub(f"here {key} done") == f"here {host.SECRET_MARK} done"


def test_turns_are_stored_with_the_reply_and_private_text_never_reaches_disk(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(host, "ready_client", lambda directory: client)
    prompt = "My token is sk-proj-" + "x" * 30 + " and <private>I was fired today</private>. Plan my week."
    host.prompt_context(tmp_path, "s1", prompt, turn_id="t1")
    stored_state = "".join(path.read_text() for path in (tmp_path / "plugin" / "sessions").glob("*.json"))
    assert "fired" not in stored_state and "x" * 30 not in stored_state and client.observed == []
    host.finish_turn(tmp_path, "s1", "Here is a calm week.", turn_id="t1")
    assert [(record["role"], record["source_event_key"]) for record in client.observed] == [
        ("user", "s1:t1:user"), ("assistant", "s1:t1:assistant")]
    assert "fired" not in client.observed[0]["text"] and host.PRIVATE_MARK in client.observed[0]["text"]
    assert client.observed[1]["parent_source_event_keys"] == ["s1:t1:user"]


def test_pausing_during_a_turn_keeps_that_turn_and_later_ones_out(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(host, "ready_client", lambda directory: client)
    host.prompt_context(tmp_path, "s1", "Don't remember this: I'm job hunting.", turn_id="t1")
    host.pause(tmp_path)
    host.finish_turn(tmp_path, "s1", "Understood, I won't keep it.", turn_id="t1")
    host.prompt_context(tmp_path, "s1", "Still secret.", turn_id="t2")
    host.finish_turn(tmp_path, "s1", "Okay.", turn_id="t2")
    assert client.observed == [] and host.paused(tmp_path)
    assert not (tmp_path / "plugin" / "spool.jsonl").exists()
    assert "Recording is currently paused." in host.session_context(tmp_path, "s2")
    host.resume(tmp_path)
    host.prompt_context(tmp_path, "s1", "Back to normal.", turn_id="t3")
    host.finish_turn(tmp_path, "s1", "Welcome back.", turn_id="t3")
    assert [record["text"] for record in client.observed] == ["Back to normal.", "Welcome back."]


def test_timed_pause_expires(tmp_path):
    assert host.pause(tmp_path, hours=1)["until"] > time.time()
    assert host.paused(tmp_path)
    path = tmp_path / "plugin" / "paused.json"
    path.write_text(json.dumps({"since": 0, "until": time.time() - 1}))
    assert not host.paused(tmp_path) and not path.exists()
    with pytest.raises(ValueError):
        host.pause(tmp_path, hours=0)


def test_spooled_records_are_scrubbed_when_the_service_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "ready_client", lambda directory: None)
    monkeypatch.setattr(host, "start_in_background", lambda directory: None)
    host.prompt_context(tmp_path, "s1", "password=correcthorsebattery remember my dog Biscuit", turn_id="t1")
    host.finish_turn(tmp_path, "s1", "Biscuit noted.", turn_id="t1")
    spooled = (tmp_path / "plugin" / "spool.jsonl").read_text()
    assert "correcthorse" not in spooled and "Biscuit" in spooled


def test_an_unfinished_turn_is_stored_by_the_next_prompt_or_when_abandoned(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(host, "ready_client", lambda directory: client)
    host.prompt_context(tmp_path, "s1", "First, never answered.", turn_id="t1")
    host.prompt_context(tmp_path, "s1", "Second.", turn_id="t2")
    assert [record["text"] for record in client.observed] == ["First, never answered."]
    host.prompt_context(tmp_path, "crashed", "Left mid-turn.", turn_id="t9")
    marker = next(path for path in (tmp_path / "plugin" / "sessions").glob("*.json")
                  if "Left mid-turn." in path.read_text())
    os.utime(marker, (0, 0))
    monkeypatch.setattr(host, "_history", lambda client, session, budget: (None, False))
    monkeypatch.setattr("membrain.notes.list_notes", lambda client: {"items": []})
    host.session_context(tmp_path, "s3")
    assert [record["text"] for record in client.observed][-1] == "Left mid-turn."


def test_mcp_offers_pause_tools_only_for_a_local_installation(tmp_path):
    import asyncio

    from membrain.mcp_server import create_server

    def tools(server):
        return {tool.name for tool in asyncio.run(server.list_tools())}

    assert {"memory_pause", "memory_resume"} <= tools(create_server(Client(), directory=tmp_path))
    assert not {"memory_pause", "memory_resume"} & tools(create_server(Client()))


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_private_text_never_reaches_the_database(tmp_path):
    directory = tmp_path / "brain"
    try:
        host.start(directory)
        host.session_context(directory, "a")
        host.prompt_context(directory, "a", "Remember Biscuit. <private>My PIN is 4821</private> "
                                            "token=ghp_" + "z" * 36, turn_id="1")
        host.finish_turn(directory, "a", "Biscuit, got it.", turn_id="1")
        with host.ready_client(directory) as client:
            stored = json.dumps(client.timeline(roles=("user", "assistant"), max_characters=200_000))
        assert "Biscuit" in stored and "4821" not in stored and "z" * 36 not in stored
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass
