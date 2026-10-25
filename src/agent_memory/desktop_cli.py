import getpass
import json
import os
import sys
from pathlib import Path


def add_commands(commands):
    desktop = commands.add_parser("desktop", help="Set up and manage the local memory service")
    actions = desktop.add_subparsers(dest="desktop_command", required=True)
    for name in ("setup", "start", "status", "stop", "restart", "mcp-config", "mcp", "logs", "_run"):
        parser = actions.add_parser(name)
        parser.add_argument("--directory", type=Path, default=None)
        if name == "setup":
            parser.add_argument("--api-port", type=int)
            parser.add_argument("--database-port", type=int)
            parser.add_argument("--base-url", help="OpenAI-compatible cognition provider URL")
            parser.add_argument("--model", help="Cognition model name")
            parser.add_argument("--api-key-env", help="Name of the environment variable containing the provider key")
            parser.add_argument("--thinking", choices=("enabled", "disabled"))
            parser.add_argument("--cognition-preset", choices=("reasoning",),
                                help="Explicitly allow 240s per cognitive batch and 16384 output tokens per request")
            parser.add_argument("--no-start", action="store_true", help="Save configuration without starting services")
            parser.add_argument("--non-interactive", action="store_true")
        elif name == "logs":
            parser.add_argument("--lines", type=int, default=30)
    from .desktop_budget import add_commands as add_budget_commands
    from .desktop_chat import add_commands as add_chat_commands
    from .desktop_cognition import add_commands as add_cognition_commands
    from .desktop_learning import add_commands as add_learning_commands
    from .desktop_recovery import add_commands as add_recovery_commands

    add_chat_commands(actions)
    add_learning_commands(actions)
    add_budget_commands(actions)
    add_cognition_commands(actions)
    add_recovery_commands(actions)


def _setup(args):
    from . import desktop
    from .desktop_connection import mcp_config
    from .local_profile import configure_cognition, ensure_profile, profile_lock

    profile = ensure_profile(args.directory, api_port=args.api_port, database_port=args.database_port)
    configuration = (args.base_url, args.model, args.api_key_env, args.thinking)
    if any(configuration):
        if not all(configuration[:3]):
            raise ValueError("Provide --base-url, --model and --api-key-env together.")
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            raise ValueError("The selected provider key environment variable is empty.")
        profile = configure_cognition(profile, base_url=args.base_url, model=args.model,
                                      api_key=api_key, thinking=args.thinking)
    elif profile.cognition is None and sys.stdin.isatty() and not args.non_interactive:
        print("Configure the model used for memory learning once. Press Enter to skip for now.", file=sys.stderr)
        print("Provider base URL: ", end="", file=sys.stderr, flush=True)
        base_url = input().strip()
        if base_url:
            print("Model name: ", end="", file=sys.stderr, flush=True)
            model = input().strip()
            api_key = getpass.getpass("Provider API key (hidden): ")
            profile = configure_cognition(profile, base_url=base_url, model=model, api_key=api_key)
    preset = getattr(args, "cognition_preset", None)
    if preset is not None:
        from .cognition_config import cognition_policy_preset, save_cognition_policy

        save_cognition_policy(cognition_policy_preset(preset), profile.directory)
    with profile_lock(profile.directory):
        configuration = mcp_config(profile.directory)
        path = profile.directory / "mcp.json"
        desktop._private_write(path, json.dumps(configuration, indent=2) + "\n")
    current = desktop.status(profile.directory)
    if args.no_start:
        result = current
    elif current["state"] == "restart_required":
        result = desktop.restart(profile.directory)
    else:
        result = desktop.start(profile.directory)
    result.update(mcp_config=configuration, mcp_config_file=str(path))
    if preset is not None:
        from dataclasses import asdict

        result["cognition_policy"] = asdict(cognition_policy_preset(preset))
    return result


def _logs(directory, lines):
    from .local_profile import load_profile

    if not 1 <= lines <= 100:
        raise ValueError("--lines must be between 1 and 100.")
    path = load_profile(directory).directory / "service.log"
    if path.is_symlink():
        raise ValueError("The log path is a symbolic link.")
    if not path.exists():
        return {"events": []}
    events = []
    for line in path.read_text(encoding="utf-8").splitlines()[-lines:]:
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and set(entry) == {"time", "event", "instance_id"}:
                events.append(entry)
        except ValueError:
            continue
    return {"events": events}


def dispatch(args):
    import psycopg

    from . import desktop
    from .agent_session import SessionError
    from .backup_transport import BackupError
    from .chat_config import ChatConfigurationError
    from .deletion_registry import RegistryError
    from .desktop_chat import configure, run
    from .desktop_connection import mcp_config, run_mcp
    from .storage import SchemaCompatibilityError

    try:
        action = args.desktop_command
        if action == "setup":
            result = _setup(args)
        elif action == "configure-chat":
            result = configure(args)
        elif action == "chat":
            run(args)
            return
        elif action == "mcp-config":
            result = mcp_config(args.directory)
        elif action == "mcp":
            run_mcp(args.directory)
            return
        elif action == "_run":
            desktop.run(args.directory)
            return
        elif action == "logs":
            result = _logs(args.directory, args.lines)
        elif action in {"cognition-policy", "configure-cognition-policy"}:
            from .desktop_cognition import run as run_cognition

            result = run_cognition(args)
        elif action in {"budget", "configure-budget"}:
            from .desktop_budget import run as run_budget

            result = run_budget(args)
        elif action in {"backup", "restore-backup", "activate-recovery", "recovery-status", "cancel-backup"}:
            from .desktop_recovery import run as run_recovery

            result = run_recovery(args)
        elif action in {"learning-status", "retry-learning"}:
            from .desktop_learning import run as run_learning

            result = run_learning(args)
        else:
            result = getattr(desktop, action)(args.directory)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if action in {"setup", "start", "restart"} and result.get("cognition") == "not_configured":
            print("Model-driven learning is not configured. MCP provides tools only; automatic conversation memory needs a host lifecycle adapter.", file=sys.stderr)
    except (desktop.DesktopError, SessionError, ChatConfigurationError, BackupError, RegistryError,
            SchemaCompatibilityError, ValueError) as error:
        print(f"{getattr(error, 'code', 'configuration_error')}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except psycopg.Error:
        print("database_unavailable: The database operation did not complete. Existing data was preserved; retry the same recovery request.", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        print("local_io_error: Local files or processes were unavailable. Existing data was preserved.", file=sys.stderr)
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        message = ("Chat interrupted; check the pending turn before retrying. An interrupted send may have an unknown outcome."
                   if args.desktop_command == "chat" else "Setup cancelled; saved configuration was preserved.")
        print(message, file=sys.stderr)
        raise SystemExit(130) from None
