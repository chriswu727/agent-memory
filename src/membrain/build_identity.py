from __future__ import annotations

import hashlib
import json
from pathlib import Path

_RUNTIME_SUFFIXES = {".py", ".json", ".sql", ".yaml", ".html", ".js", ".css"}


def package_manifest(package_root: Path, development_root: Path) -> dict[str, bytes]:
    entries = {
        path.relative_to(package_root).as_posix(): path.read_bytes()
        for path in package_root.rglob("*")
        if path.is_file() and path.suffix in _RUNTIME_SUFFIXES
        and "__pycache__" not in path.parts
    }
    schema = development_root / "architecture/memory-contract.schema.json"
    if schema.is_file():
        entries["resources/memory-contract.schema.json"] = schema.read_bytes()
    migrations = development_root / "migrations"
    if migrations.is_dir():
        entries = {name: value for name, value in entries.items()
                   if not name.startswith("resources/migrations/")}
        entries.update({"resources/migrations/" + path.name: path.read_bytes()
                        for path in migrations.iterdir() if path.is_file() and path.suffix == ".sql"})
    if ("resources/memory-contract.schema.json" not in entries
            or not any(name.startswith("resources/migrations/") for name in entries)):
        raise RuntimeError("The installed package is missing its contract or database migrations.")
    return entries


def manifest_digest(entries: dict[str, bytes]) -> str:
    digests = {name: hashlib.sha256(value).hexdigest() for name, value in entries.items()}
    return hashlib.sha256(json.dumps(digests, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_ROOT = Path(__file__).resolve().parent
_MANIFEST = package_manifest(_ROOT, _ROOT.parents[1])
BUILD_ID = manifest_digest(_MANIFEST)
MIGRATIONS = tuple(sorted(
    (name.removeprefix("resources/migrations/"), value)
    for name, value in _MANIFEST.items() if name.startswith("resources/migrations/")
))
SCHEMA_ID = manifest_digest(dict(MIGRATIONS))
del _MANIFEST
