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
            parser.add_argument("--no-start", action="store_true", help="Save configuration without starting services")
            parser.add_argument("--non-interactive", action="store_true")
        elif name == "logs":
            parser.add_argument("--lines", type=int, default=30)


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
    from . import desktop
    from .desktop_connection import mcp_config, run_mcp

    try:
        action = args.desktop_command
        if action == "setup":
            result = _setup(args)
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
        else:
            result = getattr(desktop, action)(args.directory)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if action in {"setup", "start", "restart"} and result.get("cognition") == "not_configured":
            print("Model-driven learning is not configured. MCP provides tools only; automatic conversation memory needs a host lifecycle adapter.", file=sys.stderr)
    except (desktop.DesktopError, ValueError) as error:
        print(f"{getattr(error, 'code', 'configuration_error')}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        print("local_io_error: Local files or processes were unavailable. Existing data was preserved.", file=sys.stderr)
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        print("Setup cancelled; saved configuration was preserved.", file=sys.stderr)
        raise SystemExit(130) from None
