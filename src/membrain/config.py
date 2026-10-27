import json
import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from .chat_provider import ReasoningEffort, _validate_reasoning_effort
from .helpers import Principal


@dataclass
class Settings:
    database_url: str = field(repr=False)
    migration_database_url: str | None = field(default=None, repr=False)
    api_keys_file: Path | None = None
    api_keys: dict[str, Principal] = field(default_factory=dict, repr=False)
    max_request_bytes: int = 2_000_000
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    cognition_base_url: str | None = None
    cognition_api_key: str | None = field(default=None, repr=False)
    cognition_model: str | None = None
    cognition_thinking: Literal["enabled", "disabled"] | None = None
    cognition_reasoning_effort: ReasoningEffort | None = None
    cognition_max_wall_time_ms: int = 30_000
    cognition_max_output_tokens: int = 8192
    cognition_max_response_bytes: int = 32768
    cognition_max_envelope_bytes: int = 65536
    embedding_base_url: str | None = None
    embedding_api_key: str | None = field(default=None, repr=False)
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    embedding_dimensions: int | None = None
    recovery_registry_path: Path | None = None
    recovery_registry_id: str | None = None
    recovery_installation_id: str | None = None

    def __post_init__(self):
        self.semantic_policy()
        validate_cognition_transport(self.cognition_reasoning_effort, self.cognition_thinking,
                                     self.cognition_max_envelope_bytes)
        recovery = (self.recovery_registry_path, self.recovery_registry_id, self.recovery_installation_id)
        if any(value is not None for value in recovery) and any(value is None for value in recovery):
            raise ValueError("Recovery authority requires its path, registry identity and installation identity.")
        for prefix, names in (
            ("MEMORY_COGNITION", ("base_url", "api_key", "model")),
            ("MEMORY_EMBEDDING", ("base_url", "api_key", "model", "model_version", "dimensions")),
        ):
            values = [getattr(self, prefix.removeprefix("MEMORY_").lower() + "_" + name) for name in names]
            configured = any(value is not None for value in values) or (
                prefix == "MEMORY_COGNITION" and self.cognition_thinking is not None
            )
            if configured and any(
                value is None or (isinstance(value, str) and not value.strip()) for value in values
            ):
                raise ValueError(f"{prefix} requires all of: {', '.join(name.upper() for name in names)}")
        if self.cognition_thinking is not None and (
            not isinstance(self.cognition_thinking, str)
            or self.cognition_thinking not in ("enabled", "disabled")
        ):
            raise ValueError("MEMORY_COGNITION_THINKING must be 'enabled' or 'disabled' when set")
        if self.embedding_dimensions is not None and (
            type(self.embedding_dimensions) is not int or not 1 <= self.embedding_dimensions <= 8192
        ):
            raise ValueError("MEMORY_EMBEDDING_DIMENSIONS must be an integer from 1 to 8192")

    def semantic_policy(self):
        from .semantic import SemanticPolicy

        return SemanticPolicy(max_wall_time_ms=self.cognition_max_wall_time_ms,
                              max_output_tokens=self.cognition_max_output_tokens,
                              max_response_bytes=self.cognition_max_response_bytes)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        database_url = os.environ.get("MEMORY_DATABASE_URL")
        if not database_url:
            raise ValueError("MEMORY_DATABASE_URL is required; run membrain init")
        key_file = os.environ.get("MEMORY_API_KEYS_FILE")
        dimensions = os.environ.get("MEMORY_EMBEDDING_DIMENSIONS")
        if dimensions is not None and (not dimensions.isascii() or not dimensions.isdecimal()):
            raise ValueError("MEMORY_EMBEDDING_DIMENSIONS must be an integer from 1 to 8192")
        return cls(
            database_url=database_url,
            migration_database_url=os.environ.get("MEMORY_MIGRATION_DATABASE_URL"),
            api_keys_file=Path(key_file) if key_file else None,
            allowed_hosts=tuple(os.environ.get(
                "MEMORY_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
            ).split(",")),
            cognition_base_url=os.environ.get("MEMORY_COGNITION_BASE_URL"),
            cognition_api_key=os.environ.get("MEMORY_COGNITION_API_KEY"),
            cognition_model=os.environ.get("MEMORY_COGNITION_MODEL"),
            cognition_thinking=os.environ.get("MEMORY_COGNITION_THINKING"),
            cognition_reasoning_effort=os.environ.get("MEMORY_COGNITION_REASONING_EFFORT"),
            cognition_max_wall_time_ms=_environment_integer("MEMORY_COGNITION_MAX_WALL_TIME_MS", 30_000),
            cognition_max_output_tokens=_environment_integer("MEMORY_COGNITION_MAX_OUTPUT_TOKENS", 8192),
            cognition_max_response_bytes=_environment_integer("MEMORY_COGNITION_MAX_RESPONSE_BYTES", 32768),
            cognition_max_envelope_bytes=_environment_integer("MEMORY_COGNITION_MAX_ENVELOPE_BYTES", 65536),
            embedding_base_url=os.environ.get("MEMORY_EMBEDDING_BASE_URL"),
            embedding_api_key=os.environ.get("MEMORY_EMBEDDING_API_KEY"),
            embedding_model=os.environ.get("MEMORY_EMBEDDING_MODEL"),
            embedding_model_version=os.environ.get("MEMORY_EMBEDDING_MODEL_VERSION"),
            embedding_dimensions=int(dimensions) if dimensions is not None else None,
        )

    def principals(self) -> dict[str, Principal]:
        if self.api_keys_file:
            document = json.loads(self.api_keys_file.read_text(encoding="utf-8"))
            return {key: Principal(**value) for key, value in document.items()}
        return self.api_keys


def validate_cognition_transport(reasoning_effort, thinking, max_envelope_bytes):
    try:
        _validate_reasoning_effort(reasoning_effort, thinking)
    except ValueError:
        raise ValueError("MEMORY_COGNITION_REASONING_EFFORT must be unset or a supported effort level, "
                         "and cannot be set when MEMORY_COGNITION_THINKING is disabled.") from None
    if type(max_envelope_bytes) is not int or not 1 <= max_envelope_bytes <= 1048576:
        raise ValueError("MEMORY_COGNITION_MAX_ENVELOPE_BYTES must be an integer from 1 to 1048576")


def _environment_integer(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    if not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise ValueError(f"{name} must be a bounded positive integer")
    return int(value)


@contextmanager
def configured_service(settings: Settings):
    from .embeddings import ChatCompletionsEmbeddingProvider
    from .semantic import SemanticProcessor
    from .semantic_provider import ChatCompletionsSemanticProvider
    from .service import MemoryService
    from .storage import Database

    database = Database(settings.database_url)
    database.check_compatibility(require_current=True)
    from .recovery_authority import configure_authority

    configure_authority(database, settings)
    with ExitStack() as resources:
        processor, embedding = None, None
        if settings.cognition_base_url is not None:
            provider = resources.enter_context(ChatCompletionsSemanticProvider(
                settings.cognition_base_url, settings.cognition_api_key, settings.cognition_model,
                thinking=settings.cognition_thinking,
                reasoning_effort=settings.cognition_reasoning_effort,
                max_response_bytes=settings.cognition_max_envelope_bytes,
            ))
            processor = SemanticProcessor(provider, verify=provider, policy=settings.semantic_policy())
        if settings.embedding_base_url is not None:
            embedding = resources.enter_context(ChatCompletionsEmbeddingProvider(
                settings.embedding_base_url, settings.embedding_api_key, settings.embedding_model,
                model_version=settings.embedding_model_version, dimensions=settings.embedding_dimensions,
            ))
        yield MemoryService(
            database,
            semantic_processor=processor, embedding_provider=embedding,
        )
