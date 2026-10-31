import io
import json
import os

import pytest

from membrain import desktop, embedded_database, host, plugin
from membrain.local_profile import load_profile


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    # plugin start would otherwise fetch the 135 MB embedding model and restart the service mid-test.
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")


def _memory(statement, conversation="earlier", recorded="2026-10-03T08:00:00Z", world="real_world"):
    return {"memory": {"statement": statement, "scope": {"conversation_id": conversation},
                       "system_time": {"recorded_at": recorded}, "world": world}}


def _source(statement, event_id, *, conversation="earlier", methods=("keyword",), score=0.01, start=0):
    item = _memory(statement, conversation)
    item["memory"]["evidence_refs"] = [{"relationship": "supports", "event_id": event_id, "span": {"start": start}}]
    item["rank_signals"] = [{"method": method, "score": score} for method in methods]
    return item


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
    def broken(payload, directory, producer):
        raise RuntimeError("database exploded")

    monkeypatch.setitem(plugin.HANDLERS, "stop", ("Stop", broken))
    payload = json.dumps({"session_id": "s", "last_assistant_message": "hi"}).encode()
    assert plugin.run_hook("stop", tmp_path, stream=io.BytesIO(payload)) == 0
    assert capsys.readouterr().out == ""
    assert "database exploded" in (tmp_path / "plugin" / "hooks.log").read_text()


def test_messages_are_spooled_when_the_service_is_not_ready(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: None)
    monkeypatch.setattr(host, "start_in_background", started.append)
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
        host.spool(tmp_path, {"text": f"m{index}", "conversation_id": "c", "branch_id": "main",
                                 "source_event_key": f"k{index}", "role": "user", "producer_ref": "p"})
    with open(tmp_path / "plugin" / "spool.jsonl", "a") as stream:
        stream.write('{"torn": \n')

    class Client:
        brain_id = "brain"

        def __init__(self, fail_after):
            self.seen, self.fail_after = [], fail_after

        def observe(self, **record):
            if len(self.seen) == self.fail_after:
                raise ConnectionError("down")
            self.seen.append(record["text"])

    with pytest.raises(ConnectionError):
        host.flush(Client(fail_after=1), tmp_path)
    client = Client(fail_after=99)
    assert host.flush(client, tmp_path) == 4
    assert client.seen == ["m0", "m1", "m2"]
    assert (tmp_path / "plugin" / "spool.jsonl").read_text() == ""


def test_event_keys_are_stable_without_prompt_ids():
    first, second = host.message_record("s9", "user", "same text"), host.message_record("s9", "user", "same text")
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
        assert "<membrain-source>" in hook("user-prompt", {"session_id": "a", "prompt_id": "1",
                                    "prompt": "My physio says my left knee cannot handle consecutive run days."})
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


def _hook_command(event, hooks_file="claude-hooks.json"):
    from pathlib import Path

    hooks = json.loads((Path(__file__).parents[1] / "plugins" / "membrain" / "hooks" / hooks_file).read_text())
    return hooks["hooks"][event][0]["hooks"][0]["command"]


@pytest.mark.parametrize("hooks_file,data_variable", [("claude-hooks.json", "CLAUDE_PLUGIN_DATA"),
                                                      ("codex-hooks.json", "PLUGIN_DATA")])
@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop"])
@pytest.mark.parametrize("runtime_fails", [False, True])
def test_hook_commands_do_not_persist_unfiltered_input_without_the_runtime(
        tmp_path, event, hooks_file, data_variable, runtime_fails):
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
    if runtime_fails:
        launcher = tmp_path / "uvx"
        launcher.write_text("#!/bin/sh\nexit 1\n")
        launcher.chmod(0o700)
        system_path = str(tmp_path) + ":" + system_path
    environment = {"PATH": system_path, data_variable: str(tmp_path / "data")}
    result = subprocess.run([bash, "-c", _hook_command(event, hooks_file)], input=json.dumps(payload, indent=2), text=True,
                            capture_output=True, env=environment, timeout=30)
    assert result.returncode == 0 and result.stdout == ""
    assert not (tmp_path / "data" / "pending-hooks.jsonl").exists()


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
        brain_id = "brain"

        def __init__(self):
            self.records = []

        def observe(self, **record):
            self.records.append(record)

    client = Client()
    assert plugin._import_host_queue(tmp_path / "brain") == 2
    assert not list((tmp_path / "data").iterdir())
    assert host.flush(client, tmp_path / "brain") == 2
    assert [(record["role"], record["source_event_key"]) for record in client.records] == [
        ("user", "s1:p1:user"), ("assistant", "s1:p1:assistant")]
    assert plugin._import_host_queue(tmp_path / "brain") == 0


def test_history_is_rendered_oldest_first_and_states_completeness():
    response = {"complete": True, "items": [
        {"text": "The race moved to December 7.", "recorded_at": "2026-10-15T09:00:00Z"},
        {"text": "My left knee\ncannot take consecutive run days.", "recorded_at": "2026-09-14T07:00:00Z"},
    ]}
    history = plugin.render_history(response).splitlines()
    assert history[0] == "<membrain-history>" and history[-1] == "</membrain-history>"
    assert "Everything this user wrote" in history[1]
    assert history[2:4] == ["- [2026-09-14] My left knee cannot take consecutive run days.",
                            "- [2026-10-15] The race moved to December 7."]
    partial = plugin.render_history({**response, "complete": False})
    assert "Older messages are not shown" in partial
    assert plugin.render_history({"complete": True, "items": []}) is None


class _ReadyClient:
    brain_id = "brain"

    def __init__(self, timeline, recalled=None, notes=None, messages=()):
        self.timeline_response, self.recalled, self.observed, self.recalls = timeline, recalled, [], 0
        self.notes, self.messages, self.timeline_calls = notes or {"items": [], "complete": True}, list(messages), []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def timeline(self, **options):
        if options.get("conversation_id") == "membrain-notes":
            return self.notes
        if options.get("event_ids"):
            return {"items": [item for item in self.messages if item["event_id"] in options["event_ids"]]}
        assert options["exclude_conversation"] == "s2"
        self.timeline_calls.append(options)
        return self.timeline_response

    def recall(self, *args, **kwargs):
        self.recalls += 1
        return self.recalled or {"result": {"items": []}}

    def observe(self, **record):
        self.observed.append(record)


def test_complete_history_is_injected_once_and_replaces_per_message_recall(tmp_path, monkeypatch):
    client = _ReadyClient({"complete": True, "items": [{"text": "I'm vegetarian.", "recorded_at": "2026-10-03T00:00:00Z"}]})
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
    context = plugin.session_start({"session_id": "s2"}, tmp_path)
    assert "complete history from earlier sessions" in context and "- [2026-10-03] I'm vegetarian." in context
    assert len(client.timeline_calls) == 1
    assert "<membrain-source>" in plugin.user_prompt({"session_id": "s2", "prompt_id": "p", "prompt": "Plan dinner"}, tmp_path)
    assert client.recalls == 0 and client.observed == []
    plugin.stop({"session_id": "s2", "prompt_id": "p", "last_assistant_message": "Pasta?"}, tmp_path)
    assert [(record["role"], record["text"]) for record in client.observed] == [
        ("user", "Plan dinner"), ("assistant", "Pasta?")]


def test_partial_history_keeps_per_message_recall(tmp_path, monkeypatch):
    recalled = _response(_source("user user (direct_statement): The wedding is on December 14.", "e1"))
    message = {"event_id": "e1", "conversation_id": "earlier", "actor_role": "user",
               "recorded_at": "2026-10-03T08:00:00Z", "text": "Big news. The wedding is on December 14 in Porto."}
    client = _ReadyClient({"complete": False, "items": [{"text": "latest", "recorded_at": "2026-12-01T00:00:00Z"}]},
                          recalled, messages=[message])
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
    context = plugin.session_start({"session_id": "s2"}, tmp_path)
    assert "Older messages are not shown" in context and "memory_note" in context
    recall = plugin.user_prompt({"session_id": "s2", "prompt_id": "p", "prompt": "When is the wedding?"}, tmp_path)
    assert client.recalls == 1 and "- [2026-10-03 · user said] Big news. The wedding is on December 14 in Porto." in recall


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_new_sessions_receive_the_complete_history_from_the_real_service(tmp_path, capsys):
    directory = tmp_path / "brain"

    def hook(event, payload):
        assert plugin.run_hook(event, directory, stream=io.BytesIO(json.dumps(payload).encode())) == 0
        output = capsys.readouterr().out
        return json.loads(output)["hookSpecificOutput"]["additionalContext"] if output else None

    try:
        plugin.start(directory)
        hook("session-start", {"session_id": "a"})
        hook("user-prompt", {"session_id": "a", "prompt_id": "1", "prompt": "I'm vegetarian and train for a half."})
        hook("stop", {"session_id": "a", "prompt_id": "1", "last_assistant_message": "Noted."})
        hook("user-prompt", {"session_id": "a", "prompt_id": "2", "prompt": "The race moved to December 7."})
        hook("stop", {"session_id": "a", "prompt_id": "2", "last_assistant_message": "Updated."})
        context = hook("session-start", {"session_id": "b"})
        assert context.index("I'm vegetarian") < context.index("December 7") and "complete history" in context
        assert "<membrain-source>" in hook("user-prompt", {"session_id": "b", "prompt_id": "3", "prompt": "What should I eat?"})
        hook("stop", {"session_id": "b", "prompt_id": "3", "last_assistant_message": "Lentil curry."})
        assert "What should I eat?" in hook("session-start", {"session_id": "c"})
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass


@pytest.mark.parametrize("forms", [("run", "runs", "running", "ran"), ("marry", "married", "marries"),
                                   ("race", "races", "racing"), ("knee", "knees"), ("week", "weeks"),
                                   ("plan", "planned", "planning"), ("box", "boxes")])
def test_lexical_terms_fold_common_english_word_forms(forms):
    from membrain.cognition import _terms

    assert len({frozenset(_terms(form)[0]) for form in forms}) == 1
    for word in ("seed", "speed", "class", "bus", "physio", "5k", "2026"):
        assert _terms(word)[0] == {word}


def test_lexical_prefix_query_reaches_stored_word_forms():
    from membrain.lexical_candidates import _prefixes

    assert list(_prefixes(["run", "marry", "5k", "go"])) == ["'run':*", "'marry':*", "'marri':*", "'5k'", "'go'"]


def test_start_fetches_the_semantic_model_in_the_background_once(tmp_path, monkeypatch):
    from membrain import local_embeddings

    monkeypatch.setattr(local_embeddings, "runtime_available", lambda: True)
    assert plugin.semantic_state(tmp_path) == "not_downloaded"
    launched = []
    monkeypatch.setattr(host.subprocess, "Popen", lambda args, **options: launched.append(args))
    host._fetch_in_background(tmp_path)
    host._fetch_in_background(tmp_path)
    assert len(launched) == 1 and launched[0][-3:] == ["fetch-model", "--directory", str(tmp_path)]
    assert plugin.semantic_state(tmp_path) == "downloading"
    stale = host._download_marker(tmp_path)
    os.utime(stale, (0, 0))
    host._fetch_in_background(tmp_path)
    assert len(launched) == 2
    monkeypatch.setattr(local_embeddings, "runtime_available", lambda: False)
    assert plugin.semantic_state(tmp_path) == "unavailable"


def test_fetched_model_restarts_a_running_service_and_clears_the_marker(tmp_path, monkeypatch):
    from membrain import local_embeddings

    (tmp_path / "profile.json").write_text("{}")
    monkeypatch.setattr(local_embeddings, "download", lambda directory: directory)
    monkeypatch.setattr(local_embeddings, "runtime_available", lambda: True)
    monkeypatch.setattr(local_embeddings, "installed", lambda directory: directory)
    restarted = []
    monkeypatch.setattr(desktop, "status", lambda directory: {"state": "ready"})
    monkeypatch.setattr(desktop, "restart", lambda directory: restarted.append(directory))
    host._download_marker(tmp_path).touch()
    assert plugin.fetch_model(tmp_path) == {"semantic_recall": "ready", "service_restarted": True}
    assert restarted == [tmp_path] and not host._download_marker(tmp_path).exists()

    def failing(directory):
        raise local_embeddings.LocalEmbeddingError("offline")

    monkeypatch.setattr(local_embeddings, "download", failing)
    host._download_marker(tmp_path).touch()
    with pytest.raises(local_embeddings.LocalEmbeddingError):
        plugin.fetch_model(tmp_path)
    assert not host._download_marker(tmp_path).exists()


def test_hook_recall_asks_for_one_query_embedding():
    assert plugin.recall_budget()["max_embedding_calls"] == 1
    assert plugin.recall_budget()["max_context_tokens"] == plugin.RECALL_TOKENS


def test_recall_falls_back_to_keywords_when_the_query_embedding_fails():
    from membrain.client import MemoryActionError

    class Client:
        brain_id = "brain"

        def __init__(self):
            self.budgets = []

        def recall(self, query, *, budget, evidence_cutoff, additional_cues=()):
            self.budgets.append(budget["max_embedding_calls"])
            if budget["max_embedding_calls"]:
                raise MemoryActionError({"outcome": "failed", "error": {"code": "dependency_unavailable",
                                                                         "message": "Embedding model is unavailable."}})
            return {"result": {"items": []}}

    client = Client()
    assert plugin.recall(client, "what do I eat?") == {"result": {"items": []}}
    assert client.budgets == [1, 0]


def test_recalled_statements_are_shown_as_their_source_messages_oldest_first():
    messages = {
        "old": {"event_id": "old", "conversation_id": "a", "actor_role": "user", "recorded_at": "2026-03-01T00:00:00Z",
                "text": "Mom has type 2 diabetes. " + "Filler words about the trip. " * 40 + "She also uses a walker."},
        "new": {"event_id": "new", "conversation_id": "b", "actor_role": "user", "recorded_at": "2026-04-01T00:00:00Z",
                "text": "The dinner moved to Sunday, May 17."},
        "csv": {"event_id": "csv", "conversation_id": "c", "actor_role": "user", "recorded_at": "2026-04-02T00:00:00Z",
                "text": "rows"},
    }
    walker = messages["old"]["text"].index("She also uses a walker.")
    response = _response(
        _source("user user (direct_statement): The dinner moved to Sunday, May 17.", "new"),
        _source("user user (direct_statement): She also uses a walker.", "old", start=walker),
        _source("user user (direct_statement): Mom has type 2 diabetes.", "old"),
        _source("user user (direct_statement): 100253,2026-03-30,East,C0999,SKU-2204,1,149.00", "csv"),
        _source("user user (direct_statement): Today is Monday.", "assoc", methods=("association",)),
        _source("user user (direct_statement): Visible already.", "here", conversation="current"),
    )

    class Client:
        brain_id = "brain"

        def recall(self, query, **options):
            return response

        def timeline(self, **options):
            self.requested = options["event_ids"]
            return {"items": [messages[key] for key in options["event_ids"] if key in messages]}

    client = Client()
    context = plugin.recall_context(client, "what about mom?", current_conversation="current")
    assert client.requested == ["new", "old"]
    lines = context.splitlines()[2:-1]
    assert lines[0].startswith("- [2026-03-01 · user said] …") and "She also uses a walker." in lines[0]
    assert len(lines[0]) < plugin.MESSAGE_CHARACTERS + 60
    assert lines[1] == "- [2026-04-01 · user said] The dinner moved to Sunday, May 17."


def test_meaning_only_matches_are_capped_and_timeline_failure_falls_back_to_statements():
    cap = host.VECTOR_ONLY_STATEMENTS
    items = [_source(f"user user (direct_statement): Vector match number {index} here.", f"v{index}",
                     methods=("vector",), score=0.1 - index * 0.001) for index in range(cap + 3)]

    class Client:
        brain_id = "brain"

        def recall(self, query, **options):
            return _response(*items)

        def timeline(self, **options):
            raise RuntimeError("down")

    assert [event for event, _ in host._sources(_response(*items), None)] == [
        f"v{index}" for index in range(min(cap, host.MAX_MESSAGES))]
    assert "Vector match number 0 here." in plugin.recall_context(Client(), "q")


def test_pasted_blocks_are_compacted_but_short_lines_are_kept():
    text = "My app crashes on start, why?\n" + "\n".join(f"web-1 | {i:02d}:00 error=refused id={i}" for i in range(5))
    assert plugin.compact_message(text) == "My app crashes on start, why?\n[5 pasted lines omitted]"
    for kept in ("Thanks!", "还有件事。", "Budget: $24,000 ceiling.", "x = 1"):
        assert plugin.compact_message(kept) == kept


def test_session_start_shows_notes_first_and_fills_the_rest_with_compacted_history(tmp_path, monkeypatch):
    notes = {"complete": True, "items": [
        {"event_id": "n2", "recorded_at": "2026-04-02T00:00:00Z", "text": "Mom: likely shellfish allergy."},
        {"event_id": "n1", "recorded_at": "2026-03-19T00:00:00Z", "text": "Mom uses a walker; no stairs."}]}
    history = {"complete": True, "items": [
        {"text": "x" * 9000, "recorded_at": "2026-04-03T00:00:00Z"},
        {"text": "Short earlier message.", "recorded_at": "2026-03-01T00:00:00Z"}]}
    client = _ReadyClient(history, notes=notes)
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
    context = plugin.session_start({"session_id": "s2"}, tmp_path)
    assert context.index("<membrain-notes>") < context.index("<membrain-history>")
    assert context.index("Mom uses a walker; no stairs. (id: n1)") < context.index("Mom: likely shellfish allergy.")
    assert "Short earlier message." in context and "x" * 400 not in context
    assert not host.history_complete(tmp_path, "s2")


def test_follow_up_prompts_carry_the_previous_prompt_as_a_cue(tmp_path, monkeypatch):
    cues = []

    class Client(_ReadyClient):
        brain_id = "brain"

        def recall(self, query, **options):
            cues.append(options.get("additional_cues"))
            return {"result": {"items": []}}

    client = Client({"complete": False, "items": []})
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: client)
    plugin.session_start({"session_id": "s2"}, tmp_path)
    plugin.user_prompt({"session_id": "s2", "prompt_id": "1", "prompt": "Mom fell and we're going to urgent care."}, tmp_path)
    plugin.user_prompt({"session_id": "s2", "prompt_id": "2", "prompt": "What do I tell the front desk?"}, tmp_path)
    assert cues == [[], ["Mom fell and we're going to urgent care."]]


def test_session_context_stays_within_the_host_limit(tmp_path, monkeypatch):
    notes = {"complete": True, "items": [{"event_id": f"n{index}", "recorded_at": "2026-04-02T00:00:00Z",
                                          "text": "Durable fact " + "y" * 250} for index in range(13)]}
    history = {"complete": True, "items": [{"text": f"Message {index} " + "z" * 280,
                                            "recorded_at": "2026-04-03T00:00:00Z"} for index in range(80)]}
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: _ReadyClient(history, notes=notes))
    context = plugin.session_start({"session_id": "s2"}, tmp_path)
    assert "<membrain-history>" in context and len(context) <= host.SESSION_CONTEXT_CHARACTERS


def test_codex_hooks_use_turn_ids_and_their_own_producer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: None)
    monkeypatch.setattr(host, "start_in_background", lambda directory: None)
    for event, payload in [("user-prompt", {"prompt": "Mei is my sister."}),
                           ("stop", {"last_assistant_message": "Noted.", "stop_hook_active": False})]:
        raw = json.dumps({"session_id": "019a-codex", "turn_id": "turn-7", "hook_event_name": "x", "model": "m",
                          **payload}).encode()
        assert plugin.run_hook(event, tmp_path, stream=io.BytesIO(raw), host_name="codex") == 0
    assert capsys.readouterr().out == ""
    records = [json.loads(line) for line in (tmp_path / "plugin" / "spool.jsonl").read_text().splitlines()]
    assert [(record["source_event_key"], record["producer_ref"]) for record in records] == [
        ("019a-codex:turn-7:user", "membrain-codex"), ("019a-codex:turn-7:assistant", "membrain-codex")]


def test_codex_and_claude_packages_describe_the_same_plugin():
    from pathlib import Path

    root = Path(__file__).parents[1]
    package = root / "plugins" / "membrain"
    claude = json.loads((package / ".claude-plugin" / "plugin.json").read_text())
    codex = json.loads((package / ".codex-plugin" / "plugin.json").read_text())
    assert codex["name"] == claude["name"] and codex["version"] == claude["version"]
    for field in ("skills", "mcpServers", "hooks"):
        assert codex[field].startswith("./") and (package / codex[field]).exists()
    hooks = json.loads((package / codex["hooks"]).read_text())["hooks"]
    commands = [hook for groups in hooks.values() for group in groups for hook in group["hooks"]]
    assert all("--host codex" in hook["command"] for hook in commands if "plugin hook" in hook["command"])
    assert all(hook.get("additionalContextLimit", 0) >= 7000 for hook in hooks["SessionStart"][1]["hooks"]
               + hooks["UserPromptSubmit"][0]["hooks"])
    server = json.loads((package / codex["mcpServers"]).read_text())["mcpServers"]["membrain"]
    assert "${" not in json.dumps(server)  # Codex does not expand shell defaults in MCP arguments.
    market = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text())
    assert market["plugins"][0]["source"]["path"] == "./plugins/membrain"


def test_final_reply_is_read_from_the_transcript_when_the_stop_hook_lacks_it(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("\n".join(json.dumps(entry) for entry in [
        {"type": "user", "message": {"role": "user", "content": "Earlier question"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Old reply"}]}},
        {"type": "user", "message": {"role": "user", "content": "Plan Mei's birthday dinner"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Sesame-free menu:"}, {"type": "tool_use", "name": "x"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "..."}]}},
    ]) + "\n{torn")
    assert plugin.last_assistant_message(str(transcript)) == "Sesame-free menu:"
    plain = tmp_path / "plain.jsonl"
    plain.write_text(json.dumps({"role": "assistant", "content": "Hi!"}) + "\n" + json.dumps({"role": "user", "content": "Next"}))
    assert plugin.last_assistant_message(str(plain)) is None  # The newest prompt has no reply yet.
    assert plugin.last_assistant_message(str(tmp_path / "missing.jsonl")) is None
    monkeypatch.setattr(host, "ready_client", lambda directory, user_id=None: None)
    monkeypatch.setattr(host, "start_in_background", lambda directory: None)
    plugin.user_prompt({"session_id": "wb", "prompt": "Plan Mei's birthday dinner"}, tmp_path, "membrain-workbuddy")
    plugin.stop({"session_id": "wb", "transcript_path": str(transcript), "stop_hook_active": False}, tmp_path,
                "membrain-workbuddy")
    records = [json.loads(line) for line in (tmp_path / "plugin" / "spool.jsonl").read_text().splitlines()]
    assert [(record["role"], record["text"]) for record in records] == [
        ("user", "Plan Mei's birthday dinner"), ("assistant", "Sesame-free menu:")]


def test_every_host_manifest_points_only_at_its_own_files():
    from pathlib import Path

    package = Path(__file__).parents[1] / "plugins" / "membrain"
    assert not (package / "hooks" / "hooks.json").exists() and not (package / ".mcp.json").exists()
    hosts = {".claude-plugin": "claude-code", ".codex-plugin": "codex", ".codebuddy-plugin": None}
    seen = set()
    for folder, host_name in hosts.items():
        manifest = json.loads((package / folder / "plugin.json").read_text())
        hooks_file, mcp_file = manifest["hooks"], manifest["mcpServers"]
        assert (package / hooks_file).is_file() and (package / mcp_file).is_file()
        assert (hooks_file, mcp_file) not in seen
        seen.add((hooks_file, mcp_file))
        text = (package / hooks_file).read_text()
        if host_name == "codex":
            assert "--host codex" in text
        if folder == ".codebuddy-plugin":
            assert "${CODEBUDDY_PLUGIN_ROOT}/bin/membrain-hook" in text and '"async"' not in text
            assert "${" not in (package / mcp_file).read_text()
    launcher = (package / "bin" / "membrain-hook").read_text()
    assert "--host workbuddy" in launcher and "--host workbuddy" in (package / "bin" / "membrain-hook.cmd").read_text()
    assert os.access(package / "bin" / "membrain-hook", os.X_OK)
    market = json.loads((package.parents[1] / ".codebuddy-plugin" / "marketplace.json").read_text())
    assert market["plugins"][0]["source"]["subdir"] == "plugins/membrain"


def test_workbuddy_launcher_does_not_persist_unfiltered_input_without_the_runtime(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path

    bash = shutil.which("bash")
    if bash is None or shutil.which("uvx", path="/usr/bin:/bin"):
        pytest.skip("needs bash and no system-wide uvx")
    launcher = Path(__file__).parents[1] / "plugins" / "membrain" / "bin" / "membrain-hook"
    payload = {"session_id": "s1", "prompt": "Mei turns 30.\nShe hates sesame.", "hook_event_name": "UserPromptSubmit"}
    environment = {"PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")}
    for event in ("start", "session-start", "user-prompt"):
        result = subprocess.run([bash, str(launcher), event], input=json.dumps(payload, indent=2), text=True,
                                capture_output=True, env=environment, timeout=30)
        assert result.returncode == 0 and result.stdout == ""
    assert not (tmp_path / "data" / "pending-hooks.jsonl").exists()


def test_recall_shows_the_answer_that_followed_a_recalled_question():
    question = _source("user user (inquiry): Any fun plans for the summer?", "q1", methods=("keyword",))
    thread = [  # Newest first, as the timeline returns them.
        {"event_id": "a2", "conversation_id": "c1", "actor_role": "user", "text": "Lovely!",
         "recorded_at": "2026-05-25T10:03:00Z"},
        {"event_id": "a1", "conversation_id": "c1", "actor_role": "user",
         "text": "Researching adoption agencies, it has been a dream.", "recorded_at": "2026-05-25T10:02:00Z"},
        {"event_id": "q1", "conversation_id": "c1", "actor_role": "user", "text": "Any fun plans for the summer?",
         "recorded_at": "2026-05-25T10:01:00Z"},
    ]

    class Client:
        brain_id = "brain"

        def recall(self, query, **options):
            return _response(question)

        def timeline(self, **options):
            if options.get("event_ids"):
                return {"items": [item for item in thread if item["event_id"] in options["event_ids"]]}
            assert options["conversation_id"] == "c1"
            return {"items": thread}

    context = plugin.recall_context(Client(), "What are Caroline's plans for the summer?")
    lines = context.splitlines()
    assert any("Any fun plans for the summer?" in line for line in lines)
    assert any("Researching adoption agencies" in line for line in lines) and "Lovely!" not in context
    assert [line for line in lines if line.startswith("- ")][0].endswith("Any fun plans for the summer?")


def test_recall_budget_keeps_the_highest_ranked_update_before_chronological_rendering():
    sources = [_source(f"user user (direct_statement): {'Latest corrected appointment' if index == 0 else 'Older appointment details'} "
                       + "relevant " * 80, f"e{index}", methods=("keyword",)) for index in range(12)]

    class Client:
        def recall(self, *args, **kwargs):
            return _response(*sources)

        def timeline(self, **options):
            if options.get("conversation_id"):
                return {"items": []}
            return {"items": [{"event_id": f"e{index}", "conversation_id": f"c{index}", "actor_role": "user",
                               "recorded_at": f"2026-01-{30-index:02d}T09:00:00Z",
                               "text": ("Latest corrected appointment" if index == 0 else "Older appointment details")
                                       + " relevant" * 90} for index in range(12)]}

    context = host.recall_context(Client(), "appointment", limit=1800)
    assert "Latest corrected appointment" in context and len(context) <= 1800


def test_agent_note_tool_cannot_write_while_recording_is_paused(tmp_path, monkeypatch, capsys):
    host.pause(tmp_path)
    def unexpected(*args):
        raise AssertionError('A paused note must not open a client or store content')
    monkeypatch.setattr(host, 'ready_client', unexpected)
    assert plugin.run_tool('note', tmp_path, stream=io.BytesIO(b'{"text":"confidential appointment"}')) == 0
    assert 'paused' in json.loads(capsys.readouterr().out)['error']
    assert not (tmp_path / 'plugin' / 'spool.jsonl').exists()
