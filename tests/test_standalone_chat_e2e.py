import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_memory import desktop
from agent_memory.chat_config import ChatConfiguration, save_chat_configuration
from agent_memory.desktop_connection import open_client
from agent_memory.local_profile import ensure_profile, load_profile
from agent_memory.runtime_client import RuntimeClient

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("MEMORY_DESKTOP_E2E") != "1",
    reason="Set MEMORY_DESKTOP_E2E=1 to verify standalone chat against an isolated Docker database",
)]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_chat_processes_recall_restart_deduplicate_and_block_unknown_generation(tmp_path):
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer local-fake-provider-key"
            document = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(document)
            current = document["messages"][-1]["content"]
            historical = document["messages"][-2]["content"]
            answer = "Understood."
            if current == "What is the name of my telescope?":
                answer = "Aurora Maple." if "Aurora Maple" in historical else "Unknown."
            truncated = current == "Exercise the truncated reply."
            body = json.dumps({
                "choices": [{"finish_reason": "length" if truncated else "stop",
                             "message": {"role": "assistant", "content": answer}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 3, "total_tokens": 103},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = ensure_profile(tmp_path / "standalone profile", api_port=free_port(), database_port=free_port())
    save_chat_configuration(ChatConfiguration(
        f"http://127.0.0.1:{server.server_port}/v1", "local-test-model", "local-fake-provider-key",
    ), profile.directory)
    executable = os.environ.get("MEMORY_CHAT_E2E_PYTHON", sys.executable)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("MEMORY_", "COMPOSE_")) and key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", NO_PROXY="",
                       MEMORY_DATABASE_URL="postgresql://wrong:wrong@127.0.0.1:1/wrong")

    def chat(conversation, *arguments):
        return subprocess.run([
            executable, "-m", "agent_memory.cli", "desktop", "chat", "--directory", str(profile.directory),
            "--conversation", conversation, *arguments,
        ], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=180)

    try:
        setup = subprocess.run([
            executable, "-m", "agent_memory.cli", "desktop", "setup", "--non-interactive",
            "--directory", str(profile.directory),
        ], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=180)
        assert setup.returncode == 0, setup.stderr
        assert json.loads(setup.stdout)["brain_id"] == profile.brain_id
        assert not calls
        first = chat("first", "--message", "My telescope is named Aurora Maple.", "--message-id", "event-1")
        assert first.returncode == 0, first.stderr
        assert first.stdout == "Understood.\n"
        assert len(calls) == 1
        progress = subprocess.run([
            executable, "-m", "agent_memory.cli", "desktop", "learning-status",
            "--directory", str(profile.directory),
        ], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
        assert progress.returncode == 0, progress.stderr
        assert json.loads(progress.stdout)["provider_state"] == "not_configured"
        assert json.loads(progress.stdout)["open_window_count"] == 0
        assert len(calls) == 1
        with open_client(profile.directory, start_service=False) as memory:
            assert len(memory.dashboard()["events"]) == 2
            original_sources = {event["event_id"] for event in memory.dashboard()["events"]}
        assert desktop.stop(profile.directory)["data_preserved"]
        save_chat_configuration(ChatConfiguration(
            f"http://127.0.0.1:{server.server_port}/v1", "replacement-test-model", "local-fake-provider-key",
        ), profile.directory)
        second = chat("second", "--message", "What is the name of my telescope?", "--message-id", "event-2")
        assert second.returncode == 0, second.stderr
        assert second.stdout == "Aurora Maple.\n"
        assert [call["model"] for call in calls] == ["local-test-model", "replacement-test-model"]
        with open_client(profile.directory, start_service=False) as memory:
            assert original_sources <= {event["event_id"] for event in memory.dashboard()["events"]}
            assert load_profile(profile.directory).brain_id == profile.brain_id
        assert "Aurora Maple" in calls[1]["messages"][-2]["content"]
        replay = chat("second", "--message", "What is the name of my telescope?", "--message-id", "event-2")
        assert replay.returncode == 0, replay.stderr
        assert replay.stdout == ""
        assert "already completed" in replay.stderr
        assert len(calls) == 2
        changed = chat("second", "--message", "A different message.", "--message-id", "event-2")
        assert changed.returncode == 1
        assert changed.stdout == ""
        assert "message_identity_conflict" in changed.stderr
        assert not list((profile.directory / "sessions").rglob("pending.json"))
        assert load_profile(profile.directory).brain_id == profile.brain_id
        with open_client(profile.directory, start_service=False) as memory:
            assert len(memory.brains()["brains"]) == 1
            assert len(memory.dashboard()["events"]) == 4
        failed = chat("second", "--message", "Exercise the truncated reply.", "--message-id", "event-3")
        assert failed.returncode == 1
        assert failed.stdout == ""
        assert "incomplete_response" in failed.stderr
        resumed = chat("second", "--resume")
        assert resumed.returncode == 1
        assert resumed.stdout == ""
        assert "generation_uncertain" in resumed.stderr
        assert len(calls) == 3
        pending_files = list((profile.directory / "sessions").rglob("pending.json"))
        assert len(pending_files) == 1
        pending_turn = json.loads(pending_files[0].read_text())["turn_id"]
        unacknowledged = chat("second", "--cancel", pending_turn)
        assert unacknowledged.returncode == 1
        assert "acknowledgement_required" in unacknowledged.stderr
        cancelled = chat("second", "--cancel", pending_turn, "--acknowledge-uncertain")
        assert cancelled.returncode == 0, cancelled.stderr
        assert cancelled.stdout == ""
        assert "remains unknown" in cancelled.stderr
        assert len(calls) == 3
        assert not pending_files[0].exists()
        with open_client(profile.directory, start_service=False) as memory:
            journal = RuntimeClient(memory)
            row = journal.get(pending_turn)
            assert row["stage"] == "failed"
            assert row["failure_code"] == "generation_uncertain"
            sources = journal.sources(pending_turn)
            assert len(sources["input_events"]) == 1
            assert sources["input_events"][0]["observed"]["content"]["text"] == "Exercise the truncated reply."
            assert not row["output_event_refs"]
            assert len(memory.dashboard()["events"]) == 5
        continued = chat("second", "--message", "Continue the conversation.", "--message-id", "event-4")
        assert continued.returncode == 0, continued.stderr
        assert continued.stdout == "Understood.\n"
        assert len(calls) == 4
        logs = (profile.directory / "service.log").read_text()
        for text in ("Aurora Maple", "Exercise the truncated reply", "local-fake-provider-key"):
            assert text not in logs
            for path in (profile.directory / "sessions").rglob("*.json"):
                assert text not in path.read_text()
    finally:
        try:
            try:
                desktop.stop(profile.directory)
            finally:
                desktop._compose(profile, "down", "--volumes")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
