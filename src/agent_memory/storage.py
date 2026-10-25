from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from .build_identity import MIGRATIONS, SCHEMA_ID


class SchemaCompatibilityError(RuntimeError):
    code = "database_schema_incompatible"

    def __init__(self):
        super().__init__("The database schema is not supported by this installation; use a compatible release.")


def schema_status(conn, *, require_current: bool = False) -> dict:
    names = tuple(name for name, _ in MIGRATIONS)
    exists = conn.execute("SELECT to_regclass('memory_migrations') AS name").fetchone()["name"]
    if exists:
        applied = tuple(row["name"] for row in conn.execute("SELECT name FROM memory_migrations ORDER BY name"))
        if applied != names[:len(applied)]:
            raise SchemaCompatibilityError()
    else:
        existing_tables = conn.execute(
            "SELECT 1 FROM pg_class WHERE relnamespace=current_schema()::regnamespace "
            "AND relname LIKE 'memory\\_%' ESCAPE '\\' AND relkind IN ('r','p') LIMIT 1"
        ).fetchone()
        if existing_tables:
            raise SchemaCompatibilityError()
        applied = ()
    pending = len(names) - len(applied)
    if require_current and pending:
        raise SchemaCompatibilityError()
    return {"state": "current" if not pending else "migration_required" if exists else "uninitialized",
            "schema_id": SCHEMA_ID, "applied_count": len(applied), "pending_count": pending}


class Database:
    def __init__(self, dsn: str, migration_dsn: str | None = None):
        self.dsn = dsn
        self.migration_dsn = migration_dsn or dsn

    def check_compatibility(self, *, require_current: bool = False) -> dict:
        with psycopg.connect(self.migration_dsn, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '5000'")
            return schema_status(conn, require_current=require_current)

    def migrate(self):
        with psycopg.connect(self.migration_dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(770419510)")
            schema_status(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            existing = {r["name"] for r in conn.execute("SELECT name FROM memory_migrations")}
            for name, content in MIGRATIONS:
                if name not in existing:
                    conn.execute(content.decode("utf-8"), prepare=False)
                    conn.execute("INSERT INTO memory_migrations (name) VALUES (%s)", (name,))

    @contextmanager
    def transaction(self, principal):
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (principal.tenant_id,))
            conn.execute("SELECT set_config('app.principal_id', %s, true)", (principal.principal_id,))
            yield conn
