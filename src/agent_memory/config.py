import json
import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

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
    embedding_base_url: str | None = None
    embedding_api_key: str | None = field(default=None, repr=False)
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    embedding_dimensions: int | None = None

    def __post_init__(self):
        for prefix, names in (
            ("MEMORY_COGNITION", ("base_url", "api_key", "model")),
            ("MEMORY_EMBEDDING", ("base_url", "api_key", "model", "model_version", "dimensions")),
        ):
            values = [getattr(self, prefix.removeprefix("MEMORY_").lower() + "_" + name) for name in names]
            if any(value is not None for value in values) and any(
                value is None or (isinstance(value, str) and not value.strip()) for value in values
            ):
                raise ValueError(f"{prefix} requires all of: {', '.join(name.upper() for name in names)}")
        if self.embedding_dimensions is not None and (
            type(self.embedding_dimensions) is not int or not 1 <= self.embedding_dimensions <= 8192
        ):
            raise ValueError("MEMORY_EMBEDDING_DIMENSIONS must be an integer from 1 to 8192")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        database_url = os.environ.get("MEMORY_DATABASE_URL")
        if not database_url:
            raise ValueError("MEMORY_DATABASE_URL is required; run agent-memory init")
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


@contextmanager
def configured_service(settings: Settings):
    from .embeddings import ChatCompletionsEmbeddingProvider
    from .semantic import SemanticProcessor
    from .semantic_provider import ChatCompletionsSemanticProvider
    from .service import MemoryService
    from .storage import Database

    with ExitStack() as resources:
        processor, embedding = None, None
        if settings.cognition_base_url is not None:
            provider = resources.enter_context(ChatCompletionsSemanticProvider(
                settings.cognition_base_url, settings.cognition_api_key, settings.cognition_model,
            ))
            processor = SemanticProcessor(provider, verify=provider)
        if settings.embedding_base_url is not None:
            embedding = resources.enter_context(ChatCompletionsEmbeddingProvider(
                settings.embedding_base_url, settings.embedding_api_key, settings.embedding_model,
                model_version=settings.embedding_model_version, dimensions=settings.embedding_dimensions,
            ))
        yield MemoryService(
            Database(settings.database_url, settings.migration_database_url),
            semantic_processor=processor, embedding_provider=embedding,
        )
