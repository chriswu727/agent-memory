import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from membrain import desktop, embedded_database, host

PLUGIN = Path(__file__).parents[1] / "integrations" / "openclaw" / "membrain"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the OpenClaw plugin")
@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_openclaw_plugin_through_the_real_runtime(tmp_path):
    directory = tmp_path / "brain"
    environment = {**os.environ, "MEMBRAIN_PYTHON": sys.executable, "MEMBRAIN_DIRECTORY": str(directory),
                   "MEMBRAIN_SEMANTIC": "0"}
    try:
        host.start(directory)
        result = subprocess.run(["node", "--test", "membrain.test.js"], cwd=PLUGIN, env=environment,
                                capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "# pass 3" in result.stdout and "# skipped 0" in result.stdout
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass
