import json
from dataclasses import asdict, replace
from typing import get_args

import httpx
import pytest
from test_cognition_policy import CUSTOM, FLAGS
from test_cognition_policy import policy_env as policy_env
from test_desktop_chat import arguments
from test_semantic import batch, encoded, event, proposals
from test_semantic_provider import REQUEST, completion

from membrain import desktop, desktop_cli, semantic_provider, storage
from membrain.chat_provider import ReasoningEffort
from membrain.cognition_config import (
    CONFIGURATION_FILE,
    CognitionConfigurationError,
    CognitionPolicyConfiguration,
    load_cognition_policy,
    save_cognition_policy,
)
from membrain.config import Settings, configured_service
from membrain.local_profile import configure_cognition, ensure_profile, load_profile
from membrain.semantic import SemanticPolicy
from membrain.semantic_provider import SemanticProviderError


@pytest.mark.parametrize("effort", [None, *get_args(ReasoningEffort)])
def test_environment_exposes_optional_effort_and_separate_envelope_cap(policy_env, effort):
    if effort is not None:
        policy_env.setenv("MEMORY_COGNITION_REASONING_EFFORT", effort)
    policy_env.setenv("MEMORY_COGNITION_MAX_ENVELOPE_BYTES", "1048576")
    settings = Settings.from_env()
    assert settings.cognition_reasoning_effort == effort
    assert settings.cognition_max_envelope_bytes == 1048576
    assert settings.semantic_policy() == SemanticPolicy()
    assert settings.cognition_base_url is None


def test_transport_defaults_preserve_provider_behavior(policy_env):
    settings = Settings.from_env()
    assert settings.cognition_reasoning_effort is None
    assert settings.cognition_max_envelope_bytes == 65536


@pytest.mark.parametrize("value", ["", "LOW", "private-invalid", True, [], {}])
def test_effort_validation_is_shared_and_redacts_values(value):
    for create in (lambda: Settings(database_url="unused", cognition_reasoning_effort=value),
                   lambda: CognitionPolicyConfiguration(reasoning_effort=value)):
        with pytest.raises(ValueError) as caught:
            create()
        assert "private-invalid" not in str(caught.value)


@pytest.mark.parametrize("value", [0, -1, 1048577, True, 1.0, "65536", None])
def test_envelope_bounds_match_provider_limits(value):
    with pytest.raises(ValueError):
        Settings(database_url="unused", cognition_max_envelope_bytes=value)
    with pytest.raises(CognitionConfigurationError):
        CognitionPolicyConfiguration(max_envelope_bytes=value)


@pytest.mark.parametrize("value", ["", "-1", "1.0", " 2", "١٢", "1048577", "private-invalid"])
def test_malformed_envelope_environment_fails_before_provider_creation(policy_env, value):
    policy_env.setenv("MEMORY_COGNITION_MAX_ENVELOPE_BYTES", value)
    with pytest.raises(ValueError) as caught:
        Settings.from_env()
    assert "private-invalid" not in str(caught.value)


def test_settings_reject_disabled_thinking_with_explicit_effort():
    with pytest.raises(ValueError, match="cannot be set"):
        Settings(database_url="unused", cognition_base_url="https://provider.test/v1",
                 cognition_api_key="private-key", cognition_model="unchanged-model",
                 cognition_thinking="disabled", cognition_reasoning_effort="low")


def test_old_policy_loads_without_rewriting_and_new_policy_round_trips(tmp_path):
    profile = ensure_profile(tmp_path / "profile")
    path = profile.directory / CONFIGURATION_FILE
    legacy = json.dumps({"version": 1, **CUSTOM}).encode()
    path.write_bytes(legacy)
    original_profile = (profile.directory / "profile.json").read_bytes()
    old = load_cognition_policy(profile.directory)
    assert old == CognitionPolicyConfiguration(**CUSTOM)
    assert path.read_bytes() == legacy
    selected = replace(old, reasoning_effort="low", max_envelope_bytes=1048576)
    save_cognition_policy(selected, profile.directory)
    assert json.loads(path.read_text()) == {"version": 2, **asdict(selected)}
    assert load_cognition_policy(profile.directory) == selected
    settings = load_profile(profile.directory).settings()
    assert settings.cognition_reasoning_effort == "low"
    assert settings.cognition_max_envelope_bytes == 1048576
    assert settings.semantic_policy() == SemanticPolicy(**CUSTOM)
    assert (profile.directory / "profile.json").read_bytes() == original_profile


def test_cli_changes_only_explicit_options_and_can_remove_effort(tmp_path, capsys):
    profile = ensure_profile(tmp_path / "profile")
    original = CognitionPolicyConfiguration(max_wall_time_ms=12345, max_output_tokens=333, max_response_bytes=2000)
    save_cognition_policy(original, profile.directory)
    desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory),
                                   "--reasoning-effort", "low", "--max-envelope-bytes", "1048576"))
    assert json.loads(capsys.readouterr().out)["restart_required_if_running"]
    assert load_cognition_policy(profile.directory) == replace(original, reasoning_effort="low", max_envelope_bytes=1048576)
    for options in (FLAGS, ("--preset", "reasoning")):
        desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory), *options))
        capsys.readouterr()
        assert load_cognition_policy(profile.directory) == CognitionPolicyConfiguration(
            **CUSTOM, reasoning_effort="low", max_envelope_bytes=1048576)
    desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory),
                                   "--reasoning-effort", "default"))
    capsys.readouterr()
    assert load_cognition_policy(profile.directory) == CognitionPolicyConfiguration(**CUSTOM, max_envelope_bytes=1048576)


def test_setup_preset_preserves_explicit_transport_options(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "profile")
    save_cognition_policy(CognitionPolicyConfiguration(reasoning_effort="high", max_envelope_bytes=1048576), profile.directory)
    monkeypatch.setattr(desktop, "status", lambda _: {"state": "stopped"})
    result = desktop_cli._setup(arguments("setup", "--directory", str(profile.directory), "--non-interactive",
                                          "--no-start", "--cognition-preset", "reasoning"))
    expected = CognitionPolicyConfiguration(**CUSTOM, reasoning_effort="high", max_envelope_bytes=1048576)
    assert load_cognition_policy(profile.directory) == expected
    assert result["cognition_policy"] == asdict(expected)


@pytest.mark.parametrize("field,value", [("reasoning_effort", "low"), ("max_envelope_bytes", 1048576)])
def test_transport_only_change_triggers_desktop_restart(tmp_path, monkeypatch, field, value):
    profile = ensure_profile(tmp_path / "profile")
    before = desktop._fingerprint(profile)
    save_cognition_policy(CognitionPolicyConfiguration(**{field: value}), profile.directory)
    assert profile.settings().semantic_policy() == SemanticPolicy()
    assert desktop._fingerprint(profile) != before
    monkeypatch.setattr(desktop, "_probe", lambda _: {"configuration": before, "ready": True, "state": "ready"})
    assert desktop.status(profile.directory)["state"] == "restart_required"


def test_policy_cli_rejects_disabled_thinking_before_touching_files(tmp_path, capsys):
    profile = ensure_profile(tmp_path / "profile")
    profile = configure_cognition(profile, "https://provider.test/v1", "model", "private-key", thinking="disabled")
    save_cognition_policy(CognitionPolicyConfiguration(), profile.directory)
    paths = [profile.directory / name for name in ("profile.json", CONFIGURATION_FILE)]
    original = [path.read_bytes() for path in paths]
    with pytest.raises(SystemExit):
        desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory),
                                       "--reasoning-effort", "low"))
    assert not capsys.readouterr().out
    assert [path.read_bytes() for path in paths] == original


def test_disabling_thinking_rejects_existing_effort_before_saving_profile(tmp_path):
    profile = ensure_profile(tmp_path / "profile")
    profile = configure_cognition(profile, "https://provider.test/v1", "model", "private-key", thinking="enabled")
    save_cognition_policy(CognitionPolicyConfiguration(reasoning_effort="low"), profile.directory)
    paths = [profile.directory / name for name in ("profile.json", CONFIGURATION_FILE)]
    original = [path.read_bytes() for path in paths]
    with pytest.raises(CognitionConfigurationError):
        configure_cognition(profile, "https://provider.test/v1", "other-model", "different-private-key", thinking="disabled")
    assert [path.read_bytes() for path in paths] == original


@pytest.mark.parametrize("envelope", [65536, 1048576])
def test_configured_service_wires_effort_and_envelope_without_changing_content_limit(monkeypatch, envelope):
    monkeypatch.setattr(storage.Database, "check_compatibility", lambda *args, **kwargs: None)
    original = semantic_provider.ChatCompletionsSemanticProvider
    requests = []
    content = encoded(proposals())

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(content, reasoning_content="x" * 70000))

    monkeypatch.setattr(semantic_provider, "ChatCompletionsSemanticProvider", lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(handle)))
    settings = Settings(database_url="unused", cognition_base_url="https://provider.test/v1",
                        cognition_api_key="private-key", cognition_model="unchanged-model",
                        cognition_thinking="enabled", cognition_reasoning_effort="low",
                        cognition_max_envelope_bytes=envelope, cognition_max_response_bytes=1)
    with configured_service(settings) as service:
        if envelope == 65536:
            with pytest.raises(SemanticProviderError) as caught:
                service.semantic_processor.propose(REQUEST)
            assert caught.value.code == "response_too_large"
        else:
            assert service.semantic_processor.propose(REQUEST) == content
            result = service.semantic_processor.process(batch(event("I chose a blue mug.")), reserve=lambda **_: None)
            assert result.status == "invalid_response"
        assert service.semantic_processor.policy.max_response_bytes == 1
        assert service.semantic_processor.policy.max_output_tokens == 8192
    assert len(requests) == (1 if envelope == 65536 else 2)
    assert all(request["reasoning_effort"] == "low" and request["model"] == "unchanged-model" for request in requests)
