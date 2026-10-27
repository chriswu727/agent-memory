# Text-agent integration and recovery

The desktop chat and Python runtime support synchronous text generation with a host-confirmed delivery receipt. Streaming, tool loops, automatic message edits, and generic client interception remain outside this adapter's supported mode. MCP alone does not supply missing lifecycle hooks.

## Inspect the unfinished turn

```sh
membrain desktop turn-status --conversation example --branch main
```

The default conversation is `desktop-chat`, matching `desktop chat`; the default branch is `main`. Supply the same `--directory` as the original installation when using a non-default location. This command connects only to an already-running local service. It performs one authenticated scope lookup, does not start the worker, and does not generate, deliver, resume, cancel, or retry anything.

The JSON output contains the requested scope and a minimal unfinished-turn summary:

```json
{
  "conversation_id": "example",
  "branch_id": "main",
  "turn": {
    "turn_id": "turn-0123456789abcdef0123456789abcdef01234567",
    "stage": "generated",
    "scope_reserved": true,
    "lease_active": false,
    "recovery_required": null
  }
}
```

`turn: null` means no unfinished turn was found at inspection time, not that all historical messages were delivered. A false `lease_active` does not free a reserved conversation: a saved draft or an uncertain external operation can still own it. `generated` means a draft exists, not that delivery happened. `generation_uncertain` or `delivery_uncertain` means the external outcome still requires reconciliation.

Inspection validates the brain, conversation, and branch returned by the service and emits only identifiers, fixed stages, lease/reservation booleans, and the fixed recovery category. It does not return message bodies, provider error text, lease credentials, or source IDs. Network waits use separate two-second HTTP timeouts. An unavailable or invalid result is reported as `turn_status_unavailable`; it is never interpreted as an empty scope.

## Reply-provider failures

Chat prints the original fixed error code on stderr. When the provider supplies an HTTP status, it also prints that number and fixed guidance: check the saved reply key for 401, provider permissions for 403, endpoint/model for 404, and rate or quota limits for 429. Other statuses receive a general configuration/availability suggestion. Response bodies and URLs are not printed.

A timeout or transport interruption may leave the provider outcome unknown. It does not prove that the remote model did no work. These diagnostics do not change the runtime's uncertainty classification or authorize another call.

If a local pending sidecar exists, the error also identifies its exact persisted turn ID. This is local recovery state, not proof that the remote request succeeded. If the sidecar cannot be safely read, the original error remains visible and no turn ID is guessed. A diagnostic failure cannot retry the operation.

Use `turn-status` before choosing a recovery action. `/resume` and `chat --resume` are actions: they can continue an eligible captured turn or deliver a saved draft. They are not read-only inspection commands. Keep the original message ID and exact input for a pre-capture retry. Unknown generation or delivery is not automatically repeated.

For deliberate cancellation, the local session supports `/cancel <exact-turn-id>` or `chat --cancel <exact-turn-id>`. Unknown external outcomes require `/cancel-uncertain <exact-turn-id>` or `--acknowledge-uncertain`; cancellation preserves that uncertainty and cannot undo a possible charge or send. Already confirmed delivery awaiting commit must be resumed. A changed model, instructions, or policy cannot silently replace a pending generation contract.

## Conversation already occupied

`scope_conflict` means the service rejected a new turn because an unfinished turn already occupies the same brain, conversation, and branch. The rejected candidate ID is not the occupying turn and may never have existed remotely.

On this error, desktop chat performs the same bounded read-only scope lookup and identifies the actual unfinished turn when available. If the scope changed or inspection failed, it says so instead of inventing a blocker. The original failed operation is not retried. A valid local pending sidecar, when present, is labeled separately.

Resolve an existing turn through its owning host. A draft created by a different adapter is not automatically adopted or cancelled by the desktop session. External hosts can use `RuntimeClient(memory).active_scope(conversation_id, branch_id)` to inspect the same state and reconcile their own durable message and delivery records.

## Learning is separate from delivery

Use `desktop learning-status` for background learning and `desktop learning-status --job JOB_ID` for an exact cognitive job. A completed answer, a ready service, and a queued recovery job are different from successful learning. See [cognitive policy and recovery](cognition-policy.md) for explicit reasoning limits and bounded recovery of a blocked learning interval.

The CLI diagnostics are covered by deterministic fake-transport tests for authentication failure, rate limiting, unknown outcomes, scope changes, wrong-scope responses, malformed metadata, and unavailable diagnostics. These tests make no paid calls and do not claim real provider availability.
