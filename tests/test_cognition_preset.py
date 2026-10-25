import json
from dataclasses import asdict

import pytest
from test_desktop_chat import arguments

from agent_memory import desktop, desktop_cli
from agent_memory.cognition_config import (
    CONFIGURATION_FILE,
    CognitionConfigurationError,
    CognitionPolicyConfiguration,
    cognition_policy_preset,
    load_cognition_policy,
    save_cognition_policy,
)
from agent_memory.local_profile import ensure_profile
from agent_memory.semantic import SemanticPolicy

EXPECTED = {"max_wall_time_ms": 240000, "max_output_tokens": 16384, "max_response_bytes": 65536}


def test_named_preset_saves_exact_private_policy_without_changing_defaults(tmp_path, capsys):
    profile = ensure_profile(tmp_path / "profile")
    original = (profile.directory / "profile.json").read_bytes()
    assert SemanticPolicy().max_wall_time_ms == 30000
    assert load_cognition_policy(profile.directory) == CognitionPolicyConfiguration()
    assert not (profile.directory / CONFIGURATION_FILE).exists()
    desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory),
                                   "--preset", "reasoning"))
    result = json.loads(capsys.readouterr().out)
    assert result["policy"] == EXPECTED and result["restart_required_if_running"] is True
    assert json.loads((profile.directory / CONFIGURATION_FILE).read_text()) == {"version": 1, **EXPECTED}
    assert (profile.directory / CONFIGURATION_FILE).stat().st_mode & 0o777 == 0o600
    assert (profile.directory / "profile.json").read_bytes() == original
    desktop_cli.dispatch(arguments("cognition-policy", "--directory", str(profile.directory)))
    assert json.loads(capsys.readouterr().out)["policy"] == EXPECTED
    assert asdict(cognition_policy_preset("reasoning")) == EXPECTED


@pytest.mark.parametrize("flags", [
    [], ["--max-wall-time-ms", "1"], ["--max-output-tokens", "2", "--max-response-bytes", "3"],
    ["--preset", "reasoning", "--max-wall-time-ms", "240000"],
    ["--preset", "reasoning", "--max-output-tokens", "16384"],
    ["--preset", "reasoning", "--max-response-bytes", "65536"],
])
def test_incomplete_or_mixed_options_never_modify_policy(tmp_path, capsys, flags):
    profile = ensure_profile(tmp_path / "profile")
    save_cognition_policy(CognitionPolicyConfiguration(), profile.directory)
    before = (profile.directory / CONFIGURATION_FILE).read_bytes()
    with pytest.raises(SystemExit):
        desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory), *flags))
    assert (profile.directory / CONFIGURATION_FILE).read_bytes() == before
    assert not capsys.readouterr().out


def test_unknown_preset_is_rejected_by_parser_and_configuration():
    with pytest.raises(SystemExit):
        arguments("configure-cognition-policy", "--preset", "automatic")
    with pytest.raises(SystemExit):
        arguments("setup", "--cognition-preset", "automatic")
    with pytest.raises(CognitionConfigurationError):
        cognition_policy_preset("automatic")


@pytest.mark.parametrize("selected", [False, True])
def test_setup_requires_explicit_preset_and_reuses_existing_restart_path(tmp_path, monkeypatch, capsys, selected):
    profile = ensure_profile(tmp_path / "profile")
    previous = CognitionPolicyConfiguration(max_wall_time_ms=12345)
    save_cognition_policy(previous, profile.directory)
    restarted = []
    monkeypatch.setattr(desktop, "status", lambda _: {"state": "restart_required" if selected else "ready"})
    monkeypatch.setattr(desktop, "restart", lambda directory: restarted.append(directory) or {"state": "ready"})
    monkeypatch.setattr(desktop, "start", lambda _: {"state": "ready"})
    flags = ["--cognition-preset", "reasoning"] if selected else []
    desktop_cli.dispatch(arguments("setup", "--directory", str(profile.directory), "--non-interactive", *flags))
    result = json.loads(capsys.readouterr().out)
    assert load_cognition_policy(profile.directory) == (cognition_policy_preset("reasoning") if selected else previous)
    assert restarted == ([profile.directory] if selected else [])
    assert result.get("cognition_policy") == (EXPECTED if selected else None)


def test_setup_no_start_saves_preset_without_launch(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "profile")
    monkeypatch.setattr(desktop, "status", lambda _: {"state": "stopped"})
    monkeypatch.setattr(desktop, "start", lambda _: pytest.fail("Must not start"))
    monkeypatch.setattr(desktop, "restart", lambda _: pytest.fail("Must not restart"))
    result = desktop_cli._setup(arguments("setup", "--directory", str(profile.directory), "--non-interactive",
                                          "--no-start", "--cognition-preset", "reasoning"))
    assert result["state"] == "stopped" and result["cognition_policy"] == EXPECTED
