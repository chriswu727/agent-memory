from __future__ import annotations


def usage_marker(provider) -> str | None:
    try:
        value = provider.last_call_id
    except Exception:
        return None
    return value if isinstance(value, str) and 1 <= len(value) <= 128 and value.isascii() else None


def reported_usage(provider, *, previous_call_id: str | None) -> dict | None:
    marker = usage_marker(provider)
    if marker is None or marker == previous_call_id:
        return None
    try:
        value = provider.last_usage
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    names = ("prompt_tokens", "completion_tokens", "total_tokens")
    if any(type(value.get(key)) is not int or not 0 <= value[key] <= 2**63 - 1 for key in names):
        return None
    if value["total_tokens"] != value["prompt_tokens"] + value["completion_tokens"]:
        return None
    return dict(zip(("input_tokens", "output_tokens", "total_tokens"), (value[key] for key in names), strict=True))
