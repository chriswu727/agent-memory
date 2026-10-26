import json
import logging
import os
import stat
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from test_desktop_chat import arguments
from test_semantic import batch, encoded, event, proposals
from test_semantic_provider import completion

from agent_memory import api, config, desktop, desktop_cli, semantic_provider, storage
from agent_memory.cognition_config import (
    CONFIGURATION_FILE,
    CognitionConfigurationError,
    CognitionPolicyConfiguration,
    load_cognition_policy,
    save_cognition_policy,
)
from agent_memory.config import Settings, configured_service
from agent_memory.local_profile import configure_cognition, ensure_profile, load_profile
from agent_memory.semantic import SemanticPolicy

CUSTOM = {"max_wall_time_ms": 240_000, "max_output_tokens": 16384, "max_response_bytes": 65536}
PUBLIC_CUSTOM = {**CUSTOM, "reasoning_effort": None, "max_envelope_bytes": 65536}
FLAGS = ("--max-wall-time-ms", "240000", "--max-output-tokens", "16384", "--max-response-bytes", "65536")


@pytest.fixture
def policy_env(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for key in tuple(os.environ):
        if key.startswith(("MEMORY_COGNITION_", "MEMORY_EMBEDDING_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("MEMORY_DATABASE_URL", "unused")
    return monkeypatch


def test_policy_defaults_are_unchanged_and_do_not_enable_a_provider(policy_env):
    default = Settings.from_env()
    assert default.semantic_policy() == SemanticPolicy()
    for key, value in CUSTOM.items():
        policy_env.setenv("MEMORY_COGNITION_" + key.upper(), str(value))
    changed = Settings.from_env()
    assert changed.cognition_base_url is None
    assert changed.semantic_policy() == SemanticPolicy(**CUSTOM)
    assert changed.semantic_policy().max_model_calls == default.semantic_policy().max_model_calls == 2


@pytest.mark.parametrize("field,cap", [("max_wall_time_ms", 300000), ("max_output_tokens", 32768),
                                     ("max_response_bytes", 65536)])
@pytest.mark.parametrize("value", [0, -1, True, 1.0, "123", None, {}, []])
def test_direct_limits_reject_wrong_types_and_values(field, cap, value):
    with pytest.raises(ValueError):
        Settings(database_url="unused", **{"cognition_" + field: value})
    with pytest.raises(CognitionConfigurationError):
        CognitionPolicyConfiguration(**{field: value})
    with pytest.raises(ValueError):
        Settings(database_url="unused", **{"cognition_" + field: cap + 1})


@pytest.mark.parametrize("field", CUSTOM)
@pytest.mark.parametrize("value", ["", "-1", " 1", "1.0", "true", "١٢", "9" * 10000, "private-invalid"])
def test_invalid_environment_values_are_redacted(policy_env, field, value):
    policy_env.setenv("MEMORY_COGNITION_" + field.upper(), value)
    with pytest.raises(ValueError) as caught:
        Settings.from_env()
    assert "private-invalid" not in str(caught.value)


def test_private_policy_round_trip_preserves_profile_v1_and_ignores_ambient_environment(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "profile")
    original = (profile.directory / "profile.json").read_bytes()
    assert load_cognition_policy(profile.directory) == CognitionPolicyConfiguration()
    assert not (profile.directory / CONFIGURATION_FILE).exists()
    saved = save_cognition_policy(CognitionPolicyConfiguration(**CUSTOM), profile.directory)
    assert load_cognition_policy(profile.directory) == saved
    assert (profile.directory / "profile.json").read_bytes() == original
    assert json.loads(original)["version"] == 1
    monkeypatch.setenv("MEMORY_COGNITION_MAX_OUTPUT_TOKENS", "1")
    assert load_profile(profile.directory).settings().semantic_policy() == SemanticPolicy(**CUSTOM)
    document = json.loads((profile.directory / CONFIGURATION_FILE).read_bytes())
    assert document == {"version": 2, **PUBLIC_CUSTOM}
    if os.name != "nt":
        assert stat.S_IMODE((profile.directory / CONFIGURATION_FILE).stat().st_mode) == 0o600
        assert stat.S_IMODE(profile.directory.stat().st_mode) == 0o700


@pytest.mark.parametrize("document", [
    {}, [], {"version": 1, **CUSTOM, "unknown": "private-content"}, {"version": True, **CUSTOM},
    {"version": 2, **CUSTOM}, {"version": 1, **CUSTOM, "max_output_tokens": True},
    {"version": 1, **CUSTOM, "max_response_bytes": 65537}, {"version": 1, **CUSTOM, "max_wall_time_ms": None},
])
def test_malformed_saved_policy_is_never_silently_replaced_by_defaults(tmp_path, document):
    profile = ensure_profile(tmp_path / "profile")
    path = profile.directory / CONFIGURATION_FILE
    path.write_text(json.dumps(document))
    with pytest.raises(CognitionConfigurationError, match="malformed"):
        load_cognition_policy(profile.directory)
    with pytest.raises(CognitionConfigurationError):
        profile.settings()
    assert json.loads(path.read_text()) == document


@pytest.mark.parametrize("content", [b"{" + b"x" * 4096, b"\xff", b'{"version":1,"version":1}',
                                      b"[" * 1500])
def test_bounded_invalid_private_json_does_not_echo_content(tmp_path, content):
    profile = ensure_profile(tmp_path / "profile")
    (profile.directory / CONFIGURATION_FILE).write_bytes(content)
    with pytest.raises(CognitionConfigurationError) as caught:
        load_cognition_policy(profile.directory)
    assert caught.value.code == "cognition_policy_invalid"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_unsafe_policy_paths_cannot_be_read_or_overwritten(tmp_path, kind):
    profile = ensure_profile(tmp_path / "profile")
    original = tmp_path / "original"
    original.write_text("private-original")
    path = profile.directory / CONFIGURATION_FILE
    if kind == "symlink":
        path.symlink_to(original)
    elif kind == "hardlink":
        path.hardlink_to(original)
    else:
        path.mkdir()
    for operation in (lambda: load_cognition_policy(profile.directory),
                      lambda: save_cognition_policy(CognitionPolicyConfiguration(**CUSTOM), profile.directory)):
        with pytest.raises(CognitionConfigurationError) as caught:
            operation()
        assert caught.value.code == "cognition_policy_unavailable"
    assert original.read_text() == "private-original"


def test_failed_atomic_write_keeps_prior_policy(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "profile")
    save_cognition_policy(CognitionPolicyConfiguration(), profile.directory)
    before = (profile.directory / CONFIGURATION_FILE).read_bytes()

    def fail(*args):
        raise OSError("private-failure")

    monkeypatch.setattr("agent_memory.cognition_config.os.replace", fail)
    with pytest.raises(CognitionConfigurationError):
        save_cognition_policy(CognitionPolicyConfiguration(**CUSTOM), profile.directory)
    assert (profile.directory / CONFIGURATION_FILE).read_bytes() == before
    assert not list(profile.directory.glob(".cognition-policy-*.tmp"))


def test_public_cli_saves_only_limits_and_reports_restart_without_network(tmp_path, capsys):
    profile = ensure_profile(tmp_path / "profile")
    profile = configure_cognition(profile, "https://provider.test/v1", "model", "private-provider-key")
    desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory), *FLAGS))
    output = capsys.readouterr()
    assert json.loads(output.out) == {"policy": PUBLIC_CUSTOM, "scope": "semantic_orientation_and_learning",
                                     "restart_required_if_running": True}
    assert "private-provider-key" not in output.out + output.err
    desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory), *FLAGS))
    assert json.loads(capsys.readouterr().out)["restart_required_if_running"] is False
    desktop_cli.dispatch(arguments("cognition-policy", "--directory", str(profile.directory)))
    assert json.loads(capsys.readouterr().out)["policy"] == PUBLIC_CUSTOM


def test_cli_can_repair_malformed_policy_but_invalid_input_leaves_it_unchanged(tmp_path, capsys):
    profile = ensure_profile(tmp_path / "profile")
    path = profile.directory / CONFIGURATION_FILE
    path.write_text("private-malformed")
    flags = list(FLAGS)
    flags[1] = "0"
    with pytest.raises(SystemExit):
        desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory), *flags))
    assert path.read_text() == "private-malformed"
    assert "private-malformed" not in capsys.readouterr().err
    desktop_cli.dispatch(arguments("configure-cognition-policy", "--directory", str(profile.directory), *FLAGS))
    assert json.loads(capsys.readouterr().out)["restart_required_if_running"] is True
    assert load_cognition_policy(profile.directory) == CognitionPolicyConfiguration(**CUSTOM)


def test_changed_effective_policy_requires_restart_and_supervisor_freezes_api_worker_settings(tmp_path, monkeypatch):
    profile = ensure_profile(tmp_path / "profile")
    supervisor = desktop.LocalSupervisor(profile, logging.getLogger("policy-test"))
    before = supervisor.configuration
    save_cognition_policy(CognitionPolicyConfiguration(**CUSTOM), profile.directory)
    monkeypatch.setattr(desktop, "_probe", lambda _: {"configuration": before, "ready": True, "state": "ready"})
    assert desktop.status(profile.directory)["state"] == "restart_required"
    assert supervisor.configuration == before != desktop._fingerprint(profile)
    supplied = []

    @contextmanager
    def factory(settings):
        supplied.append(settings)
        def stop(_):
            supervisor.stopped.set()
            return []
        yield SimpleNamespace(list_brains=stop)

    def app(settings):
        supplied.append(settings)
        return FastAPI()

    monkeypatch.setattr(desktop, "configured_service", factory)
    monkeypatch.setattr(api, "create_app", app)
    supervisor.work()
    supervisor.app()
    assert supplied == [supervisor.settings, supervisor.settings]
    assert all(settings.semantic_policy() == SemanticPolicy() for settings in supplied)
    restarted = desktop.LocalSupervisor(load_profile(profile.directory), supervisor.logger)
    assert restarted.settings.semantic_policy() == SemanticPolicy(**CUSTOM)
    assert restarted.configuration == desktop._fingerprint(profile)


def test_configured_service_passes_public_policy_to_real_provider_request(monkeypatch):
    observed = []
    original = semantic_provider.ChatCompletionsSemanticProvider
    monkeypatch.setattr(storage.Database, "check_compatibility", lambda *args, **kwargs: None)

    def handle(request):
        observed.append(request)
        return httpx.Response(200, json=completion(encoded(proposals())))

    monkeypatch.setattr(semantic_provider, "ChatCompletionsSemanticProvider", lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(handle)))
    settings = Settings(database_url="unused", cognition_base_url="https://provider.test/v1",
                        cognition_api_key="private-key", cognition_model="strong-model",
                        **CognitionPolicyConfiguration(**CUSTOM).settings())
    with configured_service(settings) as service:
        assert asdict(service.semantic_processor.policy) == asdict(SemanticPolicy(**CUSTOM))
        result = service.semantic_processor.process(batch(event("I chose a blue mug.")), reserve=lambda **_: None)
    assert result.status == "completed" and result.model_calls == 1
    assert json.loads(observed[0].content)["max_tokens"] == CUSTOM["max_output_tokens"]
    assert "reasoning_effort" not in json.loads(observed[0].content)
    assert 239 < observed[0].extensions["timeout"]["read"] <= 240


def test_configured_decoded_response_limit_is_enforced_without_extra_calls(monkeypatch):
    monkeypatch.setattr(storage.Database, "check_compatibility", lambda *args, **kwargs: None)
    calls = []
    original = semantic_provider.ChatCompletionsSemanticProvider

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=completion(encoded(proposals())))

    monkeypatch.setattr(semantic_provider, "ChatCompletionsSemanticProvider", lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(handle)))
    settings = Settings(database_url="unused", cognition_base_url="https://provider.test/v1",
                        cognition_api_key="private-key", cognition_model="model", cognition_max_response_bytes=1)
    with configured_service(settings) as service:
        result = service.semantic_processor.process(batch(event("I chose a blue mug.")), reserve=lambda **_: None)
    assert result.status == "invalid_response" and len(calls) == 1
