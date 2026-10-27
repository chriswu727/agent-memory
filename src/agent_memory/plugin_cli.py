from __future__ import annotations

import json
import sys
from pathlib import Path

HOOK_EVENTS = ("session-start", "user-prompt", "stop")


def add_commands(commands):
    plugin = commands.add_parser("plugin", help="Zero-configuration entry points for agent host plugins")
    actions = plugin.add_subparsers(dest="plugin_command", required=True)
    hook = actions.add_parser("hook", help="Handle one host lifecycle hook from JSON on stdin; never fails")
    hook.add_argument("event", choices=HOOK_EVENTS)
    actions.add_parser("start", help="Create the local installation if needed and start the service")
    actions.add_parser("mcp", help="Run the MCP stdio bridge, creating and starting the installation if needed")
    actions.add_parser("status", help="Show installation, service and pending-record state")
    for parser in actions.choices.values():
        parser.add_argument("--directory", type=Path, default=None)


def dispatch(args) -> None:
    from . import plugin

    directory = plugin.profile_directory(args.directory)
    if args.plugin_command == "hook":
        raise SystemExit(plugin.run_hook(args.event, directory))
    if args.plugin_command == "mcp":
        from .desktop_connection import run_mcp

        plugin.ensure_installation(directory)
        run_mcp(directory)
        return
    from .desktop import DesktopError, status

    try:
        if args.plugin_command == "start":
            result = plugin.start(directory)
        else:
            result = status(directory) if (directory / "profile.json").exists() else {
                "profile": str(directory), "state": "not_installed", "ready": False}
            spool = directory / "plugin" / "spool.jsonl"
            result["pending_records"] = (sum(1 for line in spool.read_text(encoding="utf-8").splitlines()
                                              if line.strip()) if spool.exists() else 0)
    except DesktopError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2, ensure_ascii=False))
