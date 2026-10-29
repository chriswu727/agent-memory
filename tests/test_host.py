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
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
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
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
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
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: None)
    monkeypatch.setattr(host, "start_in_background", lambda directory, user_id=None: None)
    host.prompt_context(tmp_path, "s1", "password=correcthorsebattery remember my dog Biscuit", turn_id="t1")
    host.finish_turn(tmp_path, "s1", "Biscuit noted.", turn_id="t1")
    spooled = (tmp_path / "plugin" / "spool.jsonl").read_text()
    assert "correcthorse" not in spooled and "Biscuit" in spooled


def test_an_unfinished_turn_is_stored_by_the_next_prompt_or_when_abandoned(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
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


def test_mcp_recall_for_a_local_installation_is_the_compact_hook_recall(tmp_path, monkeypatch):
    import asyncio

    from membrain.mcp_server import create_server

    seen = []
    monkeypatch.setattr(host, "recall_context", lambda client, query, **options: seen.append(query) or "<recalled>")
    server = create_server(Client(), directory=tmp_path)
    tool = {tool.name: tool for tool in asyncio.run(server.list_tools())}["memory_recall"]
    assert set(tool.inputSchema["properties"]) == {"query", "with_ids"}
    _, result = asyncio.run(server.call_tool("memory_recall", {"query": "Mei's allergy"}))
    assert result == {"context": "<recalled>"} and seen == ["Mei's allergy"]
    generic = {tool.name: tool for tool in asyncio.run(create_server(Client()).list_tools())}["memory_recall"]
    assert "intent" in generic.inputSchema["properties"]


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


def test_memory_page_codes_are_single_use_and_expire(tmp_path):
    code = host.issue_ui_code(tmp_path)
    assert host.consume_ui_code(tmp_path, code) and not host.consume_ui_code(tmp_path, code)
    stale = host.issue_ui_code(tmp_path)
    codes = json.loads((tmp_path / "plugin" / "ui-codes.json").read_text())
    (tmp_path / "plugin" / "ui-codes.json").write_text(json.dumps({key: 0 for key in codes}))
    assert not host.consume_ui_code(tmp_path, stale)
    assert not host.consume_ui_code(tmp_path, "short") and not host.consume_ui_code(tmp_path, None)
    assert code not in (tmp_path / "plugin" / "ui-codes.json").read_text()


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_memory_page_signs_in_once_and_controls_recording(tmp_path):
    import httpx

    from membrain.local_profile import load_profile

    directory = tmp_path / "brain"
    try:
        host.start(directory)
        base = load_profile(directory).base_url
        assert "memory.js" in httpx.get(base + "/memory").text
        code = host.issue_ui_code(directory)
        session = httpx.post(base + "/v1/local/ui-session", json={"code": code})
        assert session.status_code == 200 and session.json()["brain_id"] == load_profile(directory).brain_id
        assert httpx.post(base + "/v1/local/ui-session", json={"code": code}).status_code == 401
        headers = {"Authorization": "Bearer " + session.json()["api_key"]}
        assert httpx.get(base + "/v1/local/recording").status_code == 401
        assert httpx.post(base + "/v1/local/recording", json={"paused": True, "hours": 2}, headers=headers).json()[
            "recording"] == "paused" and host.paused(directory)
        assert httpx.post(base + "/v1/local/recording", json={"paused": False}, headers=headers).json() == {
            "recording": "on"}
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass


def test_one_users_pause_does_not_stop_others(tmp_path, monkeypatch):
    client = Client()
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
    monkeypatch.setattr(host, "use_brain", lambda client, user_id: client)
    host.pause(tmp_path, user_id="qq:1001")
    assert host.paused(tmp_path, "qq:1001") and not host.paused(tmp_path, "qq:2002") and not host.paused(tmp_path)
    for user in ("qq:1001", "qq:2002"):
        host.prompt_context(tmp_path, f"group:{user}", f"hello from {user}", turn_id="t1", user_id=user)
        host.finish_turn(tmp_path, f"group:{user}", "hi", turn_id="t1", user_id=user)
    assert [record["text"] for record in client.observed] == ["hello from qq:2002", "hi"]
    assert host.user_brain_id("qq:1001") == host.user_brain_id("qq:1001") != host.user_brain_id("qq:2002")
    with pytest.raises(ValueError):
        host.user_brain_id("")


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_users_of_a_shared_bot_keep_separate_memories(tmp_path, monkeypatch):
    directory = tmp_path / "brain"
    try:
        host.start(directory)
        turns = {"telegram:alice": "I'm allergic to peanuts.", "telegram:bob": "I just adopted a greyhound named Sardina."}
        for user, text in turns.items():
            host.session_context(directory, f"chat:{user}", user_id=user)
            host.prompt_context(directory, f"chat:{user}", text, turn_id="1", user_id=user)
            host.finish_turn(directory, f"chat:{user}", "Noted.", turn_id="1", user_id=user)
        monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: None)
        host.finish_turn(directory, "chat:telegram:alice", "Spooled reply.", turn_id="2",
                         prompt="My sister is called Mei.", user_id="telegram:alice")
        monkeypatch.undo()
        with host.ready_client(directory) as client:
            assert host.flush(client, directory) == 2
        for user, own in turns.items():
            with host.ready_client(directory, user) as client:
                stored = json.dumps(client.timeline(roles=("user", "assistant"), max_characters=200_000))
            other = next(text for name, text in turns.items() if name != user)
            assert own in stored and other not in stored
        with host.ready_client(directory, "telegram:alice") as client:
            assert "Mei" in json.dumps(client.timeline(max_characters=200_000))
        with host.ready_client(directory) as client:
            assert json.loads(json.dumps(client.timeline(max_characters=200_000)))["items"] == []
        context = host.prompt_context(directory, "chat2:telegram:bob", "what is my dog's name again", user_id="telegram:bob")
        assert "Sardina" in (context or "") and "peanuts" not in (context or "")
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass


def test_mcp_from_a_uv_cache_environment_hands_over_to_the_installed_tool(tmp_path, monkeypatch):
    import argparse
    import subprocess

    from membrain import plugin_cli

    tools = tmp_path / "tools"
    python = tools / "membrain-ai" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setenv("UV_TOOL_DIR", str(tools))
    monkeypatch.setattr(host.shutil, "which", lambda name: None)
    monkeypatch.delenv("UV", raising=False)
    assert host.installed_tool_python() == python
    monkeypatch.setattr(host.sys, "prefix", str(tmp_path / ".cache" / "uv" / "archive-v0" / "abc"))
    assert host.running_from_uv_cache()
    calls = []
    monkeypatch.setattr(subprocess, "call", lambda command: calls.append(command) or 0)
    args = argparse.Namespace(plugin_command="mcp", directory=tmp_path / "brain")
    with pytest.raises(SystemExit) as stopped:
        plugin_cli.dispatch(args)
    assert stopped.value.code == 0
    assert calls == [[str(python), "-m", "membrain.cli", "plugin", "mcp", "--directory", str(tmp_path / "brain")]]
    monkeypatch.setattr(host.sys, "prefix", str(tools / "membrain-ai"))
    assert not host.running_from_uv_cache()
