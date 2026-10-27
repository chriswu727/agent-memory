import shutil
from pathlib import Path

import pytest

from membrain.build_identity import manifest_digest, package_manifest


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    package = root / "src/membrain"
    (package / "resources").mkdir(parents=True)
    (root / "architecture").mkdir()
    (root / "migrations").mkdir()
    (package / "__init__.py").write_text('__version__ = "0.1.0"\n')
    (package / "runtime.py").write_text("state = 'old'\n")
    (package / "resources/desktop-compose.yaml").write_text("services: {}\n")
    (root / "architecture/memory-contract.schema.json").write_text('{"version": 1}\n')
    (root / "migrations/001_initial.sql").write_text("CREATE TABLE memory_initial(id int);\n")
    return root, package


def test_source_and_installed_wheel_paths_have_identical_identity(source, tmp_path):
    root, package = source
    installed = tmp_path / "site-packages/membrain"
    shutil.copytree(package, installed)
    shutil.copy(root / "architecture/memory-contract.schema.json", installed / "resources")
    shutil.copytree(root / "migrations", installed / "resources/migrations")
    assert package_manifest(package, root) == package_manifest(installed, tmp_path / "unrelated")


@pytest.mark.parametrize("relative", [
    "src/membrain/runtime.py", "src/membrain/resources/desktop-compose.yaml",
    "architecture/memory-contract.schema.json", "migrations/001_initial.sql",
])
def test_shipped_code_contract_resource_and_migration_changes_change_identity(source, relative):
    root, package = source
    before = manifest_digest(package_manifest(package, root))
    target = root / relative
    target.write_bytes(target.read_bytes() + b"\n")
    assert manifest_digest(package_manifest(package, root)) != before
    assert (package / "__init__.py").read_text() == '__version__ = "0.1.0"\n'


def test_location_order_and_python_cache_do_not_change_identity(source, tmp_path):
    root, package = source
    before = package_manifest(package, root)
    (package / "__pycache__").mkdir()
    (package / "__pycache__/runtime.pyc").write_bytes(b"platform specific")
    (package / "__pycache__/accidental.py").write_text("not shipped runtime")
    (package / ".DS_Store").write_bytes(b"not runtime")
    assert package_manifest(package, root) == before
    assert manifest_digest(before) == manifest_digest(dict(reversed(list(before.items()))))
    moved = tmp_path / "another source with spaces"
    shutil.copytree(root, moved)
    assert package_manifest(moved / "src/membrain", moved) == before


def test_new_migrations_are_discovered_without_a_frozen_count(source):
    root, package = source
    before = manifest_digest(package_manifest(package, root))
    (root / "migrations/002_addition.sql").write_text("SELECT 1;\n")
    assert manifest_digest(package_manifest(package, root)) != before


def test_missing_required_packaged_resources_fail_explicitly(tmp_path):
    with pytest.raises(RuntimeError, match="missing its contract or database migrations"):
        package_manifest(tmp_path, tmp_path)


def test_repository_manifest_contains_every_runtime_python_file():
    package = Path(__file__).resolve().parents[1] / "src/membrain"
    manifest = package_manifest(package, package.parents[1])
    assert {path.relative_to(package).as_posix() for path in package.glob("*.py")} <= manifest.keys()
    assert all(".env" not in name and "__pycache__" not in name for name in manifest)
