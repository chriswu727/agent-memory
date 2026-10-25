# Background learning recovery acceptance

Date: 2026-10-24. Platform: macOS, Python 3.12, local Docker and PostgreSQL.

This change repairs a long-term processing gap: with the default two-call batch budget, one failed proposal can leave insufficient budget for a fresh proposal and verification. Later successful jobs do not prove that the earlier input was assessed. The source remains stored, but its blocked window needs visible, bounded recovery. No paid model or subscription-backed requests were made for this acceptance.

## Implementation

`learning_progress` reports all background input windows using only state, scope, range, and lineage metadata from the database. It does not load source text or model snapshots. The most recent 200-job list cannot hide an older gap. Valid continuation and recovery handoffs count the child rather than both parent and child. Earliest pending input sequence determines priority, including when a newly created recovery job contains older input. At most eight blocked examples are returned.

Missing, malformed, cyclic, shared, cross-scope, or overextended recovery links report incomplete coverage. A present JSON-null recovery pointer is invalid, matching the scheduler's refusal to claim a parent once that pointer exists. Provider configuration is independent of processing state. Runnable means scheduler-claimable, not guaranteed to fit the model budget. A processed window does not establish semantic correctness or publication of a belief.

An explicit retry uses the existing brain/job transaction locks, an expected fence, and a hashed stable request identity. It creates one fresh child over the original unprocessed range, with no copied model snapshot. Repeating that request returns the same child's current public state. A different authorization conflicts. The old parent cannot be reclaimed, even by a worker using a larger attempt limit.

Parent results, call counts, input reservations, and uncertain outcomes remain auditable. A final-attempt crash can be recovered only after its lease expires; the parent is then terminalized, reserved operations become uncertain, and its old worker cannot publish. An active lease or an ordinary automatically retryable job cannot be duplicated.

Each recovery batch has at most two calls and 196,608 input bytes, subject to the existing rolling brain budget. Successful continuations retain these per-batch caps. This authorizes processing the remaining original interval, not two total calls for the entire interval. New observations cannot expand recovery windows. A larger configured selection policy is reduced to the child's durable input cap when creating a fresh snapshot. Existing frozen snapshots remain unchanged.

Source invalidation retains the normal rebuilding path: a completed prefix may need reprocessing while its continuation owns the tail. An unprocessed prefix marker does not create an inverted range, and malformed source-recovery metadata cannot halt unrelated worker progress.

The REST API, Python runtime client, TypeScript client, and desktop commands expose diagnostics and explicit recovery. The endpoint queues work without calling the provider itself. Desktop monitoring does not implicitly start the service or worker. Lost write responses must be retried with the same identity and fence; neither SDK automatically retries the write. No database migration or external chat framework was added.

## Verified scenarios

- A proposal fails; a later input is processed and indexing catches up; the older semantic gap remains visible through the HTTP API.
- Explicit recovery closes that gap; submitting the authorization alone makes no model request and preserves the old operation ledger.
- Concurrent duplicate requests and replay after rebuilding the client/store return one child, without resetting its budget.
- Multiple recovery batches cover the original input range while later observations get separate jobs.
- Source edits and deletions are revalidated without reviving old versions.
- Global quota exhaustion blocks new reservations; a permissive processor policy cannot override recovery caps.
- A crash on the final attempt is recoverable after lease expiry, while a late worker cannot publish.
- More than 200 later jobs do not hide an older blocked range; malformed lineages and summaries do not report false completion or disclose source bodies.
- Authentication, ownership, stale fences, strict request validation, safe error messages, path encoding, and uncertain HTTP writes are exercised.

## Validation

- Full regression suite with desktop tests enabled: 3,020 passed, zero skipped, in 184.20 seconds. The final lineage edge-case fixes and four additional cases were then verified in a 74-test progress/API/HTTP regression run; all 74 passed.
- Backend recovery and existing job/window/monitoring regression group: 117 passed.
- Brain-wide progress tests: 56 passed, including five real PostgreSQL tests.
- Recovery API, HTTP/database lifecycle, and backend recovery group: 43 passed.
- Desktop learning command tests: 17 passed.
- TypeScript SDK: 21 passed; strict type checking passed.
- Ruff over `src`, `tests`, and `examples`, and whitespace checks: passed.
- Source distribution and final wheel build: passed. The final wheel was reinstalled outside the source checkout; both desktop end-to-end tests passed in 21.19 seconds. The standalone test also invokes the new learning-status command through the installed CLI and live local API without an additional provider call.
- README local links and architecture JSON: passed. No CI workflow was added or triggered.

## Limits and follow-up

These deterministic providers validate scheduling, storage, accounting, and recovery. They do not measure real-model memory quality, human forgetting, or emotional continuity. Monitoring scans narrow metadata across all window jobs; very large histories still need a scale benchmark. Persistent source storage and semantic processing remain different guarantees.

The architecture review identified separate work to address next: durable server-side conversation exclusion across hosts and delivery-receipt persistence during concurrent invalidation; evidence-bound feedback before automatic memory strengthening; and finer distinctions between quota waits, partially invalid semantic output, and genuinely unassessed input. This change does not claim to resolve those paths or implement personality and emotion.
