import hashlib
import importlib.util
import json
import multiprocessing
import os
import stat
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "examples" / "longitudinal_journal.py"


def load_module():
    spec = importlib.util.spec_from_file_location("longitudinal_journal", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def journal_module():
    return load_module()


def competing_process(directory, plan, connection):
    try:
        with load_module().CampaignJournal(directory, plan) as journal:
            journal.append("session_completed", {"worker": "child"})
        connection.send("appended")
    except RuntimeError as error:
        connection.send(str(error))
    finally:
        connection.close()


def run_competitor(directory, plan):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=competing_process, args=(directory, plan, sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(10), "Competing journal process did not complete"
        result = receiver.recv()
        process.join(10)
        assert process.exitcode == 0
        return result
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(10)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode()


def test_journal_copies_inputs_and_returns_detached_events(journal_module, tmp_path):
    directory = tmp_path / "campaign"
    plan = {"brain": "private", "calls": 12, "sessions": [{"name": "first"}]}
    journal = journal_module.CampaignJournal(directory, plan)
    frozen = json.loads(canonical(plan))
    plan["sessions"][0]["name"] = "changed"
    with journal:
        journal.plan["sessions"][0]["name"] = "changed view"
        assert journal.plan == frozen
        journal.assert_resumable()
        payload = {"session": "first", "answer": {"text": "一个真实的回答"}}
        event = journal.append("session_completed", payload)
        payload["answer"]["text"] = "changed input"
        event["payload"]["answer"]["text"] = "changed return"
        journal.events[0]["payload"]["answer"]["text"] = "changed view"
        assert journal.events[0]["payload"]["answer"]["text"] == "一个真实的回答"
        journal.assert_resumable()
    before = (directory / "journal.jsonl").read_bytes()
    with journal_module.CampaignJournal(directory, frozen) as restored:
        assert restored.events == journal.events
        restored.append("paused", {"next_session": "second"})
        restored.assert_resumable()
    assert (directory / "journal.jsonl").read_bytes().startswith(before)
    assert json.loads((directory / "plan.json").read_bytes()) == frozen
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((directory / name).stat().st_mode) == 0o600
               for name in ("plan.json", "journal.jsonl", "head.json", ".lock"))


def test_chain_binds_sequence_payload_and_frozen_plan(journal_module, tmp_path):
    plan = {"ceiling": 10}
    with journal_module.CampaignJournal(tmp_path, plan) as journal:
        journal.append("session_started", {"number": 1})
        journal.append("session_completed", {"number": 1})
        events = journal.events
    expected_previous = hashlib.sha256(canonical(plan)).hexdigest()
    for sequence, event in enumerate(events, 1):
        assert event["sequence"] == sequence
        assert event["previous_sha256"] == expected_previous
        assert event["plan_sha256"] == hashlib.sha256(canonical(plan)).hexdigest()
        body = {key: value for key, value in event.items() if key != "sha256"}
        assert event["sha256"] == hashlib.sha256(canonical(body)).hexdigest()
        expected_previous = event["sha256"]


def test_other_process_cannot_append_until_context_releases_lock(journal_module, tmp_path):
    plan = {"campaign": "one"}
    with journal_module.CampaignJournal(tmp_path, plan) as journal:
        journal.append("paused", {})
        assert "locked by another process" in run_competitor(tmp_path, plan)
        assert len(journal.events) == 1
    assert run_competitor(tmp_path, plan) == "appended"
    with journal_module.CampaignJournal(tmp_path, plan) as journal:
        assert [event["kind"] for event in journal.events] == ["paused", "session_completed"]


@pytest.mark.parametrize("kind", ["actor_started", "turn_started", "learning_started",
                                 "turn_completed", "stopped", "completed"])
def test_only_safe_session_boundaries_resume(journal_module, tmp_path, kind):
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        journal.append(kind, {})
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        with pytest.raises(RuntimeError, match="no safe resumable"):
            journal.assert_resumable()
        if kind in {"stopped", "completed"}:
            with pytest.raises(RuntimeError, match="terminal"):
                journal.append("paused", {})


def test_plan_changes_are_refused_without_overwriting(journal_module, tmp_path):
    with journal_module.CampaignJournal(tmp_path, {"ceiling": 2}) as journal:
        journal.append("paused", {})
    before = {name: (tmp_path / name).read_bytes()
              for name in ("plan.json", "journal.jsonl", "head.json")}
    with pytest.raises(ValueError, match="frozen plan"):
        with journal_module.CampaignJournal(tmp_path, {"ceiling": 3}):
            pytest.fail("Changed plan was accepted")
    assert before == {name: (tmp_path / name).read_bytes() for name in before}
    with journal_module.CampaignJournal(tmp_path, {"ceiling": 2}) as journal:
        journal.assert_resumable()


@pytest.mark.parametrize("damage", ["partial_line", "removed_tail", "empty", "payload",
                                   "reorder", "duplicate_line", "blank_line", "duplicate_key"])
def test_corrupt_or_truncated_journals_fail_closed(journal_module, tmp_path, damage):
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        journal.append("session_completed", {"session": "one"})
        journal.append("paused", {"next": "two"})
    path = tmp_path / "journal.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if damage == "partial_line":
        raw = b"".join(lines)[:-5]
    elif damage == "removed_tail":
        raw = lines[0]
    elif damage == "empty":
        raw = b""
    elif damage == "payload":
        raw = b"".join(lines).replace(b'"one"', b'"other"')
    elif damage == "reorder":
        raw = b"".join(reversed(lines))
    elif damage == "duplicate_line":
        raw = b"".join(lines + [lines[-1]])
    elif damage == "blank_line":
        raw = lines[0] + b"\n" + lines[1]
    else:
        raw = b"".join(lines).replace(b'"sequence":1', b'"sequence":1,"sequence":1')
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        with journal_module.CampaignJournal(tmp_path, {}):
            pytest.fail("Damaged history was accepted")
    assert path.read_bytes() == raw


@pytest.mark.parametrize("missing", ["plan.json", "journal.jsonl", "head.json"])
def test_incomplete_initialization_is_not_recreated(journal_module, tmp_path, missing):
    with journal_module.CampaignJournal(tmp_path, {}):
        pass
    (tmp_path / missing).unlink()
    with pytest.raises(ValueError, match="incomplete"):
        with journal_module.CampaignJournal(tmp_path, {}):
            pytest.fail("Missing committed artifact was recreated")
    assert not (tmp_path / missing).exists()


@pytest.mark.parametrize("name", ["plan.json", "journal.jsonl", "head.json", ".lock"])
def test_symlinked_campaign_files_are_refused(journal_module, tmp_path, name):
    directory = tmp_path / "campaign"
    with journal_module.CampaignJournal(directory, {}):
        pass
    target = tmp_path / "external"
    source = directory / name
    source.rename(target)
    source.symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(OSError):
        with journal_module.CampaignJournal(directory, {}):
            pytest.fail("Symlink was followed")
    assert target.read_bytes() == before


@pytest.mark.parametrize("ancestor", [False, True])
def test_symlinked_directory_components_are_refused(journal_module, tmp_path, ancestor):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    directory = link / "campaign" if ancestor else link
    with pytest.raises(OSError):
        with journal_module.CampaignJournal(directory, {}):
            pytest.fail("Directory symlink was followed")
    assert not list(target.iterdir())


def test_hard_linked_file_is_refused(journal_module, tmp_path):
    directory = tmp_path / "campaign"
    with journal_module.CampaignJournal(directory, {}):
        pass
    os.link(directory / "journal.jsonl", tmp_path / "external")
    with pytest.raises(ValueError, match="without hard links"):
        with journal_module.CampaignJournal(directory, {}):
            pytest.fail("Hard-linked ledger was accepted")


def test_partial_write_poisoned_context_cannot_continue(journal_module, tmp_path, monkeypatch):
    original_write = journal_module.os.write

    def partial_write(descriptor, raw):
        if b'"kind":"session_started"' in bytes(raw):
            original_write(descriptor, raw[:12])
            raise OSError("injected interrupted write")
        return original_write(descriptor, raw)

    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        monkeypatch.setattr(journal_module.os, "write", partial_write)
        with pytest.raises(OSError, match="injected interrupted"):
            journal.append("session_started", {})
        with pytest.raises(RuntimeError, match="healthy open"):
            journal.append("paused", {})
    with pytest.raises(ValueError, match="truncated"):
        with journal_module.CampaignJournal(tmp_path, {}):
            pytest.fail("Partial event was recovered automatically")


def test_committed_head_failure_requires_diagnosis(journal_module, tmp_path, monkeypatch):
    def fail_replace(*args, **kwargs):
        raise OSError("injected head replacement failure")

    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        monkeypatch.setattr(journal_module.os, "replace", fail_replace)
        with pytest.raises(OSError, match="head replacement failure"):
            journal.append("session_completed", {})
        assert journal.events == []
        with pytest.raises(RuntimeError, match="healthy open"):
            journal.assert_resumable()
    assert (tmp_path / "journal.jsonl").read_bytes()
    with pytest.raises(ValueError, match="committed head"):
        with journal_module.CampaignJournal(tmp_path, {}):
            pytest.fail("Uncommitted head was repaired automatically")


def test_append_fsyncs_event_and_head_before_committing_directory(journal_module, tmp_path, monkeypatch):
    calls = []
    original_write, original_fsync, original_replace = os.write, os.fsync, os.replace

    def record_write(descriptor, raw):
        calls.append(("write", descriptor))
        return original_write(descriptor, raw)

    def record_fsync(descriptor):
        calls.append(("fsync", descriptor))
        return original_fsync(descriptor)

    def record_replace(*args, **kwargs):
        calls.append(("replace", None))
        return original_replace(*args, **kwargs)

    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        monkeypatch.setattr(journal_module.os, "write", record_write)
        monkeypatch.setattr(journal_module.os, "fsync", record_fsync)
        monkeypatch.setattr(journal_module.os, "replace", record_replace)
        journal.append("paused", {})
    assert [kind for kind, _ in calls] == ["write", "fsync", "write", "fsync", "replace", "fsync"]
    assert calls[0][1] == calls[1][1]
    assert calls[2][1] == calls[3][1]


@pytest.mark.parametrize("payload", [{"number": float("nan")}, {1: "non-string key"},
                                    {"tuple": (1, 2)}, {"value": object()}])
def test_non_json_payload_does_not_modify_history(journal_module, tmp_path, payload):
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        with pytest.raises(ValueError):
            journal.append("paused", payload)
        assert journal.events == []
        journal.assert_resumable()
        assert (tmp_path / "journal.jsonl").read_bytes() == b""


def test_mutations_require_active_context_and_unchanged_lock(journal_module, tmp_path):
    journal = journal_module.CampaignJournal(tmp_path, {})
    with pytest.raises(RuntimeError, match="open context"):
        journal.append("paused", {})
    with journal:
        with pytest.raises(RuntimeError, match="already open"):
            journal.__enter__()
        (tmp_path / ".lock").unlink()
        (tmp_path / ".lock").write_bytes(b"")
        with pytest.raises(RuntimeError, match="lock file was replaced"):
            journal.append("paused", {})
    with pytest.raises(RuntimeError, match="open context"):
        journal.assert_resumable()


def inherited_context_process(journal, connection):
    try:
        journal.append("paused", {"worker": "inherited"})
    except RuntimeError as error:
        connection.send(str(error))
    finally:
        connection.close()


def test_forked_process_cannot_use_inherited_locked_context(journal_module, tmp_path):
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        process = context.Process(target=inherited_context_process, args=(journal, sender))
        process.start()
        sender.close()
        try:
            assert receiver.poll(10), "Inherited journal process did not complete"
            assert "another process" in receiver.recv()
            process.join(10)
            assert process.exitcode == 0
        finally:
            receiver.close()
            if process.is_alive():
                process.terminate()
                process.join(10)
        assert journal.events == []
        journal.append("paused", {"worker": "parent"})


def test_successful_short_writes_preserve_complete_events(journal_module, tmp_path, monkeypatch):
    original_write = journal_module.os.write
    writes = []

    def short_write(descriptor, raw):
        writes.append(len(raw))
        return original_write(descriptor, raw[:7])

    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        monkeypatch.setattr(journal_module.os, "write", short_write)
        journal.append("paused", {"answer": "A complete message across short writes."})
    assert len(writes) > 2
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        journal.assert_resumable()
        assert journal.events[0]["payload"]["answer"] == "A complete message across short writes."


def test_resume_rechecks_disk_before_authorizing_more_work(journal_module, tmp_path):
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        journal.append("paused", {})
        (tmp_path / "journal.jsonl").write_bytes(b"")
        with pytest.raises(ValueError, match="changed while locked"):
            journal.assert_resumable()


def test_append_checks_small_head_without_reloading_prior_payloads(journal_module, tmp_path, monkeypatch):
    with journal_module.CampaignJournal(tmp_path, {}) as journal:
        journal.append("session_completed", {"response": "x" * 100_000})
        read_names = []
        original_read = journal._read

        def record_read(name):
            read_names.append(name)
            return original_read(name)

        monkeypatch.setattr(journal, "_read", record_read)
        journal.append("paused", {})
        assert read_names == ["head.json"]
        read_names.clear()
        journal.assert_resumable()
        assert "plan.json" in read_names and "journal.jsonl" in read_names


@pytest.mark.parametrize("damage", ["same_size", "restored_mtime", "replace", "truncate",
                                   "plan", "head"])
def test_append_rejects_out_of_band_changes_while_locked(journal_module, tmp_path, damage):
    with journal_module.CampaignJournal(tmp_path, {"ceiling": 10}) as journal:
        journal.append("paused", {"value": "old"})
        target = tmp_path / ("plan.json" if damage == "plan" else
                             "head.json" if damage == "head" else "journal.jsonl")
        before = target.stat()
        raw = target.read_bytes()
        if damage == "replace":
            target.unlink()
            target.write_bytes(raw)
            target.chmod(0o600)
        elif damage == "truncate":
            target.write_bytes(b"")
        else:
            if damage == "plan":
                changed = raw.replace(b'"ceiling":10', b'"ceiling":11')
            elif damage == "head":
                changed = raw.replace(b'"event_count":1', b'"event_count":2')
            else:
                changed = raw.replace(b'"old"', b'"new"')
            assert changed != raw and len(changed) == len(raw)
            target.write_bytes(changed)
            if damage == "restored_mtime":
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        with pytest.raises(ValueError, match="changed while locked"):
            journal.append("session_started", {})
        assert len(journal.events) == 1
