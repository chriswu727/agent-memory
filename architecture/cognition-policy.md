# Cognitive policy configuration

Situation understanding and background learning use a bounded proposal followed, when needed, by independent verification. The wall-time limit covers the whole batch; the output-token limit applies to each request. These limits are separate from the model that writes replies, the runtime context budget, and the brain's cumulative cognitive budget.

## Explicit reasoning preset

Select the preset during desktop setup:

```sh
agent-memory desktop setup --cognition-preset reasoning
```

Or change an existing installation:

```sh
agent-memory desktop configure-cognition-policy --preset reasoning
agent-memory desktop restart
agent-memory desktop cognition-policy
```

The preset allows 240,000 milliseconds per batch, 16,384 output tokens per request, and 65,536 decoded response bytes. It does not enable provider thinking mode, select a model, enable cognition without provider credentials, change the default `always` orientation policy, or increase the maximum two model calls per batch. Setup and configuration do not probe the provider or make paid requests.

Selecting the preset is explicit. Omitting it during setup preserves the existing saved policy, or the unchanged defaults of 30,000 milliseconds, 8,192 output tokens, and 32,768 decoded bytes. It is not inferred from a provider or model name. `configure-cognition-policy` also accepts all three manual limits together; they cannot be combined with `--preset`.

Inspection returns machine-readable effective values:

```json
{
  "policy": {
    "max_wall_time_ms": 240000,
    "max_output_tokens": 16384,
    "max_response_bytes": 65536
  },
  "scope": "semantic_orientation_and_learning"
}
```

The values are saved in a strict private `cognition-policy.json`, independently of version 1 provider/profile settings. No credential is included in inspection output. A changed policy requires restarting an existing service; configuration reports `restart_required_if_running`, and desktop status detects the configuration mismatch. Setup uses its existing start/restart behavior unless `--no-start` is supplied.

For a separately deployed API, the equivalent environment variables are `MEMORY_COGNITION_MAX_WALL_TIME_MS=240000`, `MEMORY_COGNITION_MAX_OUTPUT_TOKENS=16384`, and `MEMORY_COGNITION_MAX_RESPONSE_BYTES=65536`. API hosts must coordinate their HTTP response deadline with the service policy. Desktop runtime connections allow a 315-second read wait, five-second connection/pool waits, and a 30-second write wait. The general Python SDK still defaults to 30 seconds. The maximum supported batch wall time is 300 seconds, including for frozen older jobs after the current configuration is lowered.

## Recorded evidence and limits

In the October 25, 2026 DeepSeek-V4-Pro `always` smoke run, the first two completed stages spent 60.03 and 31.46 seconds on foreground proposal plus verification. The first completed background batch spent 87.83 seconds in those model calls. A later completed background proposal reported 10,662 completion tokens. These observed calls exceed the default time or output-token allowance and motivate an explicit reasoning preset. They do not establish latency guarantees for other models or inputs.

The first stage took 81.79 seconds in the foreground, including 81.11 seconds of recorded provider waiting. This experiment also enabled an optional 15.03-second answer review, which ordinary desktop chat does not enable. The remaining time includes local database, HTTP, and context work; it is not a separately measured retrieval latency. Background processing took another 88.18 seconds and is distinct from delivery latency. Increasing a timeout permits longer computation; it does not speed up recall.

The preset raises allowances, not guaranteed spending or quality. The transport still separately bounds the complete HTTP response envelope at 65,536 bytes, including provider reasoning and JSON overhead; a 65,536-byte decoded-content allowance does not guarantee that such content fits the envelope. Missing usage remains unknown. No monetary cap is inferred from these limits.

`on_demand` remains an explicit runtime option for ordinary conversation and recall. Eligible turns can skip foreground situation processing, while background learning remains active. Skipping does not derive the immediate goals, pending tasks, or situation state that `always` attempts to derive, so the policies are not equivalent.

## Visible degradation and recovery

A delivered reply does not establish successful situation understanding or long-term learning. Interactive desktop chat reads one background progress summary at startup and reports blocked or incomplete coverage on stderr. Scripted single-message invocations skip this aggregate. After a completed turn, both modes read only that turn and its exact orientation job, warning if processing was unavailable, unevaluated, invalid, or budget-limited. They do not scan history after every reply. Monitoring uses separate two-second HTTP timeouts, makes no model calls, and does not change a successful reply into a failed operation if diagnostics are unavailable. A normal empty or conservatively rejected proposal is not reported as a transport failure or as proof of learning.

New snapshots freeze the full effective semantic policy. Changing configuration does not rewrite a started snapshot, reset its call reservations, or automatically retry a blocked window. Legacy snapshots without a policy field retain their previous current-service behavior; already reserved request identities and cumulative limits remain enforced.

After fixing the provider or policy, inspect the blocked interval and explicitly authorize bounded recovery:

```sh
agent-memory desktop learning-status
agent-memory desktop learning-status --job JOB_ID
agent-memory desktop retry-learning JOB_ID --expected-fence FENCE --request-id recover-1
```

Use the exact job ID and fence from monitoring. Recovery creates a child over the original unprocessed interval. Its new snapshot uses the restarted service policy, with at most two calls and 196,608 input bytes per batch under the brain's cumulative budget. The parent's frozen policy, usage, and uncertain outcomes remain recorded. Repeating the same request ID and fence returns the same child; it does not authorize another paid attempt. Successful finite continuations may require further batches within that original interval. A subsequent failed child requires a new explicit recovery request.

This mechanism does not replay an old foreground situation analysis or retry a successfully processed semantic rejection. Queuing recovery is not proof of learning; inspect the child's processing and publication summaries.

The deterministic acceptance in `tests/test_cognition_policy_recovery.py` uses the real PostgreSQL store, public HTTP APIs, public configuration command, reconstructed `configured_service`, and a fake provider HTTP transport. It checks default-timeout failure, visible blocked progress, explicit preset persistence, service reconstruction, bounded recovery, publication, and unchanged parent accounting. It neither makes paid calls nor restarts an operating-system service process.
