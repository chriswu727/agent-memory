import json
from pathlib import Path

import pytest

from membrain import desktop, embedded_database, host
from membrain.adapters import hermes


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")


class Client:
    brain_id = "brain"

    def __init__(self, notes=()):
        self.observed, self.notes, self.forgotten = [], list(notes), []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def observe(self, text, **options):
        event_id = f"e{len(self.observed) + 1}"
        self.observed.append({"text": text, **options})
        if options.get("conversation_id") == "membrain-notes":
            self.notes.insert(0, {"event_id": event_id, "text": text, "actor_role": "assistant",
                                  "recorded_at": "2026-10-28T00:00:00Z"})
        return {"result": {"accepted_event_ids": [event_id]}}

    def timeline(self, **options):
        if options.get("conversation_id") == "membrain-notes":
            return {"complete": True, "items": [note for note in self.notes if note["event_id"] not in self.forgotten]}
        return {"complete": True, "items": []}

    def recall(self, query, **options):
        return {"result": {"items": []}}

    def dashboard(self):
        return {"brain": {"scope_generation": 3}}

    def forget(self, ids, **options):
        self.forgotten.extend(ids)


def provider(tmp_path, monkeypatch, client, **kwargs):
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
    instance = hermes.MembrainProvider()
    instance.initialize("h1", hermes_home=str(tmp_path / "home"), platform="cli", **kwargs)
    return instance


def test_register_hands_hermes_a_new_provider_each_time():
    registered = []

    class Context:
        def register_memory_provider(self, value):
            registered.append(value)

    hermes.register(Context())
    hermes.register(Context())
    assert all(isinstance(value, hermes.MembrainProvider) for value in registered)
    assert registered[0] is not registered[1] and registered[0].name == "membrain" and registered[0].is_available()
    assert [tool["name"] for tool in registered[0].get_tool_schemas()] == [
        "membrain_note", "membrain_search", "membrain_pause", "membrain_resume"]


def test_profiles_get_their_own_installation_unless_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MEMBRAIN_DIRECTORY", str(tmp_path / "shared"))
    assert hermes._default_directory(str(tmp_path / ".hermes")) == tmp_path / "shared"
    assert hermes._default_directory(str(tmp_path / "work-profile")) == tmp_path / "work-profile" / "membrain"
    instance = hermes.MembrainProvider()
    (tmp_path / "work-profile").mkdir()
    instance.save_config({"directory": str(tmp_path / "chosen")}, str(tmp_path / "work-profile"))
    assert hermes._default_directory(str(tmp_path / "work-profile")) == tmp_path / "chosen"


def test_a_turn_is_recalled_before_and_stored_after_the_reply(tmp_path, monkeypatch):
    client = Client(notes=[{"event_id": "n1", "text": "Mom uses a walker.", "actor_role": "assistant",
                            "recorded_at": "2026-04-01T00:00:00Z"}])
    instance = provider(tmp_path, monkeypatch, client, agent_context="primary")
    block = instance.system_prompt_block()
    assert "membrain_note tool" in block and "membrain_pause" in block and "Mom uses a walker. (id: n1)" in block
    assert instance.system_prompt_block() is block  # Static for the session, as Hermes expects.
    instance.prefetch("Plan Sunday dinner for Mom.", session_id="h1")
    assert client.observed == []
    instance.sync_turn("Plan Sunday dinner for Mom.", "A seated menu.", session_id="h1")
    assert [(record["role"], record["text"]) for record in client.observed] == [
        ("user", "Plan Sunday dinner for Mom."), ("assistant", "A seated menu.")]
    instance.sync_turn("A turn Hermes did not prefetch for.", "Answered.", session_id="h1")
    assert [record["text"] for record in client.observed][-2:] == ["A turn Hermes did not prefetch for.", "Answered."]


def test_subagents_and_paused_recording_store_nothing(tmp_path, monkeypatch):
    client = Client()
    sub = provider(tmp_path, monkeypatch, client, agent_context="subagent")
    sub.prefetch("internal task", session_id="h1")
    sub.sync_turn("internal task", "done", session_id="h1")
    instance = provider(tmp_path, monkeypatch, client)
    assert json.loads(instance.handle_tool_call("membrain_pause", {}))["recording"] == "paused"
    instance.sync_turn("Off the record: I'm job hunting.", "Understood.", session_id="h1")
    assert client.observed == []
    assert json.loads(instance.handle_tool_call("membrain_resume", {})) == {"recording": "on"}


def test_tools_save_and_replace_notes_and_search(tmp_path, monkeypatch):
    client = Client()
    instance = provider(tmp_path, monkeypatch, client)
    first = json.loads(instance.handle_tool_call("membrain_note", {"text": "Dinner on Sat May 9."}))
    second = json.loads(instance.handle_tool_call("membrain_note", {"text": "Dinner moved to Sun May 17.",
                                                                    "replaces": [first["note_id"]]}))
    assert second["replaced"] == [first["note_id"]] and client.forgotten == [first["note_id"]]
    assert "error" in json.loads(instance.handle_tool_call("membrain_note", {"text": "x" * 400}))
    assert json.loads(instance.handle_tool_call("membrain_search", {"query": "dinner"})) == {
        "context": "No matching records."}
    assert "Unknown tool" in instance.handle_tool_call("other", {})


def test_builtin_memory_writes_are_mirrored_as_notes(tmp_path, monkeypatch):
    client = Client()
    instance = provider(tmp_path, monkeypatch, client)
    instance.on_memory_write("add", "user", "Prefers short replies.")
    instance.on_memory_write("replace", "user", "Prefers short replies, signed Hao.",
                             {"previous_content": "Prefers short replies."})
    assert [note["text"] for note in client.timeline(conversation_id="membrain-notes")["items"]] == [
        "Prefers short replies, signed Hao."]
    instance.on_memory_write("remove", "user", "", {"previous_content": "Prefers short replies, signed Hao."})
    assert client.timeline(conversation_id="membrain-notes")["items"] == []


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_hermes_sessions_share_memory_through_a_real_service(tmp_path, monkeypatch):
    directory = tmp_path / "brain"
    monkeypatch.setenv("MEMBRAIN_DIRECTORY", str(directory))
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        host.start(directory)
        first = hermes.MembrainProvider()
        first.initialize("session-1", hermes_home=str(Path(tmp_path) / ".hermes"), platform="telegram")
        first.system_prompt_block()
        first.prefetch("My cat Biscuit is allergic to chicken.", session_id="session-1")
        first.sync_turn("My cat Biscuit is allergic to chicken.", "Noted, no chicken treats.", session_id="session-1")
        assert "note_id" in json.loads(first.handle_tool_call("membrain_note", {"text": "Cat Biscuit: chicken allergy."}))
        second = hermes.MembrainProvider()
        second.initialize("session-2", hermes_home=str(Path(tmp_path) / ".hermes"), platform="telegram")
        block = second.system_prompt_block()
        assert "Cat Biscuit: chicken allergy." in block and "allergic to chicken" in block
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass
