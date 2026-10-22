import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .helpers import DomainError

CONTRACT_VERSION = "1.0.0-draft"


@lru_cache
def schema() -> dict:
    development = Path(__file__).resolve().parents[2] / "architecture/memory-contract.schema.json"
    source = development if development.is_file() else files("agent_memory").joinpath(
        "resources/memory-contract.schema.json"
    )
    return json.loads(source.read_text(encoding="utf-8"))


@lru_cache
def validator(definition: str | None = None) -> Draft202012Validator:
    document = schema()
    if definition is not None:
        document = {
            "$schema": document["$schema"],
            "$defs": document["$defs"],
            "$ref": f"#/$defs/{definition}",
        }
    return Draft202012Validator(document, format_checker=FormatChecker())


def validate(definition: str | None, value: object) -> None:
    try:
        validator(definition).validate(value)
    except ValidationError as error:
        path = ".".join(map(str, error.absolute_path)) or "$"
        raise DomainError("validation_failed", f"Invalid {definition or 'contract'} at {path}") from error
