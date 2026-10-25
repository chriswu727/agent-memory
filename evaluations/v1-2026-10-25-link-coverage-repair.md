# Historical link coverage repair: local verification

The [first finite real-memory attempt](v1-2026-10-25-acceptance-attempt-1.md) correctly stopped on contradictory relation bookkeeping. The [versioned repair](../architecture/history-link-coverage.md) derives proposed coverage from exact relation endpoints and requires explicit abstentions for uncovered objectives. Earlier contracts retain their original requests and strict validation. This is a verified protocol repair, not a successful real-memory acceptance.

The runtime build identity is `7e40bd17ef4cdc6c894d620ea244788c34f27874ab4422072a212d169b537b13`. The [artifact record](v1-2026-10-25-link-coverage-artifact-verification.json) confirms that source, source distribution, wheel and isolated installation match all 113 runtime/resource/schema/migration files. The twenty-two migrations are unchanged.

## Verification

The exact [recorded failed request and response](../tests/fixtures/recorded_history_link_incidence_failure.json) replay with the original messages, schema, request identity, charged input bytes and rejection unchanged. A separately constructed deterministic response using the new format reaches independent semantic verification and full publication checks. An adverse verification verdict still publishes no dependent claim or relation. New contradictory or incomplete responses fail after one call without retry.

The focused protocol, recorded-pipeline, capability and speech-source tests passed **119 tests**. The focused database learning, lifecycle evaluator and source-view/fidelity group passed **112 tests**. Both old and new accounting formats retain their required outcomes; test providers select their output using the actual requested schema.

The full source regression reported **4,516 passed, one failed and four skipped in 338.89 seconds**. The failure was the unchanged fake response in `test_rebound_history_resumes_with_exact_raw_snapshot_and_separate_authority_fence[None]`: its newly versioned snapshot requested abstentions while the fake provider returned legacy decisions. Strict response rejection was expected for that mismatch. The fixture now selects the requested wire format and separately exercises both retained legacy and current snapshots. All source hashes, dependency/scope fences, one-call no-change assessment and zero-publication assertions remain. The complete package-selection follow-up passed **35 tests in 7.91 seconds**. No production code changed after the full regression. The original failed run is retained and is not described as an entirely green full-suite run.

From the isolated installed wheel, running Python with `-I` from `/tmp`, **56 tests passed in 48.17 seconds**. These include four real Docker flows—desktop/MCP/restart, direct chat with model change and durable replay, upgrade/downgrade, and streamed backup/fresh-install recovery—plus the recorded protocol and speech-frame regressions. Docker flows use deterministic/local model callbacks and make no paid provider calls. Ruff and diff checks passed; changed documentation links and credentials were checked.

The previous TypeScript checks and [local performance evidence](v1-2026-10-25-local-acceptance.md#local-overhead) remain evidence for their recorded revisions. This repair changes neither the SDK surface nor the measured retrieval/projection algorithm; unchanged checks were not rerun or relabeled as new measurements. The new history schema and instructions count toward actual request budgets, and the accounting calculation adds no model call.

## Remaining gate

No paid calls were made during this repair. The first attempt remains a 12-exchange failure with no probes. One separately frozen full follow-up remains permitted under the [finite acceptance criteria](../architecture/v1-acceptance.md); it must use a fresh ledger and database, preserve the same checkpoint and stop rules, and receive complete independent source-and-answer review. Local verification does not pass G4 or mark the release mature.
