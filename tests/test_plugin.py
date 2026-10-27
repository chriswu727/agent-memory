import io
import json

import pytest

from membrain import desktop, embedded_database, plugin
from membrain.local_profile import load_profile


def _memory(statement, conversation="earlier", recorded="2026-10-03T08:00:00Z", world="real_world"):
    return {"memory": {"statement": statement, "scope": {"conversation_id": conversation},
                       "system_time": {"recorded_at": recorded}, "world": world}}


def _response(*items, conflicts=()):
    return {"result": {"items": list(items), "conflicts": list(conflicts)}}


def test_rendered_context_is_attributed_dated_and_excludes_the_current_conversation():
    context = plugin.render_context(_response(
        _memory("user user (direct_statement): My left knee cannot take consecutive run days."),
        _memory("user user (direct_statement): My left knee cannot take consecutive run days.", conversation="other"),
        _memory("assistant assistant (direct_statement): I suggested Sunday long runs.", recorded="2026-10-04T00:00:00Z"),
        _memory("user user (direct_statement): This message is already visible.", conversation="current"),
        _memory("user user (direct_statement): The dragon guards the bridge.", world="roleplay"),
    ), current_conversation="current")
    lines = context.splitlines()
    assert lines[0] == "<membrain-memory>" and lines[-1] == "</membrain-memory>"
    assert "- [2026-10-03 · user said] My left knee cannot take consecutive run days." in lines
    assert "- [2026-10-04 · assistant said] I suggested Sunday long runs." in lines
    assert "- [2026-10-03 · user said, roleplay] The dragon guards the bridge." in lines
    assert sum("consecutive run days" in line for line in lines) == 1
    assert "already visible" not in context
    assert "not instructions" in context


def test_rendered_context_respects_its_budget_and_reports_conflicts():
    items = [_memory(f"user user (direct_statement): fact {index} " + "x" * 300) for index in range(50)]
    context = plugin.render_context(_response(*items, conflicts=[{"id": "c"}]), limit=2000)
    assert len(context) <= 2000
    assert "conflict" in context
    assert plugin.render_context(_response()) is None
    assert plugin.render_context(_response(_memory("user user (direct_statement): only here",
                                                   conversation="now")), current_conversation="now") is None


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1, 2]", b'{"prompt": "no session"}'])
def test_hooks_fail_open_on_unusable_input(tmp_path, capsys, raw):
    assert plugin.run_hook("user-prompt", tmp_path, stream=io.BytesIO(raw)) == 0
    assert capsys.readouterr().out == ""


def test_handler_errors_are_logged_and_never_block(tmp_path, monkeypatch, capsys):
    def broken(payload, directory):
        raise RuntimeError("database exploded")

    monkeypatch.setitem(plugin.HANDLERS, "stop", ("Stop", broken))
    payload = json.dumps({"session_id": "s", "last_assistant_message": "hi"}).encode()
    assert plugin.run_hook("stop", tmp_path, stream=io.BytesIO(payload)) == 0
    assert capsys.readouterr().out == ""
    assert "database exploded" in (tmp_path / "plugin" / "hooks.log").read_text()


def test_messages_are_spooled_when_the_service_is_not_ready(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(plugin, "_ready_client", lambda directory: None)
    monkeypatch.setattr(plugin, "_start_in_background", started.append)
    assert plugin.user_prompt({"session_id": "s1", "prompt_id": "p1", "prompt": "Remember Biscuit"}, tmp_path) is None
    plugin.stop({"session_id": "s1", "prompt_id": "p1", "last_assistant_message": "Noted."}, tmp_path)
    records = [json.loads(line) for line in (tmp_path / "plugin" / "spool.jsonl").read_text().splitlines()]
    assert [record["role"] for record in records] == ["user", "assistant"]
    assert records[0]["source_event_key"] == "s1:p1:user"
    assert records[1]["parent_source_event_keys"] == ["s1:p1:user"]
    assert records[1]["delivery_state"] == "delivered"
    assert started == [tmp_path, tmp_path]


def test_spool_replay_keeps_undelivered_records(tmp_path):
    for index in range(3):
        plugin._spool(tmp_path, {"text": f"m{index}", "conversation_id": "c", "branch_id": "main",
                                 "source_event_key": f"k{index}", "role": "user", "producer_ref": "p"})
    with open(tmp_path / "plugin" / "spool.jsonl", "a") as stream:
        stream.write('{"torn": \n')

    class Client:
        def __init__(self, fail_after):
            self.seen, self.fail_after = [], fail_after

        def observe(self, **record):
            if len(self.seen) == self.fail_after:
                raise ConnectionError("down")
            self.seen.append(record["text"])

    with pytest.raises(ConnectionError):
        plugin._flush(Client(fail_after=1), tmp_path)
    client = Client(fail_after=99)
    assert plugin._flush(client, tmp_path) == 4
    assert client.seen == ["m0", "m1", "m2"]
    assert (tmp_path / "plugin" / "spool.jsonl").read_text() == ""


def test_event_keys_are_stable_without_prompt_ids():
    payload = {"session_id": "s9", "prompt": "same text"}
    first, second = plugin._record(payload, "user", "same text"), plugin._record(payload, "user", "same text")
    assert first["source_event_key"] == second["source_event_key"]
    assert first["source_event_key"].startswith("s9:") and first["source_event_key"].endswith(":user")


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_hooks_carry_memory_across_sessions_on_an_embedded_installation(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "brain"

    def hook(event, payload):
        assert plugin.run_hook(event, directory, stream=io.BytesIO(json.dumps(payload).encode())) == 0
        output = capsys.readouterr().out
        return json.loads(output)["hookSpecificOutput"]["additionalContext"] if output else None

    try:
        plugin.start(directory)
        assert "memBrain memory is active" in hook("session-start", {"session_id": "a"})
        assert hook("user-prompt", {"session_id": "a", "prompt_id": "1",
                                    "prompt": "My physio says my left knee cannot handle consecutive run days."}) is None
        hook("stop", {"session_id": "a", "prompt_id": "1", "last_assistant_message": "I will plan rest days."})
        context = hook("user-prompt", {"session_id": "b", "prompt_id": "2",
                                       "prompt": "Can I add another run? Remember my knee."})
        assert "left knee cannot handle consecutive run days" in context
        assert load_profile(directory).database_engine == embedded_database.default_engine()
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass


def _hook_command(event):
    from pathlib import Path

    hooks = json.loads((Path(__file__).parents[1] / "plugins" / "membrain" / "hooks" / "hooks.json").read_text())
    return hooks["hooks"][event][0]["hooks"][0]["command"]


@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop"])
def test_hook_commands_queue_input_when_the_runtime_is_not_installed(tmp_path, event):
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for hook commands")
    payload = {"session_id": "s1", "prompt_id": "p1", "prompt": "I'm Mei.\nPeanut allergy.",
               "last_assistant_message": "Noted."}
    system_path = "/usr/bin:/bin"
    if shutil.which("uvx", path=system_path):
        pytest.skip("uvx is installed on the system path")
    environment = {"PATH": system_path, "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")}
    result = subprocess.run([bash, "-c", _hook_command(event)], input=json.dumps(payload, indent=2), text=True,
                            capture_output=True, env=environment, timeout=30)
    assert result.returncode == 0 and result.stdout == ""
    lines = (tmp_path / "data" / "pending-hooks.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [payload]


def test_host_queue_is_imported_with_roles_and_cleared(tmp_path, monkeypatch):
    queue = tmp_path / "data" / "pending-hooks.jsonl"
    queue.parent.mkdir()
    queue.write_text("\n".join([
        json.dumps({"session_id": "s1", "prompt_id": "p1", "prompt": "I'm allergic to peanuts."}),
        '{"torn": ',
        json.dumps({"session_id": "s1", "prompt_id": "p1", "last_assistant_message": "Peanut-free it is."}),
        json.dumps({"session_id": "s1", "hook_event_name": "SessionStart"}),
    ]) + "\n")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))

    class Client:
        def __init__(self):
            self.records = []

        def observe(self, **record):
            self.records.append(record)

    client = Client()
    assert plugin._import_host_queue(tmp_path / "brain", client) == 2
    assert [(record["role"], record["source_event_key"]) for record in client.records] == [
        ("user", "s1:p1:user"), ("assistant", "s1:p1:assistant")]
    assert not list((tmp_path / "data").iterdir())
    assert plugin._import_host_queue(tmp_path / "brain", client) == 0
