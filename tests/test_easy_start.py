import argparse
import json
from types import SimpleNamespace

import pytest
from test_plugin import _ReadyClient

from membrain import cognition, embedded_database, host, host_context, notes, plugin_cli


def note(key, text, **fields):
    return {"event_id": key, "text": notes.format_note(text, **fields), "actor_role": "user",
            "recorded_at": "2026-10-30T12:00:00Z"}


def test_unavailable_memory_is_not_announced_as_active_and_recovers_in_the_same_session(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "start_in_background", lambda directory: None)
    monkeypatch.setattr(host, "ready_client", lambda *args: None)
    assert "has not been loaded" in host.session_context(tmp_path, "s2")
    client = _ReadyClient({"items": [], "complete": True}, notes={"items": [note("p", "Prefers concise replies.")]})
    monkeypatch.setattr(host, "ready_client", lambda *args: client)
    first = host.prompt_context(tmp_path, "s2", "Hello")
    assert "memory is active" in first and "Prefers concise replies." in first
    assert "<membrain-updates>" not in (host.prompt_context(tmp_path, "s2", "Hello again") or "")


def test_corrected_and_deleted_notes_refresh_even_when_all_user_history_was_loaded(tmp_path, monkeypatch):
    client = _ReadyClient({"items": [{"text": "Earlier conversation.", "recorded_at": "2026-10-29"}], "complete": True},
                          notes={"items": [note("old", "My appointment is on November 4.", kind="plan")]})
    monkeypatch.setattr(host, "ready_client", lambda *args: client)
    assert "November 4" in host.session_context(tmp_path, "s2")
    client.notes = {"items": [note("new", "My appointment is on November 5.", kind="plan")]}
    refreshed = host.prompt_context(tmp_path, "s2", "Plan my day.")
    assert "November 5" in refreshed and "November 4" not in refreshed and "replaces earlier" in refreshed
    assert client.recalls == 0
    assert "November 5" not in json.dumps(host.session_state(tmp_path, "s2"))
    assert host.prompt_context(tmp_path, "s2", "Anything else?") is None
    client.notes = {"items": []}
    removed = host.prompt_context(tmp_path, "s2", "What is saved now?")
    assert "No saved notes are currently shown" in removed and "Omission does not prove a fact false" in removed


def test_a_failed_refresh_does_not_publish_an_empty_snapshot_or_forget_the_last_fingerprint(tmp_path, monkeypatch):
    client = _ReadyClient({"items": [], "complete": False}, notes={"items": [note("p", "Prefers short replies.")]})
    monkeypatch.setattr(host, "ready_client", lambda *args: client)
    host.session_context(tmp_path, "s2")
    previous = host.session_state(tmp_path, "s2")["memory_context"]["fingerprint"]
    def unavailable(*args):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(notes, "list_notes", unavailable)
    assert host.prompt_context(tmp_path, "s2", "Hello") is None
    assert host.session_state(tmp_path, "s2")["memory_context"]["fingerprint"] == previous


def test_complete_user_history_does_not_skip_a_question_about_an_assistants_recommendation(tmp_path, monkeypatch):
    client = _ReadyClient({"items": [{"text": "Suggest a bottle.", "recorded_at": "2026-10-29"}], "complete": True})
    monkeypatch.setattr(host, "ready_client", lambda *args: client)
    host.session_context(tmp_path, "s2")
    host.prompt_context(tmp_path, "s2", "Which bottle did you recommend fifth?")
    assert client.recalls == 1


def test_context_is_reloaded_for_a_different_brain_using_the_same_session_name(tmp_path, monkeypatch):
    client = _ReadyClient({"items": [{"text": "Earlier message", "recorded_at": "2026-10-29"}], "complete": True},
                          notes={"items": [note("a", "Private preference for A.")]})
    monkeypatch.setattr(host, "ready_client", lambda *args: client)
    host.session_context(tmp_path, "s2", user_id="A")
    client.brain_id = "brain-B"
    client.notes = {"items": [note("b", "Private preference for B.")]}
    context = host.prompt_context(tmp_path, "s2", "Hello", user_id="B")
    assert "preference for B" in context and "preference for A" not in context and client.recalls == 1


def test_previous_prompt_cues_do_not_cross_users_in_a_reused_session(tmp_path, monkeypatch):
    client = _ReadyClient({"items": [], "complete": False})
    cues = []
    def recall(query, **options):
        cues.append(options["additional_cues"])
        return {"result": {"items": []}}
    client.recall = recall
    monkeypatch.setattr(host, "ready_client", lambda *args: client)
    host.prompt_context(tmp_path, "s2", "Private situation for A.", user_id="A")
    client.brain_id = "brain-B"
    host.prompt_context(tmp_path, "s2", "How about today?", user_id="B")
    assert cues == [[], []]


def test_many_plans_and_long_note_metadata_stay_within_the_codex_context_limit(tmp_path, monkeypatch):
    saved = {"items": [note(f"n{index}", "Schedule " + "x" * 240, kind="plan", due="2026-01-01",
                             about="Person " + "a" * 45, aliases=["alias " + "b" * 35]) for index in range(200)]}
    client = _ReadyClient({"items": [], "complete": False}, notes=saved)
    monkeypatch.setattr(host, "ready_client", lambda *args: client)
    assert len(host.session_context(tmp_path, "s2")) <= host.SESSION_CONTEXT_CHARACTERS
    client.notes["items"].insert(0, note("changed", "User corrected this plan.", kind="plan", due="2026-01-02"))
    context = host.prompt_context(tmp_path, "s2", "My plans?", producer="membrain-codex", include_source=True)
    assert "User corrected this plan" in context and len(context) <= 6500
    assert len(notes.render_coming_up(saved, budget=1000)) <= 1000


def test_unchanged_context_and_shared_sensitive_notes_do_not_leak_through_updates():
    own = {"items": [note("story", "The dragon guards the bridge.")]}
    shared = {"items": [note("private", "Sensitive private diagnosis.", sensitive=True), note("public", "Prefers short replies.")]}
    current = host_context.snapshot(own, brain_id="story", tools=None, shared=shared)
    assert "diagnosis" not in current["text"] and "Prefers short replies" in current["text"]
    assert host_context.update(current, current["fingerprint"]) is None


@pytest.mark.parametrize("query", ["Which bottle did you recommend fifth?", "What were your previous recommendations?",
                                   "You recommended something different last time.", "你上次推荐的餐厅是哪家？", "你之前答应了什么？"])
def test_explicit_questions_about_assistant_speech_allow_attributed_speech(query):
    assert cognition.speech_requested({"intent": "explore", "query": query})


@pytest.mark.parametrize("query", ["Which bottle did my sister recommend?", "My therapist suggested walking.",
                                   "Do you know where I live?", "她上次推荐的餐厅是哪家？"])
def test_other_peoples_speech_does_not_enable_assistant_speech(query):
    assert not cognition.speech_requested({"intent": "explore", "query": query})


def test_setup_opens_a_single_use_guided_page_without_provider_configuration(tmp_path, monkeypatch, capsys):
    from membrain import local_profile, plugin

    parser = argparse.ArgumentParser()
    plugin_cli.add_commands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["plugin", "setup", "--directory", str(tmp_path), "--no-browser"])
    monkeypatch.setattr(plugin, "start", lambda directory: {"semantic_recall": "downloading", "recording": "on"})
    monkeypatch.setattr(local_profile, "load_profile", lambda directory: SimpleNamespace(base_url="http://127.0.0.1:8788"))
    plugin_cli.dispatch(args)
    output = capsys.readouterr().out
    assert "No extra model API key" in output and "/memory?code=" in output
    code = output.split("/memory?code=")[1].strip()
    assert host.consume_ui_code(tmp_path, code) and not host.consume_ui_code(tmp_path, code)


def test_model_download_failure_is_visible_and_can_be_retried_without_saving_error_content(tmp_path, monkeypatch):
    from membrain import local_embeddings

    monkeypatch.setattr(local_embeddings, "runtime_available", lambda: True)
    monkeypatch.setattr(local_embeddings, "installed", lambda directory: None)
    def fail(directory):
        raise OSError("private proxy credentials must not be written")
    monkeypatch.setattr(local_embeddings, "download", fail)
    with pytest.raises(OSError):
        host.fetch_model(tmp_path)
    assert host.semantic_state(tmp_path) == "download_failed"
    assert "credentials" not in (tmp_path / "plugin" / "model-download-error.json").read_text()
    launched = []
    monkeypatch.setattr(host, "_detached", launched.append)
    host._fetch_in_background(tmp_path)
    host._fetch_in_background(tmp_path)
    assert len(launched) == 1 and host.semantic_state(tmp_path) == "downloading"


def test_a_failed_download_launcher_does_not_leave_a_false_downloading_state(tmp_path, monkeypatch):
    from membrain import local_embeddings

    monkeypatch.setattr(local_embeddings, "runtime_available", lambda: True)
    monkeypatch.setattr(local_embeddings, "installed", lambda directory: None)
    def fail(arguments):
        raise OSError("cannot spawn")
    monkeypatch.setattr(host, "_detached", fail)
    host._fetch_in_background(tmp_path)
    assert host.semantic_state(tmp_path) == "download_failed"


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_first_use_record_new_session_correct_and_delete_against_a_real_service(tmp_path, monkeypatch, capsys):
    import re
    import socket

    from membrain import desktop, local_profile, plugin

    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")
    def port():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]
    directory = tmp_path / "first-use"
    profile = local_profile.ensure_profile(directory, database_engine="embedded", api_port=port(), database_port=port())
    try:
        parser = argparse.ArgumentParser()
        plugin_cli.add_commands(parser.add_subparsers(dest="command"))
        plugin_cli.dispatch(parser.parse_args(["plugin", "setup", "--directory", str(directory), "--no-browser"]))
        assert "single-use link" in capsys.readouterr().out and profile.cognition is None
        host.session_context(directory, "first")
        context = plugin.user_prompt({"session_id": "first", "prompt_id": "1", "prompt": "I prefer short answers."}, directory)
        sources = json.loads(re.search(r"source_messages=(\[.*?\])\.", context).group(1))
        with host.ready_client(directory) as client:
            assert client.memory_status()["recorded_messages"] == 0
            original = client.write_note("Prefers short answers.", kind="preference", source_messages=sources)
        plugin.stop({"session_id": "first", "prompt_id": "1", "last_assistant_message": "Understood."}, directory)
        with host.ready_client(directory) as client:
            assert client.memory_status()["recorded_messages"] == 2
            linked = notes.list_notes(client)["items"][0]
            assert len(linked["source_event_ids"]) == 1 and linked["pending_source_count"] == 0
        assert "Prefers short answers." in host.session_context(directory, "second")
        with host.ready_client(directory) as client:
            corrected = client.write_note("Prefers detailed answers for technical topics.", kind="preference",
                                          role="user", replaces=[original["note_id"]])
        updated = host.prompt_context(directory, "second", "How should you reply?")
        assert "detailed answers for technical topics" in updated and "Prefers short answers." not in updated
        with host.ready_client(directory) as client:
            assert notes.list_notes(client)["items"][0]["source_event_ids"] == linked["source_event_ids"]
            generation = client.dashboard()["brain"]["scope_generation"]
            client.forget([corrected["note_id"]], kind="events", mode="erase_content", expected_scope_generation=generation)
        assert "No saved notes are currently shown" in host.prompt_context(directory, "second", "What notes remain?")
    finally:
        desktop.stop(directory)
