import json
import os
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

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        database_url = os.environ.get("MEMORY_DATABASE_URL")
        if not database_url:
            raise ValueError("MEMORY_DATABASE_URL is required; run agent-memory init")
        key_file = os.environ.get("MEMORY_API_KEYS_FILE")
        return cls(
            database_url=database_url,
            migration_database_url=os.environ.get("MEMORY_MIGRATION_DATABASE_URL"),
            api_keys_file=Path(key_file) if key_file else None,
            allowed_hosts=tuple(os.environ.get(
                "MEMORY_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
            ).split(",")),
        )

    def principals(self) -> dict[str, Principal]:
        if self.api_keys_file:
            document = json.loads(self.api_keys_file.read_text(encoding="utf-8"))
            return {key: Principal(**value) for key, value in document.items()}
        return self.api_keys
