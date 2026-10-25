import sys
from contextlib import contextmanager

from .desktop_connection import open_client
from .learning_phase_monitor import sanitize_phase_counts, sanitize_phase_metadata
from .runtime_client import RuntimeClient

_UNAVAILABLE = "Memory diagnostics unavailable; use desktop learning-status to inspect learning."
_FAILED_PROCESSING = frozenset({
    "budget_exhausted", "provider_unavailable", "invalid_response", "verification_unavailable", "not_evaluated",
})


def _notice(message):
    try:
        print(message, file=sys.stderr)
    except (OSError, UnicodeError, ValueError):
        pass


def phase_notice(job):
    summary = job.get("learning_summary") if isinstance(job, dict) else None
    safe = sanitize_phase_metadata(summary)
    if not safe:
        return None
    if not safe["phase_metadata_complete"]:
        return "Learning phase diagnostics are incomplete; inspect desktop learning-status."
    if "history_integration_state" not in safe:
        return None
    if not safe["history_integration_resolved"]:
        return "Historical integration status is unresolved; inspect desktop learning-status."
    state = safe["history_integration_state"]
    local = (safe["learning_phase"] == "encoding" and job.get("state") == "completed"
             and isinstance(summary.get("learning_window"), dict)
             and summary["learning_window"].get("processing_succeeded") is True)
    prefix = "Local encoding was assessed; historical integration " if local else "Historical integration "
    unassessed = safe.get("history_integration_unassessed_objective_count", 0)
    if unassessed:
        return prefix + f"retains {unassessed} unassessed selected objective(s); inspect its jobs before recovery."
    if state == "deferred":
        return prefix + "is deferred (" + safe["history_integration_category"] + ")."
    if state == "completed":
        return prefix + "assessed its bounded comparison. New publication is a separate outcome."
    if state in {"queued", "running"}:
        return prefix + "is " + state + "."
    return prefix + "is blocked (" + state + "); inspect its job before explicit recovery."


class ChatLearningMonitor:
    def __init__(self, memory):
        self.runtime = RuntimeClient(memory)

    def startup(self):
        try:
            progress = self.runtime.learning_progress()
            count = progress.get("blocked_count")
            if type(count) is not int or not 0 <= count <= 2**63 - 1 or type(progress.get("counts_complete")) is not bool:
                raise ValueError
            if count:
                _notice(f"Background learning has {count} blocked window(s); use desktop learning-status. "
                        "Replies can continue.")
            if "learning_phases" in progress:
                phases = sanitize_phase_counts(progress["learning_phases"])
                if phases["history_integration_deferred_count"]:
                    _notice(f"Historical integration has {phases['history_integration_deferred_count']} deferred "
                            "obligation(s). Local encoding and historical integration have separate outcomes; "
                            "use desktop learning-status.")
                if not phases["counts_complete"]:
                    _notice("Learning phase coverage is incomplete; use desktop learning-status.")
            if not progress["counts_complete"]:
                _notice("Background learning coverage is incomplete; use desktop learning-status.")
        except Exception:
            _notice(_UNAVAILABLE)

    def completed(self, turn_id):
        try:
            turn = self.runtime.get(turn_id)
            job_id = turn.get("orientation_job_id")
            if job_id is None:
                return
            if not isinstance(job_id, str) or not 1 <= len(job_id) <= 256:
                raise ValueError
            job = self.runtime.job(job_id)
            summary = job.get("learning_summary")
            status = summary.get("processing_status") if isinstance(summary, dict) else None
            if job.get("id") != job_id or job.get("kind") != "orient" or not isinstance(status, str):
                raise ValueError
            if status in _FAILED_PROCESSING:
                _notice(f"Situation understanding was incomplete ({status}); the reply was delivered. "
                        "Background learning has a separate status: desktop learning-status.")
            elif status not in {"completed", "partial", "no_sources"}:
                raise ValueError
        except Exception:
            # Diagnostics must not turn confirmed delivery into a failed chat operation.
            _notice(_UNAVAILABLE)


@contextmanager
def learning_monitor(directory, *, enabled, startup=True):
    memory = monitor = None
    if enabled:
        try:
            memory = open_client(directory, start_service=False, timeout=2.0)
            monitor = ChatLearningMonitor(memory)
            if startup:
                monitor.startup()
        except Exception:
            _notice(_UNAVAILABLE)
    try:
        yield monitor
    finally:
        if memory is not None:
            try:
                memory.close()
            except Exception:
                _notice(_UNAVAILABLE)
