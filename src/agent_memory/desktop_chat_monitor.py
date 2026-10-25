import sys
from contextlib import contextmanager

from .desktop_connection import open_client
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
                        "Replies can continue; blocked learning is not retried automatically.")
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
