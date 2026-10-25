import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_memory import desktop
from agent_memory.build_identity import package_manifest
from agent_memory.client import MemoryClient
from agent_memory.local_profile import ensure_profile

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("MEMORY_DESKTOP_E2E") != "1",
    reason="Set MEMORY_DESKTOP_E2E=1 to verify independent package upgrades with isolated Docker",
)]

_SCRIPT = """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_memory import desktop
from agent_memory.local_profile import load_profile
directory, operation = Path(sys.argv[2]), sys.argv[3]
try:
    if operation == 'prepare':
        desktop._prepare_database(load_profile(directory))
    elif operation == 'run':
        desktop.run(directory)
    else:
        print(json.dumps(getattr(desktop, operation)(directory)))
except desktop.DesktopError as error:
    print(json.dumps({'code': error.code}))
    raise SystemExit(1)
"""


def test_independent_builds_detect_staleness_upgrade_and_reject_downgrade_without_data_loss(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    first = tmp_path / "first package"
    package = first / "agent_memory"
    for name, content in package_manifest(repository / "src/agent_memory", repository).items():
        destination = package / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    second = tmp_path / "second package"
    shutil.copytree(first, second)
    (second / "agent_memory/resources/migrations/999_upgrade_probe.sql").write_text(
        "CREATE TABLE memory_upgrade_probe(id int PRIMARY KEY);\n",
    )
    profile = ensure_profile(tmp_path / "upgrade profile")
    environment = desktop._environment()
    processes = []

    def invoke(root, operation, *, wait=True):
        command = [sys.executable, "-I", "-c", _SCRIPT, str(root), str(profile.directory), operation]
        if not wait:
            process = subprocess.Popen(command, cwd=tmp_path, env=environment,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            return process
        return subprocess.run(command, cwd=tmp_path, env=environment,
                              capture_output=True, text=True, timeout=180)

    def ready(process):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            state = desktop._probe(profile)
            if state and state.get("ready"):
                return state
            assert process.poll() is None, "The isolated service exited before becoming ready."
            time.sleep(0.1)
        pytest.fail("The isolated service did not become ready.")

    try:
        preparation = invoke(first, "prepare")
        assert preparation.returncode == 0, preparation.stdout
        initial = ready(invoke(first, "run", wait=False))
        with MemoryClient(profile.base_url, profile.api_key, profile.brain_id, trust_env=False) as memory:
            memory.observe("A source that must survive package upgrade.", source_event_key="upgrade-input",
                           conversation_id="upgrade", branch_id="main")
            before = memory.dashboard()["events"]
        outdated = invoke(second, "status")
        assert outdated.returncode == 0
        assert json.loads(outdated.stdout)["state"] == "restart_required"
        blocked = invoke(second, "start")
        assert blocked.returncode == 1
        assert json.loads(blocked.stdout)["code"] == "restart_required"
        assert desktop._probe(profile)["instance_id"] == initial["instance_id"]
        assert invoke(second, "stop").returncode == 0
        processes[0].wait(timeout=30)
        preparation = invoke(second, "prepare")
        assert preparation.returncode == 0, preparation.stdout
        updated = ready(invoke(second, "run", wait=False))
        assert updated["version"] == initial["version"]
        assert updated["build_id"] != initial["build_id"]
        assert updated["schema_id"] != initial["schema_id"]
        assert updated["brain_id"] == initial["brain_id"]
        assert json.loads(invoke(second, "start").stdout)["ready"] is True
        rejected = invoke(first, "restart")
        assert rejected.returncode == 1
        assert json.loads(rejected.stdout)["code"] == "database_schema_incompatible"
        assert desktop._probe(profile)["instance_id"] == updated["instance_id"]
        with MemoryClient(profile.base_url, profile.api_key, profile.brain_id, trust_env=False) as memory:
            assert memory.dashboard()["events"] == before
            memory.observe("The upgraded service remains available.", source_event_key="upgrade-after",
                           conversation_id="upgrade", branch_id="main")
            assert len(memory.dashboard()["events"]) == len(before) + 1
    finally:
        try:
            desktop.stop(profile.directory)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=30)
            desktop._compose(profile, "down", "--volumes")
