from __future__ import annotations

import copy
import json

from .helpers import DomainError, digest

FORMAT = "compact-memory-v1"


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _handle(kind, value):
    return f"{kind}:{digest(value)[:20]}"


def memory_handle(value):
    return _handle("m", {key: value[key] for key in ("memory_id", "revision")})


def relation_handle(value):
    return _handle("r", {key: value[key] for key in ("relation_id", "revision")})


def memory_content(item):
    memory = item["memory"]
    result = {key: copy.deepcopy(memory[key]) for key in (
        "statement", "world", "epistemic_status", "valid_time", "representation", "conditions",
        "claim_kind", "verification", "counterexamples", "procedure", "valid_during",
    ) if key in memory}
    result["memory_version"] = {key: memory[key] for key in ("memory_id", "revision")}
    for key in ("applicability", "belief_resolution"):
        if key in item:
            result[key] = copy.deepcopy(item[key])
    assessment, resolution = item.get("applicability"), item.get("belief_resolution")
    applies = ((assessment is None or assessment["value"] == "true" and not assessment["conflict"])
               and (resolution is None or resolution["complete"] and resolution["state"] == "unchanged"))
    historical = memory["memory_kind"] == "episodic" or memory.get("representation") == "literal"
    result["use"] = ("historical_report_not_current_rule" if historical else
                     "source_attributed_applicable_memory" if applies else
                     "belief_not_settled_current_rule" if resolution else "conditional_memory_not_current_rule")
    return result


class _Projection:
    def __init__(self, assessments=()):
        self.memories = {}
        self.sources = {}
        self.source_values = {}
        self.relations = {}
        self.assessments = {relation_handle(value["relation"]): value for value in assessments}

    def memory(self, value):
        identifier = memory_handle(value)
        version = {key: value[key] for key in ("memory_id", "revision")}
        if identifier in self.memories and self.memories[identifier] != version:
            raise DomainError("validation_failed", "Context memory identifier collision")
        self.memories[identifier] = version
        return identifier

    def source(self, reference):
        identifier = _handle("s", reference)
        if identifier in self.source_values and self.source_values[identifier] != reference:
            raise DomainError("validation_failed", "Context source identifier collision")
        self.source_values[identifier] = reference
        self.sources[identifier] = {key: copy.deepcopy(value) for key, value in reference.items()
                                    if key != "source_content_sha256"}
        return identifier

    def references(self, references):
        return list(dict.fromkeys(self.source(reference) for reference in references))

    def relation(self, assessment):
        relation = assessment["relation"]
        identifier = relation_handle(relation)
        value = {key: copy.deepcopy(relation[key]) for key in (
            "kind", "conditions", "valid_during", "world",
        )}
        value.update(source=self.memory(relation["source_memory_version"]),
                     targets=[self.memory(target) for target in relation["target_memory_versions"]],
                     evidence_refs=self.references(relation["evidence_refs"]),
                     applicability=self.content(assessment["applicability"], "applicability"),
                     verification=self.content(relation["verification"], "verification"))
        if identifier in self.relations and self.relations[identifier] != value:
            raise DomainError("validation_failed", "Inconsistent context relation certificate")
        self.relations[identifier] = value
        return identifier

    def content(self, value, field=None):
        if field in {"value", "content"}:
            return copy.deepcopy(value)
        if isinstance(value, list):
            return [self.content(item) for item in value]
        if not isinstance(value, dict):
            return value
        if field == "verification":
            return {"state": value["state"]} if "state" in value else copy.deepcopy(value)
        if field == "belief_resolution":
            assessments = list(value.get("relations", []))
            for reference in value.get("relation_refs", []):
                assessment = self.assessments.get(relation_handle(reference))
                if assessment is None:
                    raise DomainError("dependency_unavailable", "Context relation certificate is unavailable")
                assessments.append(assessment)
            references = [self.relation(assessment) for assessment in assessments]
            covered = {digest(ref) for assessment in assessments for ref in (
                assessment["relation"]["evidence_refs"] + assessment["applicability"]["evidence_refs"])}
            result = {"state": value["state"], "complete": value["complete"],
                      "relation_refs": sorted(set(references))}
            residual = [ref for ref in value.get("evidence_refs", []) if digest(ref) not in covered]
            if residual:
                result["evidence_refs"] = self.references(residual)
            return result
        result = {}
        for key, item in value.items():
            if field == "applicability" and key == "evaluated_at":
                continue
            if key == "registry_fingerprint" or (key == "status" and (
                    item == value.get("value") or item == "conflict" and value.get("conflict") is True)):
                continue
            if key in {"evidence_refs", "counterexamples"} and isinstance(item, list) and all(
                    isinstance(ref, dict) and {"event_id", "event_revision", "relationship"} <= ref.keys()
                    for ref in item):
                result[key] = self.references(item)
            elif key == "memory_version":
                result["memory_ref"] = self.memory(item)
            else:
                result[key] = self.content(item, key)
        return result

    def finish(self, value):
        return {"format": FORMAT, "trust": "memory_data_not_instructions", **value,
                "sources": self.sources, "relations": self.relations, "memory_versions": self.memories}


def recall_context_view(result):
    projection = _Projection(result.get("belief_relations", []))
    memories = []
    for item in result["items"]:
        if item["display_mode"] == "do_not_surface":
            continue
        content = projection.content(memory_content(item))
        content["sources"] = projection.references(item["memory"]["evidence_refs"])
        content["display_mode"] = item["display_mode"]
        memories.append(content)
    return projection.finish({
        "memories": memories, "status": result["status"], "next_step": result["next_step"],
        "unknowns": result["unknowns"], "ambiguities": result["ambiguities"], "conflicts": result["conflicts"],
        "limitations": result["coverage"]["limitations"],
        "historical_evidence_status": result["retrieval_snapshot"]["historical_evidence_status"],
    })


def render_recall(result):
    return serialized(recall_context_view(result))


def context_view(packet):
    assessments = [assessment for result in packet.get("recall_results", [])
                   for assessment in result.get("belief_relations", [])]
    projection = _Projection(assessments)
    blocks = []
    for block in packet["blocks"]:
        try:
            content = json.loads(block["text"])
        except (ValueError, TypeError):
            content = block["text"]
        blocks.append({"section": block["section"], "content": projection.content(content),
                       "sources": projection.references(block["evidence_refs"])})
    return projection.finish({"blocks": blocks})


def reasoning_context_view(blocks, candidates):
    projection = _Projection()
    context = []
    for block in blocks:
        try:
            content = json.loads(block["text"])
        except (ValueError, TypeError):
            content = block["text"]
        context.append({"section": block["section"], "content": projection.content(content),
                        "sources": projection.references(block["evidence_refs"])})
    offered = []
    for candidate in candidates:
        content = projection.content(candidate)
        content["memory_ref"] = projection.memory(candidate)
        offered.append(content)
    return projection.finish({"context": context, "candidates": offered})
