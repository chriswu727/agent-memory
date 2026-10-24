from __future__ import annotations

import copy
from collections import defaultdict

from .contracts import validate
from .helpers import DomainError, digest

FORMAT_VERSION = "schema-residual-v1"
MAX_MEMORIES = 64
MAX_EPISODES = 16
MEANING_FIELDS = (
    "statement", "memory_kind", "world", "conditions", "valid_time", "valid_during",
    "claim_kind", "procedure", "condition_registry_fingerprint", "epistemic_status",
    "verification", "confidence", "representation",
)


def semantic_meaning(memory: dict) -> dict:
    return copy.deepcopy({key: memory[key] for key in MEANING_FIELDS if key in memory})


def _version(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def _key(version):
    if (not isinstance(version, dict) or set(version) != {"memory_id", "revision"}
            or not isinstance(version["memory_id"], str) or not version["memory_id"]
            or type(version["revision"]) is not int or version["revision"] < 1):
        raise ValueError("Invalid memory version")
    return version["memory_id"], version["revision"]


def _references(memory):
    refs = list(memory["evidence_refs"]) + list(memory.get("counterexamples", []))
    for path in memory["disclosure_basis"]["support_paths"]:
        refs.extend(path["evidence_refs"])
    return refs


def _memory_hash(memory):
    return digest({
        "meaning": semantic_meaning(memory), "scope": memory["scope"],
        "references": sorted(_references(memory), key=digest),
        "derivation": memory["derivation"],
        "support_paths": sorted([
            {key: path[key] for key in ("path_id", "source_memory_versions", "permitted_scope",
                                       "independently_supports_complete_statement")}
            for path in memory["disclosure_basis"]["support_paths"]
        ], key=digest),
        "root_event_ids": sorted(memory.get("root_event_ids", [])),
    })


def _episode_hash(episode):
    return digest({key: episode[key] for key in (
        "episode_id", "revision", "scope", "state", "member_refs", "root_event_ids",
    ) if key in episode})


def _eligible(memory, scope):
    from .cognition import _scope_allows

    validate("MemoryAssertion", memory)
    return (memory["lifecycle"] in {"active", "dormant"}
            and memory.get("representation") not in {"schema", "episode_outline"}
            and (memory["epistemic_status"] != "derived_inference"
                 or memory.get("verification", {}).get("state") in {"supported_inference", "contested"})
            and memory["disclosure_basis"]["validation_state"] == "current"
            and bool(memory["evidence_refs"])
            and _scope_allows(memory["scope"], scope))


def _ordered(memories, episode):
    positions = {(ref["event_id"], ref["revision"]): index
                 for index, ref in enumerate(episode["member_refs"])}

    def order(memory):
        return (min((positions[(ref["event_id"], ref["event_revision"])],
                     ref.get("span", {}).get("start", 0)) for ref in _references(memory)),
                memory["memory_id"], memory["revision"])

    return sorted(memories, key=order)


def _definition(meanings):
    claims = [{"slot_id": "slot-" + key, "meaning": meanings[key]} for key in sorted(meanings)]
    content_hash = digest(claims)
    return {"schema_id": "schema-" + content_hash, "revision": 1,
            "content_sha256": content_hash, "shared_claims": copy.deepcopy(claims)}


def _assemble(scope, definition, instance_memories, episodes):
    instances = []
    observed = {}
    roots = set()
    for episode in episodes:
        episode_key = episode["episode_id"], episode["revision"]
        memories = _ordered(instance_memories[episode_key], episode)
        shared = {digest(slot["meaning"]): slot["slot_id"] for slot in definition["shared_claims"]}
        used = set()
        bindings, residuals = [], []
        for memory in memories:
            key = digest(semantic_meaning(memory))
            if key in shared and key not in used:
                bindings.append({"slot_id": shared[key], "memory_version": _version(memory)})
                used.add(key)
            else:
                residuals.append(_version(memory))
            observed[_key(_version(memory))] = memory
            roots.update(memory.get("root_event_ids", []))
            roots.update(ref["event_id"] for ref in _references(memory))
        roots.update(episode.get("root_event_ids", []))
        roots.update(ref["event_id"] for ref in episode["member_refs"])
        instances.append({
            "episode_ref": {"episode_id": episode["episode_id"], "revision": episode["revision"]},
            "episode_sha256": _episode_hash(episode),
            "observed_memory_versions": [_version(memory) for memory in memories],
            "shared_bindings": sorted(bindings, key=lambda binding: binding["slot_id"]),
            "residual_memory_versions": residuals,
        })
    if len(roots) > 256:
        raise ValueError("Pattern source closure exceeds limits")
    view = {
        "format_version": FORMAT_VERSION, "scope": copy.deepcopy(scope),
        "definition": definition, "instances": instances, "coverage": "selected_assertions_only",
        "source_memory_versions": [_version(observed[key]) for key in sorted(observed)],
        "source_snapshots": [{"memory_version": _version(observed[key]),
                              "content_sha256": _memory_hash(observed[key])} for key in sorted(observed)],
        "root_event_ids": sorted(roots),
    }
    view["view_id"] = "pattern-" + digest(view)
    return view


def build_patterns(scope: dict, memories: list[dict], episodes: list[dict]) -> list[dict]:
    from .cognition import _scope_allows

    if len(memories) > MAX_MEMORIES or len(episodes) > MAX_EPISODES:
        raise ValueError("Pattern input exceeds limits")
    validate("BoundScope", scope)
    memory_keys = [_key(_version(memory)) for memory in memories]
    if len(memory_keys) != len(set(memory_keys)):
        raise ValueError("Duplicate memory version")
    selected = {}
    members = {}
    episode_ids = set()
    for episode in episodes:
        if (not isinstance(episode["episode_id"], str) or not episode["episode_id"]
                or type(episode["revision"]) is not int or episode["revision"] < 1
                or not isinstance(episode["member_refs"], list) or len(episode["member_refs"]) > 128):
            raise ValueError("Invalid episode")
        validate("BoundScope", episode["scope"])
        key = episode["episode_id"], episode["revision"]
        if episode["episode_id"] in episode_ids:
            raise ValueError("Repeated episode identity")
        episode_ids.add(episode["episode_id"])
        if not _scope_allows(episode["scope"], scope):
            continue
        refs = {(ref["event_id"], ref["revision"]) for ref in episode["member_refs"]}
        if len(refs) != len(episode["member_refs"]):
            raise ValueError("Duplicate episode member")
        selected[key], members[key] = episode, refs
    instance_memories = defaultdict(list)
    meanings = {}
    incidence = defaultdict(set)
    for memory in memories:
        if not _eligible(memory, scope):
            continue
        refs = {(ref["event_id"], ref["event_revision"]) for ref in _references(memory)}
        matching = [key for key in members if refs.issubset(members[key])]
        if len(matching) != 1:
            continue
        key = matching[0]
        instance_memories[key].append(memory)
        meaning = semantic_meaning(memory)
        fingerprint = digest(meaning)
        meanings[fingerprint] = meaning
        incidence[fingerprint].add(key)
    groups = defaultdict(dict)
    source_ids = {key: {ref[0] for ref in refs} for key, refs in members.items()}
    for fingerprint, keys in incidence.items():
        keys = tuple(sorted(keys))
        if len(keys) < 2:
            continue
        # Overlapping episode windows are not independent repetitions.
        if any(source_ids[left] & source_ids[right]
               for index, left in enumerate(keys) for right in keys[index + 1:]):
            continue
        groups[keys][fingerprint] = meanings[fingerprint]
    return [_assemble(scope, _definition(groups[keys]), instance_memories,
                      [selected[key] for key in keys]) for keys in sorted(groups)]


def _check(view, memories):
    expected = {"view_id", "format_version", "scope", "definition", "instances", "coverage",
                "source_memory_versions", "source_snapshots", "root_event_ids"}
    if (not isinstance(view, dict) or set(view) != expected or view["format_version"] != FORMAT_VERSION
            or view["coverage"] != "selected_assertions_only"
            or view["view_id"] != "pattern-" + digest({k: v for k, v in view.items() if k != "view_id"})):
        raise ValueError("Pattern identity mismatch")
    validate("BoundScope", view["scope"])
    if not 2 <= len(view["instances"]) <= MAX_EPISODES or len(view["source_memory_versions"]) > MAX_MEMORIES:
        raise ValueError("Pattern exceeds limits")
    if len(view["root_event_ids"]) > 256 or sorted(set(view["root_event_ids"])) != view["root_event_ids"]:
        raise ValueError("Invalid source closure")
    lookup = {_key(_version(memory)): memory for memory in memories}
    if len(lookup) != len(memories):
        raise ValueError("Duplicate memory version")
    sources = [_key(version) for version in view["source_memory_versions"]]
    if sources != sorted(set(sources)) or any(key not in lookup for key in sources):
        raise ValueError("Missing exact source version")
    if view["source_snapshots"] != [
        {"memory_version": _version(lookup[key]), "content_sha256": _memory_hash(lookup[key])} for key in sources
    ]:
        raise ValueError("Source content changed")
    if any(not _eligible(lookup[key], view["scope"]) for key in sources):
        raise ValueError("Ineligible source")
    definition = view["definition"]
    meanings = {digest(slot["meaning"]): slot["meaning"] for slot in definition["shared_claims"]}
    if not meanings or definition != _definition(meanings):
        raise ValueError("Invalid shared definition")
    slots = {slot["slot_id"]: slot["meaning"] for slot in definition["shared_claims"]}
    observed_all, episode_keys, support_sets = set(), set(), []
    for instance in view["instances"]:
        if set(instance) != {"episode_ref", "episode_sha256", "observed_memory_versions",
                             "shared_bindings", "residual_memory_versions"}:
            raise ValueError("Invalid instance")
        episode = instance["episode_ref"]
        if (set(episode) != {"episode_id", "revision"} or not isinstance(episode["episode_id"], str)
                or not episode["episode_id"] or type(episode["revision"]) is not int or episode["revision"] < 1):
            raise ValueError("Invalid episode version")
        if episode["episode_id"] in episode_keys:
            raise ValueError("Repeated episode")
        episode_keys.add(episode["episode_id"])
        ordered = [_key(version) for version in instance["observed_memory_versions"]]
        if len(ordered) != len(set(ordered)) or not ordered or any(key not in sources for key in ordered):
            raise ValueError("Invalid observed set")
        if observed_all.intersection(ordered):
            raise ValueError("Observation reused across episodes")
        observed_all.update(ordered)
        selected, bound_slots = [], set()
        for binding in instance["shared_bindings"]:
            if set(binding) != {"slot_id", "memory_version"}:
                raise ValueError("Invalid binding")
            slot_id, key = binding["slot_id"], _key(binding["memory_version"])
            if (slot_id not in slots or slot_id in bound_slots or key not in ordered
                    or slots[slot_id] != semantic_meaning(lookup[key])):
                raise ValueError("Binding changes observed meaning")
            selected.append(key)
            bound_slots.add(slot_id)
        selected.extend(_key(version) for version in instance["residual_memory_versions"])
        if bound_slots != set(slots) or len(selected) != len(set(selected)) or set(selected) != set(ordered):
            raise ValueError("Instance must preserve exactly its observations")
        support = {ref["event_id"] for key in ordered for ref in _references(lookup[key])}
        if any(support & old for old in support_sets):
            raise ValueError("Instances reuse observation sources")
        support_sets.append(support)
    if observed_all != set(sources):
        raise ValueError("Source set differs from instance observations")
    roots = {identifier for key in sources for identifier in lookup[key].get("root_event_ids", [])}
    roots.update(ref["event_id"] for key in sources for ref in _references(lookup[key]))
    if not roots.issubset(view["root_event_ids"]):
        raise ValueError("Incomplete source closure")
    return lookup


def validate_pattern(view: dict, memories: list[dict], episodes: list[dict] | None = None) -> bool:
    try:
        lookup = _check(view, memories)
        if episodes is not None:
            episode_map = {(episode["episode_id"], episode["revision"]): episode for episode in episodes}
            if len(episode_map) != len(episodes) or len(episodes) > MAX_EPISODES:
                return False
            selected, instance_memories, member_sets = [], {}, []
            for instance in view["instances"]:
                ref = instance["episode_ref"]
                key = ref["episode_id"], ref["revision"]
                episode = episode_map[key]
                if instance["episode_sha256"] != _episode_hash(episode):
                    return False
                members = {(ref["event_id"], ref["revision"]) for ref in episode["member_refs"]}
                source_ids = {ref[0] for ref in members}
                if any(source_ids & old for old in member_sets):
                    return False
                member_sets.append(source_ids)
                observed = [lookup[_key(version)] for version in instance["observed_memory_versions"]]
                if any((ref["event_id"], ref["event_revision"]) not in members
                       for memory in observed for ref in _references(memory)):
                    return False
                selected.append(episode)
                instance_memories[key] = observed
            return view == _assemble(view["scope"], view["definition"], instance_memories, selected)
        return True
    except (ValueError, KeyError, TypeError, IndexError, DomainError):
        return False


def expand_instance(view: dict, instance: dict, memories: list[dict]) -> list[dict]:
    if not validate_pattern(view, memories) or instance not in view["instances"]:
        raise ValueError("Invalid pattern instance")
    lookup = {_key(_version(memory)): memory for memory in memories}
    return [copy.deepcopy(lookup[_key(version)]) for version in instance["observed_memory_versions"]]
