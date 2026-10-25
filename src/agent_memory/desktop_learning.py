from pathlib import Path

import httpx

from .desktop import DesktopError
from .desktop_connection import open_client
from .runtime_client import RuntimeClient


def add_commands(actions):
    status = actions.add_parser("learning-status", help="Inspect background learning and blocked windows")
    status.add_argument("--directory", type=Path, default=None)
    status.add_argument("--job", help="Inspect one learning job instead of brain-wide progress")
    retry = actions.add_parser("retry-learning", help="Schedule bounded recovery of a blocked learning window")
    retry.add_argument("job_id")
    retry.add_argument("--directory", type=Path, default=None)
    retry.add_argument("--request-id", required=True, help="Stable ID to reuse if the response is lost")
    retry.add_argument("--expected-fence", type=int, required=True, help="Fence from the blocked job metadata")


def run(args):
    retry = args.desktop_command == "retry-learning"
    if retry and (not args.request_id.strip() or len(args.request_id) > 256
                  or not 0 <= args.expected_fence <= 2**63 - 1):
        raise ValueError("Provide a nonempty request ID of at most 256 characters and a nonnegative fence.")
    try:
        with open_client(args.directory, start_service=False) as memory:
            runtime = RuntimeClient(memory)
            if retry:
                return runtime.retry_learning(
                    args.job_id, request_id=args.request_id, expected_fence=args.expected_fence,
                )
            result = runtime.job(args.job) if args.job else runtime.learning_progress()
            if args.job:
                from .desktop_chat_monitor import _notice, phase_notice

                if (notice := phase_notice(result)) is not None:
                    _notice(notice)
            return result
    except httpx.HTTPStatusError as error:
        messages = {
            401: "Check the saved local service credentials.",
            404: "The brain or learning job is unavailable.",
            409: "The job state changed or recovery already exists. Inspect learning-status before retrying.",
            422: "The learning recovery request is invalid or the job is not eligible.",
            503: "The database or configured learning provider is unavailable. Check desktop status.",
        }
        raise DesktopError("learning_request_rejected", messages.get(
            error.response.status_code, "The memory service rejected the request. Inspect learning-status.",
        )) from None
    except httpx.HTTPError:
        message = "Check desktop status and start the local service if needed."
        if retry:
            message += " The recovery request may have succeeded; reuse the same request ID and fence."
        raise DesktopError("memory_service_unavailable", message) from None
