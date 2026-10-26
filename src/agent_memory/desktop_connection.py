from __future__ import annotations

import sys
from contextlib import redirect_stdout
from pathlib import Path

import httpx

from .client import MemoryClient

# Frozen jobs may retain a 600-second deadline after the current policy is lowered.
_RPC_TIMEOUT = httpx.Timeout(30, connect=5, read=615, pool=5)


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


def open_client(directory: Path | None = None, *, start_service: bool = True,
                timeout: float | httpx.Timeout = _RPC_TIMEOUT) -> MemoryClient:
    from .local_profile import load_profile
    from .recovery_profile import check_start_allowed, load_binding

    if start_service:
        from . import desktop

        desktop.start(directory)
    profile = load_profile(directory)
    check_start_allowed(profile)
    binding = load_binding(profile)
    kwargs = {"recovery_epoch": binding["recovery_epoch"]} if binding and binding["recovery_epoch"] else {}
    return MemoryClient(profile.base_url, profile.api_key, profile.brain_id, timeout=timeout, trust_env=False, **kwargs)


def run_mcp(directory: Path | None = None) -> None:
    from .mcp_server import create_server

    with redirect_stdout(sys.stderr):
        client = open_client(directory)
    with client:
        with redirect_stdout(sys.stderr):
            server = create_server(client)
        server.run(transport="stdio")
