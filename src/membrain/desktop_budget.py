from pathlib import Path

import httpx

from .cognitive_budget import validate_change
from .desktop import DesktopError
from .desktop_connection import open_client
from .helpers import DomainError
from .runtime_client import RuntimeClient


def add_commands(actions):
    status = actions.add_parser("budget", help="Inspect cognitive call and input-byte limits and current usage")
    status.add_argument("--directory", type=Path, default=None)
    change = actions.add_parser("configure-budget", help="Change the durable cognitive budget")
    change.add_argument("--directory", type=Path, default=None)
    change.add_argument("--request-id", required=True, help="Stable ID to reuse if the response is lost")
    change.add_argument("--expected-revision", type=int, required=True, help="Revision from desktop budget")
    change.add_argument("--window-seconds", type=int, required=True)
    change.add_argument("--max-model-calls", type=int, required=True)
    change.add_argument("--max-input-bytes", type=int, required=True)


def run(args):
    change = args.desktop_command == "configure-budget"
    if change:
        policy = {key: getattr(args, key) for key in ("window_seconds", "max_model_calls", "max_input_bytes")}
        try:
            validate_change(args.request_id, args.expected_revision, policy)
        except DomainError as error:
            raise ValueError(str(error)) from None
    try:
        with open_client(args.directory, start_service=False) as memory:
            runtime = RuntimeClient(memory)
            if change:
                return runtime.configure_cognitive_budget(
                    request_id=args.request_id, expected_revision=args.expected_revision, **policy,
                )
            return runtime.cognitive_budget()
    except httpx.HTTPStatusError as error:
        messages = {
            401: "Check the saved local service credentials.",
            404: "The brain is unavailable.",
            409: "The budget revision or request ID conflicts. Inspect desktop budget before a new change.",
            422: "The cognitive budget configuration is invalid.",
            503: "The database is unavailable. Check desktop status.",
        }
        raise DesktopError("budget_request_rejected", messages.get(
            error.response.status_code, "The memory service rejected the request. Inspect desktop budget.",
        )) from None
    except httpx.HTTPError:
        message = "Check desktop status and start the local service if needed."
        if change:
            message += " The change may have succeeded; reuse the same request ID, revision, and policy."
        raise DesktopError("memory_service_unavailable", message) from None
