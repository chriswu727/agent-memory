import asyncio
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_memory import desktop
from agent_memory.desktop_connection import mcp_config, open_client
from agent_memory.local_profile import ensure_profile, load_profile
from agent_memory.runtime import BrainRuntime, DeliveryReceipt

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("MEMORY_DESKTOP_E2E") != "1",
    reason="Set MEMORY_DESKTOP_E2E=1 to create an isolated Docker database for desktop verification",
)]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_cold_start_concurrent_connect_restart_and_mcp_preserve_one_brain(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "桌面 memory", api_port=free_port(), database_port=free_port())
    elsewhere = tmp_path / "unrelated host"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("MEMORY_DATABASE_URL", "postgresql://wrong:wrong@127.0.0.1:1/wrong")
    monkeypatch.setenv("MEMORY_API_KEY", "wrong-inherited-key")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    generated, delivered = [], []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: desktop.start(profile.directory), range(2)))
        first = results[0]
        assert all(item["ready"] for item in results)
        assert first["cognition"] == "not_configured"
        assert {item["instance_id"] for item in results} == {first["instance_id"]}
        assert desktop.start(profile.directory)["instance_id"] == first["instance_id"]

        def generate(request):
            generated.append(request)
            return "收到，你周五想去看海。"

        def deliver(request):
            delivered.append(request)
            return DeliveryReceipt("delivered")

        with open_client(profile.directory) as client:
            assert [brain["id"] for brain in client.brains()["brains"]] == [profile.brain_id]
            runtime = BrainRuntime(client, generate, deliver=deliver)
            result = runtime.respond("我周五想去看海。", message_id="desktop-message-1", conversation_id="trip")
            assert result.stage == "completed"
            events = client.dashboard()["events"]
            assert len(events) == 2
            original_event_ids = {event["event_id"] for event in events}
        assert len(generated) == len(delivered) == 1
        assert desktop.stop(profile.directory)["data_preserved"]
        assert desktop.status(profile.directory)["state"] == "stopped"
        configuration = mcp_config(profile.directory)["mcpServers"]["agent-memory"]
        assert profile.api_key not in json.dumps(configuration)

        async def connect_mcp():
            parameters = StdioServerParameters(**configuration, cwd=str(elsewhere))
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "Agent Memory"
                    response = await session.call_tool("memory_capabilities", {})
                    assert not response.isError
                    capabilities = response.structuredContent
                    assert capabilities["adapter"]["guarantee_level"] == "tools_only"
        asyncio.run(connect_mcp())
        after = desktop.status(profile.directory)
        assert after["ready"]
        assert after["instance_id"] != first["instance_id"]
        assert load_profile(profile.directory).brain_id == profile.brain_id
        with open_client(profile.directory) as client:
            resumed = BrainRuntime(client, generate, deliver=deliver).resume(result.turn_id)
            assert resumed.stage == "completed"
            assert {event["event_id"] for event in client.dashboard()["events"]} == original_event_ids
            assert len(client.brains()["brains"]) == 1
        assert len(generated) == len(delivered) == 1
        with httpx.Client(trust_env=False) as client:
            assert client.get(profile.base_url + "/v1/local/status").status_code == 401
        log = (profile.directory / "service.log").read_text()
        assert "我周五想去看海" not in log
        assert profile.api_key not in log
    finally:
        desktop.stop(profile.directory)
        desktop._compose(profile, "down", "--volumes")
