from __future__ import annotations

import getpass
import os
import re
import sys
import unicodedata
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx

from .runtime import DeliveryReceipt, RuntimeBlocked, RuntimePolicy


def add_commands(actions):
    configure = actions.add_parser("configure-chat", help="Configure the model that answers messages")
    configure.add_argument("--directory", type=Path)
    configure.add_argument("--base-url", help="Chat Completions-compatible provider URL")
    configure.add_argument("--model")
    configure.add_argument("--api-key-env", help="Environment variable name containing the provider key")
    configure.add_argument("--thinking", choices=("enabled", "disabled"))
    configure.add_argument("--use-cognition", action="store_true", help="Explicitly copy the memory learning provider")
    configure.add_argument("--max-output-tokens", type=int, default=4096)
    configure.add_argument("--timeout-seconds", type=float, default=180)
    configure.add_argument("--max-response-bytes", type=int, default=1048576)
    configure.add_argument("--non-interactive", action="store_true")

    chat = actions.add_parser("chat", help="Chat using the local persistent brain")
    chat.add_argument("--directory", type=Path)
    chat.add_argument("--conversation", default="desktop-chat")
    chat.add_argument("--branch", default="main")
    chat.add_argument("--instructions-file", type=Path)
    chat.add_argument("--orientation", choices=("always", "on_demand"), default="always")
    mode = chat.add_mutually_exclusive_group()
    mode.add_argument("--message", help="Send one message; requires a stable --message-id")
    mode.add_argument("--resume", action="store_true", help="Resume the pending turn once without resending input")
    mode.add_argument("--cancel", metavar="TURN_ID", help="Close the specified pending turn without retrying it")
    chat.add_argument("--acknowledge-uncertain", action="store_true", help="Acknowledge unknown model or delivery outcomes when cancelling")
    chat.add_argument("--message-id", help="Keep this identity unchanged when retrying the same message")


def configure(args):
    from .chat_config import (
        ChatConfiguration,
        save_chat_configuration,
        use_cognition_configuration,
    )
    from .local_profile import ensure_profile

    explicit = (args.base_url, args.model, args.api_key_env, args.thinking)
    if args.use_cognition and any(explicit):
        raise ValueError("--use-cognition cannot be combined with explicit provider settings.")
    if args.use_cognition:
        configuration = use_cognition_configuration(args.directory)
        configuration = replace(configuration, max_output_tokens=args.max_output_tokens,
                                timeout_seconds=args.timeout_seconds,
                                max_response_bytes=args.max_response_bytes)
    else:
        base_url, model, key_variable = explicit[:3]
        if any(explicit):
            if not all(explicit[:3]):
                raise ValueError("Provide --base-url, --model and --api-key-env together.")
            api_key = os.environ.get(key_variable)
            if not api_key:
                raise ValueError("The selected provider key environment variable is empty.")
        elif sys.stdin.isatty() and not args.non_interactive:
            print("Configure the model that answers messages. Memory learning settings stay separate.", file=sys.stderr)
            print("Provider base URL: ", end="", file=sys.stderr, flush=True)
            base_url = input().strip()
            print("Model name: ", end="", file=sys.stderr, flush=True)
            model = input().strip()
            api_key = getpass.getpass("Provider API key (hidden): ")
        else:
            raise ValueError("Provide provider settings or --use-cognition, or run interactively.")
        configuration = ChatConfiguration(
            base_url=base_url, model=model, api_key=api_key, thinking=args.thinking,
            max_output_tokens=args.max_output_tokens, timeout_seconds=args.timeout_seconds,
            max_response_bytes=args.max_response_bytes,
        )
    profile = ensure_profile(args.directory)
    save_chat_configuration(configuration, profile.directory)
    return {"chat": "configured", "cognition": "configured" if profile.cognition else "not_configured",
            "service_restart_required": False}


def _deliver(request):
    text = request.text + "\n"
    try:
        if sys.stdout.write(text) != len(text):
            raise OSError
        sys.stdout.flush()
    except (OSError, UnicodeError):
        raise RuntimeBlocked("delivery_uncertain", request.turn_id) from None
    return DeliveryReceipt("delivered")


def _call(operation):
    from .agent_session import SessionError
    from .chat_provider import ChatProviderError

    try:
        return operation()
    except (SessionError, RuntimeBlocked, ChatProviderError) as error:
        turn_id = getattr(error, "turn_id", None)
        print(error.code + (f": turn {turn_id}" if turn_id else ""), file=sys.stderr)
        if error.code != "no_pending_turn":
            print("Check the turn state before retrying. Unknown generation or delivery is not retried automatically.", file=sys.stderr)
        return None
    except httpx.HTTPError:
        print("memory_service_unavailable: Check desktop status and the turn state before retrying.", file=sys.stderr)
        return None


def _perform(operation, monitor=None):
    result = _call(operation)
    if result is None:
        return False
    if result.replayed:
        print(f"Turn {result.turn_id} was already completed; its answer was not sent again.", file=sys.stderr)
    elif result.stage != "completed":
        print(f"Turn {result.turn_id} remains at {result.stage}.", file=sys.stderr)
    if result.stage == "completed" and monitor is not None:
        monitor.completed(result.turn_id)
    return result.stage == "completed"


def _cancel(agent, turn_id, acknowledge_uncertain):
    result = _call(lambda: agent.cancel(turn_id=turn_id, acknowledge_uncertain=acknowledge_uncertain))
    if result is None:
        return False
    print(f"Turn {result['turn_id']} is {result['stage']}; no answer was generated or sent by cancellation.", file=sys.stderr)
    if result.get("failure_code") in ("generation_uncertain", "delivery_uncertain"):
        print("The original external outcome remains unknown; cancellation cannot undo a charge or a send.", file=sys.stderr)
    return True


def run(args):
    from .agent_session import open_agent
    from .desktop_chat_monitor import learning_monitor
    from .local_profile import load_profile

    if (args.message is None) != (args.message_id is None):
        raise ValueError("--message and --message-id must be supplied together.")
    if args.acknowledge_uncertain and args.cancel is None:
        raise ValueError("--acknowledge-uncertain requires --cancel with an exact turn ID.")
    if args.cancel is not None and not re.fullmatch(r"turn-[0-9a-f]{40}", args.cancel):
        raise ValueError("--cancel requires an exact pending turn ID.")
    if args.message is not None:
        if not args.message.strip() or not 1 <= len(args.message_id) <= 256 or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in args.message_id
        ):
            raise ValueError("Provide a nonempty message and a stable message ID without whitespace or control characters.")
        try:
            args.message.encode("utf-8")
        except UnicodeError:
            raise ValueError("The message must contain valid UTF-8 text.") from None
    interactive = args.message is None and not args.resume and args.cancel is None
    if interactive and not sys.stdin.isatty():
        raise ValueError("Use --message with --message-id, --resume, or --cancel outside an interactive terminal.")
    instructions = ""
    if args.instructions_file is not None:
        with args.instructions_file.open("rb") as stream:
            contents = stream.read(64001)
        if len(contents) > 64000:
            raise ValueError("The instructions file exceeds 64,000 bytes.")
        try:
            instructions = contents.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("The instructions file must contain UTF-8 text.") from None
    profile = load_profile(args.directory)
    with open_agent(args.directory, conversation_id=args.conversation, branch_id=args.branch,
                    deliver=_deliver, instructions=instructions,
                    policy=RuntimePolicy(orientation_policy=args.orientation)) as agent, learning_monitor(
                        args.directory, enabled=profile.cognition is not None and args.cancel is None, startup=interactive,
                    ) as monitor:
        if profile.cognition is None:
            print("Memory learning is not configured; persistent sources and lexical recall are available.", file=sys.stderr)
        if not interactive:
            if args.cancel is not None:
                if not _cancel(agent, args.cancel, args.acknowledge_uncertain):
                    raise SystemExit(1)
                return
            operation = agent.resume if args.resume else lambda: agent.respond(args.message, message_id=args.message_id)
            if not _perform(operation, monitor):
                raise SystemExit(1)
            return
        print("Local memory chat. Commands: /quit, /resume, /retry <original input>, /cancel <turn-id>.", file=sys.stderr)
        print("Use /cancel-uncertain <turn-id> only to explicitly close a turn whose model or delivery outcome is unknown.", file=sys.stderr)
        if agent.pending:
            print(f"Pending turn {agent.pending['turn_id']}; resolve it before sending a new message.", file=sys.stderr)
        while True:
            print("You: ", end="", file=sys.stderr, flush=True)
            try:
                text = input()
            except EOFError:
                return
            command = text.strip()
            command_name = command.split(maxsplit=1)[0] if command else ""
            if command == "/quit":
                return
            if command == "/resume":
                _perform(agent.resume, monitor)
            elif command == "/retry":
                print("Specify the exact original input after /retry followed by a space.", file=sys.stderr)
            elif command in ("/cancel", "/cancel-uncertain"):
                print("Specify the exact pending turn ID after the command.", file=sys.stderr)
            elif command.startswith("/cancel ") or command.startswith("/cancel-uncertain "):
                name, turn_id = command.split(" ", 1)
                _cancel(agent, turn_id, name == "/cancel-uncertain")
            elif text.lstrip().startswith("/retry "):
                pending = agent.pending
                if not pending:
                    print("no_pending_turn", file=sys.stderr)
                    continue
                _perform(lambda: agent.respond(text.lstrip()[len("/retry "):], message_id=pending["message_id"]), monitor)
            elif command_name in (
                "/quit", "/resume", "/retry", "/cancel", "/cancel-uncertain",
            ):
                print("Invalid command arguments; no message was sent.", file=sys.stderr)
            elif text.strip():
                _perform(lambda: agent.respond(text, message_id=str(uuid4())), monitor)
