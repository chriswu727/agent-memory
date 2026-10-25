import unicodedata
from collections import Counter

from .consolidation_planning import NO_CHANGE_REASONS
from .helpers import DomainError
from .learning_allocations import (
    ALLOCATION_POLICY,
    AUTHORITY_FIELDS,
    CONTRACT_VERSION,
    ENCODING_PHASE,
    HISTORY_PHASE,
    phase,
)

PHASE_COUNTS = (
    "encoding_open_count", "encoding_assessed_count", "history_integration_open_count",
    "history_integration_deferred_count", "history_integration_running_count",
    "history_integration_assessed_count", "history_integration_unassessed_objective_count",
    "legacy_unclassified_count", "unknown_count",
)
DEFERRED_REASON = "history_integration_deferred"
DEFERRED_CATEGORIES = frozenset({"package_incomplete", "shared_allocation_exhausted", "brain_budget_unavailable"})
HISTORY_STATES = frozenset({"queued", "deferred", "running", "failed", "cancelled", "superseded", "completed", "unknown"})
PHASE_FIELDS = (*AUTHORITY_FIELDS, "encoding_parent_job_id", "encoding_input_fingerprint",
                "history_cutoff_sequence", "integration_continuation_job_id", "integration_continuation_parent_job_id",
                "encoding_replan_version", "history_replan_version", "encoding_window_snapshot")
HISTORY_LINK_FIELDS = ("history_integration_job_id", "history_integration_state",
                       "history_integration_reason", "history_integration_category")
_MAX_INTEGER = 2**63 - 1
_COVERAGE_VERSION = "bounded-history-manifest-v1"
_REPLAN_VERSION = "unreserved-encoding-replan-v1"
_HISTORY_REPLAN_VERSION = "unreserved-history-replan-v1"
_FAILED_HISTORY_STATUSES = frozenset({"invalid_response", "provider_unavailable", "verification_unavailable",
                                      "budget_exhausted"})


def _integer(value):
    return type(value) is int and 0 <= value <= _MAX_INTEGER


def _identifier(value):
    return (isinstance(value, str) and 1 <= len(value) <= 256
            and not any(c.isspace() or unicodedata.category(c).startswith("C") for c in value))


def _fingerprint(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def classify(payload):
    try:
        value = phase(payload)
    except (DomainError, TypeError, ValueError):
        return "unknown"
    if value is None and any(key in payload for key in PHASE_FIELDS):
        return "unknown"
    for key, expected, selected in (("encoding_replan_version", _REPLAN_VERSION, ENCODING_PHASE),
                                    ("history_replan_version", _HISTORY_REPLAN_VERSION, HISTORY_PHASE)):
        if key in payload and (payload[key] != expected or value != selected):
            return "unknown"
    return value


def coverage_summary(value):
    if not isinstance(value, dict) or set(value) != {"version", "objectives", "assessed_count", "published_count"}:
        return None
    objectives = value["objectives"]
    if (value["version"] != _COVERAGE_VERSION or not isinstance(objectives, list) or len(objectives) > 32
            or not _integer(value["assessed_count"]) or not _integer(value["published_count"])):
        return None
    seen, assessed, published = set(), 0, 0
    for item in objectives:
        if (not isinstance(item, dict)
                or set(item) != {"objective_id", "assessed", "status", "verified_link_ids", "reason"}
                or not _identifier(item["objective_id"]) or item["objective_id"] in seen
                or type(item["assessed"]) is not bool or not isinstance(item["verified_link_ids"], list)
                or len(item["verified_link_ids"]) > 16 or not all(_identifier(key) for key in item["verified_link_ids"])
                or len(set(item["verified_link_ids"])) != len(item["verified_link_ids"])
                or item["reason"] is not None and (not isinstance(item["reason"], str)
                                                  or item["reason"] not in NO_CHANGE_REASONS)):
            return None
        seen.add(item["objective_id"])
        expected = ("published_relation" if item["verified_link_ids"] else "assessed_no_publication"
                    if item["assessed"] else "not_evaluated")
        if (item["status"] != expected or item["verified_link_ids"] and item["reason"] is not None):
            return None
        assessed += item["assessed"]
        published += bool(item["verified_link_ids"])
    if (assessed, published) != (value["assessed_count"], value["published_count"]):
        return None
    return {"version": _COVERAGE_VERSION, "objective_count": len(objectives),
            "assessed_count": assessed, "published_count": published}


def history_assessment(row):
    coverage = coverage_summary(row.get("history_coverage"))
    status = row.get("processing_status")
    return (coverage if coverage is not None and (status == "partial" or status == "completed"
            and coverage["assessed_count"] == coverage["objective_count"]) else None)


def assessed_history(row):
    coverage = history_assessment(row)
    return coverage is not None and coverage["assessed_count"] == coverage["objective_count"]


def unassessed_history(row):
    coverage = (history_assessment(row) if row["state"] == "completed" else
                coverage_summary(row.get("history_coverage")) if failed_history(row) else None)
    return (coverage["objective_count"] - coverage["assessed_count"]
            if coverage is not None else 0)


def failed_history(row):
    return (row["state"] in {"cancelled", "failed"}
            and (isinstance(row.get("processing_status"), str)
                 and row["processing_status"] in _FAILED_HISTORY_STATUSES
                 or row.get("processing_status") == "partial" and row.get("last_error") == "invalid_proposal")
            and coverage_summary(row.get("history_coverage")) is not None)


def valid_deferred(row):
    return (row.get("history_reason") == DEFERRED_REASON
            and isinstance(row.get("history_category"), str) and row["history_category"] in DEFERRED_CATEGORIES
            and type(row.get("model_calls")) is int and row["model_calls"] == 0
            and row.get("processing_status") == "not_evaluated"
            and row.get("learning_outcome") == "not_evaluated")


def valid_encoding_root(row, windows):
    payload = row["phase_payload"]
    if not (_integer(row["window_start"]) and _integer(row["window_end"])
            and 1 <= row["window_start"] <= row["window_end"]):
        return False
    root_id = payload["authorization_root_job_id"]
    current, seen = row, set()
    while current["id"] not in seen and len(seen) <= 256:
        seen.add(current["id"])
        current_payload = current.get("phase_payload", {})
        if current["id"] == root_id:
            return "encoding_replan_version" not in current_payload
        if current_payload.get("encoding_replan_version") != _REPLAN_VERSION:
            return False
        parent_id = current.get("recovery_parent")
        parent = windows.get(parent_id) if _identifier(parent_id) else None
        if (parent is None or parent.get("learning_phase") != ENCODING_PHASE
                or parent.get("recovery_id") != current["id"] or parent.get("continuation_id") is not None
                or (parent["conversation"], parent["branch"]) != (row["conversation"], row["branch"])
                or (parent["window_start"], parent["window_end"]) != (row["window_start"], row["window_end"])
                or parent["processed"] != parent["window_start"] - 1
                or type(parent.get("model_calls")) is not int or parent["model_calls"] != 0
                or type(parent.get("input_bytes")) is not int or parent["input_bytes"] != 0
                or not (parent["state"] == "superseded" or parent["state"] == "cancelled"
                        and parent.get("last_error") in {"source_unavailable", "context_stale"})
                or any(parent.get("phase_payload", {}).get(key) != payload[key]
                       for key in ("authorization_root_job_id", "allocation_policy", "allocation_budget", "phase_budget"))):
            return False
        current = parent
    return False


def encoding_attempted(row):
    marker = row.get("phase_payload", {}).get("encoding_window_snapshot")
    if (not isinstance(marker, dict) or set(marker) != {
            "version", "from_sequence", "through_sequence", "attempted_through_sequence"}
            or marker["version"] != "encoding-window-snapshot-v1"
            or any(not _integer(marker[key]) for key in (
                "from_sequence", "through_sequence", "attempted_through_sequence"))
            or (marker["from_sequence"], marker["through_sequence"]) != (row["window_start"], row["window_end"])
            or not 1 <= marker["from_sequence"] <= marker["attempted_through_sequence"] <= marker["through_sequence"]):
        return None
    return marker["attempted_through_sequence"]


def unprocessed_encoding_tail(row, child):
    attempted = encoding_attempted(row)
    first, second = row.get("phase_payload", {}), child.get("phase_payload", {})
    if (row["learning_phase"] != ENCODING_PHASE or child["learning_phase"] != ENCODING_PHASE
            or attempted is None or attempted >= row["window_end"] or row["processed"] != row["window_start"] - 1
            or not (row["state"] == "superseded" or row["state"] == "cancelled"
                    and row.get("last_error") in {"source_unavailable", "context_stale"})
            or child.get("continuation_parent") != row["id"]
            or second.get("authorization_root_job_id") != child["id"]
            or second.get("authorization_root_job_id") == first.get("authorization_root_job_id")
            or any(first.get(key) != second.get(key) for key in ("allocation_policy", "allocation_budget", "phase_budget"))
            or child["window_end"] != row["window_end"]):
        return None
    return attempted + 1, row["window_end"]


def annotate_phases(windows):
    for identifier, row in windows.items():
        row["phase_issues"] = set()
        selected = row["learning_phase"]
        payload = row.get("phase_payload", {})
        if selected is None:
            if (row["state"] == "deferred"
                    or any(row.get(key) is not None for key in ("history_child", "history_reason", "history_category"))):
                row["issues"].add("invalid_metadata")
            continue
        if selected == "unknown":
            row["issues"].add("invalid_metadata")
            continue
        root = windows.get(payload["authorization_root_job_id"])
        if (root is None or root["learning_phase"] in {None, "unknown"}
                or root.get("phase_payload", {}).get("authorization_root_job_id") != root["id"]
                or (root["conversation"], root["branch"]) != (row["conversation"], row["branch"])
                or any(root["phase_payload"].get(key) != payload[key] for key in ("allocation_policy", "allocation_budget"))
                or payload["allocation_policy"] == ALLOCATION_POLICY and root["learning_phase"] != ENCODING_PHASE
                or selected == ENCODING_PHASE and not valid_encoding_root(row, windows)):
            row["issues"].add("invalid_lineage")
        if selected == ENCODING_PHASE:
            if "encoding_window_snapshot" in payload and encoding_attempted(row) is None:
                row["issues"].add("invalid_metadata")
            if (any(key in payload for key in ("encoding_parent_job_id", "integration_continuation_job_id",
                                               "integration_continuation_parent_job_id"))
                    or row["state"] == "deferred"):
                row["issues"].add("invalid_metadata")
            if (row.get("proposal_mode") == "local-observation-v1" and row["state"] == "completed"
                    and not row.get("has_history_child")):
                row["phase_issues"].add("missing_lineage")
            continue
        parent = windows.get(payload.get("encoding_parent_job_id")) if _identifier(payload.get("encoding_parent_job_id")) else None
        if (parent is None or parent["learning_phase"] != ENCODING_PHASE
                or (parent["conversation"], parent["branch"]) != (row["conversation"], row["branch"])
                or parent["window_start"] != row["window_start"] or parent.get("attempted") != row["window_end"]
                or not _fingerprint(payload.get("encoding_input_fingerprint"))
                or not _integer(payload.get("history_cutoff_sequence"))
                or payload["history_cutoff_sequence"] < row["window_end"]):
            row["issues"].add("invalid_lineage")
        if row["state"] == "completed" and history_assessment(row) is None:
            row["issues"].add("invalid_metadata")
        if row["state"] == "deferred" and not valid_deferred(row):
            row["issues"].add("invalid_metadata")
        if "history_replan_version" in payload and not _identifier(row.get("integration_parent")):
            row["issues"].add("invalid_lineage")

    for identifier, row in windows.items():
        child_id = row.get("history_child")
        if child_id is None:
            if row.get("has_history_child"):
                row["phase_issues"].add("invalid_lineage")
            continue
        child = windows.get(child_id) if _identifier(child_id) else None
        if (row["learning_phase"] != ENCODING_PHASE or child is None
                or child["learning_phase"] != HISTORY_PHASE
                or child.get("phase_payload", {}).get("encoding_parent_job_id") != identifier
                or child["recovery_parent"] is not None or child.get("integration_parent") is not None
                or (child["conversation"], child["branch"]) != (row["conversation"], row["branch"])
                or child["window_start"] != row["window_start"] or child["window_end"] != row.get("attempted")):
            row["phase_issues"].add("invalid_lineage" if child is not None else "missing_lineage")
            if child is not None:
                child["issues"].add("invalid_lineage")
    for identifier, row in windows.items():
        if row["learning_phase"] != HISTORY_PHASE or row["recovery_parent"] is not None or row.get("integration_parent") is not None:
            continue
        parent_id = row.get("phase_payload", {}).get("encoding_parent_job_id")
        parent = windows.get(parent_id) if _identifier(parent_id) else None
        if parent is None or parent.get("history_child") != identifier:
            row["issues"].add("invalid_lineage")


def valid_phase_edge(parent, child, relation):
    if parent["learning_phase"] != child["learning_phase"]:
        return False
    if parent["learning_phase"] != HISTORY_PHASE:
        return relation != "integration"
    if relation == "continuation":
        return False
    if relation == "recovery" and parent["state"] not in {"failed", "cancelled", "superseded"}:
        return False
    if relation == "integration" and not valid_integration_predecessor(parent, child):
        return False
    first, second = parent.get("phase_payload", {}), child.get("phase_payload", {})
    return all(first.get(key) == second.get(key) for key in (
        "encoding_parent_job_id", "encoding_input_fingerprint",
    )) and (parent["window_start"], parent["window_end"]) == (child["window_start"], child["window_end"])


def valid_integration_predecessor(row, child):
    previous = row.get("phase_payload", row.get("payload", {}))
    following = child.get("phase_payload", child.get("payload", {}))
    if "history_replan_version" in following:
        return (following["history_replan_version"] == _HISTORY_REPLAN_VERSION
                and type(row.get("model_calls")) is int and row["model_calls"] == 0
                and type(row.get("input_bytes")) is int and row["input_bytes"] == 0
                and (row["state"] == "superseded" or row["state"] == "cancelled"
                     and row.get("last_error") in {"source_unavailable", "context_stale"})
                and all(previous.get(key) == following.get(key) for key in (
                    "authorization_root_job_id", "allocation_policy", "allocation_budget", "phase_budget",
                    "history_cutoff_sequence")))
    return (row["state"] == "completed" and history_assessment(row) is not None or failed_history(row)
            or row["state"] == "deferred" and valid_deferred(row)
            and "allocation_input_fingerprint" not in previous)


def summarize_phases(windows, edges):
    counts = Counter(dict.fromkeys(PHASE_COUNTS, 0))
    counted_history = set()
    history_parents = {child for identifier, children in edges.items()
                       if windows[identifier]["learning_phase"] == HISTORY_PHASE for child in children}
    for identifier, row in windows.items():
        selected = row["learning_phase"]
        if row["issues"] or row["phase_issues"]:
            counts["unknown_count"] += 1
        if selected is None:
            counts["legacy_unclassified_count"] += 1
        elif selected == ENCODING_PHASE:
            counts["encoding_open_count"] += bool(row.get("remaining_ranges")) or bool(row["issues"])
            counts["encoding_assessed_count"] += (
                isinstance(row.get("processing_status"), str)
                and row["processing_status"] in {"completed", "partial"} and not row["issues"])
            counts["history_integration_open_count"] += bool(row["phase_issues"])
        elif selected == HISTORY_PHASE and identifier not in history_parents:
            chain, current, seen = [], identifier, set()
            while current not in seen:
                seen.add(current)
                chain.append(windows[current])
                children = edges[current]
                if len(children) != 1:
                    break
                current = children[0]
            counted_history.update(seen)
            healthy = all(not item["issues"] for item in chain)
            opened = not healthy or any(item.get("remaining_ranges") for item in chain)
            counts["history_integration_open_count"] += opened
            counts["history_integration_assessed_count"] += healthy and any(
                (coverage := history_assessment(item)) is not None
                and (coverage["assessed_count"] > 0 or coverage["objective_count"] == 0) for item in chain)
            counts["history_integration_unassessed_objective_count"] += sum(unassessed_history(item) for item in chain)
            if opened and healthy:
                counts["history_integration_deferred_count"] += chain[-1]["state"] == "deferred"
                counts["history_integration_running_count"] += (
                    chain[-1]["state"] == "running" and chain[-1]["lease_expires_at"] > chain[-1]["observed_at"])
    counts["history_integration_open_count"] += sum(
        row["learning_phase"] == HISTORY_PHASE and identifier not in counted_history
        for identifier, row in windows.items())
    return {"version": CONTRACT_VERSION, **counts, "counts_complete": not counts["unknown_count"]}


def sanitize_phase_counts(value):
    value = value if isinstance(value, dict) else {}
    valid = (value.get("version") == CONTRACT_VERSION and value.get("counts_complete") is True
             and all(_integer(value.get(key)) for key in PHASE_COUNTS))
    result = {"version": CONTRACT_VERSION,
              **{key: value[key] if _integer(value.get(key)) else 0 for key in PHASE_COUNTS}}
    valid = (valid and not result["unknown_count"]
             and result["history_integration_deferred_count"] + result["history_integration_running_count"]
             <= result["history_integration_open_count"])
    return {**result, "counts_complete": bool(valid)}


def sanitize_phase_metadata(value):
    keys = {"learning_contract_version", "learning_phase", "phase_metadata_complete", *HISTORY_LINK_FIELDS,
            "history_integration_resolved", "history_integration_current_job_id", "history_integration",
            "history_integration_open", "history_integration_unassessed_objective_count"}
    if not isinstance(value, dict) or not keys.intersection(value):
        return {}
    valid = (value.get("learning_contract_version") == CONTRACT_VERSION
             and value.get("learning_phase") in (ENCODING_PHASE, HISTORY_PHASE)
             and value.get("phase_metadata_complete") is True)
    safe = {"learning_contract_version": CONTRACT_VERSION,
            "learning_phase": value["learning_phase"] if valid else "unknown", "phase_metadata_complete": bool(valid)}
    if any(key in value for key in HISTORY_LINK_FIELDS) or "history_integration_resolved" in value:
        linked, current = value.get("history_integration_job_id"), value.get("history_integration_current_job_id")
        state = value.get("history_integration_state")
        resolved = (valid and value.get("history_integration_resolved") is True
                    and isinstance(state, str) and state in HISTORY_STATES - {"unknown"}
                    and _identifier(linked) and _identifier(current))
        if _identifier(linked):
            safe["history_integration_job_id"] = linked
        if resolved and state == "deferred":
            reason, category = value.get("history_integration_reason"), value.get("history_integration_category")
            resolved = reason == DEFERRED_REASON and isinstance(category, str) and category in DEFERRED_CATEGORIES
            if resolved:
                safe.update(history_integration_reason=reason, history_integration_category=category)
        safe.update(history_integration_state=state if resolved else "unknown", history_integration_resolved=bool(resolved))
        if resolved:
            safe["history_integration_current_job_id"] = current
            for key in ("history_integration_open", "history_integration_unassessed_objective_count"):
                if key in value:
                    valid_count = _integer(value[key]) and value[key] <= 32 * 257
                    if (type(value[key]) is bool if key == "history_integration_open" else valid_count):
                        safe[key] = value[key]
                    else:
                        safe["phase_metadata_complete"] = False
    if "history_integration" in value:
        coverage = value["history_integration"]
        if (isinstance(coverage, dict) and set(coverage) == {"version", "objective_count", "assessed_count", "published_count"}
                and coverage["version"] == _COVERAGE_VERSION
                and all(_integer(coverage.get(key)) for key in ("objective_count", "assessed_count", "published_count"))
                and coverage["published_count"] <= coverage["objective_count"] <= 32
                and coverage["assessed_count"] <= coverage["objective_count"]):
            safe["history_integration"] = dict(coverage)
        else:
            safe["phase_metadata_complete"] = False
    return safe


def job_phase_metadata(row):
    selected = classify(row.get("payload", {}))
    if selected is None:
        return {}
    safe = {"learning_contract_version": CONTRACT_VERSION, "learning_phase": selected,
            "phase_metadata_complete": selected != "unknown"}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    if "history_integration" in result and result["history_integration"] != {}:
        safe["history_integration"] = coverage_summary(result["history_integration"])
    if selected == HISTORY_PHASE or "history_integration_job_id" in result:
        safe.update(history_integration_job_id=result.get("history_integration_job_id")
                    if selected != HISTORY_PHASE else row["id"],
                    history_integration_state="unknown", history_integration_resolved=False)
    return sanitize_phase_metadata(safe)


def _selected_json(expression, fields):
    return ("coalesce((SELECT jsonb_object_agg(key,value) FROM jsonb_each(CASE WHEN jsonb_typeof("
            f"{expression})='object' THEN {expression} ELSE '{{}}'::jsonb END) WHERE key IN ("
            + ",".join(repr(key) for key in fields) + ")),'{}'::jsonb)")


def _exact_projection(alias=""):
    prefix = alias + "." if alias else ""
    fields = (*PHASE_FIELDS, "from_sequence", "through_sequence", "processed_through_sequence", "target", "recovery_job_id",
              "recovery_parent_job_id", "continuation_job_id", "continuation_parent_job_id")
    result_fields = (*HISTORY_LINK_FIELDS, "status", "learning_outcome", "learning_window", "history_integration",
                     "integration_continuation_job_id")
    return (f"{prefix}id,{prefix}kind,{prefix}state,{prefix}model_calls,{prefix}input_bytes,{prefix}last_error,"
            + _selected_json(prefix + "payload", fields) + " AS payload,"
            + _selected_json(prefix + "result", result_fields) + " AS result")


def _valid_exact_encoding(connection, params, parent):
    payload = parent["payload"]
    if payload["authorization_root_job_id"] == parent["id"]:
        return "encoding_replan_version" not in payload
    rows = list(connection.execute(
        "WITH RECURSIVE ancestry AS ("
        "SELECT j.id,j.kind,j.state,j.model_calls,j.input_bytes,j.last_error,j.payload,j.result,"
        "ARRAY[j.id] AS path,false AS cycle,0 AS depth FROM memory_jobs j "
        "WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s UNION ALL "
        "SELECT j.id,j.kind,j.state,j.model_calls,j.input_bytes,j.last_error,j.payload,j.result,"
        "a.path||j.id,j.id=ANY(a.path),a.depth+1 FROM ancestry a JOIN memory_jobs j "
        "ON j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s AND j.id=a.payload->>'recovery_parent_job_id' "
        "WHERE NOT a.cycle AND a.depth<256) SELECT " + _exact_projection("ancestry")
        + ",cycle,depth FROM ancestry LIMIT 258", (*params, parent["id"], *params),
    ))
    if (not rows or len(rows) > 257 or any(item["cycle"] for item in rows)
            or len({item["id"] for item in rows}) != len(rows)):
        return False
    windows = {}
    for item in rows:
        value = item["payload"]
        start, end = value.get("from_sequence"), value.get("through_sequence")
        scope = value.get("target")
        if (item["kind"] != "consolidate" or classify(value) != ENCODING_PHASE
                or not _integer(start) or not _integer(end) or not 1 <= start <= end or not isinstance(scope, dict)):
            return False
        windows[item["id"]] = {**item, "phase_payload": value, "learning_phase": ENCODING_PHASE,
                               "window_start": start, "window_end": end,
                               "processed": value.get("processed_through_sequence", start - 1),
                               "conversation": scope.get("conversation_ref"), "branch": scope.get("branch_ref"),
                               "recovery_id": value.get("recovery_job_id"),
                               "recovery_parent": value.get("recovery_parent_job_id"),
                               "continuation_id": value.get("continuation_job_id")}
    return parent["id"] in windows and valid_encoding_root(windows[parent["id"]], windows)


def resolve_history_status(connection, row):
    safe = job_phase_metadata(row)
    selected = classify(row.get("payload", {}))
    if selected not in (ENCODING_PHASE, HISTORY_PHASE):
        return safe
    params = tuple(row.get(key) for key in ("tenant_id", "owner_id", "brain_id"))
    if not all(_identifier(value) for value in params):
        return safe
    parent = row
    if selected == HISTORY_PHASE:
        parent_id = row["payload"].get("encoding_parent_job_id")
        if not _identifier(parent_id):
            return safe
        parent = connection.execute(
            "SELECT " + _exact_projection() + " FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s",
            (*params, parent_id),
        ).fetchone()
    if parent is None or parent["kind"] != "consolidate" or classify(parent["payload"]) != ENCODING_PHASE:
        return safe
    if not _valid_exact_encoding(connection, params, parent):
        return safe
    parent_result = parent.get("result") if isinstance(parent.get("result"), dict) else {}
    linked = parent_result.get("history_integration_job_id")
    if not _identifier(linked):
        return safe
    window = parent_result.get("learning_window")
    if not isinstance(window, dict):
        return safe
    start, end = window.get("from_sequence"), window.get("attempted_through_sequence")
    original_end = parent["payload"].get("through_sequence")
    if not (_integer(start) and _integer(end) and _integer(original_end) and 1 <= start <= end <= original_end
            and start == parent["payload"].get("from_sequence")):
        return safe
    scope = parent["payload"].get("target")
    if (not isinstance(scope, dict) or not all(_identifier(scope.get(key)) for key in ("conversation_ref", "branch_ref"))):
        return safe
    rows = list(connection.execute(
        "WITH RECURSIVE lineage AS ("
        "SELECT j.id,j.kind,j.state,j.model_calls,j.input_bytes,j.last_error,j.payload,j.result,"
        "ARRAY[j.id] AS path,false AS cycle,0 AS depth "
        "FROM memory_jobs j WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s UNION ALL "
        "SELECT j.id,j.kind,j.state,j.model_calls,j.input_bytes,j.last_error,j.payload,j.result,"
        "l.path||j.id,j.id=ANY(l.path),l.depth+1 "
        "FROM lineage l JOIN memory_jobs j ON j.tenant_id=%s AND j.owner_id=%s AND j.brain_id=%s "
        "AND j.id IN (l.payload->>'recovery_job_id',l.payload->>'integration_continuation_job_id') "
        "WHERE NOT l.cycle AND l.depth<256) SELECT " + _exact_projection("lineage")
        + ",cycle,depth FROM lineage LIMIT 258",
        (*params, linked, *params),
    ))
    if (not rows or len(rows) > 257 or any(item["cycle"] for item in rows)
            or len({item["id"] for item in rows}) != len(rows)):
        return safe
    by_id = {item["id"]: item for item in rows}
    if selected == HISTORY_PHASE and row["id"] not in by_id:
        return safe
    root_ids = {item["payload"].get("authorization_root_job_id") for item in rows
                if classify(item["payload"]) == HISTORY_PHASE}
    roots = {parent["id"]: parent}
    if root_ids - roots.keys():
        roots.update({item["id"]: item for item in connection.execute(
            "SELECT " + _exact_projection() + " FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s)",
            (*params, sorted(root_ids - roots.keys())),
        )})
    current, previous, relation, fingerprint, unassessed = linked, None, None, None, 0
    seen = set()
    while current in by_id and current not in seen:
        seen.add(current)
        item = by_id[current]
        payload, result = item["payload"], item["result"]
        if (item["kind"] != "consolidate" or classify(payload) != HISTORY_PHASE
                or payload.get("encoding_parent_job_id") != parent["id"] or payload.get("target") != scope
                or (payload.get("from_sequence"), payload.get("through_sequence")) != (start, end)
                or not _fingerprint(payload.get("encoding_input_fingerprint"))
                or not _integer(payload.get("history_cutoff_sequence")) or payload["history_cutoff_sequence"] < end
                or "continuation_job_id" in payload or "continuation_parent_job_id" in payload):
            return safe
        root = roots.get(payload["authorization_root_job_id"])
        if (root is None or root["kind"] != "consolidate" or classify(root["payload"]) in (None, "unknown")
                or root["payload"].get("authorization_root_job_id") != root["id"]
                or root["payload"].get("target") != scope
                or any(root["payload"].get(key) != payload[key] for key in ("allocation_policy", "allocation_budget"))
                or payload["allocation_policy"] == ALLOCATION_POLICY and classify(root["payload"]) != ENCODING_PHASE):
            return safe
        if fingerprint is not None and fingerprint != payload["encoding_input_fingerprint"]:
            return safe
        fingerprint = payload["encoding_input_fingerprint"]
        reverse = {key: payload[key] for key in ("recovery_parent_job_id", "integration_continuation_parent_job_id") if key in payload}
        if reverse != ({relation + "_parent_job_id": previous} if previous is not None else {}):
            return safe
        if "integration_continuation_job_id" in result and result["integration_continuation_job_id"] != payload.get("integration_continuation_job_id"):
            return safe
        assessment = {"state": item["state"], "processing_status": result.get("status"),
                      "history_coverage": result.get("history_integration")}
        if item["state"] == "completed" and history_assessment(assessment) is None:
            return safe
        unassessed += unassessed_history(assessment)
        links = {key: payload[key] for key in ("recovery_job_id", "integration_continuation_job_id") if key in payload}
        if len(links) > 1 or any(not _identifier(value) for value in links.values()):
            return safe
        if links:
            key, child = next(iter(links.items()))
            predecessor = {**item, "processing_status": result.get("status"),
                           "learning_outcome": result.get("learning_outcome"),
                           "history_coverage": result.get("history_integration"),
                           "history_reason": result.get("history_integration_reason"),
                           "history_category": result.get("history_integration_category")}
            if (child not in by_id
                    or (key == "integration_continuation_job_id" and not valid_integration_predecessor(predecessor, by_id[child]))
                    or (key == "recovery_job_id" and item["state"] not in {"failed", "cancelled", "superseded"})):
                return safe
            previous, relation, current = current, key.removesuffix("_job_id"), child
            continue
        if len(seen) != len(rows) or item["state"] not in HISTORY_STATES - {"unknown"}:
            return safe
        safe.update(history_integration_job_id=linked, history_integration_current_job_id=current,
                    history_integration_state=item["state"], history_integration_resolved=True,
                    history_integration_open=item["state"] != "completed" or unassessed > 0,
                    history_integration_unassessed_objective_count=unassessed)
        if item["state"] == "deferred":
            safe.update(history_integration_reason=result.get("history_integration_reason"),
                        history_integration_category=result.get("history_integration_category"))
            if not valid_deferred({**item, "processing_status": result.get("status"),
                                   "learning_outcome": result.get("learning_outcome"),
                                   "history_reason": result.get("history_integration_reason"),
                                   "history_category": result.get("history_integration_category")}):
                safe["history_integration_resolved"] = False
        return sanitize_phase_metadata(safe)
    return safe


def projection():
    fields = ",".join(repr(key) for key in PHASE_FIELDS)
    return (
        "coalesce((SELECT jsonb_object_agg(key,value) FROM jsonb_each(CASE WHEN jsonb_typeof(payload)='object' "
        f"THEN payload ELSE '{{}}'::jsonb END) WHERE key IN ({fields})),'{{}}'::jsonb) AS phase_payload,"
        "model_calls,input_bytes,result #> '{learning_window,attempted_through_sequence}' AS attempted,"
        "result->'proposal_mode' AS proposal_mode,"
        "result ? 'history_integration_job_id' AS has_history_child,"
        "result->'history_integration_job_id' AS history_child,"
        "result->'history_integration_reason' AS history_reason,"
        "result->'history_integration_category' AS history_category,"
        "result->'history_integration' AS history_coverage,"
        "payload->'integration_continuation_job_id' AS integration_id,"
        "payload ? 'integration_continuation_job_id' AS has_integration,"
        "payload->'integration_continuation_parent_job_id' AS integration_parent"
    )
