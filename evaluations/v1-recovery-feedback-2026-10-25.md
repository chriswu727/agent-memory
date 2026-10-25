# Recovery, feedback, and operating controls: 2026-10-25

This batch connects ordinary interaction feedback, complete desktop recovery, and explicit cognitive operating controls. It follows the [foundation batch](v1-foundations-2026-10-25.md). Sustained-history quality and final first-release acceptance remain open in the [release plan](../architecture/v1-release-plan.md).

## Delivered behavior

Prepared turns bind the exact server-created memory packet and rendered context to a durable selection record. Later preparations cannot replace that selection. A custom host context explicitly records that binding is unavailable; it cannot accidentally inherit a normal packet's credit. After actual delivery, a narrowly attributable external confirmation or correction can update the contribution ledger automatically. Ambiguous answers, imported acknowledgements, unsupported paraphrases, and literal tail sources without an assertion remain trace-only. Event sequence and immutable turn state establish causality without assuming identical application and database clocks. Corrections and erasure invalidate contributions, and negative outcomes cannot increase activation.

The version 4 consolidation planner prefers distinct complete source pairs within its existing strong/weak slots. It keeps version 3 objective identities, so changing candidate order does not authorize another paid assessment of an unchanged pair. Frozen older plans retain their behavior. Deterministic database tests cover publication and repeated fragmented source pairs; those tests do not establish a real-model generalization improvement.

Desktop backup streams a complete PostgreSQL logical snapshot into a private registered bundle. An independent SQLite deletion authority durably records resolved targets before deletion acknowledgement. Startup and brain access reconcile pending intents. Restore creates a new installation and credentials, replays newer deletions, preserves known delivery and usage records, fences old permissions, and quarantines pending work. Every restored job has a replay guard, preventing a later deletion from making old learning work runnable again. Explicit activation compares the current authority head, retires the old installation, and requires external adapters to acknowledge the recovery epoch. Python and TypeScript clients expose recovery metadata without silently adopting an epoch.

The [recovery guide](../architecture/desktop-recovery.md) states the limits: plaintext private backups, a separately current deletion authority, no automatic replay of external messages, and no exactly-once guarantee across rollback. A lost authority or unreconciled host history cannot be repaired by treating an old snapshot as current.

The [cognitive policy](../architecture/cognition-policy.md) supports explicit per-batch limits and a `reasoning` preset. New job snapshots retain their effective policy across restart. Changing limits does not rewrite old snapshots or retry uncertain work. Desktop chat reports degraded processing through bounded read-only diagnostics; unavailable or broken diagnostic output cannot change an already delivered answer into failure.

The [cumulative budget API](../architecture/cognitive-budgets.md), both SDKs, and desktop commands expose revisioned policy changes. Exact retries return their historical receipt without undoing newer settings. Changes preserve reservations and per-job limits. Cognitive usage covers both semantic work and embeddings; a valid embedding response may report zero output tokens, while missing or inconsistent usage stays unknown.

## Verification record

Recovery tests use real subprocess streams and isolated PostgreSQL installations. The full Docker path imports a database containing 5,007 events with a dump above 16 MiB, preserves retained sources, applies post-checkpoint erasure, rejects old credentials and missing epochs, and resumes interruptions after import, quarantine, and authority activation. The same path passed from a wheel installed outside the repository. Quarantine regressions cover generation, drafts, sending, confirmed delivery awaiting commit, every job phase, existing operation reservations, and activation idempotency.

Development tests exposed two consequential bugs that were repaired before acceptance: rejecting a valid prepared binding when application and database clocks disagreed, and automatically re-enqueuing a quarantined learning window after deletion replay. Additional review repaired activation replay that could otherwise transfer authority backward, resumed publication of an already registered backup after newer deletion, safe FIFO rejection, and best-effort stderr handling. Failing regressions were rerun after their fixes; prior paid experiment verdicts remain unchanged.

The frozen integrated run passed **3,673 Python tests in 298.68 seconds**, including Docker and installed-wheel chat checks. The TypeScript SDK passed **36 tests** and strict type checking. Ruff across runtime, tests, and examples, `git diff --check`, distribution builds, and the isolated wheel installation passed. Distribution inspection confirmed all 93 expected runtime/schema/migration files and all 17 migrations matched the source. No routine CI, Codex subscription tests, or Claude subscription tests are part of this batch.

```sh
uv build
uv pip install --python .local/standalone-chat-wheel/bin/python --no-deps --reinstall dist/agent_memory_runtime-0.1.0-py3-none-any.whl
MEMORY_DESKTOP_E2E=1 MEMORY_CHAT_E2E_PYTHON=/Users/yichen/projects/agent-memory/.local/standalone-chat-wheel/bin/python .venv/bin/pytest -q
.venv/bin/ruff check src tests examples
# In sdk/typescript:
npm test
npm run typecheck
```

The separate [DeepSeek smoke](deepseek-2026-10-25-lifecycle-smoke.md) made 24 calls with 172,905 provider-reported tokens. Five delivered answers matched their short source-grounded scenarios. The report retains one unsupported episode detail and substantial foreground model latency. The newer evaluation path uses production `configured_service` with transport-only metering, freezes provider identity and cognitive policy, and records explicit public budget authorization before long runs. Its deterministic API checks do not substitute for the pending sustained live experiment.

A subsequent evaluator audit found that successful restore counters alone could miss a dropped cognitive view or belief relation. The long-run harness now downloads the frozen archive and compares a fresh probe export's complete recall graph before any paid probe. Tests deliberately drop a relation, episode, or association while leaving the old success counters unchanged. After these evaluator-only changes, all 136 affected evaluation tests passed in 7.96 seconds. An additional zero-call preflight against the actual smoke database preserved all eight sources, ten assertions, two episodes, one supersession relation, and twelve event dependencies, with the source graph unchanged. Working state and execution/access caches are explicitly excluded from graph equivalence; this is probe isolation, not complete installation recovery.
