import hashlib
import json
from contextlib import ExitStack, asynccontextmanager
from importlib.resources import files
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import Settings, configured_service
from .contracts import CONTRACT_VERSION, schema, validate
from .helpers import DomainError, Principal

RuntimeIdentifier = Annotated[str, Field(min_length=1, max_length=256)]
RuntimeFingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RuntimeCounter = Annotated[int, Field(ge=0, le=2**63 - 1)]
RuntimeRevision = Annotated[int, Field(ge=1, le=2**63 - 1)]
RuntimeLeaseSeconds = Annotated[int, Field(ge=1, le=300)]


class RuntimeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RuntimeBegin(RuntimeBody):
    turn_id: RuntimeIdentifier
    conversation_id: RuntimeIdentifier
    branch_id: RuntimeIdentifier
    input_fingerprint: RuntimeFingerprint


class RuntimeClaim(RuntimeBody):
    worker_id: RuntimeIdentifier
    lease_seconds: RuntimeLeaseSeconds = 30
    expected_revision: RuntimeRevision | None = None


class RuntimeLearningRetry(RuntimeBody):
    request_id: RuntimeIdentifier
    expected_fence: RuntimeCounter


class RuntimeRenew(RuntimeBody):
    lease_token: RuntimeIdentifier
    lease_seconds: RuntimeLeaseSeconds = 30
    expected_revision: RuntimeRevision | None = None


class RuntimeLeaseMutation(RuntimeBody):
    lease_token: RuntimeIdentifier
    expected_revision: RuntimeRevision


class RuntimeEventRef(RuntimeBody):
    event_id: RuntimeIdentifier
    revision: RuntimeRevision


class RuntimeOutput(RuntimeLeaseMutation):
    text: Annotated[str, Field(max_length=64000)]
    delivery_state: Literal["draft", "delivered", "partially_delivered", "cancelled"]


class RuntimeAdvance(RuntimeLeaseMutation):
    stage: Literal[
        "captured", "prepared", "generating", "generated", "delivering",
        "delivered",
    ]
    input_event_refs: Annotated[list[RuntimeEventRef], Field(max_length=64)] | None = None
    output_event_refs: Annotated[list[RuntimeEventRef], Field(max_length=64)] | None = None
    context_event_refs: Annotated[list[RuntimeEventRef], Field(max_length=64)] | None = None
    context_fingerprint: RuntimeFingerprint | None = None
    context_generation: RuntimeCounter | None = None
    working_revision: RuntimeCounter | None = None
    durable_sequence: RuntimeCounter | None = None
    context_tokens: RuntimeCounter | None = None
    recall_plan: dict | None = None
    response_review_policy: RuntimeFingerprint | None = None
    orientation_route: dict | None = None


class RuntimeResponseReviewReservation(RuntimeLeaseMutation):
    policy_fingerprint: RuntimeFingerprint
    request_fingerprint: RuntimeFingerprint
    draft_sha256: RuntimeFingerprint


class RuntimeResponseReviewCompletion(RuntimeLeaseMutation):
    request_fingerprint: RuntimeFingerprint
    status: Literal["approved", "rejected", "inconclusive"]
    issue_codes: Annotated[list[Literal[
        "current_input_misread", "unsupported_memory_claim", "applicability_override", "uncertainty_erased",
        "unconfirmed_action", "uncertain_current_input_fidelity", "uncertain_memory_fidelity",
        "uncertain_applicability", "uncertain_uncertainty", "uncertain_action_claims",
        "incomplete_review", "review_unavailable",
    ]], Field(max_length=16)]


class RuntimeRecallReservation(RuntimeLeaseMutation):
    input_bytes: Annotated[int, Field(ge=1, le=262144)]
    max_model_calls: Annotated[int, Field(ge=1, le=8)]
    max_input_bytes: Annotated[int, Field(ge=1, le=262144)]
    context_generation: RuntimeCounter
    source_refs: Annotated[list[RuntimeEventRef], Field(max_length=64)]
    request_fingerprint: RuntimeFingerprint


class RuntimeFail(RuntimeLeaseMutation):
    failure_code: Literal[
        "capture_failed", "prepare_failed", "generation_failed", "generation_uncertain",
        "delivery_failed", "delivery_uncertain", "commit_failed", "source_unavailable",
        "context_stale", "cancelled", "budget_exceeded", "dependency_unavailable", "internal_error",
    ]


def runtime_metadata(record: dict, *, include_lease: bool = False) -> dict:
    from .turns import PUBLIC_FIELDS

    allowed = {*PUBLIC_FIELDS, "lease_active", "unavailable_event_refs", "sources_available"}
    if include_lease:
        allowed.add("lease_token")
    result = {key: value for key, value in record.items() if key in allowed}
    if result.get("recall_plan") is not None:
        validate("RecallPlan", result["recall_plan"])
    if result.get("response_review") is not None:
        validate("ResponseReview", result["response_review"])
    if result.get("orientation_route") is not None:
        validate("OrientationRoute", result["orientation_route"])
    for field in ("input_event_refs", "output_event_refs", "context_event_refs", "unavailable_event_refs"):
        if field in result:
            result[field] = [
                {key: value for key, value in ref.items() if key in {"event_id", "revision"}}
                for ref in result[field]
            ]
    return result


def capabilities(*, semantic_enabled=False, embedding_enabled=False) -> dict:
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
            ("Semantic embedding recall is configured; index coverage may be partial."
             if embedding_enabled else "Semantic embeddings are not configured; recall uses lexical and structural cues."),
            ("Bounded semantic proposals and verification are configured; long-term memory quality is not validated."
             if semantic_enabled else "Model-driven semantic processing is not configured."),
            "Typed conditions use a fixed versioned registry; unresolved or expired context does not activate a rule.",
            "Provider billing is unknown; call and input-byte limits are enforced, monetary ceilings are not certified.",
        ],
    }
    validate("IntegrationCapabilities", result)
    return result


def create_app(settings: Settings | None = None, service=None, turns=None, jobs=None) -> FastAPI:
    settings = settings or Settings.from_env()
    resources = ExitStack()
    if service is None:
        service = resources.enter_context(configured_service(settings))

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            resources.close()

    app = FastAPI(title="Agent Memory", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.turns = turns
    app.state.jobs = jobs
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

    def turn_store():
        if app.state.turns is None:
            from .storage import Database
            from .turns import TurnStore

            app.state.turns = TurnStore(Database(
                settings.database_url, settings.migration_database_url,
            ), service=service)
        return app.state.turns

    def job_store():
        if app.state.jobs is None:
            from .jobs import JobStore
            from .storage import Database

            app.state.jobs = JobStore(Database(settings.database_url, settings.migration_database_url))
        return app.state.jobs

    def job_metadata(record):
        from .jobs import FAILURE_CODES, PUBLIC_FIELDS, sanitize_learning_summary

        result = {key: value for key, value in record.items() if key in {*PUBLIC_FIELDS, "lease_active"}}
        if result.get("last_error") is not None and result["last_error"] not in FAILURE_CODES:
            result["last_error"] = "internal_error"
        if result.get("kind") in {"orient", "consolidate"}:
            summary = sanitize_learning_summary(record.get("learning_summary"))
            if summary is not None:
                result["learning_summary"] = summary
        return result

    async def runtime_body(request: Request, model: type[RuntimeBody]) -> dict:
        document = await read_document(request)
        try:
            return model.model_validate(document).model_dump(exclude_none=True)
        except ValidationError as error:
            raise DomainError("validation_failed", "Runtime request fields are invalid.") from error

    def runtime_limit(request: Request) -> int:
        if set(request.query_params) - {"limit"} or len(request.query_params.getlist("limit")) > 1:
            raise DomainError("validation_failed", "Only one limit parameter is supported.")
        value = request.query_params.get("limit", "100")
        if len(value) > 3 or not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 200:
            raise DomainError("validation_failed", "Limit must be an integer from 1 to 200.")
        return int(value)

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
            "lease_conflict": 409, "stage_conflict": 409, "turn_terminal": 409,
            "source_unavailable": 409, "context_stale": 409,
            "response_review_required": 409, "response_review_uncertain": 409, "response_review_blocked": 409,
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
        return capabilities(semantic_enabled=getattr(service, "semantic_processor", None) is not None,
                            embedding_enabled=getattr(service, "embedding_provider", None) is not None)

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

    @app.get("/v1/brains/{brain_id}/runtime/status")
    def runtime_status(brain_id: str, principal: Principal = Depends(authenticate)):
        from .learning_progress import sanitize_learning_progress

        result = turn_store().status(principal, brain_id)
        fields = {
            "brain_id", "durable_sequence", "indexed_sequence", "dependency_generation",
            "scope_generation", "turn_count", "active_lease_count", "stage_counts", "pending_job_count",
            "response_review_counts", "response_review_calls",
        }
        public = {key: value for key, value in result.items() if key in fields}
        if "learning_progress" in result:
            public["learning_progress"] = sanitize_learning_progress(result["learning_progress"])
        return public

    @app.get("/v1/brains/{brain_id}/runtime/learning-progress")
    def learning_progress(brain_id: str, principal: Principal = Depends(authenticate)):
        from .learning_progress import sanitize_learning_progress

        return sanitize_learning_progress(job_store().learning_progress(
            principal, brain_id, configured=getattr(service, "semantic_processor", None) is not None,
        ))

    @app.get("/v1/brains/{brain_id}/runtime/jobs")
    def list_jobs(brain_id: str, request: Request, principal: Principal = Depends(authenticate)):
        return {"jobs": [job_metadata(record) for record in job_store().list(
            principal, brain_id, limit=runtime_limit(request),
        )]}

    @app.get("/v1/brains/{brain_id}/runtime/jobs/{job_id}")
    def get_job(brain_id: str, job_id: str, principal: Principal = Depends(authenticate)):
        return job_metadata(job_store().get(principal, brain_id, job_id))

    @app.post("/v1/brains/{brain_id}/runtime/jobs/{job_id}/retry-learning")
    async def retry_learning(
        brain_id: str, job_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeLearningRetry)
        from starlette.concurrency import run_in_threadpool

        if getattr(service, "semantic_processor", None) is None:
            await run_in_threadpool(job_store().get, principal, brain_id, job_id)
            raise DomainError("dependency_unavailable", "Model-driven learning is not configured.")
        result = await run_in_threadpool(job_store().retry_learning, principal, brain_id, job_id, **body)
        return job_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns", status_code=201)
    async def begin_turn(
        brain_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeBegin)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().begin, principal, brain_id, **body)
        return runtime_metadata(result)

    @app.get("/v1/brains/{brain_id}/runtime/turns")
    def list_turns(
        brain_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        return {"turns": [runtime_metadata(record) for record in turn_store().list(
            principal, brain_id, limit=runtime_limit(request),
        )]}

    @app.get("/v1/brains/{brain_id}/runtime/turns/{turn_id}")
    def get_turn(brain_id: str, turn_id: str, principal: Principal = Depends(authenticate)):
        return runtime_metadata(turn_store().get(principal, brain_id, turn_id))

    @app.get("/v1/brains/{brain_id}/runtime/turns/{turn_id}/sources")
    def turn_sources(brain_id: str, turn_id: str, principal: Principal = Depends(authenticate)):
        return turn_store().sources(principal, brain_id, turn_id)

    @app.get("/v1/brains/{brain_id}/runtime/turns/{turn_id}/history")
    def turn_history(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        fields = {"revision", "stage", "operation", "fence", "failure_code", "occurred_at"}
        return {"history": [
            {key: value for key, value in record.items() if key in fields}
            for record in turn_store().history(
                principal, brain_id, turn_id, limit=runtime_limit(request),
            )
        ]}

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/claim")
    async def claim_turn(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeClaim)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().claim, principal, brain_id, turn_id, **body)
        return runtime_metadata(result, include_lease=True)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/renew")
    async def renew_turn(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeRenew)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().renew, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/advance")
    async def advance_turn(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeAdvance)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().advance, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/recall-reservations")
    async def reserve_turn_recall(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeRecallReservation)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().reserve_recall, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/response-review/reserve")
    async def reserve_turn_response_review(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeResponseReviewReservation)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().reserve_response_review, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/response-review/complete")
    async def complete_turn_response_review(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeResponseReviewCompletion)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().complete_response_review, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/output")
    async def record_turn_output(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeOutput)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().record_output, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/orient")
    async def orient_turn(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeLeaseMutation)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(service.orient_turn, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/release")
    async def release_turn(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeLeaseMutation)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().release, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/fail")
    async def fail_turn(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeFail)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().fail, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

    @app.post("/v1/brains/{brain_id}/runtime/turns/{turn_id}/finish")
    async def finish_turn(
        brain_id: str, turn_id: str, request: Request, principal: Principal = Depends(authenticate),
    ):
        body = await runtime_body(request, RuntimeLeaseMutation)
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(turn_store().finish, principal, brain_id, turn_id, **body)
        return runtime_metadata(result)

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
