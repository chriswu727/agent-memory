# Cross-host conversation and delivery recovery acceptance

Date: 2026-10-25

This change separates three facts: which turn owns a conversation branch, which worker currently holds its execution lease, and whether the host confirmed sending a response. It fixes overlapping turns across hosts and loss of delivery evidence when memory changes during an external send.

## Implemented behavior

A durable reservation permits one unfinished turn per brain, conversation, and branch. A second new message is rejected with `scope_conflict` before input capture, model generation, or delivery. Releasing or expiring the execution lease retains the reservation. Finishing or explicitly failing the owning turn releases it. Completed message replay remains available while a newer turn owns the scope; other conversation branches can proceed independently.

Migration 012 keeps existing unfinished turns and assigns each scope's oldest turn its reservation. Later legacy turns remain queued for ordered recovery. Their previous execution tokens cannot mutate an unreserved turn. A unique partial index prevents duplicate reservations, and a separate partial index supports unfinished-scope lookup without scanning terminal history. Migration fails for a role that cannot see all protected rows; it does not silently migrate only the visible subset.

The authenticated `GET /v1/brains/{brain_id}/runtime/scope` endpoint requires exact `conversation_id` and `branch_id` parameters. It returns the reserved turn, the oldest queued legacy turn if no reservation remains, or `{"turn": null}`. It does not claim a turn and is independent of the recent 200-turn list. Python exposes `RuntimeClient.active_scope`; TypeScript exposes `MemoryClient.activeScope`. Monitoring distinguishes `reserved_scope_count` from active worker leases.

Migration 013 adds a bounded delivery receipt. Once the host confirms sending, the server still checks the worker token, revision, and scope ownership, then records the actual text's SHA-256 digest and byte count, the sending authorization, disposition, and fixed failure reasons. Monitoring never includes the response body or lease credentials in this receipt.

If unrelated context, working state, or the situation proposal changes during sending, valid actual output can be retained while the turn closes as failed. Stale situation updates are not applied to newer working state. If any bound source is unavailable, only receipt metadata is stored: erased or superseded text is not appended again. A later invalidation between receipt storage and `finish` also closes the uncommitted turn with its receipt intact. `stage=failed` and `delivery_receipt.state=delivered` intentionally describe different outcomes.

Saved receipt replay requires the same text and delivery state. Recovery never calls the delivery callback again for a recorded send. A local session clears its pending marker only after re-reading and validating a matching terminal receipt. A new pending marker can also be removed after a structured scope rejection and a fresh remote lookup proving that no such turn was created. Ambiguous transport failures preserve pending recovery; cancellation of a confirmed never-started local turn returns `not_started` rather than inventing a remote failure record.

Legacy delivered turns without a receipt can also reach the authoritative `finish` operation after their sources are erased. The server closes them as failed and releases the reservation without restoring text. Since those older records lack a verifiable receipt, the local pending marker requires explicit cancellation after that failed resume. Integration tests verify that this cancellation needs no erased source text and that the next message can proceed.

## Verification

The verification uses real PostgreSQL, HTTP clients, isolated migration schemas, deterministic model and delivery callbacks, and local desktop processes. It makes no paid model requests and does not consume Codex or Claude subscription usage for tests.

The first full run had 3,142 passes and one failure: an existing orientation invalidation regression expected an HTTP conflict instead of the new terminal `RuntimeBlocked("context_stale")` result. Its original assertion that stale working state must not be committed remains unchanged. The test now also requires a valid delivered receipt, released scope, and recovery without another send. The focused receipt, API, session, and orientation group then passed all 95 tests.

- Final complete Python regression suite with desktop end-to-end tests enabled: 3,143 passed, zero skipped, in 189.26 seconds. This includes the standalone chat flow using the installed wheel outside the source checkout.
- Host/session regression group, including legacy delivered recovery: 153 passed.
- Backend receipt, turn, learning, and orientation routing group: 101 passed.
- TypeScript SDK: 24 passed; strict type checking passed.
- Ruff, whitespace checks, architecture JSON parsing, and local documentation links: passed.
- Source distribution and wheel build: passed. The installed wheel matches the current runtime modules and contains both new migrations; `.env` and `.local` files are absent from the wheel.
- No CI workflow was added or triggered.

Covered cases include concurrent host requests with no rejected-message side effects; lease expiry and release; scope isolation and completed replay; deterministic migration and restricted-role rollback; scope lookup beyond 205 newer terminal records; malformed and nested private receipt metadata; post-send source correction and erasure; working-state and orientation invalidation; receipt replay after erasure; stale worker rejection; and recovery through local session markers.

## Limits

All API server processes sharing the database must be upgraded and restarted. Running older servers alongside this implementation is unsupported because they do not enforce durable scope ownership. Operational reservations and receipts are not part of portable memory archives.

An abnormal pre-existing `committed` row still requires manual recovery and retains its scope reservation. The normal atomic `finish` transaction cannot leave that intermediate state persisted.

This does not establish exactly-once external delivery. A process crash, lost lease, or uncertain network result between the external send and receipt persistence still needs host reconciliation using stable delivery identities. The runtime cannot retract output already accepted by an external channel. Separate conversations can run concurrently, but brain-wide context invalidation can still require a new attempt before sending or prevent memory-state commit after sending.

These results concern storage, exclusion, and recovery. They do not measure semantic recall quality, the biological accuracy of forgetting policies, or emotional continuity. Long-term quality evaluation, evidence-bound memory strengthening, and finer learning-progress classifications remain separate work.
