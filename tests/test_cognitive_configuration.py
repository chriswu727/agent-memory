import hashlib
import traceback
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from membrain import api, cli, config, embeddings, semantic_provider, service, storage
from membrain.config import Settings, configured_service
from membrain.helpers import Principal


@pytest.fixture
def cognitive_env(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for prefix in ("MEMORY_COGNITION", "MEMORY_EMBEDDING"):
        for key in ("BASE_URL", "API_KEY", "MODEL", "MODEL_VERSION", "DIMENSIONS", "THINKING",
                    "REASONING_EFFORT", "MAX_ENVELOPE_BYTES"):
            monkeypatch.delenv(prefix + "_" + key, raising=False)
    monkeypatch.setenv("MEMORY_DATABASE_URL", "postgresql://unused")
    return monkeypatch


def test_model_backends_are_disabled_without_explicit_configuration(cognitive_env):
    settings = Settings.from_env()
    assert settings.cognition_base_url is None
    assert settings.cognition_thinking is None
    assert settings.embedding_base_url is None


def test_complete_environment_preserves_model_identity_and_hides_keys(cognitive_env):
    for key, value in {
        "MEMORY_COGNITION_BASE_URL": "https://cognition.example/v1",
        "MEMORY_COGNITION_API_KEY": "private-cognition-token",
        "MEMORY_COGNITION_MODEL": "configured-model",
        "MEMORY_COGNITION_THINKING": "disabled",
        "MEMORY_EMBEDDING_BASE_URL": "https://embedding.example/v1",
        "MEMORY_EMBEDDING_API_KEY": "private-embedding-token",
        "MEMORY_EMBEDDING_MODEL": "configured-embedding",
        "MEMORY_EMBEDDING_MODEL_VERSION": "release-7",
        "MEMORY_EMBEDDING_DIMENSIONS": "1536",
    }.items():
        cognitive_env.setenv(key, value)
    settings = Settings.from_env()
    assert settings.cognition_model == "configured-model"
    assert settings.cognition_thinking == "disabled"
    assert settings.embedding_model_version == "release-7"
    assert settings.embedding_dimensions == 1536
    assert "private-cognition-token" not in repr(settings)
    assert "private-embedding-token" not in repr(settings)


@pytest.mark.parametrize("key,value", [
    ("MEMORY_COGNITION_MODEL", "configured-model"),
    ("MEMORY_COGNITION_THINKING", "enabled"),
    ("MEMORY_COGNITION_THINKING", "disabled"),
    ("MEMORY_EMBEDDING_MODEL", "configured-embedding"),
    ("MEMORY_EMBEDDING_DIMENSIONS", "not-a-number"),
])
def test_partial_or_malformed_configuration_does_not_silently_activate_defaults(cognitive_env, key, value):
    cognitive_env.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.from_env()


@pytest.mark.parametrize("thinking", ["enabled", "disabled"])
def test_thinking_environment_is_preserved_with_complete_provider(cognitive_env, thinking):
    for key, value in {
        "BASE_URL": "https://cognition.example/v1", "API_KEY": "private-token",
        "MODEL": "configured-model", "THINKING": thinking,
    }.items():
        cognitive_env.setenv("MEMORY_COGNITION_" + key, value)
    assert Settings.from_env().cognition_thinking == thinking


@pytest.mark.parametrize("thinking", ["", "ENABLED", " disabled", "none", "private-invalid-value"])
def test_invalid_thinking_environment_is_rejected_without_echoing_configuration(cognitive_env, thinking):
    for key, value in {
        "BASE_URL": "https://cognition.example/v1", "API_KEY": "private-token",
        "MODEL": "configured-model", "THINKING": thinking,
    }.items():
        cognitive_env.setenv("MEMORY_COGNITION_" + key, value)
    with pytest.raises(ValueError, match="MEMORY_COGNITION_THINKING") as error:
        Settings.from_env()
    rendered = "".join(traceback.format_exception(error.value))
    assert "private-token" not in rendered
    assert "private-invalid-value" not in rendered


@pytest.mark.parametrize("thinking", [True, 1, [], {}])
def test_direct_settings_reject_nonstring_thinking(thinking):
    with pytest.raises(ValueError, match="MEMORY_COGNITION_THINKING"):
        Settings(database_url="unused", cognition_base_url="https://cognition.example/v1",
                 cognition_api_key="token", cognition_model="model", cognition_thinking=thinking)


@pytest.mark.parametrize("missing", ["cognition_base_url", "cognition_api_key", "cognition_model"])
@pytest.mark.parametrize("empty", [None, "", "   "])
def test_thinking_cannot_enable_an_incomplete_provider(missing, empty):
    values = {
        "cognition_base_url": "https://cognition.example/v1",
        "cognition_api_key": "private-token", "cognition_model": "configured-model",
        "cognition_thinking": "disabled",
    }
    values[missing] = empty
    with pytest.raises(ValueError, match="MEMORY_COGNITION requires all of"):
        Settings(database_url="unused", **values)


@pytest.mark.parametrize("dimensions", [0, 8193, True, 1.2])
def test_embedding_dimensions_must_fit_the_persistent_index(dimensions):
    with pytest.raises(ValueError, match="DIMENSIONS"):
        Settings(database_url="unused", embedding_base_url="https://embedding.example/v1",
                 embedding_api_key="token", embedding_model="model", embedding_model_version="v1",
                 embedding_dimensions=dimensions)


@pytest.mark.parametrize("thinking", [None, "enabled", "disabled"])
def test_factory_uses_one_explicit_provider_for_proposal_and_verification_and_closes_it(monkeypatch, thinking):
    opened, closed = [], []
    monkeypatch.setattr(storage.Database, "check_compatibility", lambda *args, **kwargs: None)

    class Provider:
        def __init__(self, *args, **kwargs):
            opened.append((args, kwargs, self))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(self)

        def __call__(self, request):
            pytest.fail("Configuration must not make a model request")

    monkeypatch.setattr(semantic_provider, "ChatCompletionsSemanticProvider", Provider)
    monkeypatch.setattr(embeddings, "ChatCompletionsEmbeddingProvider", Provider)
    monkeypatch.setattr(service, "MemoryService", lambda database, **kwargs: SimpleNamespace(database=database, **kwargs))
    settings = Settings(
        database_url="unused", cognition_base_url="https://cognition.example/v1",
        cognition_api_key="cognition-token", cognition_model="cognition-model",
        cognition_thinking=thinking,
        embedding_base_url="https://embedding.example/v1", embedding_api_key="embedding-token",
        embedding_model="embedding-model", embedding_model_version="revision-2", embedding_dimensions=3,
    )
    with configured_service(settings) as configured:
        assert configured.semantic_processor.propose is configured.semantic_processor.verify is opened[0][2]
        assert configured.embedding_provider is opened[1][2]
        assert opened[0][0] == (settings.cognition_base_url, "cognition-token", "cognition-model")
        assert opened[0][1] == {"thinking": thinking, "reasoning_effort": None, "max_response_bytes": 65536}
        assert opened[1][1] == {"model_version": "revision-2", "dimensions": 3}
        assert closed == []
    assert closed == [opened[1][2], opened[0][2]]


def test_default_factory_constructs_no_external_provider(monkeypatch):
    monkeypatch.setattr(storage.Database, "check_compatibility", lambda *args, **kwargs: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Unconfigured provider must not be created")

    monkeypatch.setattr(semantic_provider, "ChatCompletionsSemanticProvider", forbidden)
    monkeypatch.setattr(embeddings, "ChatCompletionsEmbeddingProvider", forbidden)
    monkeypatch.setattr(service, "MemoryService", lambda database, **kwargs: SimpleNamespace(**kwargs))
    with configured_service(Settings(database_url="unused")) as configured:
        assert configured.semantic_processor is None
        assert configured.embedding_provider is None


def test_api_and_worker_share_factory_lifetime(monkeypatch):
    lifetimes = []

    @contextmanager
    def factory(settings):
        lifetimes.append("opened")
        try:
            yield SimpleNamespace(list_brains=lambda principal: [])
        finally:
            lifetimes.append("closed")

    monkeypatch.setattr(api, "configured_service", factory)
    monkeypatch.setattr(cli, "configured_service", factory)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    settings = Settings(database_url="unused", api_keys={
        hashlib.sha256(b"key").hexdigest(): Principal("tenant", "owner"),
    })
    with TestClient(api.create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert lifetimes == ["opened"]
    cli.run_worker(settings, once=True)
    assert lifetimes == ["opened", "closed", "opened", "closed"]
