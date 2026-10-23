from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

Version = tuple[str, int]
RANK_METHODS = frozenset({"keyword", "vector", "entity", "temporal", "association", "reranker"})


def _version(value: Sequence) -> Version:
    if (len(value) != 2 or not isinstance(value[0], str) or not value[0]
            or type(value[1]) is not int or value[1] < 1):
        raise ValueError("Memory references require an identifier and positive revision")
    return value[0], value[1]


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def fuse_rankings(
    rankings: Mapping[str, Sequence[Version]], *, weights: Mapping[str, float] | None = None,
    index_versions: Mapping[str, str] | None = None, rank_constant: int = 60, limit: int = 128,
) -> list[dict]:
    _positive_integer(rank_constant, "rank_constant")
    _positive_integer(limit, "limit")
    weights, index_versions = weights or {}, index_versions or {}
    if set(weights) - set(rankings) or set(index_versions) - set(rankings):
        raise ValueError("Rank metadata must name an existing route")
    fused = {}
    for method in sorted(rankings):
        if method not in RANK_METHODS:
            raise ValueError("Unsupported candidate ranking method")
        weight = weights.get(method, 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight < 0:
            raise ValueError("Rank weights must be finite and nonnegative")
        version = index_versions.get(method, "reciprocal-rank-v1")
        if not isinstance(version, str) or not version:
            raise ValueError("Rank index versions must be nonempty strings")
        seen = set()
        for value in rankings[method]:
            key = _version(value)
            if key in seen:
                continue
            seen.add(key)
            if not weight:
                continue
            score = weight / (rank_constant + len(seen))
            candidate = fused.setdefault(key, {
                "memory_id": key[0], "revision": key[1], "score": 0.0, "rank_signals": [],
            })
            candidate["score"] += score
            candidate["rank_signals"].append({
                "method": method, "score": score, "meaning": "ranking_only_not_probability",
                "model_or_index_version": version,
            })
    return sorted(fused.values(), key=lambda item: (-item["score"], item["memory_id"], item["revision"]))[:limit]


@dataclass(frozen=True)
class DependencyClosure:
    memories: tuple[dict, ...]
    missing_versions: tuple[Version, ...]
    truncated: bool
    cycle_versions: tuple[Version, ...]


def dependency_versions(memory: Mapping) -> tuple[Version, ...]:
    references = list(memory.get("derivation", {}).get("source_memory_versions", []))
    basis = memory.get("disclosure_basis", {})
    selected = set(basis.get("selected_path_ids", []))
    for path in basis.get("support_paths", []):
        if path["path_id"] in selected:
            references.extend(path.get("source_memory_versions", []))
    return tuple(sorted({_version((ref["memory_id"], ref["revision"])) for ref in references}))


def resolve_dependency_closure(
    roots: Sequence[dict], fetch: Callable[[list[Version]], Sequence[dict]], *,
    max_nodes: int = 256, max_depth: int = 8,
) -> DependencyClosure:
    _positive_integer(max_nodes, "max_nodes")
    if type(max_depth) is not int or max_depth < 0:
        raise ValueError("max_depth must be a nonnegative integer")
    documents = {}

    def add(memory):
        key = _version((memory["memory_id"], memory["revision"]))
        if key in documents and documents[key] != memory:
            raise ValueError("A memory version returned conflicting documents")
        documents[key] = memory
        return key

    for root in roots:
        add(root)
    if len(documents) > max_nodes:
        raise ValueError("Root candidates exceed the dependency node budget")
    frontier = list(documents)
    attempted, missing, edges = set(documents), set(), {}
    truncated = False
    depth = 0
    while frontier:
        needed = set()
        for key in frontier:
            edges[key] = dependency_versions(documents[key])
            needed.update(parent for parent in edges[key] if parent not in attempted)
        if not needed:
            break
        capacity = max_nodes - len(attempted)
        if depth >= max_depth or capacity <= 0:
            truncated = True
            break
        batch = sorted(needed)[:capacity]
        truncated = truncated or len(batch) < len(needed)
        attempted.update(batch)
        fetched = {}
        for memory in fetch(batch):
            key = _version((memory["memory_id"], memory["revision"]))
            if key not in batch:
                raise ValueError("Dependency loader returned an unrequested memory version")
            if key in fetched and fetched[key] != memory:
                raise ValueError("A memory version returned conflicting documents")
            fetched[key] = memory
        missing.update(set(batch) - set(fetched))
        frontier = [add(fetched[key]) for key in sorted(fetched)]
        depth += 1
    for key, memory in documents.items():
        edges.setdefault(key, dependency_versions(memory))
    cycles, done = set(), set()
    for start in documents:
        if start in done:
            continue
        stack, path, positions = [(start, False)], [], {}
        while stack:
            key, leaving = stack.pop()
            if leaving:
                positions.pop(key)
                path.pop()
                done.add(key)
            elif key in positions:
                cycles.update(path[positions[key]:])
            elif key not in done and key in documents:
                positions[key] = len(path)
                path.append(key)
                stack.append((key, True))
                stack.extend((parent, False) for parent in reversed(edges[key]))
    return DependencyClosure(tuple(documents.values()), tuple(sorted(missing)), truncated, tuple(sorted(cycles)))
