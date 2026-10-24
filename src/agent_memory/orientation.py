from .helpers import digest

ORIENTATION_ROUTE_VERSION = "orientation-route-v1"


def assess_orientation(turn, working, state, *, planned=False, embedded=False, historical=False):
    reasons = []
    memories = state["belief_memories"]
    relations = state["belief_relations"]
    if turn["orientation_job_id"]:
        reasons.append("existing_orientation")
    if planned:
        reasons.append("recall_plan")
    if embedded:
        reasons.append("embedding_recall")
    if not historical:
        reasons.append("historical_boundary_required")
    if any(memory.get("conditions", {"op": "always"}) != {"op": "always"}
           or memory.get("procedure") is not None for memory in memories):
        reasons.append("conditional_memory")
    if any(relation.get("conditions") != {"op": "always"} for relation in relations):
        reasons.append("conditional_relation")
    if working["items"] or working["open_loops"] or state["context_observations"]:
        reasons.append("working_state")
    if (state["source_truncated"] or state["rebuilding"]
            or state["belief_incomplete_versions"] or not state["context_observations_complete"]):
        reasons.append("incomplete_dependencies")
    return {
        "version": ORIENTATION_ROUTE_VERSION,
        "decision": "orient" if reasons else "skip",
        "reasons": reasons or ["no_contextual_dependencies"],
        "input_fingerprint": digest([
            turn["turn_id"], turn["conversation_id"], turn["branch_id"], turn["input_event_refs"],
        ]),
        "snapshot": {**{key: state[key] for key in (
            "scope_generation", "dependency_generation", "durable_sequence", "indexed_sequence",
        )}, "working_revision": working["revision"]},
        "dependency_fingerprint": digest({
            "memories": sorted((memory["memory_id"], memory["revision"], digest(memory)) for memory in memories),
            "relations": sorted((relation["relation_id"], relation["revision"], digest(relation))
                                for relation in relations),
            "sources": sorted((event["event_id"], event["revision"], event["content_sha256"])
                              for event in state["working_event_metadata"].values()),
            "working": {key: value for key, value in working.items() if key != "updated_at"},
        }),
    }


def orientation_route(policy, decision, reasons, assessment=None):
    return {"version": ORIENTATION_ROUTE_VERSION, "policy": policy,
            "decision": decision, "reasons": reasons, "assessment": assessment}
