import hashlib
import json
from importlib.resources import files

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import Settings
from .contracts import CONTRACT_VERSION, schema, validate
from .helpers import DomainError, Principal


def capabilities() -> dict:
    result = {
        "adapter_id": "agent-memory-rest",
        "adapter_version": __version__,
        "protocol_version": CONTRACT_VERSION,
        "transport": "rest",
        "supported_actions": schema()["x-fixed-actions"],
        "capture": {key: False for key in (
            "user_messages", "delivered_assistant_messages", "tool_results", "message_edits",
            "message_deletions", "stable_source_ids", "branch_identity",
        )},
        "hooks": {key: False for key in (
            "before_turn", "after_delivery", "before_compaction", "on_resume",
        )},
        "context_control": "tool_output_only",
        "active_recall": True,
        "automatic_capture": False,
        "automatic_recall": False,
        "guarantee_level": "tools_only",
        "verification_state": "declared",
        "verified_at": None,
        "limitations": [
            "Host integration must explicitly observe messages and prepare each turn.",
            "Current recall uses lexical and Chinese character cues, not semantic embeddings.",
            "Consolidation preserves source-backed episodes; statistical memory quality is not validated.",
        ],
    }
    validate("IntegrationCapabilities", result)
    return result


def create_app(settings: Settings | None = None, service=None) -> FastAPI:
    settings = settings or Settings.from_env()
    if service is None:
        from .service import MemoryService
        from .storage import Database

        service = MemoryService(Database(settings.database_url, settings.migration_database_url))
    app = FastAPI(title="Agent Memory", version=__version__)
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise DomainError("unauthorized", "A valid API key is required")
        key_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        principal = settings.principals().get(key_hash)
        if principal is None:
            raise DomainError("unauthorized", "A valid API key is required")
        return principal

    async def read_document(request: Request, max_bytes: int | None = None) -> dict:
        limit = settings.max_request_bytes if max_bytes is None else max_bytes
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise DomainError("budget_exceeded", "Request exceeds the configured size limit")
        try:
            def invalid_constant(value):
                raise ValueError(value)
            document = json.loads(chunks, parse_constant=invalid_constant)
            if not isinstance(document, dict):
                raise ValueError("Object required")
            return document
        except (ValueError, UnicodeDecodeError) as error:
            raise DomainError("validation_failed", "A valid JSON object is required") from error

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        status = {
            "unauthorized": 401, "not_found_or_forbidden": 404,
            "version_conflict": 409, "idempotency_conflict": 409,
            "dependency_unavailable": 503, "budget_exceeded": 413,
        }.get(error.code, 422)
        return JSONResponse({"error": error.as_dict()}, status_code=status)

    @app.exception_handler(psycopg.OperationalError)
    async def database_error(request, error):
        return JSONResponse({"error": {
            "code": "dependency_unavailable", "message": "Database unavailable", "retryable": True,
        }}, status_code=503)

    @app.get("/health")
    def health():
        return {"status": "alive", "version": __version__}

    @app.get("/v1/capabilities")
    def get_capabilities(principal: Principal = Depends(authenticate)):
        return capabilities()

    @app.get("/v1/schema")
    def get_schema(principal: Principal = Depends(authenticate)):
        return schema()

    @app.get("/v1/brains")
    def list_brains(principal: Principal = Depends(authenticate)):
        return {"brains": service.list_brains(principal)}

    @app.post("/v1/brains", status_code=201)
    async def create_brain(request: Request, principal: Principal = Depends(authenticate)):
        body = await read_document(request)
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise DomainError("validation_failed", "Name must contain 1 to 120 characters")
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(service.create_brain, principal, name.strip())

    @app.get("/v1/brains/{brain_id}/dashboard")
    def dashboard(brain_id: str, principal: Principal = Depends(authenticate)):
        return service.dashboard(principal, brain_id)

    @app.post("/v1/brains/{brain_id}/work")
    def work(brain_id: str, principal: Principal = Depends(authenticate)):
        return {"processed": service.work_once(principal, brain_id)}

    @app.post("/v1/actions")
    async def action(request: Request, principal: Principal = Depends(authenticate)):
        body = await read_document(request)
        validate("ActionRequest", body)
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(service.handle, principal, body)
        validate("ActionResponse", result)
        return result

    @app.get("/v1/archives/{archive_id}")
    def download_archive(archive_id: str, principal: Principal = Depends(authenticate)):
        return service.download_archive(principal, archive_id)

    @app.post("/v1/brains/{brain_id}/archives", status_code=201)
    async def upload_archive(
        brain_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        document = await read_document(request, max_bytes=16_000_000)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(service.upload_archive, principal, brain_id, document)

    static = files("agent_memory").joinpath("web")
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def console():
        return static.joinpath("index.html").read_text(encoding="utf-8")

    return app
