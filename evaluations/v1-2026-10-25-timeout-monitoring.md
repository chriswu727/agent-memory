# Timeout monitoring repair: local verification

The [second finite real-memory attempt](v1-2026-10-25-acceptance-attempt-2.md) stopped after twenty-three exchanges when a historical comparison received no response body before its timeout. Provider usage remained unknown. That campaign remains failed, its original database and evidence are preserved, and its remaining history and twelve probes were not executed. These offline repairs do not pass G4 or authorize another paid campaign.

## Confirmed defects and repair

The failed run retained three admitted comparison objectives, but monitoring reported zero unassessed objectives because no coverage result existed. Monitoring now validates the retained selected and admitted manifests against their frozen input fingerprint and history cutoff, and counts pending work before a model result exists. Missing or malformed manifests make the count explicitly incomplete. Exact job status and aggregate monitoring agree. An explicit recovery transfers the same obligation without double counting; continuation preserves the failed selected prefix and transfers only the untouched remainder.

The expired running job was also shown as runnable despite its unsettled provider reservation. A scoped read of retained reservations now reports such jobs as blocked with `provider_uncertain` under their frozen `no_unsettled_retry-v1` policy. An active lease remains running; legacy policies retain their existing behavior. Monitoring does not settle, reclaim, retry or publish anything. The existing execution fence remains responsible for preventing another paid attempt.

Settlement errors retain their public `provider_uncertain` code. When the actual exception is specifically a lease conflict, the message includes that fixed diagnostic. Other exceptions retain the generic message; exception text and payloads are never exposed. The real campaign did not retain the original settlement exception or its timestamp, so this diagnostic does not establish the cause of that historical failure. Provider timeouts, lease duration, authorization and retry policy are unchanged.

## Verification

Seven new timeout tests include four real PostgreSQL lifecycle cases spanning orientation/consolidation and proposal/verification. Deterministic callbacks force lease expiry before returning a timeout. Settlement and cleanup reject the stale lease; reservations stay charged, no dependent result is published, and reclaim cannot call the provider again. Three public API cases verify diagnostic redaction. The timeout, settlement and usage group passed **45 tests in 6.50 seconds**.

Forty-eight new monitor tests include sixteen real PostgreSQL cases. They cover pending objectives, incomplete metadata, active and expired leases, unchanged legacy policies, read-only monitoring, recovery and continuation accounting. The focused monitor, progress, phase, replanning and settlement group passed **263 tests in 21.45 seconds**. These groups overlap and are not additive unique-test counts.

The integrated source run reported **4,451 passed, two failed, 122 errors and four skipped in 371.28 seconds**. One failure and all 122 setup errors came from the test launcher's omitted migration connection while dotenv loading was disabled. Supplying the dedicated development database's migration credentials resolved those **123 exact affected checks, which passed in 25.52 seconds**. No production change was made for that environment error.

The other failure was an intermittent test assumption that every admitted historical objective must fit a single funded package. The contract instead bounds complete source and assertion closures. The test now requires the original obligation to remain unchanged, selected and remaining objectives to form an exact disjoint partition, and any remainder to exist as a linked zero-call deferred job. Paid-failure non-retry, source/authority binding and delivered-context checks remain. A deterministic case packages the same real three-objective admission both as one selected plus two remaining and as three selected with none remaining, without changing state or spending. All **four continuous-learning acceptance tests passed in 15.90 seconds**. This was a test-only repair; no production code changed after the integrated run. The original failed result is retained rather than described as an entirely green full-suite run.

## Artifact and release boundary

The runtime build is `80bc3130b2f73cd479c2f650dfb177693ab92f3b78430546920679cb9eb95a4c`. The [artifact record](v1-2026-10-25-timeout-monitoring-artifacts.json) verifies identical source, source-distribution, wheel and isolated-installation bytes for all **113 runtime/schema/migration files**. All **22 migrations** match the preceding verified artifact.

From that isolated installed wheel, running Python with `-I` from `/tmp`, **59 tests passed in 56.55 seconds**. These cover the new monitoring and timeout regressions and four real Docker flows: desktop/MCP/restart, direct chat with model change and durable replay, upgrade/downgrade, and backup/fresh-install recovery. The run emitted one upstream Starlette test-client deprecation warning about `httpx`; no check failed or skipped.

Ruff and diff checks passed. All 114 relative documentation links in the changed Markdown files resolve, configured provider credentials are absent from changed files, and all fourteen published hashes of the second campaign's private evidence still match.

The source test logs and machine-readable results are retained under `.local/v1-timeout-monitoring/`. Database tests use the dedicated development database; the failed acceptance databases are not used for repairs. Test providers are deterministic/local callbacks. No paid model call, new spending allowance or routine CI run was introduced by this repair.

Earlier [retrieval and relation-capability measurements](v1-2026-10-25-local-acceptance.md#local-overhead) remain evidence for their recorded revisions; those algorithms and the TypeScript SDK surface are unchanged and were not remeasured. The [frozen acceptance criteria](../architecture/v1-acceptance.md) still require completed real-memory history and isolated probes. The permitted full follow-up is exhausted. First-release acceptance remains **not passed**.
