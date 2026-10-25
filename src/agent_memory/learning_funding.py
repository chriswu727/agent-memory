from .helpers import DomainError, digest
from .history_attention import POLICY as ATTENTION_POLICY
from .turns import _hash, _identifier

PERMISSION_VERSION = "private-brain-history-funding-v1"
ORIGIN_VERSION = "unattempted-history-funding-v1"
FUNDING_FIELDS = ("history_funding_permission", "history_funding_origin")


def _invalid():
    return DomainError("dependency_unavailable", "Historical funding authority is unavailable or inconsistent.")


def funding_permission(scope):
    if scope["visibility"] != "private":
        raise _invalid()
    return {"version": PERMISSION_VERSION, "visibility": "private",
            "access_policy_id": scope["access_policy_id"], "policy_revision": scope["policy_revision"],
            "branch_id": scope["branch_id"]}


def ordinary_root_metadata(scope):
    return {"history_funding_permission": funding_permission(scope),
            "history_attention_policy": ATTENTION_POLICY}


def validate_funding_metadata(payload):
    if "history_attention_policy" in payload and payload["history_attention_policy"] != ATTENTION_POLICY:
        raise _invalid()
    if "history_funding_permission" in payload:
        permission = payload["history_funding_permission"]
        if (not isinstance(permission, dict) or set(permission) != {
                "version", "visibility", "access_policy_id", "policy_revision", "branch_id"}
                or permission["version"] != PERMISSION_VERSION or permission["visibility"] != "private"
                or type(permission["policy_revision"]) is not int or permission["policy_revision"] != 1
                or payload.get("learning_phase") != "encoding"
                or payload.get("allocation_policy") != "encoding-integration-budget-v1"
                or any(key in payload for key in ("recovery_budget", "recovery_root_job_id", "encoding_replan_version"))):
            raise _invalid()
        _identifier(permission["access_policy_id"])
        _identifier(permission["branch_id"])
    if "history_funding_origin" in payload:
        origin = payload["history_funding_origin"]
        if (not isinstance(origin, dict) or set(origin) != {
                "version", "parent_job_id", "obligation_fingerprint", "manifest_fingerprint"}
                or origin["version"] != ORIGIN_VERSION
                or payload.get("learning_phase") != "history_integration"
                or payload.get("allocation_policy") != "encoding-integration-budget-v1"):
            raise _invalid()
        _identifier(origin["parent_job_id"])
        _hash(origin["obligation_fingerprint"])
        _hash(origin["manifest_fingerprint"])


def permitted_target(root, target, *, brain_id=None):
    payload = root["payload"]
    try:
        validate_funding_metadata(payload)
        permission = payload.get("history_funding_permission")
        original = payload["target"]
        if not isinstance(target, dict) or set(target) != {"conversation_ref", "branch_ref"}:
            return False
        for value in target.values():
            _identifier(value)
        return bool(permission and payload["authorization_root_job_id"] == root["id"]
                    and original["branch_ref"] == target["branch_ref"] == permission["branch_id"]
                    and (brain_id is None or permission["access_policy_id"] == f"owner:{brain_id}"))
    except (DomainError, ValueError, TypeError, KeyError):
        return False


def funding_origin(parent, manifest):
    return {"version": ORIGIN_VERSION, "parent_job_id": parent["id"],
            "obligation_fingerprint": digest(parent["payload"]["history_obligation_manifest"]),
            "manifest_fingerprint": digest(manifest)}


def origin_matches(payload, parent, authorized_manifest):
    try:
        validate_funding_metadata(payload)
        origin = payload["history_funding_origin"]
        original = parent["payload"]["history_obligation_manifest"]
        manifest = payload["history_obligation_manifest"]
        return (origin["parent_job_id"] == parent["id"]
                and origin["obligation_fingerprint"] == digest(original)
                and origin["manifest_fingerprint"] == digest(authorized_manifest)
                and parent["payload"]["learning_phase"] == "history_integration"
                and all(payload.get(key) == parent["payload"].get(key) for key in (
                    "target", "encoding_parent_job_id", "encoding_input_fingerprint", "history_cutoff_sequence",
                    "from_sequence", "through_sequence"))
                and all(original[key] == manifest[key] for key in (
                    "version", "input_refs", "input_fingerprint", "through_sequence"))
                and all(original[key] == authorized_manifest[key] for key in (
                    "version", "input_refs", "input_fingerprint", "through_sequence"))
                and (authorized_manifest == original
                     or not original["objectives"] and original["unresolved_discovery"] is True))
    except (DomainError, KeyError, ValueError, TypeError):
        return False


def funded_lineage_matches(row, root, rows):
    try:
        payload = row["payload"]
        if not permitted_target(root, payload["target"]):
            return False
        origin = payload["history_funding_origin"]
        parent = rows[origin["parent_job_id"]]
        anchor = rows[parent["payload"]["integration_continuation_job_id"]]
        return (type(parent["model_calls"]) is int and parent["model_calls"] == 0
                and type(parent["input_bytes"]) is int and parent["input_bytes"] == 0
                and anchor["payload"].get("history_funding_origin") == origin
                and anchor["payload"].get("authorization_root_job_id") == root["id"]
                and origin_matches(payload, parent, anchor["payload"].get("history_obligation_manifest")))
    except (DomainError, KeyError, ValueError, TypeError):
        return False


def validate_funded_root(conn, params, row, root, *, assigned=False):
    payload = row["payload"]
    validate_funding_metadata(payload)
    if not permitted_target(root, payload.get("target"), brain_id=params[2]):
        raise _invalid()
    origin = payload.get("history_funding_origin")
    if not isinstance(origin, dict):
        raise _invalid()
    parent = conn.execute(
        "SELECT id,kind,state,payload,model_calls,input_bytes FROM memory_jobs "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id)",
        (*params, origin.get("parent_job_id")),
    ).fetchone()
    anchor = (conn.execute(
        "SELECT id,payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s "
        "AND kind='consolidate' AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q "
        "WHERE q.tenant_id=memory_jobs.tenant_id AND q.owner_id=memory_jobs.owner_id "
        "AND q.brain_id=memory_jobs.brain_id AND q.object_kind='job' AND q.object_id=memory_jobs.id)",
        (*params, parent["payload"].get("integration_continuation_job_id")),
    ).fetchone() if parent is not None else None)
    if (parent is None or anchor is None or parent["kind"] != "consolidate"
            or parent["model_calls"] or parent["input_bytes"]
            or anchor["payload"].get("history_funding_origin") != origin
            or anchor["payload"].get("authorization_root_job_id") != root["id"]
            or not origin_matches(payload, parent, anchor["payload"].get("history_obligation_manifest"))
            or assigned and root["payload"].get("history_allocation_job_id") != row["id"]
            or conn.execute("SELECT 1 FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s "
                            "AND brain_id=%s AND job_id=%s LIMIT 1", (*params, parent["id"])).fetchone()):
        raise _invalid()
    identifiers = [item["objective_id"] for item in payload["history_obligation_manifest"]["objectives"]]
    if assigned and not set(identifiers) <= {
            item["objective_id"] for item in anchor["payload"]["history_obligation_manifest"]["objectives"]}:
        raise _invalid()
    if conn.execute(
        "SELECT 1 FROM memory_jobs j CROSS JOIN LATERAL jsonb_array_elements(CASE "
        "WHEN jsonb_typeof(j.result #> '{integration_manifest,objectives}')='array' "
        "THEN j.result #> '{integration_manifest,objectives}' "
        "WHEN jsonb_typeof(j.payload #> '{history_obligation_manifest,objectives}')='array' "
        "THEN j.payload #> '{history_obligation_manifest,objectives}' ELSE '[]'::jsonb END) objective "
        "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.id<>%s "
        "AND j.payload->>'encoding_parent_job_id'=%s "
        "AND j.payload->>'authorization_root_job_id'<>%s "
        "AND (j.model_calls>0 OR EXISTS (SELECT 1 FROM memory_cognitive_operations o "
        "WHERE o.tenant_id=j.tenant_id AND o.owner_id=j.owner_id AND o.brain_id=j.brain_id AND o.job_id=j.id)) "
        "AND objective->>'objective_id'=ANY(%s::text[]) LIMIT 1",
        (*params, row["id"], payload["encoding_parent_job_id"], root["id"], identifiers),
    ).fetchone():
        raise _invalid()


def validate_private_funding_sources(conn, params, row, root):
    permission = root["payload"]["history_funding_permission"]
    checked = conn.execute(
        "SELECT cardinality(j.source_event_ids) AS expected,count(DISTINCT e.id) AS available "
        "FROM memory_jobs j LEFT JOIN memory_events e ON e.tenant_id=j.tenant_id AND e.owner_id=j.owner_id "
        "AND e.brain_id=j.brain_id AND e.id=ANY(j.source_event_ids) AND e.is_current AND NOT e.retired "
        "AND NOT e.deleted AND NOT e.suppressed AND e.document #>> '{scope,visibility}'='private' "
        "AND e.document #>> '{scope,access_policy_id}'=%s AND e.document #> '{scope,policy_revision}'='1'::jsonb "
        "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.id=%s GROUP BY j.source_event_ids",
        (permission["access_policy_id"], *params, row["id"]),
    ).fetchone()
    if checked is None or not checked["expected"] or checked["expected"] != checked["available"]:
        raise _invalid()
