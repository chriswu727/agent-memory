import json

import httpx
import pytest
from test_desktop_chat import arguments
from test_desktop_chat_monitor import connect
from test_desktop_learning import install_client
from test_learning_phase_monitor import encoding, history
from test_learning_progress import summarize

from agent_memory import desktop_chat_monitor, desktop_cli


def phase_job(state="deferred", *, phase="encoding", resolved=True):
    return {"id": "encoding", "kind": "consolidate", "state": "completed", "learning_summary": {
        "learning_contract_version": "encoding-integration-v1", "learning_phase": phase,
        "phase_metadata_complete": True, "history_integration_resolved": resolved,
        "history_integration_state": state, "history_integration_job_id": "history",
        "history_integration_current_job_id": "current", "learning_window": {"processing_succeeded": True},
        "history_integration_reason": "history_integration_deferred",
        "history_integration_category": "package_incomplete",
    }}


def test_exact_learning_status_emits_phase_notice_without_extra_reads_or_changing_json(monkeypatch, capsys):
    calls = []
    result = phase_job()

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=result)

    install_client(monkeypatch, handle)
    desktop_cli.dispatch(arguments("learning-status", "--job", "encoding"))
    output = capsys.readouterr()
    assert json.loads(output.out) == result
    assert output.err.strip() == "Local encoding was assessed; historical integration is deferred (package_incomplete)."
    assert len(calls) == 1 and calls[0].method == "GET"


def test_chat_startup_reports_deferred_history_separately_without_per_turn_scans(monkeypatch, capsys):
    progress = summarize(encoding(), history())

    def handle(request):
        return httpx.Response(200, json=progress if request.url.path.endswith("learning-progress")
                              else {"orientation_job_id": None})

    _, requests = connect(monkeypatch, handle)
    with desktop_chat_monitor.learning_monitor(None, enabled=True) as monitor:
        monitor.completed("one")
        monitor.completed("two")
    output = capsys.readouterr()
    assert "1 deferred obligation(s)" in output.err
    assert "separate outcomes" in output.err and "private-" not in output.err
    assert sum(request.url.path.endswith("learning-progress") for request in requests) == 1
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize("state,phrase", [
    ("queued", "is queued"), ("running", "is running"), ("completed", "bounded comparison"),
    ("failed", "is blocked"), ("cancelled", "is blocked"), ("superseded", "is blocked"),
])
def test_exact_notice_uses_current_leaf_state_without_promising_new_memories(state, phrase):
    notice = desktop_chat_monitor.phase_notice(phase_job(state))
    assert phrase in notice
    if state == "completed":
        assert "New publication is a separate outcome" in notice


def test_history_job_notice_does_not_relabel_it_as_local_encoding():
    notice = desktop_chat_monitor.phase_notice(phase_job(phase="history_integration"))
    assert notice == "Historical integration is deferred (package_incomplete)."


def test_completed_tail_notice_preserves_unassessed_selected_ancestor_debt():
    job = phase_job("completed")
    job["learning_summary"].update(history_integration_open=True, history_integration_unassessed_objective_count=3)
    notice = desktop_chat_monitor.phase_notice(job)
    assert "retains 3 unassessed selected objective(s)" in notice
    assert "assessed its bounded comparison" not in notice


@pytest.mark.parametrize("field,value", [
    ("history_integration_state", "private-state"), ("history_integration_category", "private-provider-error"),
    ("history_integration_resolved", False), ("learning_phase", "private-phase"),
])
def test_unresolved_phase_notices_do_not_echo_private_errors_or_claim_encoding_success(field, value):
    job = phase_job()
    job["learning_summary"][field] = value
    notice = desktop_chat_monitor.phase_notice(job)
    assert "private-" not in notice and "Local encoding was assessed" not in notice


def test_missing_phase_metadata_preserves_legacy_silent_behavior():
    assert desktop_chat_monitor.phase_notice({"learning_summary": {"processing_status": "completed"}}) is None
