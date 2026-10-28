import json

import pytest

from membrain import desktop, embedded_database, host, imports, plugin
from membrain.notes import render_notes


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")
    for variable, folder in [("CLAUDE_CONFIG_DIR", "claude"), ("HERMES_HOME", "hermes"),
                             ("OPENCLAW_WORKSPACE", "openclaw"), ("CODEX_HOME", "codex")]:
        monkeypatch.setenv(variable, str(tmp_path / folder))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def host_files(tmp_path):
    memory = tmp_path / "claude" / "projects" / "-home-hao-app" / "memory"
    write(memory / "MEMORY.md", "- [User role](user_role.md) — who Hao is\n")
    write(memory / "user_role.md", "---\nname: user role\ndescription: Hao is a nurse\ntype: user\n---\n"
          "Hao is a night-shift nurse in Leeds.\n\n- Mom uses a walker; avoid restaurants with stairs.\n")
    write(memory / "feedback_style.md", "---\nname: style\ntype: feedback\n---\nPrefers short answers.\n")
    write(memory / "project_api.md", "---\nname: api\ntype: project\n---\nThe API uses FastAPI.\n")
    write(tmp_path / "hermes" / "memories" / "USER.md",
          "Allergic to sesame.\n§\nSister Mei, birthday 14 November.\n§\nAPI key sk-abcdefghijklmnopqrstuvwxyz123456 for tests.")
    write(tmp_path / "hermes" / "memories" / "MEMORY.md", "Server runs Ubuntu 24.04.")
    write(tmp_path / "openclaw" / "USER.md", "# User\n\n- Call me Hao.\n- prefers short answers\n")
    write(tmp_path / "codex" / "memories" / "MEMORY.md", "- Repo uses uv and ruff.\n")


def test_only_memory_about_the_person_is_found_unless_all_is_asked(tmp_path):
    host_files(tmp_path)
    found = imports.discover()
    assert sorted((source.host, source.path.name) for source in found) == [
        ("claude-code", "feedback_style.md"), ("claude-code", "user_role.md"), ("hermes", "USER.md"),
        ("openclaw", "USER.md")]
    every = imports.discover(include_general=True)
    assert {source.path.name for source in every} >= {"project_api.md", "MEMORY.md"}
    assert all(source.path.name != "MEMORY.md" or source.host != "claude-code" for source in every)
    assert imports.discover(("hermes",)) == [source for source in found if source.host == "hermes"]
    by_name = {(source.host, source.path.name): source for source in found}
    assert imports.entries(by_name["claude-code", "user_role.md"]) == [
        "Hao is a night-shift nurse in Leeds.", "Mom uses a walker; avoid restaurants with stairs."]
    assert imports.entries(by_name["hermes", "USER.md"])[:2] == ["Allergic to sesame.", "Sister Mei, birthday 14 November."]


class Client:
    def __init__(self, notes=()):
        self.notes = [{"event_id": f"n{i}", "text": text, "actor_role": "assistant", "producer": "membrain-notes",
                       "recorded_at": "2026-10-01T00:00:00Z"} for i, text in enumerate(notes)]

    def timeline(self, **options):
        return {"complete": True, "items": list(reversed(self.notes))}

    def observe(self, text, **options):
        self.notes.append({"event_id": f"e{len(self.notes)}", "text": text, "actor_role": "assistant",
                           "producer": options["producer_ref"], "recorded_at": "2026-10-28T00:00:00Z"})
        return {"result": {"accepted_event_ids": [self.notes[-1]["event_id"]]}}


def test_entries_become_labelled_notes_once_without_secrets_or_duplicates(tmp_path):
    host_files(tmp_path)
    client = Client(notes=["allergic to sesame"])
    sources = imports.discover()
    preview = imports.import_notes(client, sources, dry_run=True)
    assert preview["imported"] == 6 and preview["duplicates"] == 2 and len(client.notes) == 1
    report = imports.import_notes(client, sources)
    assert report["imported"] == 6 and report["duplicates"] == 2
    texts = {note["text"]: note["producer"] for note in client.notes[1:]}
    assert texts["Sister Mei, birthday 14 November."] == "membrain-import:hermes"
    assert texts["Call me Hao."] == "membrain-import:openclaw"
    assert "API key [secret removed] for tests." in texts and not any("sk-abc" in text for text in texts)
    assert "prefers short answers" not in texts  # Same as Claude Code's "Prefers short answers."
    assert imports.import_notes(client, sources)["imported"] == 0  # Importing again adds nothing.
    rendered = render_notes(client.timeline(), tool="membrain_note", skip_imports_from=("hermes",))
    assert "imported from OpenClaw] Call me Hao." in rendered and "Sister Mei" not in rendered
    assert "save the new note with membrain_note" in rendered
    assert "save the new note" not in render_notes(client.timeline(), tool=None)


def test_long_entries_are_shortened_at_a_sentence():
    text = "Hao works nights at St James's hospital in Leeds. " * 8
    short = imports.shorten(text)
    assert len(short) <= 300 and short.endswith(".…")


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_imported_notes_reach_other_hosts_but_not_the_host_they_came_from(tmp_path, capsys):
    import argparse

    from membrain import plugin_cli

    host_files(tmp_path)
    directory = tmp_path / "brain"
    try:
        plugin_cli.dispatch(argparse.Namespace(plugin_command="import", directory=directory, hosts=None, all=False,
                                               dry_run=False))
        assert json.loads(capsys.readouterr().out)["imported"] == 7
        claude = plugin.session_start({"session_id": "c1"}, directory)
        assert "imported from Hermes] Sister Mei, birthday 14 November." in claude
        assert "imported from OpenClaw] Call me Hao." in claude
        openclaw = plugin.session_start({"session_id": "o1"}, directory, plugin.PRODUCERS["openclaw"])
        assert "Sister Mei" in openclaw and "Call me Hao." not in openclaw and "membrain_note" in openclaw
        with host.ready_client(directory) as client:
            items = client.timeline(conversation_id="membrain-notes", roles=("assistant",))["items"]
        assert {item["producer"] for item in items} == {"membrain-import:claude-code", "membrain-import:hermes",
                                                        "membrain-import:openclaw"}
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass
