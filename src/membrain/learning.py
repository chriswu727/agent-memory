import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from . import cognition
from .consolidation_planning import plan_consolidation, select_consolidation_candidates
from .contracts import validate
from .helpers import DomainError, digest, now, uid
from .jobs import NO_UNSETTLED_RETRY_POLICY, JobStore
from .learning_allocations import ENCODING_PHASE, HISTORY_PHASE, freeze_input, phase
from .learning_packages import empty_working, local_neighborhood, snapshot_fingerprint
from .learning_recovery import POLICY_REFRESH_VERSION
from .learning_windows import bounds, record_progress, recover_windows, select_window
from .local_semantics import BOUNDED_MODES, HISTORY_INTEGRATION_MODE, LOCAL_OBSERVATION_MODE
from .semantic import (
    GENERAL_PROPOSAL_MODE,
    LATEST_INPUT_CONTRACT_VERSION,
    ORIENT_PROPOSAL_MODE,
    CognitiveBatch,
    SemanticPolicy,
    SemanticResult,
    check_result_mode,
    validate_input_contract,
)
from .turns import TurnStore, _hash, _identifier, _integer, _refs


def _snapshot_policy(snapshot, default=None):
    if "semantic_policy" not in snapshot:
        return default
    value = snapshot["semantic_policy"]
    try:
        if (not isinstance(value, dict) or set(value) != {"version", "limits"}
                or value["version"] != "semantic-policy-v1"
                or not isinstance(value["limits"], dict)
                or set(value["limits"]) != set(SemanticPolicy.__dataclass_fields__)):
            raise ValueError
        return SemanticPolicy(**value["limits"])
    except (TypeError, ValueError):
        raise DomainError("context_stale", "Cognitive policy snapshot is malformed or unsupported.") from None


def _snapshot_proposal_mode(snapshot, kind=None):
    mode = snapshot.get("proposal_mode", GENERAL_PROPOSAL_MODE)
    if (mode not in (GENERAL_PROPOSAL_MODE, ORIENT_PROPOSAL_MODE, *BOUNDED_MODES)
            or kind is not None and (kind != "orient" and mode == ORIENT_PROPOSAL_MODE
                                     or kind != "consolidate" and mode in BOUNDED_MODES)):
        raise DomainError("context_stale", "Cognitive proposal mode is unavailable for this task.")
    return mode


def _snapshot_input_contract(snapshot):
    version = snapshot.get("input_contract_version")
    try:
        validate_input_contract(version)
    except ValueError as error:
        raise DomainError("context_stale", "Cognitive input contract is unavailable.") from error
    return version


def _current_input_authorization(result):
    binding = result.get("current_input_binding")
    try:
        refs = _refs(result.get("current_input_refs", []))
        if refs or binding is not None:
            if not isinstance(binding, dict) or set(binding) != {"turn_id", "job_id"} or not refs:
                raise DomainError("validation_failed", "Missing current input binding.")
            for value in binding.values():
                _identifier(value)
            scope = result.get("scope")
            validate("BoundScope", scope)
            for key in ("conversation_id", "branch_id"):
                _identifier(scope[key])
    except DomainError as error:
        raise DomainError("context_stale", "Current input authorization is malformed.") from error
    return refs, binding


def _validate_current_input_binding(conn, service, principal, brain, result, events):
    refs, binding = _current_input_authorization(result)
    if not refs and binding is None:
        return
    turn = conn.execute(
        "SELECT t.input_event_refs,t.conversation_id,t.branch_id FROM memory_runtime_turns t "
        "JOIN memory_jobs j ON j.tenant_id=t.tenant_id AND j.owner_id=t.owner_id AND j.brain_id=t.brain_id "
        "AND j.id=t.orientation_job_id WHERE t.tenant_id=%s AND t.owner_id=%s AND t.brain_id=%s "
        "AND t.turn_id=%s AND j.id=%s AND j.kind='orient' AND j.payload->>'turn_id'=t.turn_id "
        "AND j.state NOT IN ('cancelled','superseded')",
        (*service._params(principal, brain["id"]), binding["turn_id"], binding["job_id"]),
    ).fetchone()
    scope = result.get("scope", {})
    if (not turn or turn["input_event_refs"] != refs
            or (turn["conversation_id"], turn["branch_id"]) != (scope.get("conversation_id"), scope.get("branch_id"))
            or any((ref["event_id"], ref["revision"]) not in events for ref in refs)):
        raise DomainError("context_stale", "Current input authorization changed.")


def _learning_inputs(snapshot, job, events):
    if _snapshot_input_contract(snapshot) is None:
        return ()
    try:
        refs = _refs(snapshot.get("learning_input_refs", []))
        if "learning_input_refs" not in snapshot or not refs and "learning_window" not in snapshot:
            return ()
        window, payload, scope = snapshot["learning_window"], job["payload"], snapshot["scope"]
        validate("BoundScope", scope)
        start, end = bounds(payload)
        attempted = _integer(window["attempted_through_sequence"], 1)
        if (job["kind"] != "consolidate"
                or _snapshot_proposal_mode(snapshot) not in (GENERAL_PROPOSAL_MODE, LOCAL_OBSERVATION_MODE)
                or snapshot.get("current_input_refs") or snapshot.get("current_input_binding") is not None
                or bounds(window) != (start, end) or not start <= attempted <= end
                or _integer(window["input_count"]) != len(refs)
                or any(payload["target"][target] != scope[field] for target, field in (
                    ("conversation_ref", "conversation_id"), ("branch_ref", "branch_id")))):
            raise ValueError("Learning window binding differs")
        for field in ("conversation_id", "branch_id"):
            _identifier(scope[field])
        lookup = {(event["event_id"], event["revision"]): event for event in events}
        sequences = []
        for ref in refs:
            event = lookup[(ref["event_id"], ref["revision"])]
            sequence = _integer(event["sequence"], 1)
            if (not start <= sequence <= attempted or not cognition._scope_allows(event["scope"], scope)
                    or any(event["scope"][field] != scope[field] for field in ("conversation_id", "branch_id"))
                    or event["observed"]["delivery_state"] not in ("received", "delivered", "partially_delivered")
                    or event["observed"]["event_kind"] in ("source_correction", "delivery_receipt")):
                raise ValueError("Learning source differs from its window")
            sequences.append(sequence)
        if sequences != sorted(set(sequences)):
            raise ValueError("Learning inputs are not strictly ordered")
    except (DomainError, ValueError, KeyError, TypeError) as error:
        raise DomainError("context_stale", "Learning input window is malformed or changed.") from error
    return tuple(copy.deepcopy(refs))


def validate_snapshot(conn, service, principal, brain, result):
    events, _ = _validated_snapshot(conn, service, principal, brain, result)
    return events


def _snapshot_documents(conn, service, principal, brain, refs, table, identifier, eligibility):
    try:
        if not isinstance(refs, (list, tuple)):
            raise DomainError("validation_failed", "Invalid snapshot references.")
        versions = set()
        for ref in refs:
            if not isinstance(ref, dict):
                raise DomainError("validation_failed", "Invalid snapshot reference.")
            versions.add((_identifier(ref[identifier]), _integer(ref["revision"], 1)))
            _hash(ref["content_sha256"])
    except (DomainError, KeyError) as error:
        raise DomainError("context_stale", "Cognitive document snapshot is malformed.") from error
    if not versions:
        return {}
    versions = sorted(versions)
    rows = conn.execute(
        "SELECT id,revision,document FROM " + table + " WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
        "AND (id,revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) AND " + eligibility,
        (*service._params(principal, brain["id"]), [key[0] for key in versions], [key[1] for key in versions]),
    ).fetchall()
    return {(row["id"], row["revision"]): row["document"] for row in rows}


def _validated_snapshot(conn, service, principal, brain, result):
    _snapshot_proposal_mode(result)
    _snapshot_input_contract(result)
    _snapshot_policy(result)
    scope = result.get("scope", {})
    try:
        validate("BoundScope", scope)
    except DomainError as error:
        raise DomainError("context_stale", "Cognitive scope binding is malformed.") from error
    if scope.get("scope_generation") != brain["scope_generation"]:
        raise DomainError("context_stale", "Cognitive scope changed.")
    if "dependency_generation" in result and result["dependency_generation"] != brain["dependency_generation"]:
        raise DomainError("context_stale", "Cognitive dependency generation changed.")
    refs = result.get("source_snapshot", [])
    events = service._events(conn, principal, brain, event_ids=[r["event_id"] for r in refs], limit=128)
    lookup = {(e["event_id"], e["revision"]): e for e in events}
    for ref in refs:
        event = lookup.get((ref["event_id"], ref["revision"]))
        if (event is None or event["content_sha256"] != ref["content_sha256"]
                or not cognition._scope_allows(event["scope"], scope)):
            raise DomainError("source_unavailable", "Cognitive source changed.")
    _validate_current_input_binding(conn, service, principal, brain, result, lookup)
    superseded = service._superseded_evidence(conn, principal, brain, [r["event_id"] for r in refs])
    if any((ref["event_id"], ref["event_revision"]) in lookup
           and lookup[(ref["event_id"], ref["event_revision"])]["content_sha256"] == ref["source_content_sha256"]
           for ref in superseded):
        raise DomainError("context_stale", "Cognitive source was corrected.")
    references = result.get("memory_snapshot", [])
    memories = _snapshot_documents(conn, service, principal, brain, references, "memory_assertions", "memory_id",
                                   "NOT invalidated AND NOT deleted AND NOT suppressed")
    for ref in references:
        document = memories.get((ref["memory_id"], ref["revision"]))
        if (document is None or document["lifecycle"] == "retracted"
                or digest(document) != ref["content_sha256"]
                or not cognition._scope_allows(service._rebind(document, principal, brain)["scope"], scope)):
            raise DomainError("source_unavailable", "Cognitive parent changed.")
    references = result.get("episode_snapshot", [])
    episodes = _snapshot_documents(conn, service, principal, brain, references, "memory_episodes", "episode_id",
                                   "is_current AND NOT invalidated AND NOT deleted AND document IS NOT NULL")
    for ref in references:
        document = episodes.get((ref["episode_id"], ref["revision"]))
        if (document is None or digest(document) != ref["content_sha256"]
                or not cognition._scope_allows(service._rebind(document, principal, brain)["scope"], scope)):
            raise DomainError("context_stale", "Cognitive episode changed.")
    references = result.get("belief_relation_snapshot", [])
    relations = _snapshot_documents(conn, service, principal, brain, references, "memory_belief_relations", "relation_id",
                                    "NOT invalidated AND NOT deleted AND document IS NOT NULL")
    for ref in references:
        raw = relations.get((ref["relation_id"], ref["revision"]))
        if raw is None:
            raise DomainError("context_stale", "Cognitive belief relationship changed.")
        document = service._rebind(raw, principal, brain)
        if digest(document) != ref["content_sha256"] or not cognition._scope_allows(document["scope"], scope):
            raise DomainError("context_stale", "Cognitive belief relationship changed.")
    return events, {"memory_snapshot": memories, "episode_snapshot": episodes}


def orientation_result(conn, service, principal, brain, row):
    if not row.get("orientation_job_id"):
        return None
    job = conn.execute(
        "SELECT kind,payload,state,result FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
        (*service._params(principal, brain["id"]), row["orientation_job_id"]),
    ).fetchone()
    if not job or job["state"] in ("cancelled", "superseded"):
        raise DomainError("context_stale", "Orientation was invalidated.")
    if job["kind"] != "orient" or job["payload"].get("turn_id") != row["turn_id"]:
        raise DomainError("invalid_evidence", "Orientation belongs to another turn.")
    if job["state"] != "completed" or not job["result"]:
        return None
    result = job["result"]
    mode = _snapshot_proposal_mode(result, job["kind"])
    check_result_mode(mode, result)
    _current_input_authorization(result)
    scope = result.get("scope", {})
    if not isinstance(scope, dict):
        raise DomainError("context_stale", "Cognitive scope binding is malformed.")
    if (scope.get("conversation_id"), scope.get("branch_id")) != (row["conversation_id"], row["branch_id"]):
        raise DomainError("invalid_evidence", "Orientation belongs to another context.")
    events = validate_snapshot(conn, service, principal, brain, result)
    current_input_refs = result.get("current_input_refs", [])
    if current_input_refs and result.get("current_input_binding") != {
        "turn_id": row["turn_id"], "job_id": row["orientation_job_id"],
    }:
        raise DomainError("context_stale", "Orientation current input binding changed.")
    requests = tuple(item for item in result.get("situation_updates", [])
                     if item.get("evidence_use") == "current_request")
    if requests:
        from .consolidation import _check_result

        batch = CognitiveBatch(row["orientation_job_id"], scope, tuple(events), proposal_mode=mode,
                               input_contract_version=_snapshot_input_contract(result))
        _check_result(batch, SemanticResult(result["status"], situation_updates=requests,
                                           verification_assessments=tuple(result.get("verification_assessments", ()))),
                      {"events": {(event["event_id"], event["revision"]): event for event in events},
                       "assertions": {}, "superseded_evidence": []},
                      current_input_refs=current_input_refs)
    return result


def orientation_updates(conn, service, principal, brain, row):
    result = orientation_result(conn, service, principal, brain, row)
    if not result:
        return []
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    return [{"key": "situation:" + item["key"],
             "value": {"orientation_job_id": row["orientation_job_id"],
                       **{key: item[key] for key in ("kind", "value", "state", "world", "conditions", "verification", "observation",
                                                    "evidence_use", "request_verification")
                          if key in item}},
             "knowledge_state": "inferred", "evidence_refs": item["evidence_refs"], "expires_at": expires,
             "updated_at": now()}
            for item in result.get("situation_updates", [])]


class LearningEngine:
    def __init__(self, service):
        self.service = service
        self.jobs = JobStore(service.database)

    def _resume_snapshot(self, conn, principal, brain, job, snapshot):
        service = self.service
        mode = _snapshot_proposal_mode(snapshot, job["kind"])
        learning_phase = phase(job.get("payload", {}))
        if (mode == LOCAL_OBSERVATION_MODE and learning_phase != ENCODING_PHASE
                or mode == HISTORY_INTEGRATION_MODE and learning_phase != HISTORY_PHASE):
            raise DomainError("context_stale", "Bounded learning phase changed.")
        events, loaded = _validated_snapshot(conn, service, principal, brain, snapshot)
        if learning_phase is not None:
            snapshot = self._refresh_recovery_policy(conn, principal, brain, job, snapshot)
            freeze_input(conn, service._params(principal, brain["id"]), job["id"],
                         input_fingerprint=snapshot_fingerprint(snapshot), window=snapshot.get("learning_window"))
        if job["kind"] == "orient":
            current = service._working(conn, principal, brain, job["payload"]["target"], None)
            if current["revision"] != snapshot["working_revision"]:
                raise DomainError("context_stale", "Working context changed before orientation retry.")
        documents = []
        for field, identifier in (("memory_snapshot", "memory_id"), ("episode_snapshot", "episode_id")):
            documents.append(tuple(service._rebind(loaded[field][(ref[identifier], ref["revision"])], principal, brain)
                                   for ref in snapshot.get(field, [])))
        return CognitiveBatch(
            job["id"], snapshot["scope"], tuple(events), documents[0], snapshot["working_snapshot"], documents[1],
            sampling=snapshot["sampling"], belief_relations=tuple(snapshot.get("belief_relations", ())),
            consolidation_plan=snapshot.get("consolidation_plan"),
            current_input_refs=tuple(snapshot.get("current_input_refs", ())),
            proposal_mode=mode,
            learning_input_refs=_learning_inputs(snapshot, job, events),
            input_contract_version=_snapshot_input_contract(snapshot),
            integration_manifest=copy.deepcopy(snapshot.get("integration_manifest")),
        ), snapshot

    def _refresh_recovery_policy(self, conn, principal, brain, job, snapshot):
        params = self.service._params(principal, brain["id"])
        current = conn.execute(
            "SELECT payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s FOR UPDATE",
            (*params, job["id"]),
        ).fetchone()["payload"]
        if "recovery_policy_refresh" not in current or "allocation_input_fingerprint" in current:
            return snapshot
        parent = conn.execute(
            "SELECT payload FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, current.get("recovery_parent_job_id")),
        ).fetchone()
        if (current["recovery_policy_refresh"] != POLICY_REFRESH_VERSION or parent is None
                or current.get("authorization_root_job_id") != job["id"]
                or current.get("allocation_policy") != "explicit-phase-recovery-budget-v1"
                or parent["payload"].get("recovery_job_id") != job["id"]
                or current.get("recovery_input_fingerprint") != parent["payload"].get("allocation_input_fingerprint")
                or snapshot_fingerprint(snapshot) != current.get("recovery_input_fingerprint")
                or any(current.get(key) != parent["payload"].get(key) for key in (
                    "learning_phase", "learning_contract_version", "target", "from_sequence", "through_sequence"))):
            raise DomainError("context_stale", "Explicit recovery no longer matches its original input plan.")
        snapshot = {**snapshot, "semantic_policy": {
            "version": "semantic-policy-v1", "limits": asdict(self.service.semantic_processor.policy)}}
        conn.execute(
            "UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (Jsonb(snapshot), *params, job["id"]),
        )
        return snapshot

    def _snapshot(self, principal, brain_id, job):
        service = self.service
        with service.database.transaction(principal) as conn:
            brain = service._brain(conn, principal, brain_id)
            params = service._params(principal, brain_id)
            payload = job["payload"]
            learning_phase = phase(payload)
            prior = conn.execute("SELECT result FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                                 (*params, job["id"])).fetchone()["result"]
            if (learning_phase is not None and any(key in payload for key in (
                    "allocation_input_fingerprint", "recovery_input_fingerprint", "recovery_policy_refresh"))
                    and (not isinstance(prior, dict) or "working_snapshot" not in prior)):
                raise DomainError("context_stale", "The frozen learning input plan is no longer available.")
            if prior:
                _current_input_authorization(prior)
            policy = _snapshot_policy(prior or {}, service.semantic_processor.policy)
            mode = (_snapshot_proposal_mode(prior, job["kind"]) if prior else
                    ORIENT_PROPOSAL_MODE if job["kind"] == "orient" else GENERAL_PROPOSAL_MODE)
            input_contract = (_snapshot_input_contract(prior) if prior else
                              LATEST_INPUT_CONTRACT_VERSION)
            windowed = (job["kind"] == "consolidate" and "from_sequence" in payload
                        and learning_phase != HISTORY_PHASE)
            frozen_orientation = job["kind"] == "orient" and mode == ORIENT_PROPOSAL_MODE
            if (windowed or frozen_orientation or learning_phase == HISTORY_PHASE) and prior and "working_snapshot" in prior:
                snapshot = {key: copy.deepcopy(value) for key, value in prior.items() if key in {
                    "scope", "source_snapshot", "memory_snapshot", "episode_snapshot", "working_revision",
                    "working_snapshot", "sampling", "learning_window", "learning_input_refs", "dependency_generation",
                    "belief_relations", "belief_relation_snapshot", "consolidation_plan",
                    "current_input_refs", "current_input_binding", "proposal_mode", "input_contract_version",
                    "operation_settlement_policy", "semantic_policy",
                    "history_sampling", "integration_manifest", "remaining_manifest",
                }}
                if windowed and (snapshot.get("current_input_refs") or snapshot.get("current_input_binding")):
                    raise DomainError("context_stale", "Consolidation cannot authorize current input.")
                return self._resume_snapshot(conn, principal, brain, job, snapshot)
            if learning_phase == HISTORY_PHASE:
                raise DomainError("dependency_unavailable", "Historical integration has no ready frozen package.")
            if "event_id" in payload:
                source = service._events(conn, principal, brain, event_ids=[payload["event_id"]], limit=1)
                if not source or source[0]["revision"] != payload["revision"]:
                    raise DomainError("source_unavailable", "Consolidation trigger changed.")
            target = payload.get("target", {})
            scope = service._scope(principal, brain, target)
            from .neighborhood import Neighborhood, select_neighborhood

            required = []
            current_input_refs = []
            window = None
            through = payload["through_sequence"]
            selection_policy = policy
            if windowed and "recovery_budget" in payload and job["max_input_bytes"] is not None:
                input_limit = min(selection_policy.max_input_bytes, job["max_input_bytes"])
                selection_policy = replace(selection_policy, max_input_bytes=input_limit,
                                           max_request_bytes=min(selection_policy.max_request_bytes, input_limit // 2))
            if windowed:
                required, window = select_window(conn, service, principal, brain, payload,
                                                policy=selection_policy,
                                                input_contract_version=input_contract)
                through = window["attempted_through_sequence"]
            elif "event_id" in payload:
                required = [{"event_id": payload["event_id"], "revision": payload["revision"]}]
            elif "turn_id" in payload:
                turn = conn.execute(
                    "SELECT input_event_refs FROM memory_runtime_turns WHERE tenant_id=%s AND owner_id=%s "
                    "AND brain_id=%s AND turn_id=%s AND conversation_id=%s AND branch_id=%s",
                    (*params, payload["turn_id"], target.get("conversation_ref"), target.get("branch_ref")),
                ).fetchone()
                if turn is None:
                    raise DomainError("source_unavailable", "Orientation input turn is unavailable.")
                required = turn["input_event_refs"]
                if job["kind"] == "orient" and (not prior or "current_input_refs" in prior):
                    current_input_refs = copy.deepcopy(required)
            neighborhood = (select_neighborhood(
                conn, service, principal, brain, target, through_sequence=through,
                required_event_refs=required, policy=selection_policy,
                learning_input_refs=required if window is not None and input_contract is not None else (),
                input_contract_version=input_contract,
            ) if required or not windowed else Neighborhood((), (), (), {"source_count": 0}))
            history_sampling = None
            if (learning_phase == ENCODING_PHASE and prior is None and required
                    and neighborhood.sampling.get("publication_restricted")):
                history_sampling = copy.deepcopy(neighborhood.sampling)
                mode = LOCAL_OBSERVATION_MODE
                neighborhood = local_neighborhood(
                    conn, service, principal, brain, target, through_sequence=through,
                    required_event_refs=required, policy=selection_policy, input_contract_version=input_contract,
                )
            sources, assertion_documents, episode_documents = (
                neighborhood.sources, neighborhood.assertions, neighborhood.episodes,
            )
            source_ids = [event["event_id"] for event in sources]
            assertions = tuple(service._rebind(document, principal, brain) for document in assertion_documents)
            working = service._working(conn, principal, brain, target, sources)
            if mode == LOCAL_OBSERVATION_MODE:
                working = empty_working(working)
            episodes = tuple(service._rebind(document, principal, brain) for document in episode_documents)
            snapshot = {"scope": scope, "dependency_generation": brain["dependency_generation"], "source_snapshot": [
                {"event_id": e["event_id"], "revision": e["revision"], "content_sha256": e["content_sha256"]}
                for e in sources], "memory_snapshot": [
                    {"memory_id": m["memory_id"], "revision": m["revision"], "content_sha256": digest(m)}
                    for m in assertion_documents],
                "episode_snapshot": [
                    {"episode_id": e["episode_id"], "revision": e["revision"], "content_sha256": digest(e)}
                    for e in episode_documents], "working_revision": working["revision"],
                        "sampling": neighborhood.sampling,
                "belief_relations": list(neighborhood.belief_relations),
                "belief_relation_snapshot": [{"relation_id": item["relation_id"], "revision": item["revision"],
                                              "content_sha256": digest(item)}
                                             for item in neighborhood.belief_relations]}
            if not prior or "proposal_mode" in prior:
                snapshot["proposal_mode"] = mode
            if not prior:
                snapshot["operation_settlement_policy"] = NO_UNSETTLED_RETRY_POLICY
            elif "operation_settlement_policy" in prior:
                snapshot["operation_settlement_policy"] = prior["operation_settlement_policy"]
            if not prior:
                snapshot["semantic_policy"] = {"version": "semantic-policy-v1", "limits": asdict(policy)}
            elif "semantic_policy" in prior:
                snapshot["semantic_policy"] = copy.deepcopy(prior["semantic_policy"])
            if input_contract is not None or prior and "input_contract_version" in prior:
                snapshot["input_contract_version"] = input_contract
            if history_sampling is not None:
                snapshot["history_sampling"] = history_sampling
            if frozen_orientation:
                snapshot["working_snapshot"] = copy.deepcopy(working)
            if current_input_refs:
                snapshot.update(current_input_refs=current_input_refs,
                                current_input_binding={"turn_id": payload["turn_id"], "job_id": job["id"]})
            if prior and (prior.get("current_input_refs", []) != current_input_refs
                          or prior.get("current_input_binding") != snapshot.get("current_input_binding")):
                raise DomainError("context_stale", "Cognitive retry current input changed.")
            _validate_current_input_binding(conn, service, principal, brain, snapshot,
                                            {(event["event_id"], event["revision"]): event for event in sources})
            candidate_plan = plan_consolidation(sources, assertions, neighborhood.belief_relations,
                                                neighborhood.sampling, include_all=True)
            identifiers = [item["objective_id"] for item in candidate_plan["objectives"]]
            attempted = []
            if identifiers and job["kind"] == "consolidate":
                attempted = [row["objective_id"] for row in conn.execute(
                    "SELECT DISTINCT objective->>'objective_id' AS objective_id FROM memory_jobs j "
                    "CROSS JOIN LATERAL jsonb_array_elements(CASE "
                    "WHEN jsonb_typeof(j.result #> '{consolidation,objectives}')='array' "
                    "THEN j.result #> '{consolidation,objectives}' ELSE '[]'::jsonb END) objective "
                    "WHERE j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.kind='consolidate' "
                    "AND j.state='completed' AND objective->>'dedup_eligible'='true' "
                    "AND objective->>'objective_id'=ANY(%s::text[]) LIMIT %s",
                    (*params, identifiers, len(identifiers)),
                ).fetchall()]
            snapshot["consolidation_plan"] = select_consolidation_candidates(
                candidate_plan, assertions, attempted_ids=attempted)
            if job["kind"] == "orient":
                snapshot["consolidation_plan"]["objectives"] = []
            if window is not None:
                snapshot.update(learning_window=window, learning_input_refs=required,
                                working_snapshot=working)
            learning_inputs = _learning_inputs(snapshot, job, sources)
            if prior and "consolidation_plan" in prior:
                snapshot["consolidation_plan"] = prior["consolidation_plan"]
            if prior and prior.get("source_snapshot") != snapshot["source_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry sources changed.")
            if prior and prior.get("episode_snapshot", []) != snapshot["episode_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry episodes changed.")
            if prior and prior.get("belief_relation_snapshot", []) != snapshot["belief_relation_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry relationships changed.")
            if prior and prior.get("memory_snapshot", []) != snapshot["memory_snapshot"]:
                raise DomainError("context_stale", "Cognitive retry parents changed.")
            if prior and prior.get("sampling"):
                def sampling_identity(value):
                    return {**{key: item for key, item in value.items()
                               if key not in {"indexed_through_sequence", "limitations"}},
                            "limitations": [reason for reason in value.get("limitations", []) if reason != "index_lag"]}

                if sampling_identity(prior["sampling"]) != sampling_identity(snapshot["sampling"]):
                    raise DomainError("context_stale", "Cognitive retry sampling changed.")
                snapshot["sampling"] = prior["sampling"]
            if learning_phase is not None:
                freeze_input(conn, params, job["id"], input_fingerprint=snapshot_fingerprint(snapshot),
                             window=snapshot.get("learning_window"))
            conn.execute(
                "UPDATE memory_jobs SET result=%s,source_event_ids=%s,source_memory_ids=%s "
                "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s AND fence=%s AND state='running'",
                (Jsonb(snapshot), source_ids, [m["memory_id"] for m in assertions], *params, job["id"], job["fence"]),
            )
            batch = CognitiveBatch(job["id"], scope, sources, assertions, working, episodes,
                                   sampling=snapshot["sampling"], belief_relations=neighborhood.belief_relations,
                                   consolidation_plan=snapshot["consolidation_plan"],
                                   current_input_refs=tuple(current_input_refs), proposal_mode=mode,
                                   learning_input_refs=learning_inputs, input_contract_version=input_contract)
            return batch, snapshot

    def semantic(self, principal, brain_id, job):
        service = self.service
        batch, snapshot = self._snapshot(principal, brain_id, job)
        processor = copy.copy(service.semantic_processor)
        policy = processor.policy = _snapshot_policy(snapshot, processor.policy)
        lease = {"lease_token": job["lease_token"], "fence": job["fence"]}
        operations = []
        settled_operations = set()
        renewable_budget = False
        # Synchronous provider calls cannot renew their lease while waiting for a response.
        lease_seconds = max(120, (policy.max_wall_time_ms + 999) // 1000 + 5)

        def check():
            self.jobs.renew(principal, brain_id, job["id"], **lease, lease_seconds=lease_seconds)

        def reserve(**request):
            operation_id = f"{job['fence']}:{request['phase']}"
            reservation = self.jobs.reserve_operation(
                principal, brain_id, job["id"], **lease, operation_id=operation_id,
                request_fingerprint=request["request_fingerprint"], input_bytes=request["input_bytes"],
                max_model_calls=policy.max_model_calls, max_input_bytes=policy.max_input_bytes,
            )
            if not reservation["newly_reserved"]:
                raise DomainError("budget_exceeded", "Cognitive operation already reserved.")
            operations.append(operation_id)

        def on_call_result(*, phase, outcome, usage, response_fingerprint):
            operation_id = f"{job['fence']}:{phase}"
            try:
                self.jobs.settle_operation(
                    principal, brain_id, job["id"], **lease, operation_id=operation_id,
                    outcome=outcome, usage=usage, response_fingerprint=response_fingerprint,
                )
            except Exception as error:
                message = "Provider settlement requires explicit reconciliation."
                if isinstance(error, DomainError) and error.code == "lease_conflict":
                    message = "Provider settlement requires explicit reconciliation (lease_conflict)."
                raise DomainError("provider_uncertain", message) from None
            settled_operations.add(operation_id)

        try:
            call_limit = min(policy.max_model_calls, job["max_model_calls"] if job["max_model_calls"] is not None
                             else policy.max_model_calls)
            if "learning_window" in snapshot and batch.sources and call_limit - job["model_calls"] < 2:
                raise DomainError("budget_exceeded", "Remaining learning budget cannot cover proposal and verification.")
            result = processor.process(
                batch, reserve=reserve, check=check, prior_model_calls=job["model_calls"], prior_input_bytes=job["input_bytes"],
                on_call_result=on_call_result,
            )
        except DomainError as error:
            if error.code != "budget_exceeded":
                raise
            renewable_budget = error.retryable
            usage = self.jobs.get(principal, brain_id, job["id"])
            result = SemanticResult("budget_exhausted", observed_event_refs=tuple(
                {"event_id": e["event_id"], "revision": e["revision"]} for e in batch.sources),
                model_calls=usage["model_calls"], input_bytes=usage["input_bytes"])
        for operation_id in operations:
            if operation_id not in settled_operations:
                self.jobs.settle_operation(principal, brain_id, job["id"], **lease, operation_id=operation_id,
                                           outcome="uncertain")
        window = snapshot.get("learning_window")
        only_invalid_proposals = (result.rejected and all(item.get("reason") == "invalid_proposal" for item in result.rejected)
                                  and not any((result.claims, result.situation_updates, result.episode_actions, result.belief_links)))
        succeeded = (result.status in {"completed", "partial"} and not only_invalid_proposals
                     and not batch.sampling.get("publication_restricted")
                     or window is not None and not snapshot["learning_input_refs"] and result.status == "no_sources")
        if phase(job["payload"]) == HISTORY_PHASE and not succeeded:
            from .learning_integration import fail_integration

            return fail_integration(service, principal, brain_id, job, snapshot, result)

        def publish(conn, _):
            brain = service._brain(conn, principal, brain_id)
            validate_snapshot(conn, service, principal, brain, snapshot)
            if job["kind"] == "orient":
                from .consolidation import _check_result, validate_batch

                check_result_mode(batch.proposal_mode, asdict(result))
                _check_result(batch, result, validate_batch(conn, service, principal, brain, batch, result),
                              current_input_refs=batch.current_input_refs)
                target = job["payload"]["target"]
                working = service._working(conn, principal, brain, target, None)
                if working["revision"] != snapshot["working_revision"]:
                    raise DomainError("context_stale", "Working context changed during orientation.")
                publication = {}
            else:
                from .consolidation import publish_semantic

                publication = publish_semantic(conn, service, principal, brain, batch, result, job_id=job["id"])
            data = {**snapshot, **asdict(result), "publication": publication}
            if batch.proposal_mode == LOCAL_OBSERVATION_MODE and succeeded:
                from .learning_integration import enqueue_integration

                data.update(enqueue_integration(conn, service, principal, brain, job, snapshot, policy))
            elif phase(job["payload"]) == HISTORY_PHASE:
                from .learning_integration import finish_integration

                data.update(finish_integration(conn, service, principal, brain, job, snapshot, result, policy))
            if window is not None:
                data["learning_window"] = record_progress(
                    conn, service, principal, brain, job, window, succeeded=succeeded,
                )
            if phase(job["payload"]) == ENCODING_PHASE and succeeded:
                from .learning_integration import notify_encoding_complete

                notify_encoding_complete(conn, service, principal, brain)
            conn.execute("UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                         (Jsonb(data), *service._params(principal, brain_id), job["id"]))
            return data

        if window is not None and not succeeded:
            with service.database.transaction(principal) as conn:
                service._brain(conn, principal, brain_id)
                self.jobs._lease(self.jobs._row(conn, principal, brain_id, job["id"]), **lease)
                data = {**snapshot, **asdict(result), "publication": {},
                        "learning_window": {**window, "status": "deferred", "processing_succeeded": False}}
                conn.execute(
                    "UPDATE memory_jobs SET result=%s WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                    (Jsonb(data), *service._params(principal, brain_id), job["id"]),
                )
                failure = ("budget_exceeded" if result.status == "budget_exhausted"
                           else "provider_failed" if result.status in {"provider_unavailable", "verification_unavailable"}
                           else "dependency_unavailable" if batch.sampling.get("publication_restricted")
                           else "invalid_proposal")
                current = self.jobs._row(conn, principal, brain_id, job["id"])
                call_limit = min(policy.max_model_calls, current["max_model_calls"] or policy.max_model_calls)
                byte_limit = min(policy.max_input_bytes, current["max_input_bytes"] or policy.max_input_bytes)
                remaining = call_limit - current["model_calls"]
                can_retry = (job["attempts"] < 3 and remaining >= 2
                             and current["input_bytes"] < byte_limit
                             and failure != "dependency_unavailable"
                             and (failure != "budget_exceeded" or renewable_budget))
                delay = 3600 if failure == "budget_exceeded" else min(3600, 60 * 2 ** (job["attempts"] - 1))
                conn.execute(
                    "UPDATE memory_jobs SET state=%s,last_error=%s,available_at=clock_timestamp()+make_interval(secs=>%s),"
                    "lease_token_digest=NULL,lease_expires_at=NULL,worker_id=NULL,updated_at=clock_timestamp() "
                    "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                    ("failed" if can_retry else "cancelled", failure, delay,
                     *service._params(principal, brain_id), job["id"]),
                )
            return data
        return self.jobs.complete(principal, brain_id, job["id"], **lease, publish=publish)

    def orient(self, principal, brain_id, turn_id, *, lease_token, expected_revision):
        service = self.service
        turns = TurnStore(service.database)
        with turns._transaction(principal, brain_id) as (conn, brain):
            row = turns._row(conn, principal, brain_id, turn_id)
            turns._lease(row, lease_token, expected_revision)
            if row["stage"] != "captured":
                raise DomainError("stage_conflict", "Orientation requires a captured turn.")
            if service.semantic_processor is None:
                return turns._public(conn, principal, row)
            job_id = row["orientation_job_id"]
            if job_id is None:
                payload = {"turn_id": turn_id, "target": {"conversation_ref": row["conversation_id"],
                           "branch_ref": row["branch_id"]}, "through_sequence": row["durable_sequence"] or brain["durable_sequence"]}
                job_id = "orient:" + digest([brain_id, turn_id])
                conn.execute(
                    "INSERT INTO memory_jobs(tenant_id,owner_id,brain_id,id,kind,payload,state) "
                    "VALUES(%s,%s,%s,%s,'orient',%s,'queued') ON CONFLICT DO NOTHING",
                    (*service._params(principal, brain_id), job_id, Jsonb(payload)),
                )
        row = turns.bind_orientation(principal, brain_id, turn_id, lease_token=lease_token,
                                     expected_revision=expected_revision, job_id=job_id)
        job = self.jobs.claim(principal, brain_id, worker_id=uid(), job_id=job_id, kinds=["orient"], lease_seconds=120)
        if job:
            self._execute(principal, brain_id, job)
        with turns._transaction(principal, brain_id) as (conn, brain):
            current = turns._row(conn, principal, brain_id, turn_id)
            turns._lease(current, lease_token, row["revision"])
            result = orientation_result(conn, service, principal, brain, current)
            status = conn.execute("SELECT state FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
                                  (*service._params(principal, brain_id), job_id)).fetchone()["state"]
            if status == "running":
                raise DomainError("lease_conflict", "Orientation is already running.", True)
            generation = brain["dependency_generation"]
        if result:
            row = turns.record_orientation_sources(
                principal, brain_id, turn_id, lease_token=lease_token, expected_revision=row["revision"],
                job_id=job_id, source_refs=result["observed_event_refs"], context_generation=generation,
            )
        return row

    def _literal(self, principal, brain_id, job):
        service = self.service
        with service.database.transaction(principal) as conn:
            brain = service._brain(conn, principal, brain_id)
            event_ids = ([job["payload"]["event_id"]] if job["kind"] == "extract"
                         else job["payload"].get("event_ids", []))
            events = service._events(conn, principal, brain, event_ids=event_ids[:128], limit=128)
            if job["kind"] == "extract":
                events = [e for e in events if e["revision"] == job["payload"]["revision"]]
            memories = [(event, service._current_captures(conn, principal, brain, event)) for event in events]

        def publish(conn, _):
            brain = service._brain(conn, principal, brain_id)
            current = {(e["event_id"], e["revision"]) for e in service._events(
                conn, principal, brain, event_ids=event_ids[:128], limit=128)}
            for event, captures in memories:
                if (event["event_id"], event["revision"]) in current:
                    blocked = service._superseded_evidence(conn, principal, brain, [event["event_id"]])
                    for memory in captures:
                        if not any(cognition.evidence_overlaps(ref, old) for ref in memory["evidence_refs"] for old in blocked):
                            service._publish(conn, principal, brain, memory)
            if len(event_ids) > 128:
                service._job(conn, principal, brain, "rebuild", {"event_ids": event_ids[128:]})
            pending = conn.execute(
                "SELECT min(e.sequence) AS value FROM memory_events e JOIN memory_jobs j ON "
                "j.tenant_id=e.tenant_id AND j.brain_id=e.brain_id AND j.payload->>'event_id'=e.id "
                "AND (j.payload->>'revision')::bigint=e.revision "
                "WHERE e.tenant_id=%s AND e.owner_id=%s AND e.brain_id=%s AND j.kind='extract' "
                "AND j.state<>'completed' AND j.id<>%s", (*service._params(principal, brain_id), job["id"]),
            ).fetchone()["value"]
            indexed = brain["durable_sequence"] if pending is None else pending - 1
            conn.execute("UPDATE memory_brains SET indexed_sequence=%s WHERE tenant_id=%s AND owner_id=%s AND id=%s",
                         (indexed, *service._params(principal, brain_id)))

        self.jobs.complete(principal, brain_id, job["id"], lease_token=job["lease_token"], fence=job["fence"], publish=publish)

    def _execute(self, principal, brain_id, job):
        try:
            if job["kind"] in ("extract", "rebuild"):
                self._literal(principal, brain_id, job)
            elif job["kind"] in ("orient", "consolidate"):
                self.semantic(principal, brain_id, job)
            elif job["kind"] == "embed":
                from .vector_jobs import embed_memory

                embed_memory(self.service, principal, brain_id, job)
        except Exception as error:
            try:
                self.jobs.fail(principal, brain_id, job["id"], lease_token=job["lease_token"], fence=job["fence"],
                               failure_code=error.code if isinstance(error, DomainError) and error.code in {
                                   "source_unavailable", "context_stale", "budget_exceeded", "provider_uncertain"} else "internal_error",
                               retry=not isinstance(error, DomainError),
                               retry_delay_seconds=(60 * 2 ** min(job["attempts"] - 1, 5)
                                                    if job["kind"] == "consolidate" else 0))
            except DomainError:
                pass
            raise

    def work_once(self, principal, brain_id):
        kinds = ["extract", "rebuild"]
        if self.service.semantic_processor:
            with self.service.database.transaction(principal) as conn:
                brain = self.service._brain(conn, principal, brain_id)
                recover_windows(conn, self.service, principal, brain)
                from .learning_integration import reconsider

                reconsider(conn, self.service, principal, brain)
            kinds.append("consolidate")
        if self.service.embedding_provider:
            kinds.append("embed")
        job = self.jobs.claim(principal, brain_id, worker_id=uid(), kinds=kinds, lease_seconds=120)
        if job is None and self.service.embedding_provider:
            from .vector_jobs import backfill_embeddings

            if backfill_embeddings(self.service, principal, brain_id):
                job = self.jobs.claim(principal, brain_id, worker_id=uid(), kinds=kinds, lease_seconds=120)
        if job is None:
            return 0
        self._execute(principal, brain_id, job)
        return 1
