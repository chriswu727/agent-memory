from __future__ import annotations

import json
import sys
from pathlib import Path

HOOK_EVENTS = ("session-start", "user-prompt", "stop")
HOOK_HOSTS = ("claude-code", "codex", "openclaw", "workbuddy")


def add_commands(commands):
    plugin = commands.add_parser("plugin", help="Zero-configuration entry points for agent host plugins")
    actions = plugin.add_subparsers(dest="plugin_command", required=True)
    hook = actions.add_parser("hook", help="Handle one host lifecycle hook from JSON on stdin; never fails")
    hook.add_argument("event", choices=HOOK_EVENTS)
    hook.add_argument("--host", choices=HOOK_HOSTS, default="claude-code", help="Host whose hook format is read")
    tool = actions.add_parser("tool", help="Run one memory tool with JSON arguments on stdin (for hosts without MCP)")
    tool.add_argument("name", choices=("note", "search", "pause", "resume"))
    actions.add_parser("start", help="Create the local installation if needed and start the service")
    actions.add_parser("mcp", help="Run the MCP stdio bridge, creating and starting the installation if needed")
    actions.add_parser("status", help="Show installation, service and pending-record state")
    actions.add_parser("fetch-model", help="Download the local multilingual embedding model for semantic recall")
    pause = actions.add_parser("pause", help="Stop recording messages (recall keeps working) until resumed")
    pause.add_argument("--hours", type=float, default=None, help="Resume automatically after this many hours")
    actions.add_parser("resume", help="Resume recording messages")
    ui = actions.add_parser("ui", help="Open the memory page in a browser (starts the service if needed)")
    ui.add_argument("--no-browser", action="store_true", help="Only print the single-use link")
    for parser in actions.choices.values():
        parser.add_argument("--directory", type=Path, default=None)


def dispatch(args) -> None:
    from . import plugin

    directory = plugin.profile_directory(args.directory)
    if args.plugin_command == "hook":
        raise SystemExit(plugin.run_hook(args.event, directory, host_name=args.host))
    if args.plugin_command == "tool":
        raise SystemExit(plugin.run_tool(args.name, directory))
    if args.plugin_command == "mcp":
        from .desktop_connection import run_mcp
        from .host import installed_tool_python, running_from_uv_cache

        durable = installed_tool_python() if running_from_uv_cache() else None
        if durable is not None:
            # Serve MCP from the installed tool so the service it starts outlives uv cache cleanups.
            import subprocess

            raise SystemExit(subprocess.call([str(durable), "-m", "membrain.cli", "plugin", "mcp",
                                              "--directory", str(directory)]))

        plugin.ensure_installation(directory)
        run_mcp(directory)
        return
    from .desktop import DesktopError, status

    try:
        if args.plugin_command == "start":
            result = plugin.start(directory)
        elif args.plugin_command == "fetch-model":
            result = plugin.fetch_model(directory)
        elif args.plugin_command == "ui":
            from . import host
            from .local_profile import load_profile

            plugin.start(directory)
            url = f"{load_profile(directory).base_url}/memory?code={host.issue_ui_code(directory)}"
            if not args.no_browser:
                import webbrowser

                webbrowser.open(url)
            print(f"Memory page (single-use link, valid for {host.UI_CODE_SECONDS // 60} minutes):\n{url}")
            return
        elif args.plugin_command in ("pause", "resume"):
            from . import host

            result = host.pause(directory, args.hours) if args.plugin_command == "pause" else host.resume(directory)
        else:
            result = status(directory) if (directory / "profile.json").exists() else {
                "profile": str(directory), "state": "not_installed", "ready": False}
            spool = directory / "plugin" / "spool.jsonl"
            result["pending_records"] = (sum(1 for line in spool.read_text(encoding="utf-8").splitlines()
                                              if line.strip()) if spool.exists() else 0)
            result["semantic_recall"] = plugin.semantic_state(directory)
            from . import host

            result["recording"] = "paused" if host.paused(directory) else "on"
    except DesktopError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2, ensure_ascii=False))
