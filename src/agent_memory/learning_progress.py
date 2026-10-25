from collections import Counter, deque
from datetime import UTC, datetime

from .jobs import FAILURE_CODES, LEARNING_OUTCOMES, PROCESSING_STATUSES, REJECTION_REASONS

_MAX_INTEGER = 2**63 - 1
_EXAMPLE_LIMIT = 8
_MAX_ATTEMPTS = 3
_STATES = frozenset({"queued", "running", "failed", "cancelled", "superseded", "completed"})
_LIMITATIONS = frozenset({
    "invalid_window", "invalid_schedule", "invalid_lineage", "missing_lineage", "lineage_cycle",
    "invalid_metadata", "invalid_summary",
})
_REASONS = FAILURE_CODES | {"attempts_exhausted", "missing_continuation", "terminal_window"}
_COUNTS = (
    "window_count", "open_window_count", "runnable_count", "waiting_retry_count", "running_count",
    "blocked_count", "unknown_count",
)
_ACCEPTED = ("claims", "situation_updates", "episode_actions", "belief_links")
_PUBLISHED = {"claims": "memory_versions", "episode_versions": "episode_versions",
              "pattern_views": "pattern_view_ids", "belief_relations": "belief_relation_versions"}
_OUTCOME_COUNTS = ("job_count", "evaluated_job_count", "unevaluated_job_count", "unknown_job_count")
_OUTCOME_LIMITATIONS = frozenset({
    "missing_result", "invalid_result", "invalidated_result", "invalid_metadata", "invalid_summary",
})
_OUTCOME_REASONS = frozenset({
    "no_accepted_proposals", "partial_acceptance", "accepted_without_publication", "unavailable_result",
})


def _safe_counts(value, allowed, *, required=False):
    if not isinstance(value, dict):
        return {key: 0 for key in allowed} if required else {}, False
    valid = (not set(value) - set(allowed) and all(_integer(count) for count in value.values())
             and (not required or set(value) == set(allowed)))
    return {key: value.get(key, 0) if _integer(value.get(key, 0)) else 0 for key in sorted(allowed)
            if required or _integer(value.get(key)) and value[key]}, valid


def _safe_outcome_example(value):
    result = _safe_window(value)
    if result is None or not _integer(value.get("fence")):
        return None
    enums = (("processing_status", PROCESSING_STATUSES | {"unknown"}),
             ("learning_outcome", LEARNING_OUTCOMES | {"unknown"}), ("reason", _OUTCOME_REASONS))
    if any(not isinstance(value.get(key), str) or value[key] not in allowed for key, allowed in enums):
        return None
    return {**result, "fence": value["fence"], **{key: value[key] for key, _ in enums}}


def _sanitize_outcomes(value, job_count):
    value = value if isinstance(value, dict) else {}
    complete = value.get("counts_complete") is True and value.get("basis") == "retained_latest_job_results"
    result = {"basis": "retained_latest_job_results"}
    for key in _OUTCOME_COUNTS:
        result[key] = value[key] if _integer(value.get(key)) else 0
        complete = complete and _integer(value.get(key))
    for key, allowed, required in (
        ("processing_status_counts", PROCESSING_STATUSES, False),
        ("learning_outcome_counts", LEARNING_OUTCOMES, False),
        ("accepted_counts", _ACCEPTED, True), ("published_counts", _PUBLISHED, True),
        ("rejection_reason_counts", REJECTION_REASONS, False),
        ("limitation_counts", _OUTCOME_LIMITATIONS, False),
    ):
        result[key], valid = _safe_counts(value.get(key), allowed, required=required)
        complete = complete and valid
    examples = value.get("examples")
    complete = complete and isinstance(examples, list) and type(value.get("examples_truncated")) is bool
    examples = examples if isinstance(examples, list) else []
    result["examples"] = [safe for item in examples[:_EXAMPLE_LIMIT]
                          if (safe := _safe_outcome_example(item)) is not None]
    result["examples_truncated"] = value.get("examples_truncated") is True or len(examples) > _EXAMPLE_LIMIT
    complete = complete and len(result["examples"]) == min(len(examples), _EXAMPLE_LIMIT)
    known = result["evaluated_job_count"] + result["unevaluated_job_count"]
    complete = complete and (
        result["job_count"] == job_count == known + result["unknown_job_count"]
        and sum(result["processing_status_counts"].values()) == known
        and sum(result["learning_outcome_counts"].values()) == known
        and sum(result["processing_status_counts"].get(key, 0) for key in ("completed", "partial"))
        == result["evaluated_job_count"]
        and result["learning_outcome_counts"].get("not_evaluated", 0) == result["unevaluated_job_count"]
        and not result["unknown_job_count"] and not result["limitation_counts"]
    )
    result["counts_complete"] = bool(complete)
    if not complete and not result["limitation_counts"]:
        result["limitation_counts"] = {"invalid_summary": 1}
    return result


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= _MAX_INTEGER


def _identifier(value):
    return isinstance(value, str) and 1 <= len(value) <= 256 and not any(ord(c) < 32 for c in value)


def _instant(value):
    try:
        if isinstance(value, str) and len(value) <= 64:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    return None


def _timestamp(value):
    stamp = _instant(value)
    return stamp.isoformat() if stamp is not None else None


def _safe_window(value, *, example=False):
    if not isinstance(value, dict) or not _identifier(value.get("job_id")):
        return None
    start, end, processed = (value.get(key) for key in (
        "from_sequence", "through_sequence", "processed_through_sequence",
    ))
    if not (_integer(start, 1) and _integer(end, 1) and _integer(processed)
            and start - 1 <= processed <= end and start <= end):
        return None
    result = {"job_id": value["job_id"], "from_sequence": start,
              "through_sequence": end, "processed_through_sequence": processed}
    if example:
        if (not _integer(value.get("fence")) or not isinstance(value.get("reason"), str)
                or value["reason"] not in _REASONS):
            return None
        error = value.get("last_error")
        result.update(fence=value["fence"], reason=value["reason"],
                      last_error=error if isinstance(error, str) and error in FAILURE_CODES else None)
    else:
        stamp = _timestamp(value.get("created_at"))
        if stamp is None:
            return None
        result["created_at"] = stamp
    return result


def sanitize_learning_progress(value):
    if not isinstance(value, dict):
        value = {}
    complete = value.get("counts_complete") is True
    result = {"provider_state": value.get("provider_state") if value.get("provider_state") in (
        "configured", "not_configured",
    ) else "unknown"}
    complete = complete and result["provider_state"] != "unknown"
    for key in _COUNTS:
        count = value.get(key)
        result[key] = count if _integer(count) else 0
        complete = complete and _integer(count)
    for key, allowed in (("blocked_reason_counts", _REASONS), ("limitation_counts", _LIMITATIONS)):
        counts = value.get(key)
        result[key] = {reason: counts[reason] for reason in sorted(allowed)
                       if isinstance(counts, dict) and _integer(counts.get(reason)) and counts[reason]}
        if not isinstance(counts, dict) or any(
            name not in allowed or not _integer(count) for name, count in counts.items()
        ):
            complete = False
    result["oldest_pending_window"] = _safe_window(value.get("oldest_pending_window"))
    if value.get("oldest_pending_window") is not None and result["oldest_pending_window"] is None:
        complete = False
    result["next_retry_at"] = _timestamp(value.get("next_retry_at"))
    if value.get("next_retry_at") is not None and result["next_retry_at"] is None:
        complete = False
    examples = value.get("blocked_examples")
    if not isinstance(examples, list) or type(value.get("blocked_examples_truncated")) is not bool:
        complete = False
    examples = examples if isinstance(examples, list) else []
    result["blocked_examples"] = [safe for item in examples[:_EXAMPLE_LIMIT]
                                  if (safe := _safe_window(item, example=True)) is not None]
    result["blocked_examples_truncated"] = (
        value.get("blocked_examples_truncated") is True or len(examples) > _EXAMPLE_LIMIT
    )
    if len(result["blocked_examples"]) != min(len(examples), _EXAMPLE_LIMIT):
        complete = False
    if result["open_window_count"] != sum(result[key] for key in (
        "runnable_count", "waiting_retry_count", "running_count", "blocked_count", "unknown_count",
    )) or result["open_window_count"] > result["window_count"]:
        complete = False
    if (sum(result["blocked_reason_counts"].values()) != result["blocked_count"]
            or bool(result["waiting_retry_count"]) != (result["next_retry_at"] is not None)
            or bool(result["open_window_count"]) != (result["oldest_pending_window"] is not None)):
        complete = False
    complete = complete and not result["unknown_count"] and not result["limitation_counts"]
    result["counts_complete"] = complete
    if not complete and not result["limitation_counts"]:
        result["limitation_counts"] = {"invalid_summary": 1}
    result["semantic_outcomes"] = _sanitize_outcomes(value.get("semantic_outcomes"), result["window_count"])
    return result


def _outcome_record(row):
    if row["issues"]:
        return None, "invalid_metadata"
    if row["invalidated"]:
        return None, "invalidated_result"
    absent = not row.get("has_result") or row.get("result_object") is True and not row.get("has_status")
    if absent:
        if (row["state"] == "queued" and row["attempts"] == 0
                or row["state"] == "running" and row["attempts"] == 1):
            return {"processing_status": "not_evaluated", "learning_outcome": "not_evaluated",
                    "accepted_counts": dict.fromkeys(_ACCEPTED, 0),
                    "published_counts": dict.fromkeys(_PUBLISHED, 0), "rejection_reason_counts": {}}, None
        return None, "missing_result"
    status, outcome = row.get("processing_status"), row.get("learning_outcome")
    accepted, accepted_valid = _safe_counts(row.get("accepted_counts"), _ACCEPTED, required=True)
    published, published_valid = _safe_counts(row.get("published_counts"), _PUBLISHED, required=True)
    reasons, reasons_valid = _safe_counts(row.get("rejection_reason_counts"), REJECTION_REASONS)
    if (row.get("result_object") is not True or row.get("publication_object") is not True
            or not isinstance(status, str) or status not in PROCESSING_STATUSES
            or not isinstance(outcome, str) or outcome not in LEARNING_OUTCOMES
            or not (accepted_valid and published_valid and reasons_valid)
            or not _integer(row.get("rejected_count")) or sum(reasons.values()) != row["rejected_count"]):
        return None, "invalid_result"
    evaluated = status in {"completed", "partial"}
    expected = ("accepted_proposals" if sum(accepted.values()) else "no_accepted_proposals" if status == "partial"
                else "no_proposals" if status == "completed" else "not_evaluated")
    if (outcome != expected or status == "completed" and row["rejected_count"]
            or status == "partial" and not row["rejected_count"]
            or not evaluated and (sum(accepted.values()) or sum(published.values()))
            or sum(published.values()) and row["state"] != "completed"):
        return None, "invalid_result"
    return {"processing_status": status, "learning_outcome": outcome, "accepted_counts": accepted,
            "published_counts": published, "rejection_reason_counts": reasons}, None


def _outcomes(windows):
    result = dict.fromkeys(_OUTCOME_COUNTS, 0)
    result.update(basis="retained_latest_job_results", job_count=len(windows), counts_complete=True,
                  accepted_counts=Counter(dict.fromkeys(_ACCEPTED, 0)),
                  published_counts=Counter(dict.fromkeys(_PUBLISHED, 0)),
                  processing_status_counts=Counter(), learning_outcome_counts=Counter(),
                  rejection_reason_counts=Counter(), limitation_counts=Counter())
    examples = []
    for identifier, row in windows.items():
        record, limitation = _outcome_record(row)
        reason = None
        if limitation:
            result["unknown_job_count"] += 1
            result["limitation_counts"][limitation] += 1
            reason = "unavailable_result"
        else:
            evaluated = record["processing_status"] in {"completed", "partial"}
            result["evaluated_job_count" if evaluated else "unevaluated_job_count"] += 1
            for key in ("processing_status", "learning_outcome"):
                result[key + "_counts"][record[key]] += 1
            for key in ("accepted_counts", "published_counts", "rejection_reason_counts"):
                result[key].update(record[key])
            if record["learning_outcome"] == "no_accepted_proposals":
                reason = "no_accepted_proposals"
            elif record["learning_outcome"] == "accepted_proposals":
                reason = ("accepted_without_publication" if not sum(record["published_counts"].values())
                          else "partial_acceptance" if record["processing_status"] == "partial" else None)
        if reason:
            example = _safe_outcome_example({
                "job_id": identifier, "fence": row["fence"], "from_sequence": row["window_start"],
                "through_sequence": row["window_end"], "processed_through_sequence": row["processed"],
                "created_at": row["created_at"], "reason": reason,
                "processing_status": record["processing_status"] if record else "unknown",
                "learning_outcome": record["learning_outcome"] if record else "unknown",
            })
            if example:
                examples.append(example)
    examples.sort(key=lambda item: (item["from_sequence"], item["created_at"], item["job_id"]))
    result.update(examples=examples[:_EXAMPLE_LIMIT], examples_truncated=len(examples) > _EXAMPLE_LIMIT)
    return _sanitize_outcomes(result, len(windows))


def _array_length(expression, *, optional=False, element_type="object"):
    absent = f"WHEN {expression} IS NULL THEN 0 " if optional else ""
    return (f"CASE {absent}WHEN jsonb_typeof({expression})='array' THEN CASE WHEN NOT EXISTS "
            f"(SELECT 1 FROM jsonb_array_elements({expression}) AS element "
            f"WHERE jsonb_typeof(element)<>'{element_type}') THEN jsonb_array_length({expression}) END END")


def _outcome_projection():
    accepted = ",".join(f"'{key}',{_array_length('result->' + repr(key))}" for key in _ACCEPTED)
    published = ",".join(f"'{key}'," + _array_length(
        "result #> " + repr("{publication," + field + "}"), optional=True,
        element_type="string" if key == "pattern_views" else "object",
    ) for key, field in _PUBLISHED.items())
    reasons = ",".join(f"'{reason}',count(*) FILTER (WHERE item->>'reason'='{reason}')"
                       for reason in sorted(REJECTION_REASONS))
    return (
        "result IS NOT NULL AS has_result,jsonb_typeof(result)='object' AS result_object,"
        "result ? 'status' AS has_status,result->'status' AS processing_status,"
        "result->'learning_outcome' AS learning_outcome,"
        "jsonb_typeof(result->'publication')='object' AS publication_object,"
        f"jsonb_build_object({accepted}) AS accepted_counts,"
        f"jsonb_build_object({published}) AS published_counts,"
        f"{_array_length('result->' + repr('rejected'))} AS rejected_count,"
        f"(SELECT jsonb_build_object({reasons}) FROM jsonb_array_elements("
        "CASE WHEN jsonb_typeof(result->'rejected')='array' THEN result->'rejected' ELSE '[]'::jsonb END"
        ") AS item) AS rejection_reason_counts"
    )


def _cycles(edges):
    colors, cyclic = {}, set()
    for start in edges:
        if colors.get(start):
            continue
        colors[start] = 1
        path, positions = [start], {start: 0}
        stack = [(start, iter(edges[start]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                path.pop()
                positions.pop(node)
                colors[node] = 2
            elif colors.get(child) == 1:
                cyclic.update(path[positions[child]:])
            elif not colors.get(child):
                colors[child] = 1
                positions[child] = len(path)
                path.append(child)
                stack.append((child, iter(edges[child])))
    return cyclic


def _subtract(ranges, covered):
    for low, high in covered:
        remaining = []
        for start, end in ranges:
            if high < start or low > end:
                remaining.append((start, end))
            else:
                if low > start:
                    remaining.append((start, low - 1))
                if high < end:
                    remaining.append((high + 1, end))
        ranges = remaining
    return ranges


def _summary(rows, *, configured):
    windows = {}
    for raw in rows:
        row = dict(raw)
        row["issues"] = set()
        start, end = row["window_start"], row["window_end"]
        processed = row["processed"] if row["has_processed"] else (start - 1 if _integer(start, 1) else None)
        row["processed"] = processed
        if not (_integer(start, 1) and _integer(end, 1) and start <= end and _integer(processed)
                and start - 1 <= processed <= end):
            row["issues"].add("invalid_window")
        if (not _identifier(row["id"]) or row["state"] not in _STATES
                or not all(_integer(row[key]) for key in ("attempts", "fence"))
                or not all(_identifier(row[key]) for key in ("conversation", "branch"))):
            row["issues"].add("invalid_metadata")
        for key in ("created_at", "available_at", "lease_expires_at", "observed_at"):
            original = row[key]
            row[key] = _instant(original)
            if row[key] is None and (original is not None or key != "lease_expires_at"):
                row["issues"].add("invalid_schedule")
        if row["state"] == "running" and row["lease_expires_at"] is None:
            row["issues"].add("invalid_schedule")
        row["last_error"] = (row["last_error"] if isinstance(row["last_error"], str)
                             and row["last_error"] in FAILURE_CODES else
                             "internal_error" if row["last_error"] is not None else None)
        row["invalidated"] = row["state"] == "superseded" or (
            row["state"] == "cancelled" and row["last_error"] in {"source_unavailable", "context_stale"}
        )
        row["ranges"] = [] if row["issues"] else (
            [(start, end)] if row["invalidated"] else [(processed + 1, end)] if processed < end else []
        )
        if row["state"] != "completed" and not row["invalidated"] and not row["ranges"] and not row["issues"]:
            row["issues"].add("invalid_window")
        windows[row["id"]] = row

    edges = {identifier: [] for identifier in windows}
    delegated = {identifier: [] for identifier in windows}
    parents = {identifier: [] for identifier in windows}
    for identifier, row in windows.items():
        if row["issues"]:
            continue
        for relation in ("continuation", "recovery"):
            child_id = row[relation + "_id"]
            if child_id is None:
                if row.get("has_" + relation) is True:
                    row["issues"].add("invalid_lineage")
                continue
            if not _identifier(child_id) or child_id not in windows:
                row["issues"].add("missing_lineage")
                continue
            child = windows[child_id]
            start, end, processed = row["window_start"], row["window_end"], row["processed"]
            if relation == "recovery" and row["invalidated"]:
                required = (start, processed if processed >= start else end)
            else:
                required = (processed + 1, end)
            back_reference = child[relation + "_parent"]
            if (child["issues"] or required[0] > required[1]
                    or (relation == "continuation" and not row["has_processed"])
                    or (back_reference is not None and back_reference != identifier)
                    or (child["conversation"], child["branch"]) != (row["conversation"], row["branch"])
                    or child["window_start"] != required[0] or child["window_end"] < required[1]
                    or (relation == "recovery" and child["window_end"] != required[1])):
                row["issues"].add("invalid_lineage")
                continue
            if any(not (required[1] < low or required[0] > high) for low, high in delegated[identifier]):
                row["issues"].add("invalid_lineage")
            edges[identifier].append(child_id)
            parents[child_id].append(identifier)
            delegated[identifier].append(required)
        for relation in ("continuation", "recovery"):
            parent_id = row[relation + "_parent"]
            if parent_id is not None and (
                not _identifier(parent_id) or parent_id not in windows
                or windows[parent_id][relation + "_id"] != identifier
            ):
                row["issues"].add("invalid_lineage")
    for identifier in _cycles(edges):
        windows[identifier]["issues"].add("lineage_cycle")
    for identifier, owners in parents.items():
        if len(set(owners)) > 1:
            windows[identifier]["issues"].add("invalid_lineage")
    invalid = deque(identifier for identifier, row in windows.items() if row["issues"])
    while invalid:
        for parent in parents[invalid.popleft()]:
            if not windows[parent]["issues"]:
                windows[parent]["issues"].add("invalid_lineage")
                invalid.append(parent)

    result = {key: 0 for key in _COUNTS}
    result.update(provider_state="configured" if configured else "not_configured",
                  window_count=len(windows), counts_complete=True, oldest_pending_window=None,
                  next_retry_at=None, blocked_examples=[], blocked_examples_truncated=False)
    reasons, limitations = Counter(), Counter()
    oldest, blocked = [], []
    for identifier, row in windows.items():
        if row["issues"]:
            result["unknown_count"] += 1
            result["open_window_count"] += 1
            limitations.update(row["issues"])
            continue
        ranges = _subtract(row["ranges"], delegated[identifier])
        if not ranges:
            continue
        result["open_window_count"] += 1
        window = {"job_id": identifier, "from_sequence": ranges[0][0], "through_sequence": ranges[-1][1],
                  "processed_through_sequence": ranges[0][0] - 1, "created_at": row["created_at"].isoformat()}
        oldest.append((window["from_sequence"], row["created_at"], identifier, window))
        reason = None
        if row["state"] == "running" and row["lease_expires_at"] > row["observed_at"]:
            category = "running_count"
        elif row["state"] in {"cancelled", "superseded", "completed"}:
            category = "blocked_count"
            reason = row["last_error"] or ("missing_continuation" if row["state"] == "completed" else "terminal_window")
        elif row["attempts"] >= _MAX_ATTEMPTS:
            category, reason = "blocked_count", "attempts_exhausted"
        elif row["available_at"] > row["observed_at"]:
            category = "waiting_retry_count"
            retry = row["available_at"].isoformat()
            result["next_retry_at"] = min(result["next_retry_at"] or retry, retry)
        else:
            category = "runnable_count"
        result[category] += 1
        if reason is not None:
            reasons[reason] += 1
            blocked.append((window["from_sequence"], row["created_at"], identifier, {
                **{key: value for key, value in window.items() if key != "created_at"},
                "fence": row["fence"], "last_error": row["last_error"], "reason": reason,
            }))
    if oldest:
        result["oldest_pending_window"] = min(oldest, key=lambda item: item[:3])[3]
    result["blocked_examples"] = [item[3] for item in sorted(blocked, key=lambda item: item[:3])[:_EXAMPLE_LIMIT]]
    result["blocked_examples_truncated"] = len(blocked) > _EXAMPLE_LIMIT
    result.update(blocked_reason_counts=dict(reasons), limitation_counts=dict(limitations),
                  semantic_outcomes=_outcomes(windows))
    return sanitize_learning_progress(result)


def learning_progress(connection, principal, brain_id, *, configured: bool):
    """Count windows, ordered by pending source sequence, without claiming semantic coverage.

    Runnable means claimable, not guaranteed to fit the model budget. Creation times refer to jobs.
    Semantic totals count retained latest job results, not every attempt or currently usable memories.
    Evaluated includes partial structural rejection; it does not assert verification of every proposal.
    """
    if type(configured) is not bool:
        raise ValueError("Learning provider configuration must be a boolean")
    rows = connection.execute(
        "SELECT id,state,attempts,fence,last_error,created_at,available_at,lease_expires_at,"
        "statement_timestamp() AS observed_at,payload->'from_sequence' AS window_start,"
        "payload->'through_sequence' AS window_end,payload->'processed_through_sequence' AS processed,"
        "payload ? 'processed_through_sequence' AS has_processed,"
        "payload #> '{target,conversation_ref}' AS conversation,payload #> '{target,branch_ref}' AS branch,"
        "payload->'continuation_job_id' AS continuation_id,payload->'recovery_job_id' AS recovery_id,"
        "payload ? 'continuation_job_id' AS has_continuation,payload ? 'recovery_job_id' AS has_recovery,"
        "payload->'continuation_parent_job_id' AS continuation_parent,"
        "payload->'recovery_parent_job_id' AS recovery_parent," + _outcome_projection() + " "
        "FROM memory_jobs WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND kind='consolidate' "
        "AND payload ? 'from_sequence'",
        (principal.tenant_id, principal.principal_id, brain_id),
    )
    return _summary(rows, configured=configured)
