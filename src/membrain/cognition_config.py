from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from .chat_provider import ReasoningEffort
from .config import validate_cognition_transport
from .local_profile import LocalProfileError, _open_private, _pairs, load_profile, profile_lock
from .semantic import SemanticPolicy

CONFIGURATION_FILE = "cognition-policy.json"
_MAX_CONFIGURATION_BYTES = 4096
_SEMANTIC_FIELDS = frozenset({"max_wall_time_ms", "max_output_tokens", "max_response_bytes"})
_FIELDS = _SEMANTIC_FIELDS | {"reasoning_effort", "max_envelope_bytes"}


class CognitionConfigurationError(ValueError):
    def __init__(self, code):
        messages = {
            "cognition_policy_invalid": "Cognition policy is malformed or outside its supported limits.",
            "cognition_policy_unavailable": "Cognition policy could not be accessed safely.",
        }
        if not isinstance(code, str) or code not in messages:
            raise ValueError("Unknown cognition policy error code")
        self.code = code
        super().__init__(messages[code])


@dataclass(frozen=True)
class CognitionPolicyConfiguration:
    max_wall_time_ms: int = 30_000
    max_output_tokens: int = 8192
    max_response_bytes: int = 32768
    reasoning_effort: ReasoningEffort | None = None
    max_envelope_bytes: int = 65536

    def __post_init__(self):
        try:
            SemanticPolicy(**{key: getattr(self, key) for key in _SEMANTIC_FIELDS})
            validate_cognition_transport(self.reasoning_effort, None, self.max_envelope_bytes)
        except (TypeError, ValueError):
            raise CognitionConfigurationError("cognition_policy_invalid") from None

    def settings(self):
        return {"cognition_" + key: value for key, value in asdict(self).items()}

    def validate_thinking(self, thinking):
        try:
            validate_cognition_transport(self.reasoning_effort, thinking, self.max_envelope_bytes)
        except ValueError:
            raise CognitionConfigurationError("cognition_policy_invalid") from None


def cognition_policy_preset(name: str) -> CognitionPolicyConfiguration:
    if name != "reasoning":
        raise CognitionConfigurationError("cognition_policy_invalid")
    return CognitionPolicyConfiguration(max_wall_time_ms=240_000, max_output_tokens=16384,
                                        max_response_bytes=65536)


def _read(directory):
    try:
        descriptor = _open_private(directory / CONFIGURATION_FILE, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return CognitionPolicyConfiguration()
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(_MAX_CONFIGURATION_BYTES + 1)
    try:
        if len(content) > _MAX_CONFIGURATION_BYTES:
            raise ValueError
        value = json.loads(content, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or type(value.get("version")) is not int:
            raise ValueError
        fields = {1: _SEMANTIC_FIELDS, 2: _FIELDS}.get(value["version"])
        if fields is None or set(value) != fields | {"version"}:
            raise ValueError
        return CognitionPolicyConfiguration(**{key: value[key] for key in fields})
    except (ValueError, TypeError, RecursionError):
        raise CognitionConfigurationError("cognition_policy_invalid") from None


def load_cognition_policy(directory: Path | None = None) -> CognitionPolicyConfiguration:
    try:
        profile = load_profile(directory)
        with profile_lock(profile.directory):
            load_profile(profile.directory)
            return _read(profile.directory)
    except CognitionConfigurationError:
        raise
    except (OSError, LocalProfileError):
        raise CognitionConfigurationError("cognition_policy_unavailable") from None


def save_cognition_policy(configuration: CognitionPolicyConfiguration,
                          directory: Path | None = None) -> CognitionPolicyConfiguration:
    if type(configuration) is not CognitionPolicyConfiguration:
        raise CognitionConfigurationError("cognition_policy_invalid")
    configuration = CognitionPolicyConfiguration(**asdict(configuration))
    content = (json.dumps({"version": 2, **asdict(configuration)}, indent=2) + "\n").encode()
    try:
        profile = load_profile(directory)
        with profile_lock(profile.directory):
            current_profile = load_profile(profile.directory)
            configuration.validate_thinking((current_profile.cognition or {}).get("thinking"))
            destination = profile.directory / CONFIGURATION_FILE
            if destination.exists() or destination.is_symlink():
                descriptor = _open_private(destination, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
                os.close(descriptor)
            temporary = profile.directory / (".cognition-policy-" + secrets.token_hex(16) + ".tmp")
            try:
                descriptor = _open_private(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                if os.name != "nt":
                    descriptor = os.open(profile.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            finally:
                temporary.unlink(missing_ok=True)
        return configuration
    except CognitionConfigurationError:
        raise
    except (OSError, LocalProfileError):
        raise CognitionConfigurationError("cognition_policy_unavailable") from None
