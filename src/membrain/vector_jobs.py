import json

from psycopg.types.json import Jsonb

from .embeddings import EmbeddingRequest, EmbeddingResult, EmbeddingSpace
from .helpers import DomainError, digest, uid
from .jobs import JobStore


def _call(service, principal, brain_id, job, request):
    jobs = JobStore(service.database)
    provider = service.embedding_provider
    lease = {"lease_token": job["lease_token"], "fence": job["fence"]}
    try:
        metadata = provider.request_metadata(request)
    except ValueError:
        raise DomainError("budget_exceeded", "Embedding input exceeds the provider request limits.") from None
    operation_id = f"embedding:{job['fence']}"
    reserved = jobs.reserve_operation(
        principal, brain_id, job["id"], **lease, operation_id=operation_id,
        **metadata, max_model_calls=1, max_input_bytes=262144,
    )
    if not reserved["newly_reserved"]:
        raise DomainError("budget_exceeded", "Embedding was already reserved.")
    try:
        result = provider(request)
        if (result.space != provider.space or len(result.vectors) != len(request.texts)
                or result.request_fingerprint != metadata["request_fingerprint"]):
            raise DomainError("invalid_evidence", "Embedding response does not match its request.")
    except Exception:
        jobs.settle_operation(principal, brain_id, job["id"], **lease, operation_id=operation_id, outcome="uncertain")
        raise DomainError("dependency_unavailable", "Embedding provider is unavailable.", True) from None
    jobs.settle_operation(principal, brain_id, job["id"], **lease, operation_id=operation_id,
                          outcome="completed", usage=result.usage, response_fingerprint=digest(result.vectors))
    return result


def embed_memory(service, principal, brain_id, job):
    params = service._params(principal, brain_id)
    version = job["payload"]
    with service.database.transaction(principal) as conn:
        service._brain(conn, principal, brain_id)
        row = conn.execute(
            "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND is_current AND NOT invalidated AND NOT suppressed AND NOT deleted",
            (*params, version["memory_id"], version["revision"]),
        ).fetchone()
        if row is None or row["document"] is None:
            raise DomainError("source_unavailable", "Embedding source is unavailable.")
        statement = row["document"]["statement"]
    result = _call(service, principal, brain_id, job, EmbeddingRequest((statement,)))

    def publish(conn, _):
        row = conn.execute(
            "SELECT document FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=%s AND revision=%s AND is_current AND NOT invalidated AND NOT suppressed AND NOT deleted",
            (*params, version["memory_id"], version["revision"]),
        ).fetchone()
        if not row or not row["document"] or row["document"]["statement"] != statement:
            raise DomainError("source_unavailable", "Embedding source changed.")
        conn.execute(
            "INSERT INTO memory_embeddings(tenant_id,owner_id,brain_id,memory_id,memory_revision,model,model_version,"
            "dimensions,content_hash,embedding) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector) ON CONFLICT DO NOTHING",
            (*params, version["memory_id"], version["revision"], result.space.model, result.space.model_version,
             result.space.dimensions, digest(statement), json.dumps(result.vectors[0])),
        )

    JobStore(service.database).complete(principal, brain_id, job["id"], lease_token=job["lease_token"],
                                       fence=job["fence"], publish=publish)


def query_embedding(service, principal, request):
    from .learning import validate_snapshot

    payload = request["payload"]
    if (service.embedding_provider is None or request["action"] not in {"prepare", "recall"}
            or not payload.get("budget", {}).get("max_embedding_calls", 0)):
        return None, None, 0
    brain_id = request["target"]["brain_ref"]
    params = service._params(principal, brain_id)
    fingerprint = digest({"action": request["action"], "target": request["target"], "payload": payload})
    job_id = "query:" + digest([payload.get("idempotency_key", request["request_id"]), request["action"]])
    with service.database.transaction(principal) as conn:
        brain = service._brain(conn, principal, brain_id)
        cached = conn.execute(
            "SELECT request_hash FROM memory_requests WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND action=%s AND key=%s",
            (*params, request["action"], payload.get("idempotency_key", request["request_id"])),
        ).fetchone()
        if cached:
            return None, None, 0
        events = []
        if request["action"] == "prepare":
            events = service._events(conn, principal, brain, event_ids=payload["latest_input_event_ids"], limit=128)
            lookup = {e["event_id"]: e for e in events}
            if any(key not in lookup for key in payload["latest_input_event_ids"]):
                raise DomainError("source_unavailable", "Query input is unavailable.")
            text = "\n".join(lookup[key]["observed"]["content"].get("text", "") for key in payload["latest_input_event_ids"])
        else:
            text = " ".join([payload["query"], *payload["additional_cues"]])
        if not text.strip():
            return None, None, 0
        snapshot = {"scope": service._scope(principal, brain, request["target"]), "source_snapshot": [
            {"event_id": e["event_id"], "revision": e["revision"], "content_sha256": e["content_sha256"]} for e in events]}
        conn.execute(
            "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state,source_event_ids) "
            "VALUES(%s,%s,%s,%s,'query_embedding',%s,'queued',%s) ON CONFLICT DO NOTHING",
            (*params, job_id, Jsonb({"request_hash": fingerprint}), [e["event_id"] for e in events]),
        )
        existing = conn.execute("SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                                (*params, job_id)).fetchone()
        if existing["payload"].get("request_hash") != fingerprint:
            raise DomainError("idempotency_conflict", "Query job already binds another request.")
        if existing["state"] == "completed" and existing["result"]:
            data = existing["result"]
            validate_snapshot(conn, service, principal, brain, data["snapshot"])
            return EmbeddingResult(EmbeddingSpace(**data["space"]), (tuple(data["vector"]),), data["usage"],
                                   data["fingerprint"], data["input_bytes"]), data["snapshot"], 1
    jobs = JobStore(service.database)
    job = jobs.claim(principal, brain_id, worker_id=uid(), job_id=job_id, kinds=["query_embedding"], lease_seconds=30)
    if job is None:
        raise DomainError("dependency_unavailable", "Query embedding is unavailable or already running.", True)
    try:
        timeout = min(10, payload["budget"]["max_wall_time_ms"] / 1000)
        result = _call(service, principal, brain_id, job, EmbeddingRequest((text,), timeout_seconds=timeout))

        def publish(conn, _):
            brain = service._brain(conn, principal, brain_id)
            validate_snapshot(conn, service, principal, brain, snapshot)
            data = {"snapshot": snapshot, "space": result.space.descriptor(), "vector": result.vectors[0],
                    "usage": result.usage, "fingerprint": result.request_fingerprint, "input_bytes": result.input_bytes}
            conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(data), *params, job_id))

        jobs.complete(principal, brain_id, job_id, lease_token=job["lease_token"], fence=job["fence"], publish=publish)
        return result, snapshot, 1
    except Exception:
        try:
            jobs.fail(principal, brain_id, job_id, lease_token=job["lease_token"], fence=job["fence"],
                      failure_code="provider_uncertain", retry=False)
        except DomainError:
            pass
        raise
