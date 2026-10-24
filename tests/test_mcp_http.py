import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_memory.client import MemoryClient
from agent_memory.helpers import uid

pytestmark = pytest.mark.integration


def test_stdio_tools_recall_persistent_events_through_real_http(tmp_path):
    load_dotenv()
    dsn = os.environ.get("MEMORY_DATABASE_URL")
    if not dsn:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    key = uid()
    key_file = tmp_path / "keys.json"
    key_file.write_text(json.dumps({hashlib.sha256(key.encode()).hexdigest(): {
        "tenant_id": uid(), "principal_id": uid(),
    }}))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    environment = {**os.environ, "MEMORY_DATABASE_URL": dsn,
                   "MEMORY_API_KEYS_FILE": str(key_file)}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agent_memory.api:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        with httpx.Client(timeout=0.5, trust_env=False) as health:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    pytest.fail(f"HTTP server exited: {server.stderr.read()}")
                try:
                    if health.get(base_url + "/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                pytest.fail("HTTP server did not become ready")
        with MemoryClient(base_url, key) as client:
            brain_id = client.create_brain("MCP to HTTP to PostgreSQL")['id']
            client.brain_id = brain_id
            client.observe(
                "我们约定周五去看海。", conversation_id="trip", branch_id="main",
                source_event_key="promise", interpretation_hint="real_world_report",
            )
            client.work()

        async def recall():
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "agent_memory.mcp_server"],
                env={**environment, "MEMORY_BASE_URL": base_url, "MEMORY_API_KEY": key,
                     "MEMORY_BRAIN_ID": brain_id},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "Agent Memory"
                    response = await session.call_tool("memory_recall", {
                        "query": "周五看海", "conversation_id": "trip", "branch_id": "main",
                    })
                    assert not response.isError, response
                    result = response.structuredContent
                    assert result["outcome"] == "completed"
                    view = json.loads(result["context"])
                    assert any("周五去看海" in item["statement"] for item in view["memories"])
                    assert all(item["sources"] for item in view["memories"])
                    assert all(ref in view["sources"] for item in view["memories"] for ref in item["sources"])
        asyncio.run(recall())
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        server.stderr.close()
