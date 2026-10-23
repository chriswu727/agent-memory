from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

REPRESENTATIONS = frozenset({"episode_fragment", "episode_outline", "semantic_anchor", "schema", "procedure"})


def _number(value, name, minimum=0.0, maximum=1.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and within its permitted range")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be finite and within its permitted range") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be finite and within its permitted range")
    return number


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Access timestamps must include a valid timezone") from None


def _counter(value, name):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError(f"{name} must be a nonnegative bounded integer")
    return value


@dataclass(frozen=True)
class AccessPolicy:
    version: str = "contextual-access-v1"
    half_life_days: Mapping[str, float] = field(default_factory=lambda: {
        "episode_fragment": 14.0, "episode_outline": 60.0, "semantic_anchor": 365.0,
        "schema": 180.0, "procedure": 90.0,
    })
    everyday_threshold: float = 0.3
    cue_threshold: float = 0.06
    retention_half_life_gain: float = 2.0
    retention_protection_threshold: float = 0.9
    retention_activation_floor: float = 0.12
    explicit_activation_floor: float = 0.4
    external_gain: float = 0.45
    external_half_life_days: float = 30.0
    external_use_scale: float = 4.0
    rank_floor: float = 0.15
    distinctive_rank_floor: float = 0.25

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version or len(self.version) > 128:
            raise ValueError("Access policy version must be explicitly named")
        if not isinstance(self.half_life_days, Mapping) or set(self.half_life_days) != REPRESENTATIONS:
            raise ValueError("Access half lives must cover every supported representation")
        half_lives = {key: _number(value, "half_life_days", 0.001, 365250)
                      for key, value in self.half_life_days.items()}
        object.__setattr__(self, "half_life_days", MappingProxyType(half_lives))
        for name in ("everyday_threshold", "cue_threshold", "retention_protection_threshold",
                     "retention_activation_floor", "explicit_activation_floor", "external_gain",
                     "rank_floor", "distinctive_rank_floor"):
            _number(getattr(self, name), name)
        _number(self.retention_half_life_gain, "retention_half_life_gain", 0, 100)
        _number(self.external_half_life_days, "external_half_life_days", 0.001, 365250)
        _number(self.external_use_scale, "external_use_scale", 0.001, 1_000_000)
        if not 0 < self.cue_threshold < self.everyday_threshold <= self.explicit_activation_floor:
            raise ValueError("Access thresholds must preserve deep, cue, and protected everyday ordering")
        if not self.cue_threshold <= self.retention_activation_floor <= self.explicit_activation_floor:
            raise ValueError("Retention protection must preserve at least cue accessibility")
        if not 0 < self.rank_floor <= self.distinctive_rank_floor <= 1:
            raise ValueError("Access ranking floors must be positive and ordered")


DEFAULT_POLICY = AccessPolicy()


def access_state(memory: Mapping, usage: Mapping | None, *, at: datetime | str,
                 policy: AccessPolicy = DEFAULT_POLICY) -> dict:
    if not isinstance(memory, Mapping) or (usage is not None and not isinstance(usage, Mapping)):
        raise ValueError("Memory and usage must be mappings")
    if not isinstance(policy, AccessPolicy):
        raise ValueError("policy must be an AccessPolicy")
    reference_time = _time(at)
    dynamics = memory.get("dynamics", {})
    system_time = memory.get("system_time", {})
    if not isinstance(dynamics, Mapping) or not isinstance(system_time, Mapping):
        raise ValueError("Memory dynamics and system time must be mappings")
    recorded_at = _time(system_time.get("recorded_at"))
    representation = memory.get("representation")
    if representation is None:
        representation = {"semantic": "semantic_anchor", "procedural": "procedure"}.get(memory.get("memory_kind"), "episode_fragment")
    if not isinstance(representation, str) or representation not in REPRESENTATIONS:
        raise ValueError("Unsupported memory representation")
    activation = _number(dynamics.get("activation", 0.5), "activation")
    retention = _number(dynamics.get("retention_priority", 0.5), "retention_priority")
    pinned = dynamics.get("user_pinned", False)
    if type(pinned) is not bool:
        raise ValueError("user_pinned must be a boolean")
    memory_reasons = dynamics.get("reasons", [])
    if not isinstance(memory_reasons, (list, tuple)) or any(not isinstance(reason, str) for reason in memory_reasons):
        raise ValueError("Memory reasons must be strings")
    elapsed_days = max(0, (reference_time - recorded_at).total_seconds() / 86400)
    half_life = policy.half_life_days[representation] * (1 + policy.retention_half_life_gain * retention)
    activation *= math.exp2(-elapsed_days / half_life)
    reasons = ["timestamp_decay", representation]
    usage = usage or {}
    uses = _counter(usage.get("external_uses", 0), "external_uses")
    successes = _counter(usage.get("successes", 0), "successes")
    failures = _counter(usage.get("failures", 0), "failures")
    if successes + failures > uses:
        raise ValueError("External outcomes cannot exceed external uses")
    utility = (1 + successes) / (2 + successes + failures)
    last_external = usage.get("last_external_use_at")
    if last_external is not None:
        last_external = _time(last_external)
    if uses and last_external is not None:
        external_age = max(0, (reference_time - last_external).total_seconds() / 86400)
        frequency = -math.expm1(-uses / policy.external_use_scale)
        activation += policy.external_gain * frequency * math.exp2(-external_age / policy.external_half_life_days)
        reasons.append("external_use")
    elif uses:
        reasons.append("external_use_time_unknown")
    if successes or failures:
        reasons.append("external_outcome_utility")
    protected = False
    if retention >= policy.retention_protection_threshold:
        activation = max(activation, policy.retention_activation_floor)
        protected = True
        reasons.append("high_retention")
    if pinned or "open_commitment" in memory_reasons:
        activation = max(activation, policy.explicit_activation_floor)
        protected = True
        if pinned:
            reasons.append("user_pinned")
        if "open_commitment" in memory_reasons:
            reasons.append("open_commitment")
    if "distinctive_event" in memory_reasons:
        reasons.append("distinctive_event")
    activation = min(1.0, activation)
    tier = ("everyday" if activation >= policy.everyday_threshold else
            "cue" if activation >= policy.cue_threshold else "deep")
    return {"tier": tier, "activation": activation, "utility": utility, "protected": protected,
            "reasons": reasons, "policy_version": policy.version}


def rank_weight(state: Mapping, *, direct: bool = False, policy: AccessPolicy = DEFAULT_POLICY) -> float:
    if not isinstance(state, Mapping) or not isinstance(policy, AccessPolicy) or type(direct) is not bool:
        raise ValueError("Rank weighting requires a state, policy, and boolean direct flag")
    activation = _number(state.get("activation"), "activation")
    utility = _number(state.get("utility"), "utility")
    if state.get("tier") not in {"everyday", "cue", "deep"}:
        raise ValueError("Unknown accessibility tier")
    if direct:
        return 1.0
    floor = policy.distinctive_rank_floor if "distinctive_event" in state.get("reasons", []) else policy.rank_floor
    return floor + (1 - floor) * activation * (0.5 + 0.5 * utility)
