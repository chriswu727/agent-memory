import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from agent_memory import desktop_connection
from agent_memory.client import MemoryClient


@pytest.fixture
def local_connection(monkeypatch, tmp_path):
    profile = SimpleNamespace(
        directory=tmp_path / "profile with spaces", base_url="http://127.0.0.1:18787",
        api_key="private-profile-access-token", brain_id="persistent-brain",
    )
    profile.directory.mkdir()
    profiles = ModuleType("agent_memory.local_profile")
    profiles.load_profile = Mock(return_value=profile)
    desktop = ModuleType("agent_memory.desktop")
    desktop.start = Mock(return_value={"ready": True})
    monkeypatch.setitem(sys.modules, "agent_memory.local_profile", profiles)
    monkeypatch.setitem(sys.modules, "agent_memory.desktop", desktop)
    monkeypatch.setattr("agent_memory.desktop", desktop, raising=False)
    return SimpleNamespace(profile=profile, start=desktop.start, load=profiles.load_profile)


def test_mcp_configuration_has_absolute_launcher_and_no_credentials(
    local_connection, monkeypatch, tmp_path, capsys,
):
    profile = local_connection.profile
    outside = tmp_path / "other directory"
    outside.mkdir()
    monkeypatch.chdir(outside)
    config = desktop_connection.mcp_config(profile.directory)
    launcher = config["mcpServers"]["agent-memory"]
    assert launcher == {
        "command": str(Path(sys.executable).absolute()),
        "args": [
            "-m", "agent_memory.cli", "desktop", "mcp", "--directory", str(profile.directory),
        ],
    }
    assert Path(launcher["command"]).is_absolute()
    serialized = json.dumps(config)
    assert profile.api_key not in serialized
    assert profile.brain_id not in serialized
    assert profile.base_url not in serialized
    assert "env" not in launcher
    local_connection.start.assert_not_called()
    local_connection.load.assert_called_once_with(profile.directory)
    assert capsys.readouterr() == ("", "")


def test_mcp_launcher_preserves_virtual_environment_executable(
    local_connection, monkeypatch, tmp_path,
):
    executable = tmp_path / "venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(executable))
    config = desktop_connection.mcp_config()
    assert config["mcpServers"]["agent-memory"]["command"] == str(executable)
    local_connection.load.assert_called_once_with(None)


def test_open_client_starts_service_before_loading_bound_credentials(local_connection, monkeypatch):
    operations = []
    local_connection.start.side_effect = lambda directory: operations.append(("start", directory))
    local_connection.load.side_effect = lambda directory: (
        operations.append(("load", directory)) or local_connection.profile
    )
    factory = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(desktop_connection, "MemoryClient", factory)
    client = desktop_connection.open_client(local_connection.profile.directory)
    assert client is factory.return_value
    assert operations == [
        ("start", local_connection.profile.directory), ("load", local_connection.profile.directory),
    ]
    profile = local_connection.profile
    factory.assert_called_once_with(profile.base_url, profile.api_key, profile.brain_id, trust_env=False)


def test_open_client_can_connect_without_starting_services(local_connection, capsys):
    with desktop_connection.open_client(start_service=False) as client:
        assert client.brain_id == local_connection.profile.brain_id
        assert str(client._http.base_url) == local_connection.profile.base_url + "/"
        assert client._http.headers["Authorization"] == "Bearer " + local_connection.profile.api_key
        assert client._http.trust_env is False
        assert not client._http.is_closed
    assert client._http.is_closed
    local_connection.start.assert_not_called()
    local_connection.load.assert_called_once_with(None)
    assert capsys.readouterr() == ("", "")


def test_failed_start_does_not_read_profile_or_construct_client(local_connection, monkeypatch):
    local_connection.start.side_effect = RuntimeError("database_unavailable")
    factory = Mock()
    monkeypatch.setattr(desktop_connection, "MemoryClient", factory)
    with pytest.raises(RuntimeError, match="database_unavailable"):
        desktop_connection.open_client()
    local_connection.load.assert_not_called()
    factory.assert_not_called()


def test_mcp_keeps_stdout_for_protocol_and_closes_client(local_connection, monkeypatch, capsys):
    clients = []

    def connect(*args, **kwargs):
        client = MemoryClient(*args, **kwargs)
        clients.append(client)
        return client

    def start(directory):
        print("Starting local memory service")
        return {"api_key": local_connection.profile.api_key}

    def run(server, *, transport):
        assert transport == "stdio"
        assert not clients[0]._http.is_closed
        print('{"jsonrpc":"2.0","id":1,"result":{}}')

    local_connection.start.side_effect = start
    monkeypatch.setattr(desktop_connection, "MemoryClient", connect)
    monkeypatch.setattr(FastMCP, "run", run)
    desktop_connection.run_mcp(local_connection.profile.directory)
    captured = capsys.readouterr()
    assert captured.out == '{"jsonrpc":"2.0","id":1,"result":{}}\n'
    assert captured.err == "Starting local memory service\n"
    assert local_connection.profile.api_key not in captured.out + captured.err
    assert clients[0]._http.is_closed
    assert clients[0]._http.trust_env is False


def test_mcp_start_failure_does_not_start_protocol(local_connection, monkeypatch, capsys):
    def start(directory):
        print("Database is unavailable")
        raise RuntimeError("database_unavailable")

    local_connection.start.side_effect = start
    run = Mock()
    monkeypatch.setattr(FastMCP, "run", run)
    with pytest.raises(RuntimeError, match="database_unavailable"):
        desktop_connection.run_mcp()
    run.assert_not_called()
    local_connection.load.assert_not_called()
    assert capsys.readouterr() == ("", "Database is unavailable\n")


@pytest.mark.parametrize("failure_stage", ["create", "run"])
def test_mcp_closes_client_on_server_failure(local_connection, monkeypatch, failure_stage):
    from agent_memory import mcp_server

    client = desktop_connection.open_client(start_service=False)
    monkeypatch.setattr(desktop_connection, "open_client", lambda directory: client)
    if failure_stage == "create":
        monkeypatch.setattr(mcp_server, "create_server", Mock(side_effect=RuntimeError("server_failed")))
    else:
        monkeypatch.setattr(FastMCP, "run", Mock(side_effect=RuntimeError("server_failed")))
    with pytest.raises(RuntimeError, match="server_failed"):
        desktop_connection.run_mcp()
    assert client._http.is_closed


def test_desktop_mcp_reports_tool_only_capabilities(local_connection, monkeypatch):
    def connect(base_url, api_key, brain_id, **kwargs):
        return MemoryClient(base_url, api_key, brain_id, **kwargs, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "available"}),
        ))

    def run(server, *, transport):
        async def inspect():
            async with create_connected_server_and_client_session(server) as session:
                result = await session.call_tool("memory_capabilities", {})
                assert not result.isError
                adapter = result.structuredContent["adapter"]
                assert adapter["guarantee_level"] == "tools_only"
                assert adapter["automatic_capture"] is False
                assert adapter["automatic_recall"] is False
                assert not any(adapter["hooks"].values())

        asyncio.run(inspect())

    monkeypatch.setattr(desktop_connection, "MemoryClient", connect)
    monkeypatch.setattr(FastMCP, "run", run)
    desktop_connection.run_mcp()


@pytest.mark.parametrize("trust_env", [True, False])
def test_memory_client_forwards_proxy_policy(monkeypatch, trust_env):
    client_factory = Mock()
    monkeypatch.setattr("agent_memory.client.httpx.Client", client_factory)
    MemoryClient("http://127.0.0.1:18787", "private-access-token", trust_env=trust_env)
    assert client_factory.call_args.kwargs["trust_env"] is trust_env


def test_memory_client_preserves_existing_proxy_default(monkeypatch):
    client_factory = Mock()
    monkeypatch.setattr("agent_memory.client.httpx.Client", client_factory)
    MemoryClient("http://127.0.0.1:18787", "private-access-token")
    assert client_factory.call_args.kwargs["trust_env"] is True
