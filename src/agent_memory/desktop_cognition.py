from dataclasses import asdict
from pathlib import Path

from .cognition_config import (
    CognitionConfigurationError,
    CognitionPolicyConfiguration,
    cognition_policy_preset,
    load_cognition_policy,
    save_cognition_policy,
)


def add_commands(actions):
    inspect = actions.add_parser("cognition-policy", help="Inspect saved cognition timeout and response limits")
    inspect.add_argument("--directory", type=Path, default=None)
    change = actions.add_parser(
        "configure-cognition-policy", help="Save cognition limits for the next service restart",
        description="Save cognition limits for new jobs after a service restart. Desktop connections allow a "
        "315-second response wait to cover frozen jobs with a 300-second policy. Other hosts must configure "
        "their memory API response timeout to cover the server policy; the Python SDK default remains 30 seconds.",
    )
    change.add_argument("--directory", type=Path, default=None)
    change.add_argument("--preset", choices=("reasoning",),
                        help="Explicitly select 240000 ms, 16384 output tokens and 65536 decoded bytes")
    change.add_argument("--max-wall-time-ms", type=int,
                        help="Total proposal and verification time per batch, from 1 to 300000 milliseconds")
    change.add_argument("--max-output-tokens", type=int,
                        help="Output token cap per model request, from 1 to 32768")
    change.add_argument("--max-response-bytes", type=int,
                        help="Decoded cognitive response cap, from 1 to 65536 bytes")


def run(args):
    configuration = None
    if args.desktop_command == "configure-cognition-policy":
        limits = (args.max_wall_time_ms, args.max_output_tokens, args.max_response_bytes)
        if args.preset is not None:
            if any(value is not None for value in limits):
                raise ValueError("--preset cannot be combined with explicit cognition limits.")
            configuration = cognition_policy_preset(args.preset)
        else:
            if any(value is None for value in limits):
                raise ValueError("Choose --preset or provide all three cognition limits.")
            configuration = CognitionPolicyConfiguration(
                max_wall_time_ms=args.max_wall_time_ms, max_output_tokens=args.max_output_tokens,
                max_response_bytes=args.max_response_bytes,
            )
    try:
        current = load_cognition_policy(args.directory)
    except CognitionConfigurationError as error:
        if configuration is None or error.code != "cognition_policy_invalid":
            raise
        current = None
    changed = configuration is not None and configuration != current
    if configuration is not None:
        current = save_cognition_policy(configuration, args.directory)
    result = {"policy": asdict(current), "scope": "semantic_orientation_and_learning"}
    if configuration is not None:
        result["restart_required_if_running"] = changed
    return result
