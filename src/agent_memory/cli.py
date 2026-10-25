import argparse
import hashlib
import json
import os
import secrets
import signal
import threading
from pathlib import Path

from .config import Settings, configured_service
from .helpers import uid


def initialize(directory: Path) -> None:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / ".env"
    if config.exists():
        raise SystemExit(".env already exists; existing configuration was left unchanged")
    private = directory / ".local"
    private.mkdir(exist_ok=True, mode=0o700)
    admin_password, app_password = secrets.token_hex(24), secrets.token_hex(24)
    token = "am_" + secrets.token_urlsafe(36)
    entries = {
        "MEMORY_DATABASE_ADMIN_PASSWORD": admin_password,
        "MEMORY_DATABASE_APP_PASSWORD": app_password,
        "MEMORY_DATABASE_URL": f"postgresql://memory_app:{app_password}@127.0.0.1:55432/agent_memory",
        "MEMORY_MIGRATION_DATABASE_URL": f"postgresql://postgres:{admin_password}@127.0.0.1:55432/agent_memory",
        "MEMORY_API_KEYS_FILE": str(private / "keys.json"),
        "MEMORY_DATABASE_PORT": "55432",
    }
    credentials = {
        hashlib.sha256(token.encode()).hexdigest(): {"tenant_id": uid(), "principal_id": uid()},
    }
    documents = {
        config: "".join(f"{key}={value}\n" for key, value in entries.items()),
        private / "access-token": token + "\n",
        private / "keys.json": json.dumps(credentials, indent=2) + "\n",
    }
    for destination, content in documents.items():
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(content)
        destination.chmod(0o600)
    print(f"Created configuration in {directory}; API key is in .local/access-token")


def bootstrap(settings: Settings, *, app_password: str | None = None) -> None:
    import psycopg
    from psycopg import sql

    from .storage import Database

    if not settings.migration_database_url:
        raise SystemExit("MEMORY_MIGRATION_DATABASE_URL is required for migration")
    app_password = app_password or os.environ.get("MEMORY_DATABASE_APP_PASSWORD")
    if not app_password:
        raise SystemExit("MEMORY_DATABASE_APP_PASSWORD is required for local role provisioning")
    database = Database(settings.database_url, settings.migration_database_url)
    database.check_compatibility()
    with psycopg.connect(settings.migration_database_url) as connection:
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = 'memory_app'").fetchone()
        if not exists:
            connection.execute(sql.SQL(
                "CREATE ROLE memory_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}"
            ).format(sql.Literal(app_password)))
    database.migrate()
    with psycopg.connect(settings.migration_database_url) as connection:
        connection.execute("GRANT USAGE ON SCHEMA public TO memory_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO memory_app")
        connection.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO memory_app")
    print("Migrations applied; application role has no superuser or RLS bypass privileges")


def run_worker(settings: Settings, once: bool = False) -> None:
    stopped = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda signum, frame: stopped.set())
    with configured_service(settings) as service:
        while not stopped.is_set():
            for principal in set(settings.principals().values()):
                for brain in service.list_brains(principal):
                    try:
                        service.work_once(principal, brain.get("id", brain.get("brain_id")))
                    except Exception as error:
                        print(f"Background processing failed ({type(error).__name__}); will retry")
            if once:
                return
            stopped.wait(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-memory")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create private local configuration")
    init.add_argument("--directory", type=Path, default=Path.cwd())
    commands.add_parser("migrate", help="Provision local application role and apply migrations")
    serve = commands.add_parser("serve", help="Run HTTP API and memory console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    worker = commands.add_parser("worker", help="Process durable background jobs")
    worker.add_argument("--once", action="store_true")
    commands.add_parser("mcp", help="Run the MCP stdio bridge")
    from .desktop_cli import add_commands
    add_commands(commands)
    args = parser.parse_args()
    if args.command == "desktop":
        from .desktop_cli import dispatch
        dispatch(args)
    elif args.command == "init":
        initialize(args.directory)
    elif args.command == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
    elif args.command == "serve":
        import uvicorn

        from .api import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    elif args.command == "migrate":
        bootstrap(Settings.from_env())
    elif args.command == "worker":
        run_worker(Settings.from_env(), args.once)


if __name__ == "__main__":
    main()
