import unicodedata
from pathlib import Path

import httpx

from .desktop import DesktopError
from .desktop_connection import open_client
from .local_profile import LocalProfileError
from .runtime_client import RuntimeClient

_STAGES = frozenset({"started", "captured", "prepared", "generating", "generated", "delivering", "delivered", "committed"})


def _identifier(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 256
            or any(character.isspace() or unicodedata.category(character).startswith("C") for character in value)):
        raise ValueError("Provide bounded identifiers without whitespace or control characters.")
    return value


def add_commands(actions):
    status = actions.add_parser("turn-status", help="Inspect the unfinished turn occupying a conversation without resuming it")
    status.add_argument("--directory", type=Path, default=None)
    status.add_argument("--conversation", default="desktop-chat")
    status.add_argument("--branch", default="main")


def inspect_scope(directory, conversation_id, branch_id):
    _identifier(conversation_id)
    _identifier(branch_id)
    try:
        with open_client(directory, start_service=False, timeout=2.0) as memory:
            result = RuntimeClient(memory).active_scope(conversation_id, branch_id)
            if not isinstance(result, dict) or "turn" not in result:
                raise ValueError
            row = result["turn"]
            if row is not None:
                if (not isinstance(row, dict)
                        or (row.get("brain_id"), row.get("conversation_id"), row.get("branch_id"))
                        != (memory.brain_id, conversation_id, branch_id)
                        or not isinstance(row.get("stage"), str) or row["stage"] not in _STAGES
                        or type(row.get("scope_reserved")) is not bool or type(row.get("lease_active")) is not bool
                        or row.get("recovery_required") not in (None, "generation_uncertain", "delivery_uncertain")):
                    raise ValueError
                _identifier(row.get("turn_id"))
                row = {key: row.get(key) for key in (
                    "turn_id", "stage", "scope_reserved", "lease_active", "recovery_required",
                )}
            return {"conversation_id": conversation_id, "branch_id": branch_id, "turn": row}
    except httpx.HTTPStatusError as error:
        message = {
            401: "Check the saved local service credentials.",
            404: "The brain is unavailable.",
            409: "The service requires recovery or its state changed. Check desktop status.",
            422: "The service rejected the conversation identifiers.",
        }.get(error.response.status_code, "The memory service rejected the inspection. Check desktop status.")
        raise DesktopError("turn_status_unavailable", message) from None
    except httpx.HTTPError:
        raise DesktopError("turn_status_unavailable", "Check desktop status and start the local service if needed.") from None
    except LocalProfileError:
        raise DesktopError("turn_status_unavailable", "The local connection configuration could not be loaded safely. Check desktop status.") from None
    except (ValueError, TypeError, KeyError):
        raise DesktopError("turn_status_unavailable", "The service returned invalid turn metadata.") from None


def run(args):
    return inspect_scope(args.directory, args.conversation, args.branch)
