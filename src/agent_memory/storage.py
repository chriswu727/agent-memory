from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, dsn: str, migration_dsn: str | None = None):
        self.dsn = dsn
        self.migration_dsn = migration_dsn or dsn

    def migrate(self):
        migration_root = Path(__file__).resolve().parents[2] / "migrations"
        if not migration_root.is_dir():
            migration_root = files("agent_memory").joinpath("resources/migrations")
        migrations = sorted(
            (entry for entry in migration_root.iterdir() if entry.name.endswith(".sql")),
            key=lambda entry: entry.name,
        )
        if not migrations:
            raise RuntimeError("No database migrations were packaged.")
        with psycopg.connect(self.migration_dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(770419510)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            existing = {r["name"] for r in conn.execute("SELECT name FROM memory_migrations")}
            for migration in migrations:
                if migration.name not in existing:
                    conn.execute(migration.read_text(), prepare=False)
                    conn.execute("INSERT INTO memory_migrations (name) VALUES (%s)", (migration.name,))

    @contextmanager
    def transaction(self, principal):
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (principal.tenant_id,))
            conn.execute("SELECT set_config('app.principal_id', %s, true)", (principal.principal_id,))
            yield conn
