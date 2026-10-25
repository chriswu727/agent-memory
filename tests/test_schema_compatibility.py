import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from agent_memory import storage
from agent_memory.config import Settings
from agent_memory.helpers import uid
from agent_memory.storage import Database, SchemaCompatibilityError

pytestmark = pytest.mark.integration


@pytest.fixture
def schema_database(monkeypatch):
    settings = Settings.from_env()
    admin = settings.migration_database_url or settings.database_url
    namespace = "upgrade_check_" + uid().replace("-", "")
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace)))
    monkeypatch.setattr(storage, "MIGRATIONS", (
        ("001_initial.sql", b"CREATE TABLE memory_first(id int PRIMARY KEY, value text);"),
        ("002_second.sql", b"CREATE TABLE memory_second(id int PRIMARY KEY);"),
    ))
    database = Database(make_conninfo(admin, options="-c search_path=" + namespace))
    try:
        yield database
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(namespace)))


def execute(database, statement, parameters=None):
    with psycopg.connect(database.dsn, row_factory=dict_row) as connection:
        cursor = connection.execute(statement, parameters)
        return cursor.fetchall() if cursor.description else None


def test_preflight_is_read_only_and_initialization_and_repeat_migration_preserve_data(schema_database):
    database = schema_database
    state = database.check_compatibility()
    assert state["state"] == "uninitialized"
    assert state["applied_count"] == 0 and state["pending_count"] == 2
    assert execute(database, "SELECT to_regclass('memory_migrations') AS name") == [{"name": None}]
    with pytest.raises(SchemaCompatibilityError):
        database.check_compatibility(require_current=True)
    database.migrate()
    execute(database, "INSERT INTO memory_first VALUES(1,'retained')")
    database.migrate()
    assert database.check_compatibility(require_current=True)["state"] == "current"
    assert execute(database, "SELECT value FROM memory_first") == [{"value": "retained"}]


def test_known_prefix_upgrades_and_preserves_existing_data(schema_database, monkeypatch):
    database = schema_database
    first, second = storage.MIGRATIONS
    monkeypatch.setattr(storage, "MIGRATIONS", (first,))
    database.migrate()
    execute(database, "INSERT INTO memory_first VALUES(1,'retained')")
    monkeypatch.setattr(storage, "MIGRATIONS", (first, second))
    assert database.check_compatibility()["state"] == "migration_required"
    with pytest.raises(SchemaCompatibilityError):
        database.check_compatibility(require_current=True)
    database.migrate()
    assert database.check_compatibility(require_current=True)["pending_count"] == 0
    assert execute(database, "SELECT value FROM memory_first") == [{"value": "retained"}]


@pytest.mark.parametrize("invalid", ["999_newer.sql", "001_secret_migration_password.sql"])
def test_unknown_migrations_block_preflight_and_migration_without_mutation(schema_database, invalid):
    database = schema_database
    database.migrate()
    execute(database, "INSERT INTO memory_migrations(name) VALUES(%s)", (invalid,))
    before = execute(database, "SELECT * FROM memory_migrations ORDER BY name")
    for operation in (database.check_compatibility, database.migrate):
        with pytest.raises(SchemaCompatibilityError) as error:
            operation()
        assert error.value.code == "database_schema_incompatible"
        assert invalid not in str(error.value)
    assert execute(database, "SELECT * FROM memory_migrations ORDER BY name") == before


def test_downgrade_and_missing_middle_history_are_rejected(schema_database, monkeypatch):
    database = schema_database
    database.migrate()
    complete = storage.MIGRATIONS
    monkeypatch.setattr(storage, "MIGRATIONS", complete[:1])
    with pytest.raises(SchemaCompatibilityError):
        database.migrate()
    monkeypatch.setattr(storage, "MIGRATIONS", complete)
    execute(database, "DELETE FROM memory_migrations WHERE name='001_initial.sql'")
    with pytest.raises(SchemaCompatibilityError):
        database.check_compatibility()


def test_missing_registry_does_not_adopt_existing_memory_tables(schema_database):
    database = schema_database
    execute(database, "CREATE TABLE memory_orphan(id int)")
    with pytest.raises(SchemaCompatibilityError):
        database.migrate()
    assert execute(database, "SELECT to_regclass('memory_migrations') AS name") == [{"name": None}]


def test_migration_rechecks_after_successful_preflight(schema_database):
    database = schema_database
    database.migrate()
    assert database.check_compatibility()["state"] == "current"
    execute(database, "INSERT INTO memory_migrations(name) VALUES('999_racing_upgrade.sql')")
    with pytest.raises(SchemaCompatibilityError):
        database.migrate()
