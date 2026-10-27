from dataclasses import asdict, replace
from pathlib import Path
from typing import get_args

from .chat_provider import ReasoningEffort
from .cognition_config import (
    CognitionConfigurationError,
    CognitionPolicyConfiguration,
    cognition_policy_preset,
    load_cognition_policy,
    save_cognition_policy,
)


def add_commands(actions):
    inspect = actions.add_parser("cognition-policy", help="Inspect saved cognition limits and provider options")
    inspect.add_argument("--directory", type=Path, default=None)
    change = actions.add_parser(
        "configure-cognition-policy", help="Save cognition limits for the next service restart",
        description="Save cognition limits for new jobs after a service restart. Desktop connections allow a "
        "615-second response wait to cover frozen jobs with a 600-second policy. Other hosts must configure "
        "their memory API response timeout to cover the server policy; the Python SDK default remains 30 seconds.",
    )
    change.add_argument("--directory", type=Path, default=None)
    change.add_argument("--preset", choices=("reasoning",),
                        help="Explicitly select 240000 ms, 16384 output tokens and 65536 decoded bytes")
    change.add_argument("--max-wall-time-ms", type=int,
                        help="Total proposal and verification time per batch, from 1 to 600000 milliseconds")
    change.add_argument("--max-output-tokens", type=int,
                        help="Output token cap per model request, from 1 to 32768")
    change.add_argument("--max-response-bytes", type=int,
                        help="Final cognitive content cap, from 1 to 65536 UTF-8 bytes")
    change.add_argument("--reasoning-effort", choices=(*get_args(ReasoningEffort), "default"),
                        help="Explicit provider effort override; default removes the override")
    change.add_argument("--max-envelope-bytes", type=int,
                        help="Complete decoded HTTP response envelope cap, from 1 to 1048576 bytes")


def run(args):
    configuration = None
    changing = args.desktop_command == "configure-cognition-policy"
    overrides = {}
    if changing:
        limits = (args.max_wall_time_ms, args.max_output_tokens, args.max_response_bytes)
        if args.reasoning_effort is not None:
            overrides["reasoning_effort"] = None if args.reasoning_effort == "default" else args.reasoning_effort
        if args.max_envelope_bytes is not None:
            overrides["max_envelope_bytes"] = args.max_envelope_bytes
        if args.preset is not None:
            if any(value is not None for value in limits):
                raise ValueError("--preset cannot be combined with explicit cognition limits.")
            configuration = cognition_policy_preset(args.preset)
        elif any(value is not None for value in limits):
            if any(value is None for value in limits):
                raise ValueError("Choose --preset or provide all three cognition limits.")
            configuration = CognitionPolicyConfiguration(
                max_wall_time_ms=args.max_wall_time_ms, max_output_tokens=args.max_output_tokens,
                max_response_bytes=args.max_response_bytes,
            )
        elif not overrides:
            raise ValueError("Choose --preset, provide all three cognition limits, or set a provider option.")
    try:
        current = load_cognition_policy(args.directory)
    except CognitionConfigurationError as error:
        if configuration is None or error.code != "cognition_policy_invalid":
            raise
        current = None
    if changing:
        if configuration is not None and current is not None:
            configuration = replace(configuration, reasoning_effort=current.reasoning_effort,
                                    max_envelope_bytes=current.max_envelope_bytes)
        configuration = replace(configuration or current, **overrides)
    changed = changing and configuration != current
    if changing:
        current = save_cognition_policy(configuration, args.directory)
    result = {"policy": asdict(current), "scope": "semantic_orientation_and_learning"}
    if changing:
        result["restart_required_if_running"] = changed
    return result
