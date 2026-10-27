from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal

from .local_profile import (
    LocalProfileError,
    _cognition,
    _open_private,
    _pairs,
    load_profile,
    profile_lock,
)

_CONFIGURATION_FILE = "chat.json"
_MAX_CONFIGURATION_BYTES = 65536
_ERROR_MESSAGES = {
    "chat_not_configured": "No chat model is configured; configure one explicitly first.",
    "chat_configuration_invalid": "Chat configuration is malformed or invalid.",
    "chat_configuration_unavailable": "Chat configuration could not be accessed safely.",
    "cognition_not_configured": "No cognition model is configured to copy.",
}


class ChatConfigurationError(ValueError):
    def __init__(self, code: str):
        if not isinstance(code, str) or code not in _ERROR_MESSAGES:
            raise ValueError("Unknown chat configuration error code")
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


@dataclass(frozen=True)
class ChatConfiguration:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    thinking: Literal["enabled", "disabled"] | None = None
    max_output_tokens: int = 4096
    timeout_seconds: float = 180
    max_response_bytes: int = 1048576

    def __post_init__(self):
        try:
            _cognition({
                "base_url": self.base_url, "model": self.model,
                "api_key": self.api_key, "thinking": self.thinking,
            })
            self.base_url.encode("utf-8")
            self.model.encode("utf-8")
            if ("?" in self.base_url or "#" in self.base_url or "\x7f" in self.base_url
                    or self.model != self.model.strip() or "\x7f" in self.model):
                raise ValueError
            if any(type(value) is not int or value < 1 for value in (
                self.max_output_tokens, self.max_response_bytes,
            )):
                raise ValueError
            if (type(self.timeout_seconds) not in (int, float)
                    or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise ChatConfigurationError("chat_configuration_invalid") from None

    @property
    def fingerprint(self) -> str:
        values = {item.name: getattr(self, item.name) for item in fields(self)
                  if item.name != "api_key"}
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def generator(self):
        from .chat_provider import ChatCompletionsGenerator

        return ChatCompletionsGenerator(
            self.base_url, self.api_key, self.model, thinking=self.thinking,
            max_output_tokens=self.max_output_tokens, timeout_seconds=self.timeout_seconds,
            max_response_bytes=self.max_response_bytes,
        )


def _read(directory: Path) -> ChatConfiguration:
    try:
        descriptor = _open_private(directory / _CONFIGURATION_FILE,
                                   os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise ChatConfigurationError("chat_not_configured") from None
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(_MAX_CONFIGURATION_BYTES + 1)
    if len(content) > _MAX_CONFIGURATION_BYTES:
        raise ChatConfigurationError("chat_configuration_invalid")
    try:
        document = json.loads(content, object_pairs_hook=_pairs)
        names = {item.name for item in fields(ChatConfiguration)}
        if (not isinstance(document, dict) or set(document) != names | {"version"}
                or type(document["version"]) is not int or document["version"] != 1):
            raise ValueError
        return ChatConfiguration(**{name: document[name] for name in names})
    except (ValueError, TypeError, RecursionError):
        raise ChatConfigurationError("chat_configuration_invalid") from None


def load_chat_configuration(directory: Path | None = None) -> ChatConfiguration:
    try:
        profile = load_profile(directory)
        with profile_lock(profile.directory):
            load_profile(profile.directory)
            return _read(profile.directory)
    except ChatConfigurationError:
        raise
    except (OSError, LocalProfileError):
        raise ChatConfigurationError("chat_configuration_unavailable") from None


def _write(configuration: ChatConfiguration, directory: Path) -> None:
    document = {item.name: getattr(configuration, item.name) for item in fields(configuration)}
    document["version"] = 1
    content = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    if len(content) > _MAX_CONFIGURATION_BYTES:
        raise ChatConfigurationError("chat_configuration_invalid")
    destination = directory / _CONFIGURATION_FILE
    if destination.exists() or destination.is_symlink():
        descriptor = _open_private(destination, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        os.close(descriptor)
    temporary = directory / (".chat-" + secrets.token_hex(16) + ".tmp")
    try:
        descriptor = _open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if os.name != "nt":
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def save_chat_configuration(
    configuration: ChatConfiguration, directory: Path | None = None,
) -> ChatConfiguration:
    if type(configuration) is not ChatConfiguration:
        raise ChatConfigurationError("chat_configuration_invalid")
    configuration = ChatConfiguration(**{
        item.name: getattr(configuration, item.name) for item in fields(configuration)
    })
    try:
        profile = load_profile(directory)
        with profile_lock(profile.directory):
            load_profile(profile.directory)
            _write(configuration, profile.directory)
        return configuration
    except ChatConfigurationError:
        raise
    except (OSError, LocalProfileError):
        raise ChatConfigurationError("chat_configuration_unavailable") from None


def use_cognition_configuration(directory: Path | None = None) -> ChatConfiguration:
    """Copy cognition settings without persisting a chat configuration."""
    try:
        profile = load_profile(directory)
        with profile_lock(profile.directory):
            current = load_profile(profile.directory)
            if current.cognition is None:
                raise ChatConfigurationError("cognition_not_configured")
            return ChatConfiguration(**current.cognition)
    except ChatConfigurationError:
        raise
    except (OSError, LocalProfileError):
        raise ChatConfigurationError("chat_configuration_unavailable") from None
