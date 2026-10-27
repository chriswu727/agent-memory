import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    principal_id: str


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        current_revision: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.current_revision = current_revision

    def as_dict(self) -> dict:
        result = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.current_revision is not None:
            result["current_revision"] = self.current_revision
        return result


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
