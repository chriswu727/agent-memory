from __future__ import annotations

import sys
from contextlib import redirect_stdout
from pathlib import Path

from .client import MemoryClient


def mcp_config(directory: Path | None = None) -> dict:
    from .local_profile import load_profile

    profile = load_profile(directory)
    return {
        "mcpServers": {
            "agent-memory": {
                "command": str(Path(sys.executable).absolute()),
                "args": [
                    "-m", "agent_memory.cli", "desktop", "mcp",
                    "--directory", str(profile.directory.resolve()),
                ],
            },
        },
    }


def open_client(directory: Path | None = None, *, start_service: bool = True) -> MemoryClient:
    from .local_profile import load_profile

    if start_service:
        from . import desktop

        desktop.start(directory)
    profile = load_profile(directory)
    return MemoryClient(profile.base_url, profile.api_key, profile.brain_id, trust_env=False)


def run_mcp(directory: Path | None = None) -> None:
    from .mcp_server import create_server

    with redirect_stdout(sys.stderr):
        client = open_client(directory)
    with client:
        with redirect_stdout(sys.stderr):
            server = create_server(client)
        server.run(transport="stdio")
