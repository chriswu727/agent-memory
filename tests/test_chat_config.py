import json
import os
import stat
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace

import httpx
import pytest

from agent_memory import chat_config, local_profile
from agent_memory.chat_config import (
    ChatConfiguration,
    ChatConfigurationError,
    load_chat_configuration,
    save_chat_configuration,
    use_cognition_configuration,
)
from agent_memory.desktop import _fingerprint
from agent_memory.local_profile import configure_cognition, ensure_profile, load_profile


@pytest.fixture
def profile(tmp_path):
    return ensure_profile(tmp_path / "private profile")


@pytest.fixture
def configuration():
    return ChatConfiguration("https://chat.example/v1", "chat-model", "private-chat-key")


def assert_redacted(error):
    text = "".join(traceback.format_exception(error.value))
    assert "private-chat-key" not in text
    assert "private-filesystem-detail" not in text


def test_saved_chat_is_separate_from_identity_cognition_and_service_configuration(profile, configuration):
    profile = configure_cognition(profile, "https://memory.example/v1", "memory-model",
                                  "private-cognition-key", "enabled")
    before = (profile.directory / "profile.json").read_bytes()
    service_fingerprint = _fingerprint(profile)
    saved = save_chat_configuration(configuration, profile.directory)
    assert saved == configuration == load_chat_configuration(profile.directory)
    assert (profile.directory / "profile.json").read_bytes() == before
    assert load_profile(profile.directory) == profile
    assert _fingerprint(load_profile(profile.directory)) == service_fingerprint
    document = json.loads((profile.directory / "chat.json").read_text())
    assert document == {
        "version": 1, "base_url": "https://chat.example/v1", "model": "chat-model",
        "api_key": "private-chat-key", "thinking": None,
        "max_output_tokens": 4096, "timeout_seconds": 180, "max_response_bytes": 1048576,
    }
    assert "private-chat-key" not in repr(configuration)
    assert not list(profile.directory.glob(".chat-*.tmp"))


def test_chat_configuration_is_frozen(configuration):
    with pytest.raises(FrozenInstanceError):
        configuration.model = "other-model"


def test_cognition_is_never_an_implicit_chat_fallback(profile):
    profile = configure_cognition(profile, "https://memory.example/v1", "memory-model",
                                  "private-cognition-key", "disabled")
    before = (profile.directory / "profile.json").read_bytes()
    with pytest.raises(ChatConfigurationError) as error:
        load_chat_configuration(profile.directory)
    assert error.value.code == "chat_not_configured"
    copied = use_cognition_configuration(profile.directory)
    assert copied == ChatConfiguration("https://memory.example/v1", "memory-model",
                                       "private-cognition-key", "disabled")
    assert not (profile.directory / "chat.json").exists()
    assert (profile.directory / "profile.json").read_bytes() == before
    save_chat_configuration(replace(copied, max_output_tokens=128), profile.directory)
    assert load_chat_configuration(profile.directory).max_output_tokens == 128
    assert load_profile(profile.directory) == profile


def test_copying_cognition_does_not_replace_existing_chat(profile, configuration):
    profile = configure_cognition(profile, "https://memory.example", "memory-model", "memory-key")
    save_chat_configuration(configuration, profile.directory)
    before = (profile.directory / "chat.json").read_bytes()
    copied = use_cognition_configuration(profile.directory)
    with pytest.raises(ChatConfigurationError):
        replace(copied, max_output_tokens=True)
    assert (profile.directory / "chat.json").read_bytes() == before


def test_absent_cognition_is_an_explicit_safe_error(profile):
    with pytest.raises(ChatConfigurationError) as error:
        use_cognition_configuration(profile.directory)
    assert error.value.code == "cognition_not_configured"
    assert not (profile.directory / "chat.json").exists()


def test_loading_and_saving_do_not_read_environment_or_construct_http_clients(
    profile, configuration, monkeypatch, tmp_path,
):
    monkeypatch.setenv("MEMORY_CHAT_API_KEY", "wrong-chat-key")
    monkeypatch.setenv("MEMORY_COGNITION_API_KEY", "wrong-memory-key")
    monkeypatch.setattr("agent_memory.config.load_dotenv", lambda: pytest.fail("No dotenv lookup"))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("No HTTP client"))
    monkeypatch.setattr(local_profile, "default_profile_directory", lambda: profile.directory)
    monkeypatch.chdir(tmp_path)
    assert save_chat_configuration(configuration) == configuration
    assert load_chat_configuration() == configuration


def test_fingerprint_ignores_secret_rotation_but_binds_all_generation_parameters(configuration):
    digest = configuration.fingerprint
    assert len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
    assert replace(configuration, api_key="rotated-private-key").fingerprint == digest
    for change in (
        {"base_url": "https://other.example/v1"}, {"model": "another-model"},
        {"thinking": "disabled"}, {"max_output_tokens": 2048},
        {"timeout_seconds": 90.5}, {"max_response_bytes": 65536},
    ):
        assert replace(configuration, **change).fingerprint != digest


def test_generator_is_lazy_and_receives_only_explicit_configuration(configuration, monkeypatch):
    from agent_memory import chat_provider

    seen = []
    instance = object()

    def construct(*args, **kwargs):
        seen.append((args, kwargs))
        return instance

    monkeypatch.setattr(chat_provider, "ChatCompletionsGenerator", construct)
    configured = replace(configuration, thinking="enabled", max_output_tokens=77,
                         timeout_seconds=3.5, max_response_bytes=999)
    assert seen == []
    assert configured.generator() is instance
    assert seen == [((configured.base_url, configured.api_key, configured.model), {
        "thinking": "enabled", "max_output_tokens": 77,
        "timeout_seconds": 3.5, "max_response_bytes": 999,
    })]


@pytest.mark.parametrize("changes", [
    {"base_url": None}, {"base_url": ""}, {"base_url": "ftp://chat.example"},
    {"base_url": "https://private-chat-key@chat.example"},
    {"base_url": "https://chat.example?key=private-chat-key"},
    {"base_url": "https://chat.example?"}, {"base_url": "https://chat.example#"},
    {"base_url": "https://chat.example:0"}, {"base_url": "https://chat.example:99999"},
    {"base_url": "https://chat .example"}, {"base_url": "https://chat.example/\x7f"},
    {"base_url": "https://chat.example/\ud800"},
    {"model": None}, {"model": ""}, {"model": " model "}, {"model": "model\x7f"},
    {"model": "model\n"}, {"model": "\ud800"},
    {"api_key": ""}, {"api_key": "private-chat-key\n"}, {"api_key": "private chat key"},
    {"api_key": "非ASCII"}, {"thinking": True}, {"thinking": "auto"}, {"thinking": {}},
    {"max_output_tokens": True}, {"max_output_tokens": 0}, {"max_output_tokens": 1.5},
    {"max_response_bytes": False}, {"max_response_bytes": -1},
    {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": -1},
    {"timeout_seconds": "180"}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": float("inf")}, {"timeout_seconds": 10 ** 400},
])
def test_invalid_configuration_is_rejected_without_secret_details(configuration, changes):
    with pytest.raises(ChatConfigurationError) as error:
        replace(configuration, **changes)
    assert error.value.code == "chat_configuration_invalid"
    assert_redacted(error)


@pytest.mark.parametrize("operation", ["load", "save", "copy"])
def test_missing_profile_is_not_created(tmp_path, configuration, operation):
    missing = tmp_path / "missing"
    with pytest.raises(ChatConfigurationError) as error:
        if operation == "save":
            save_chat_configuration(configuration, missing)
        elif operation == "copy":
            use_cognition_configuration(missing)
        else:
            load_chat_configuration(missing)
    assert error.value.code == "chat_configuration_unavailable"
    assert not missing.exists()


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"version": 1.0},
    {"extra": "private-chat-key"}, {"model": ""}, {"max_output_tokens": True},
    {"timeout_seconds": float("nan")},
])
def test_invalid_stored_fields_are_not_repaired_or_replaced(profile, configuration, change):
    save_chat_configuration(configuration, profile.directory)
    path = profile.directory / "chat.json"
    document = json.loads(path.read_text())
    document.update(change)
    original = json.dumps(document).encode()
    path.write_bytes(original)
    with pytest.raises(ChatConfigurationError) as error:
        load_chat_configuration(profile.directory)
    assert error.value.code == "chat_configuration_invalid"
    assert_redacted(error)
    assert path.read_bytes() == original


@pytest.mark.parametrize("content", [
    b"{private-chat-key", b"\xff", b"null", b"[]", b"{}",
    b'{"version":1,"version":1}', b" " * 65537,
])
def test_malformed_or_oversized_chat_file_is_rejected(profile, content):
    path = profile.directory / "chat.json"
    path.write_bytes(content)
    with pytest.raises(ChatConfigurationError) as error:
        load_chat_configuration(profile.directory)
    assert error.value.code == "chat_configuration_invalid"
    assert_redacted(error)
    assert path.read_bytes() == content


def test_duplicate_configuration_key_is_rejected_even_when_the_values_match(profile, configuration):
    save_chat_configuration(configuration, profile.directory)
    path = profile.directory / "chat.json"
    original = path.read_text().replace('"model": "chat-model",',
                                        '"model": "chat-model", "model": "chat-model",')
    path.write_text(original)
    with pytest.raises(ChatConfigurationError, match="malformed"):
        load_chat_configuration(profile.directory)
    assert path.read_text() == original


def test_missing_optional_constructor_field_is_not_defaulted_when_loading(profile, configuration):
    save_chat_configuration(configuration, profile.directory)
    path = profile.directory / "chat.json"
    document = json.loads(path.read_text())
    del document["thinking"]
    path.write_text(json.dumps(document))
    with pytest.raises(ChatConfigurationError, match="malformed"):
        load_chat_configuration(profile.directory)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_chat_file_has_private_mode_and_loading_tightens_existing_mode(profile, configuration):
    save_chat_configuration(configuration, profile.directory)
    path = profile.directory / "chat.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(profile.directory.stat().st_mode) == 0o700
    path.chmod(0o644)
    assert load_chat_configuration(profile.directory) == configuration
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("operation", ["load", "save"])
def test_symlinked_chat_file_never_reads_or_replaces_the_target(
    profile, configuration, tmp_path, operation,
):
    target = tmp_path / "outside"
    target.write_text("private-chat-key")
    (profile.directory / "chat.json").symlink_to(target)
    with pytest.raises(ChatConfigurationError) as error:
        if operation == "save":
            save_chat_configuration(configuration, profile.directory)
        else:
            load_chat_configuration(profile.directory)
    assert error.value.code == "chat_configuration_unavailable"
    assert_redacted(error)
    assert target.read_text() == "private-chat-key"
    assert (profile.directory / "chat.json").is_symlink()


def test_symlinked_profile_directory_is_rejected(profile, configuration, tmp_path):
    link = tmp_path / "linked profile"
    link.symlink_to(profile.directory, target_is_directory=True)
    with pytest.raises(ChatConfigurationError):
        save_chat_configuration(configuration, link)
    assert not (profile.directory / "chat.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard-link restriction")
@pytest.mark.parametrize("operation", ["load", "save"])
def test_hardlinked_chat_file_cannot_be_read_or_replaced(profile, configuration, tmp_path, operation):
    save_chat_configuration(configuration, profile.directory)
    path = profile.directory / "chat.json"
    before = path.read_bytes()
    os.link(path, tmp_path / "linked chat")
    with pytest.raises(ChatConfigurationError, match="safely"):
        if operation == "save":
            save_chat_configuration(replace(configuration, model="other"), profile.directory)
        else:
            load_chat_configuration(profile.directory)
    assert path.read_bytes() == before == (tmp_path / "linked chat").read_bytes()


def test_nonregular_chat_path_is_rejected_without_modifying_it(profile, configuration):
    (profile.directory / "chat.json").mkdir()
    with pytest.raises(ChatConfigurationError, match="safely"):
        save_chat_configuration(configuration, profile.directory)
    assert (profile.directory / "chat.json").is_dir()


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_failure_before_atomic_replace_preserves_complete_existing_configuration(
    profile, configuration, monkeypatch, failure,
):
    save_chat_configuration(configuration, profile.directory)
    path = profile.directory / "chat.json"
    before = path.read_bytes()

    def fail(*args):
        raise OSError("private-filesystem-detail")

    monkeypatch.setattr(chat_config.os, failure, fail)
    with pytest.raises(ChatConfigurationError) as error:
        save_chat_configuration(replace(configuration, model="other-model"), profile.directory)
    assert error.value.code == "chat_configuration_unavailable"
    assert_redacted(error)
    assert path.read_bytes() == before
    assert not list(profile.directory.glob(".chat-*.tmp"))


def test_save_revalidates_a_tampered_configuration_before_writing(profile, configuration):
    save_chat_configuration(configuration, profile.directory)
    before = (profile.directory / "chat.json").read_bytes()
    object.__setattr__(configuration, "max_output_tokens", True)
    with pytest.raises(ChatConfigurationError):
        save_chat_configuration(configuration, profile.directory)
    assert (profile.directory / "chat.json").read_bytes() == before


def test_concurrent_saves_produce_one_complete_configuration_and_keep_profile(profile, configuration):
    before = (profile.directory / "profile.json").read_bytes()
    candidates = [replace(configuration, model=f"model-{i}", api_key=f"key-{i}") for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        saved = list(executor.map(lambda value: save_chat_configuration(value, profile.directory), candidates))
    assert saved == candidates
    assert load_chat_configuration(profile.directory) in candidates
    assert (profile.directory / "profile.json").read_bytes() == before
    assert not list(profile.directory.glob(".chat-*.tmp"))


def test_wrong_type_and_unknown_error_codes_do_not_echo_values(profile):
    invalid = {"api_key": "private-chat-key"}
    with pytest.raises(ChatConfigurationError) as error:
        save_chat_configuration(invalid, profile.directory)
    assert_redacted(error)
    code = invalid["api_key"]
    with pytest.raises(ValueError) as error:
        ChatConfigurationError(code)
    assert_redacted(error)


def test_required_provider_identity_has_no_invented_defaults():
    with pytest.raises(TypeError):
        ChatConfiguration(api_key="private-chat-key")
