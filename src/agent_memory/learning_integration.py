from __future__ import annotations

import copy
from dataclasses import asdict, replace

from psycopg.types.json import Jsonb

from .cognitive_budget import cognitive_budget
from .consolidation_planning import plan_consolidation
from .helpers import DomainError, digest
from .history_semantics import integration_coverage, validate_manifest
from .jobs import NO_UNSETTLED_RETRY_POLICY, JobStore
from .learning_allocations import (
    ALLOCATION_POLICY,
    CONTRACT_VERSION,
    ENCODING_PHASE,
    HISTORY_PHASE,
    PHASE_BYTES,
    RECOVERY_POLICY,
    allocation_metadata,
    encoding_allocation_ready,
    freeze_input,
    phase,
)
from .learning_funding import funding_origin, permitted_target
from .learning_packages import (
    discover_manifest,
    empty_working,
    input_fingerprint,
    integration_package,
    snapshot_fingerprint,
)
from .local_semantics import HISTORY_INTEGRATION_MODE
from .semantic import LATEST_INPUT_CONTRACT_VERSION, SemanticResult

DEFERRED_REASON = "history_integration_deferred"
RECONSIDERATION_VERSION = "learning-reconsideration-v1"
HISTORY_REPLAN_VERSION = "unreserved-history-replan-v1"
SCAN_LIMIT = 8
SNAPSHOT_FIELDS = frozenset({
    "scope", "dependency_generation", "source_snapshot", "memory_snapshot", "episode_snapshot",
    "belief_relations", "belief_relation_snapshot", "working_snapshot", "working_revision", "sampling",
    "consolidation_plan", "proposal_mode", "input_contract_version", "operation_settlement_policy",
    "semantic_policy", "integration_manifest", "remaining_manifest",
})


def _skeleton():
    return {**asdict(SemanticResult("not_evaluated")), "publication": {}}


def _deferred(manifest, category):
    return {**_skeleton(), "integration_manifest": copy.deepcopy(manifest),
            "history_integration_reason": DEFERRED_REASON, "history_integration_category": category}


def _row(conn, params, identifier):
    row = conn.execute(
        "SELECT *,clock_timestamp() AS db_now FROM memory_jobs "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s FOR UPDATE",
        (*params, identifier),
    ).fetchone()
    if row is None:
        raise DomainError("dependency_unavailable", "Historical integration lineage is unavailable.")
    return row


def _wake(conn, params):
    conn.execute(
        "UPDATE memory_brains SET learning_reconsideration=jsonb_build_object('generation',"
        "coalesce((learning_reconsideration->>'generation')::bigint,0)+1) "
        "WHERE tenant_id=%s AND owner_id=%s AND id=%s", params,
    )


def notify_encoding_complete(conn, service, principal, brain):
    _wake(conn, service._params(principal, brain["id"]))


def _parent_fields(row):
    result = {"history_integration_job_id": row["id"], "history_integration_state": row["state"]}
    if row["state"] == "deferred":
        result.update(history_integration_reason=DEFERRED_REASON,
                      history_integration_category=row["result"]["history_integration_category"])
    return result


def _remaining_budget(conn, params, root_id):
    usage = conn.execute(
        "SELECT count(*) AS calls,coalesce(sum(o.input_bytes),0) AS bytes,"
        "count(*) FILTER (WHERE j.payload->>'learning_phase'='history_integration') AS phase_calls,"
        "coalesce(sum(o.input_bytes) FILTER (WHERE j.payload->>'learning_phase'='history_integration'),0) AS phase_bytes "
        "FROM memory_cognitive_operations o JOIN memory_jobs j ON j.tenant_id=o.tenant_id "
        "AND j.owner_id=o.owner_id AND j.brain_id=o.brain_id AND j.id=o.job_id "
        "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.authorization_root_job_id=%s",
        (*params, root_id),
    ).fetchone()
    root = _row(conn, params, root_id)
    budget = root["payload"]["allocation_budget"]
    return root, min(2 - usage["phase_calls"], budget["max_model_calls"] - usage["calls"]), min(
        PHASE_BYTES - usage["phase_bytes"], budget["max_input_bytes"] - usage["bytes"])


def _capacity(conn, service, principal, brain, row, policy):
    params = service._params(principal, brain["id"])
    root, calls, size = _remaining_budget(conn, params, row["payload"]["authorization_root_job_id"])
    assigned = root["payload"].get("history_allocation_job_id")
    root_ready = (encoding_allocation_ready(conn, params, root, allow_running=True)
                  if phase(root["payload"]) == ENCODING_PHASE else root["state"] in {"running", "completed"})
    if (phase(root["payload"]) == HISTORY_PHASE and assigned == row["id"]
            and row["payload"].get("history_replan_version") == HISTORY_REPLAN_VERSION
            and root["model_calls"] == root["input_bytes"] == 0):
        root_ready = True
    if (calls < 2 or size < min(policy.max_input_bytes, PHASE_BYTES)
            or policy.max_model_calls < 2
            or not root_ready
            or root["payload"]["allocation_policy"] != ALLOCATION_POLICY
            and phase(root["payload"]) != HISTORY_PHASE
            or assigned is not None and assigned != row["id"]):
        return "shared_allocation_exhausted"
    budget = cognitive_budget(conn, principal, brain["id"])
    if (budget["remaining"]["model_calls"] < 2
            or budget["remaining"]["input_bytes"] < min(policy.max_input_bytes, PHASE_BYTES)):
        return "brain_budget_unavailable"
    return None


def _snapshot(conn, service, principal, brain, row, package, selected, remaining, policy):
    working = empty_working(service._working(conn, principal, brain, row["payload"]["target"], package.sources))
    plan = plan_consolidation(package.sources, package.assertions, package.belief_relations, package.sampling)
    plan["objectives"] = []
    return {
        "scope": service._scope(principal, brain, row["payload"]["target"]),
        "dependency_generation": brain["dependency_generation"],
        "source_snapshot": [{"event_id": event["event_id"], "revision": event["revision"],
                             "content_sha256": event["content_sha256"]} for event in package.sources],
        "memory_snapshot": [{"memory_id": memory["memory_id"], "revision": memory["revision"],
                             "content_sha256": digest(memory)} for memory in package.assertions],
        "episode_snapshot": [], "belief_relations": list(package.belief_relations),
        "belief_relation_snapshot": [{"relation_id": item["relation_id"], "revision": item["revision"],
                                      "content_sha256": digest(item)} for item in package.belief_relations],
        "working_snapshot": working, "working_revision": working["revision"],
        "sampling": copy.deepcopy(package.sampling), "consolidation_plan": plan,
        "proposal_mode": HISTORY_INTEGRATION_MODE, "input_contract_version": LATEST_INPUT_CONTRACT_VERSION,
        "operation_settlement_policy": NO_UNSETTLED_RETRY_POLICY,
        "semantic_policy": {"version": "semantic-policy-v1", "limits": asdict(policy)},
        "integration_manifest": selected, "remaining_manifest": remaining,
    }


def _prepare(conn, service, principal, brain, row, manifest, policy):
    params = service._params(principal, brain["id"])
    if row["state"] != "deferred":
        return row
    validate_manifest(manifest)
    if "allocation_input_fingerprint" in row["payload"]:
        from .learning import _snapshot_policy, validate_snapshot

        snapshot = {key: copy.deepcopy(value) for key, value in row["result"].items() if key in SNAPSHOT_FIELDS}
        validate_snapshot(conn, service, principal, brain, snapshot)
        policy = _snapshot_policy(snapshot)
        reason = _capacity(conn, service, principal, brain, row, policy)
        if reason is None:
            freeze_input(conn, params, row["id"], input_fingerprint=snapshot_fingerprint(snapshot))
            conn.execute("UPDATE memory_jobs SET state='queued',result=%s,last_error=NULL "
                         "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(snapshot), *params, row["id"]))
        return _row(conn, params, row["id"])
    if not manifest["objectives"] and not manifest["unresolved_discovery"]:
        result = {**_skeleton(), "status": "completed", "learning_outcome": "no_proposals",
                  "integration_manifest": manifest, "history_integration": integration_coverage(
                      manifest, [], [], assessed=True), "bounded_discovery_assessed": True}
        conn.execute(
            "UPDATE memory_jobs SET state='completed',result=%s,last_error=NULL,completed_at=clock_timestamp(),"
            "updated_at=clock_timestamp() WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb(result), *params, row["id"]),
        )
        return _row(conn, params, row["id"])
    reason = _capacity(conn, service, principal, brain, row, policy)
    if reason is None:
        limited = replace(policy, max_input_bytes=min(policy.max_input_bytes, PHASE_BYTES),
                          max_model_calls=min(policy.max_model_calls, 2),
                          max_request_bytes=min(policy.max_request_bytes, PHASE_BYTES // 2))
        package, selected, remaining = integration_package(
            conn, service, principal, brain, row["payload"]["target"], manifest=manifest,
            policy=limited, input_contract_version=LATEST_INPUT_CONTRACT_VERSION,
        )
        if not selected["objectives"]:
            reason = "package_incomplete"
        else:
            snapshot = _snapshot(conn, service, principal, brain, row, package, selected, remaining, limited)
            root = _row(conn, params, row["payload"]["authorization_root_job_id"])
            if root["payload"]["target"] != row["payload"]["target"]:
                if (not permitted_target(root, row["payload"]["target"], brain_id=brain["id"])
                        or any(item["scope"]["visibility"] != "private"
                               for item in (*package.sources, *package.assertions, *package.belief_relations))):
                    raise DomainError("dependency_unavailable", "Private historical funding scope changed.")
            assigned = root["payload"].get("history_allocation_job_id")
            if assigned not in (None, row["id"]):
                raise DomainError("dependency_unavailable", "Historical funding is already assigned.")
            conn.execute(
                "UPDATE memory_jobs SET payload=payload || %s "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb({"history_allocation_job_id": row["id"]}), *params, root["id"]),
            )
            freeze_input(conn, params, row["id"], input_fingerprint=snapshot_fingerprint(snapshot))
            conn.execute(
                "UPDATE memory_jobs SET state='queued',result=%s,source_event_ids=%s,source_memory_ids=%s,"
                "last_error=NULL,updated_at=clock_timestamp() "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb(snapshot), [event["event_id"] for event in package.sources],
                 [memory["memory_id"] for memory in package.assertions], *params, row["id"]),
            )
            return _row(conn, params, row["id"])
    conn.execute(
        "UPDATE memory_jobs SET state='deferred',result=%s,last_error=%s,updated_at=clock_timestamp() "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
        (Jsonb(_deferred(manifest, reason)), DEFERRED_REASON, *params, row["id"]),
    )
    return _row(conn, params, row["id"])


def _insert(conn, service, principal, brain, *, identifier, payload, manifest):
    params = service._params(principal, brain["id"])
    payload = {**payload, "history_obligation_manifest": copy.deepcopy(manifest)}
    conn.execute(
        "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,state,payload,result,source_event_ids,"
        "source_memory_ids,max_model_calls,max_input_bytes) "
        "VALUES(%s,%s,%s,%s,'consolidate','deferred',%s,%s,%s,%s,2,%s) ON CONFLICT DO NOTHING",
        (*params, identifier, Jsonb(payload), Jsonb(_deferred(manifest, "package_incomplete")),
         [reference["event_id"] for reference in manifest["input_refs"]],
         [objective["target_memory_version"]["memory_id"] for objective in manifest["objectives"]], PHASE_BYTES),
    )
    row = _row(conn, params, identifier)
    if any(row["payload"].get(key) != value for key, value in payload.items()):
        raise DomainError("idempotency_conflict", "Historical integration identifier binds another obligation.")
    return row


def enqueue_integration(conn, service, principal, brain, job, snapshot, policy):
    if phase(job["payload"]) != ENCODING_PHASE:
        raise DomainError("stage_conflict", "Historical obligations require an encoding parent.")
    params = service._params(principal, brain["id"])
    window = snapshot["learning_window"]
    refs = snapshot["learning_input_refs"]
    manifest = discover_manifest(
        conn, service, principal, brain, job["payload"]["target"],
        through_sequence=window["attempted_through_sequence"], required_event_refs=refs, policy=policy,
        input_contract_version=LATEST_INPUT_CONTRACT_VERSION,
        attention_policy=job["payload"].get("history_attention_policy"),
    )
    identifier = "integrate:" + digest([job["id"], window["from_sequence"], window["attempted_through_sequence"],
                                       manifest["input_fingerprint"]])
    payload = {"target": copy.deepcopy(job["payload"]["target"]), "encoding_parent_job_id": job["id"],
               "from_sequence": window["from_sequence"], "through_sequence": window["attempted_through_sequence"],
               "encoding_input_fingerprint": manifest["input_fingerprint"],
               "history_cutoff_sequence": manifest["through_sequence"],
               **allocation_metadata(identifier, phase=HISTORY_PHASE,
                                     root_job_id=job["payload"]["authorization_root_job_id"],
                                     recovery=job["payload"]["allocation_policy"] == RECOVERY_POLICY)}
    if "history_attention_policy" in job["payload"]:
        payload["history_attention_policy"] = job["payload"]["history_attention_policy"]
    row = _insert(conn, service, principal, brain, identifier=identifier, payload=payload, manifest=manifest)
    _fund_oldest(conn, service, principal, brain, job, policy)
    row = _prepare(conn, service, principal, brain, row, manifest, policy)
    _wake(conn, params)
    return _parent_fields(row)


def _continuation(conn, service, principal, brain, parent, manifest, policy, *, root_id=None, replan=False):
    params = service._params(principal, brain["id"])
    root_id = root_id or parent["payload"]["authorization_root_job_id"]
    identifier = "integrate:" + digest([parent["id"], root_id, manifest])
    payload = {key: copy.deepcopy(value) for key, value in parent["payload"].items() if key not in {
        "allocation_input_fingerprint", "integration_continuation_job_id", "recovery_parent_job_id",
        "recovery_root_job_id", "recovery_job_id", "recovery_request_fingerprint", "recovery_expected_fence",
        "recovery_budget", "history_allocation_job_id", "last_reconsidered_token",
        "history_replan_version", "history_obligation_manifest", "last_history_replan_token",
    }}
    root = _row(conn, params, root_id)
    if root_id != parent["payload"]["authorization_root_job_id"]:
        payload.pop("history_funding_origin", None)
        if root["payload"]["target"] != parent["payload"]["target"]:
            if (not permitted_target(root, parent["payload"]["target"], brain_id=brain["id"])
                    or parent["model_calls"] or parent["input_bytes"]
                    or conn.execute("SELECT 1 FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s "
                                    "AND brain_id=%s AND job_id=%s LIMIT 1", (*params, parent["id"])).fetchone()):
                raise DomainError("dependency_unavailable", "Historical funding requires an unattempted obligation.")
            payload["history_funding_origin"] = funding_origin(parent, manifest)
    payload.update(allocation_metadata(identifier, phase=HISTORY_PHASE, root_job_id=root_id,
                                       recovery=root["payload"]["allocation_policy"] == RECOVERY_POLICY),
                   integration_continuation_parent_job_id=parent["id"],
                   history_cutoff_sequence=manifest["through_sequence"])
    if replan:
        payload["history_replan_version"] = HISTORY_REPLAN_VERSION
    child = _insert(conn, service, principal, brain, identifier=identifier, payload=payload, manifest=manifest)
    conn.execute(
        "UPDATE memory_jobs SET payload=payload || %s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
        (Jsonb({"integration_continuation_job_id": identifier}), *params, parent["id"]),
    )
    child = _prepare(conn, service, principal, brain, child, manifest, policy)
    _wake(conn, params)
    return child


def _fund_oldest(conn, service, principal, brain, donor, policy):
    if donor["payload"]["allocation_policy"] != ALLOCATION_POLICY:
        return
    params = service._params(principal, brain["id"])
    root = _row(conn, params, donor["payload"]["authorization_root_job_id"])
    broader = permitted_target(root, donor["payload"]["target"], brain_id=brain["id"])
    scope_clause = ("AND payload #>> '{target,branch_ref}'=%s " if broader else "AND payload->'target'=%s ")
    scope_value = donor["payload"]["target"]["branch_ref"] if broader else Jsonb(donor["payload"]["target"])
    rows = conn.execute(
        "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND state='deferred' AND model_calls=0 "
        "AND payload->>'learning_phase'='history_integration' " + scope_clause +
        "AND (payload->>'through_sequence')::bigint<%s "
        "AND NOT payload ?| ARRAY['integration_continuation_job_id','recovery_job_id'] "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id) "
        "ORDER BY (payload->>'from_sequence')::bigint,created_at,id LIMIT 8 FOR UPDATE",
        (*params, scope_value, donor["payload"]["from_sequence"]),
    ).fetchall()
    for old in rows:
        if "allocation_input_fingerprint" in old["payload"]:
            continue
        manifest = (old.get("result") or {}).get("integration_manifest")
        try:
            validate_manifest(manifest)
            manifest = _refresh_manifest(conn, service, principal, brain, old, manifest, policy)
            if not manifest["objectives"]:
                continue
            with conn.transaction():
                child = _continuation(conn, service, principal, brain, old, manifest, policy,
                                      root_id=donor["payload"]["authorization_root_job_id"])
            if child["state"] == "queued":
                return
        except (DomainError, ValueError, TypeError, KeyError):
            continue


def _refresh_manifest(conn, service, principal, brain, row, manifest, policy):
    if "attention" in manifest or manifest["objectives"] or not manifest["unresolved_discovery"]:
        return manifest
    rows = conn.execute(
        "SELECT result->'integration_manifest' AS manifest FROM memory_jobs "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND kind='consolidate' "
        "AND payload->>'learning_contract_version'=%s AND payload->>'learning_phase'='history_integration' "
        "AND payload->>'encoding_parent_job_id'=%s AND payload->>'encoding_input_fingerprint'=%s "
        "AND payload->'target'=%s AND (model_calls>0 OR state='completed')",
        (*service._params(principal, brain["id"]), CONTRACT_VERSION,
         row["payload"]["encoding_parent_job_id"], row["payload"]["encoding_input_fingerprint"],
         Jsonb(row["payload"]["target"])),
    )
    excluded = set()
    for prior in rows:
        try:
            validate_manifest(prior["manifest"])
            if (prior["manifest"]["input_fingerprint"] != manifest["input_fingerprint"]
                    or prior["manifest"]["input_refs"] != manifest["input_refs"]):
                return manifest
        except (ValueError, KeyError, TypeError):
            return manifest
        excluded.update((item["target_memory_version"]["memory_id"], item["target_memory_version"]["revision"])
                        for item in prior["manifest"]["objectives"])
    return discover_manifest(
        conn, service, principal, brain, row["payload"]["target"],
        through_sequence=manifest["through_sequence"], required_event_refs=manifest["input_refs"], policy=policy,
        input_contract_version=LATEST_INPUT_CONTRACT_VERSION, excluded_versions=tuple(sorted(excluded)),
    )


def finish_integration(conn, service, principal, brain, job, snapshot, result, policy):
    remaining = copy.deepcopy(snapshot["remaining_manifest"])
    if not remaining["objectives"] and not remaining["unresolved_discovery"]:
        return {}
    child = _continuation(conn, service, principal, brain, job, remaining, policy)
    return {"integration_continuation_job_id": child["id"]}


def fail_integration(service, principal, brain_id, job, snapshot, result):
    store = JobStore(service.database)
    data = {**snapshot, **asdict(result), "publication": {}}
    failure = ("budget_exceeded" if result.status == "budget_exhausted" else "provider_failed"
               if result.status in {"provider_unavailable", "verification_unavailable"} else "invalid_proposal")
    with store._transaction(principal, brain_id) as conn:
        row = store._row(conn, principal, brain_id, job["id"])
        store._lease(row, job["lease_token"], job["fence"])
        deferred = row["model_calls"] == 0 and result.status == "budget_exhausted"
        if deferred:
            data = {**snapshot, **_skeleton(), "history_integration_reason": DEFERRED_REASON,
                    "history_integration_category": "brain_budget_unavailable"}
        else:
            from .learning import _snapshot_policy, validate_snapshot

            brain = service._brain(conn, principal, brain_id)
            try:
                validate_snapshot(conn, service, principal, brain, snapshot)
            except DomainError:
                pass
            else:
                data.update(finish_integration(conn, service, principal, brain, row, snapshot, result,
                                               _snapshot_policy(snapshot)))
        conn.execute(
            "UPDATE memory_jobs SET result=%s,state=%s,last_error=%s,lease_token_digest=NULL,"
            "lease_expires_at=NULL,worker_id=NULL,updated_at=clock_timestamp() "
            "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb(data), "deferred" if deferred else "cancelled", DEFERRED_REASON if deferred else failure,
             *store._params(principal, brain_id), job["id"]),
        )
    return data


def _donor(conn, service, principal, brain, row, policy):
    params = service._params(principal, brain["id"])
    candidates = conn.execute(
        "SELECT authorization_root_job_id FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND state='completed' AND payload->>'learning_contract_version'=%s "
        "AND payload->>'learning_phase'='encoding' AND payload->>'allocation_policy'=%s "
        "AND (payload->'target'=%s OR (payload ? 'history_funding_permission' "
        "AND payload #>> '{target,branch_ref}'=%s)) AND NOT payload ? 'history_allocation_job_id' "
        "AND (payload->>'from_sequence')::bigint>%s "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id) ORDER BY created_at,id LIMIT 8 FOR UPDATE",
        (*params, CONTRACT_VERSION, ALLOCATION_POLICY, Jsonb(row["payload"]["target"]),
         row["payload"]["target"]["branch_ref"], row["payload"]["through_sequence"]),
    ).fetchall()
    for candidate in candidates:
        identifier = candidate["authorization_root_job_id"]
        root = _row(conn, params, identifier)
        if (root["payload"]["target"] != row["payload"]["target"]
                and not permitted_target(root, row["payload"]["target"], brain_id=brain["id"])):
            continue
        probe = {**row, "payload": {**row["payload"], "authorization_root_job_id": identifier}}
        if _capacity(conn, service, principal, brain, probe, policy) is None:
            return identifier
    return None


def _replan_unreserved_history(conn, service, principal, brain, policy, token):
    params = service._params(principal, brain["id"])
    rows = conn.execute(
        "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND payload->>'learning_contract_version'=%s "
        "AND payload->>'learning_phase'='history_integration' AND model_calls=0 AND input_bytes=0 "
        "AND (state='superseded' OR state='cancelled' AND last_error IN ('context_stale','source_unavailable')) "
        "AND NOT payload ?| ARRAY['integration_continuation_job_id','recovery_job_id'] "
        "AND payload->>'last_history_replan_token' IS DISTINCT FROM %s "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id) ORDER BY created_at,id LIMIT %s FOR UPDATE",
        (*params, CONTRACT_VERSION, token, SCAN_LIMIT),
    ).fetchall()
    for old in rows:
        try:
            if phase(old["payload"]) != HISTORY_PHASE:
                continue
            manifest = old["payload"].get("history_obligation_manifest")
            validate_manifest(manifest)
            if (manifest["input_fingerprint"] != old["payload"]["encoding_input_fingerprint"]
                    or manifest["through_sequence"] != old["payload"]["history_cutoff_sequence"]):
                raise ValueError
            root_id = old["payload"]["authorization_root_job_id"]
            root, calls, _ = _remaining_budget(conn, params, root_id)
            if calls != 2 or conn.execute(
                "SELECT 1 FROM memory_cognitive_operations o JOIN memory_jobs j ON j.tenant_id=o.tenant_id "
                "AND j.owner_id=o.owner_id AND j.brain_id=o.brain_id AND j.id=o.job_id "
                "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.authorization_root_job_id=%s "
                "AND j.payload->>'learning_phase'='history_integration' LIMIT 1", (*params, root_id),
            ).fetchone():
                continue
            if conn.execute(
                "SELECT 1 FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
                "AND authorization_root_job_id=%s AND payload->>'learning_phase'='history_integration' "
                "AND (model_calls<>0 OR input_bytes<>0) LIMIT 1", (*params, root_id),
            ).fetchone():
                continue
            assigned = root["payload"].get("history_allocation_job_id")
            if assigned not in (None, old["id"]):
                continue
            with conn.transaction():
                integration_package(conn, service, principal, brain, old["payload"]["target"], manifest=manifest,
                                    policy=policy, input_contract_version=LATEST_INPUT_CONTRACT_VERSION)
                successor = "integrate:" + digest([old["id"], root_id, manifest])
                conn.execute(
                    "UPDATE memory_jobs SET payload=payload || %s "
                    "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                    (Jsonb({"history_allocation_job_id": successor}), *params, root_id),
                )
                _continuation(conn, service, principal, brain, old, manifest, policy, replan=True)
        except (DomainError, ValueError, TypeError, KeyError):
            pass
        finally:
            conn.execute(
                "UPDATE memory_jobs SET payload=payload || %s "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (Jsonb({"last_history_replan_token": token}), *params, old["id"]),
            )


def reconsider(conn, service, principal, brain):
    params = service._params(principal, brain["id"])
    budget = cognitive_budget(conn, principal, brain["id"])
    policy = service.semantic_processor.policy
    current = conn.execute(
        "SELECT learning_reconsideration,clock_timestamp() AS db_now FROM memory_brains "
        "WHERE tenant_id=%s AND owner_id=%s AND id=%s", params,
    ).fetchone()
    state = current["learning_reconsideration"]
    generation = state.get("generation", 0)
    token = digest([brain["durable_sequence"], brain["dependency_generation"], brain["scope_generation"],
                    brain["indexed_sequence"], budget["revision"], budget["remaining"], asdict(policy), generation])
    _replan_unreserved_history(conn, service, principal, brain, policy, token)
    if state.get("token") == token and state.get("done") is True:
        return
    cursor = state.get("cursor") if state.get("token") == token else None
    clause = "AND (created_at,id)>(%s::timestamptz,%s) " if cursor else ""
    rows = conn.execute(
        "SELECT * FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND kind='consolidate' AND state='deferred' AND payload->>'learning_contract_version'=%s "
        "AND payload->>'learning_phase'='history_integration' "
        "AND NOT payload ?| ARRAY['integration_continuation_job_id','recovery_job_id'] "
        "AND NOT EXISTS (SELECT 1 FROM memory_recovery_quarantine q WHERE q.tenant_id=memory_jobs.tenant_id "
        "AND q.owner_id=memory_jobs.owner_id AND q.brain_id=memory_jobs.brain_id "
        "AND q.object_kind='job' AND q.object_id=memory_jobs.id) "
        + clause + "ORDER BY created_at,id LIMIT %s FOR UPDATE",
        (*params, CONTRACT_VERSION, *(cursor or ()), SCAN_LIMIT),
    ).fetchall()
    for row in rows:
        if row["model_calls"] or row["payload"].get("last_reconsidered_token") == token:
            continue
        if phase(row["payload"]) != HISTORY_PHASE:
            continue
        manifest = (row.get("result") or {}).get("integration_manifest")
        try:
            validate_manifest(manifest)
            required = [{"event_id": ref["event_id"], "revision": ref["revision"]} for ref in manifest["input_refs"]]
            sources = service._events(conn, principal, brain, event_ids=[ref["event_id"] for ref in required], limit=8)
            if input_fingerprint(sources) != row["payload"]["encoding_input_fingerprint"]:
                raise DomainError("source_unavailable", "Historical integration inputs changed.")
            frozen = "allocation_input_fingerprint" in row["payload"]
            refreshed = manifest if frozen else _refresh_manifest(conn, service, principal, brain, row, manifest, policy)
            reason = _capacity(conn, service, principal, brain, row, policy)
            donor = (_donor(conn, service, principal, brain, row, policy)
                     if not frozen and reason == "shared_allocation_exhausted" else None)
            with conn.transaction():
                if donor is not None or refreshed != manifest:
                    _continuation(conn, service, principal, brain, row, refreshed, policy, root_id=donor)
                else:
                    _prepare(conn, service, principal, brain, row, manifest, policy)
        except (DomainError, ValueError, KeyError, TypeError) as error:
            code = error.code if isinstance(error, DomainError) and error.code in {
                "source_unavailable", "context_stale"} else "dependency_unavailable"
            conn.execute(
                "UPDATE memory_jobs SET state='cancelled',last_error=%s,result=NULL "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                (code, *params, row["id"]),
            )
        conn.execute(
            "UPDATE memory_jobs SET payload=payload || %s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb({"last_reconsidered_token": token}), *params, row["id"]),
        )
    updated = conn.execute("SELECT learning_reconsideration FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                           params).fetchone()["learning_reconsideration"]
    if updated.get("generation", 0) == generation:
        cursor = [rows[-1]["created_at"].isoformat(), rows[-1]["id"]] if rows else cursor
        conn.execute("UPDATE memory_brains SET learning_reconsideration=%s WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                     (Jsonb({"version": RECONSIDERATION_VERSION, "generation": generation, "token": token,
                             "cursor": cursor, "done": len(rows) < SCAN_LIMIT}), *params))
