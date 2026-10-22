import json
import stat

import pytest

from agent_memory.cli import initialize


def test_initialization_preserves_existing_configuration_and_limits_permissions(tmp_path):
    initialize(tmp_path)
    original = (tmp_path / ".env").read_bytes()
    with pytest.raises(SystemExit):
        initialize(tmp_path)
    assert (tmp_path / ".env").read_bytes() == original
    for name in (".env", ".local/keys.json", ".local/access-token"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600
    token = (tmp_path / ".local/access-token").read_text().strip()
    document = (tmp_path / ".local/keys.json").read_text()
    assert token not in document
    assert len(json.loads(document)) == 1
