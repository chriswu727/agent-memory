from __future__ import annotations


def cognitive_usage(conn, principal, brain_id):
    rows = conn.execute(
        "WITH counters AS (SELECT outcome,split_part(operation_id,':',2) AS phase,"
        "CASE WHEN jsonb_typeof(usage->'input_tokens')='number' AND usage->>'input_tokens' ~ '^[0-9]+$' "
        "THEN (usage->>'input_tokens')::numeric END AS input_tokens,"
        "CASE WHEN jsonb_typeof(usage->'output_tokens')='number' AND usage->>'output_tokens' ~ '^[0-9]+$' "
        "THEN (usage->>'output_tokens')::numeric END AS output_tokens,"
        "CASE WHEN jsonb_typeof(usage->'total_tokens')='number' AND usage->>'total_tokens' ~ '^[0-9]+$' "
        "THEN (usage->>'total_tokens')::numeric END AS total_tokens "
        "FROM memory_cognitive_operations WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s),"
        "classified AS (SELECT *,COALESCE(input_tokens+output_tokens=total_tokens "
        "AND total_tokens<=9223372036854775807,false) AS usage_known "
        "FROM counters) SELECT CASE WHEN phase IN ('proposal','verification') THEN phase ELSE 'other' END AS phase,"
        "outcome,count(*) AS reservation_count,count(*) FILTER (WHERE usage_known) AS usage_known_count,"
        "COALESCE(sum(input_tokens) FILTER (WHERE usage_known),0) AS input_tokens,"
        "COALESCE(sum(output_tokens) FILTER (WHERE usage_known),0) AS output_tokens,"
        "COALESCE(sum(total_tokens) FILTER (WHERE usage_known),0) AS total_tokens "
        "FROM classified GROUP BY 1,2 ORDER BY 1,2",
        (principal.tenant_id, principal.principal_id, brain_id),
    ).fetchall()
    result = {
        "basis": "retained_cognitive_operation_reservations",
        "scope": "semantic_orientation_and_learning",
        "token_source": "provider_reported",
        "reservation_count": 0, "usage_known_count": 0, "usage_unknown_count": 0,
        "outcome_counts": {"reserved": 0, "completed": 0, "failed": 0, "uncertain": 0},
        "reported_tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "counts_complete": True, "phases": [],
    }
    phases = {}
    for row in rows:
        phase = phases.setdefault(row["phase"], {
            "phase": row["phase"], "reservation_count": 0, "usage_known_count": 0,
            "usage_unknown_count": 0,
            "outcome_counts": {"reserved": 0, "completed": 0, "failed": 0, "uncertain": 0},
            "reported_tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        })
        for target in (result, phase):
            target["reservation_count"] += row["reservation_count"]
            target["usage_known_count"] += row["usage_known_count"]
            target["usage_unknown_count"] += row["reservation_count"] - row["usage_known_count"]
            target["outcome_counts"][row["outcome"]] += row["reservation_count"]
            for name in target["reported_tokens"]:
                target["reported_tokens"][name] += int(row[name])
    result["phases"] = list(phases.values())
    result["counts_complete"] = result["usage_unknown_count"] == 0
    return result
