# Retrieval, recovery diagnostics, and combined lifecycle verification

This batch improves local retrieval and desktop recovery diagnostics and adds three combined memory lifecycle acceptance cases. It does not complete first-release acceptance: the separately frozen [long-history experiment](deepseek-2026-10-25-long-history-stopped.md) exposed a sustained-learning failure that remains a release blocker.

## Implemented behavior

The lexical candidate pool now combines the existing bounded feature-hash route with a bounded exact-word full-text route. Migration 018 stores generated scope projections so filtering does not repeatedly extract those values from full assertion documents. Scope, principal, lifecycle, source validity, and context limits remain enforced. In the [local scale diagnostic](retrieval-scale-2026-10-25.md), 20 queries against the same 10,000-record fixture improved from 18 expected labels found to 20, with median measured recall time changing from 487.009 ms to 92.3195 ms. This diagnostic does not establish production latency, multilingual semantic retrieval quality, or exhaustive search.

The desktop CLI adds `desktop turn-status`, a bounded authenticated inspection of one conversation and branch. It does not start the daemon or invoke a model. Chat failure notices preserve the original fixed error category and report persisted local pending identity or validated remote active-scope identity rather than inventing a turn ID from an error. Authentication, transport, and diagnostic failures remain distinguishable from an empty scope. The [troubleshooting guide](../architecture/integration-troubleshooting.md) documents the supported recovery path.

Three database-backed acceptance cases combine existing mechanisms: synthetic multi-year decay, cue recovery, trusted feedback, and restart; complete schema dependencies and later counterevidence; and deletion that continues to suppress dependent memory after restoring an older archive. They validate program behavior under controlled clocks, not human forgetting rates or actual years of operation.

The bilingual setup instructions now distinguish a fixed host-reply demo from optional paid server cognition/embeddings, explain the managed desktop embedding configuration limitation, and give tested same-version source-URL upgrade commands for both the installed tool and a separate host Python environment.

## Verification

The integrated run completed **3,739 Python tests in 356.61 seconds**, including Docker desktop flows and chat executed from the installed wheel. The TypeScript SDK completed **36 tests and strict type checking**. Global Ruff and whitespace checks passed. An earlier lint invocation included a nonexistent `scripts` directory; rerunning the repository-wide command completed successfully.

The wheel built and installed successfully. All 100 packaged runtime, contract, and migration members matched the corresponding source bytes, including all 18 migrations. Its SHA-256 at that check was `7042b8444bfaa2667a5f0b114f9db462f373edcc6b95b3f23527bde925effa53`; subsequent documentation edits are not claimed to be represented by that artifact hash.

The focused lexical groups passed 92 existing related tests and seven new regression cases. The three combined acceptance cases also passed independently before the integrated suite. The raw stopped-evaluation archive passed byte-exact restoration and confidential-value checks. No routine CI workflow was added or triggered.

A subsequent evaluator-only safeguard change passed **57 offline tests in 1.54 seconds**, plus scoped Ruff and whitespace checks. It stops on an explicitly unsuccessful learning window or publication-restricted learning result, while preserving valid partial processing and the higher priority of unknown usage, runtime failure, and provider/budget errors. An output-root `STOP` marker takes effect at safe boundaries, preserving completed work and closing transports. An existing evidence directory remains non-resumable for paid work. These changes were made after the 3,739-test run and are covered by their separate focused result, not retrospectively attributed to that full-suite count. No database or provider call was used for these safeguard tests.

## Outstanding release work

The real-model experiment stopped after 11 exchanges and 38 settled calls. All 11 current-input answers were faithful, but eight consecutive background learning windows were blocked by incomplete optional history. Two stored episode descriptions also turned uncertain persistence status into negative assertions. Current-input answer quality must not be confused with successful memory formation.

The [local observation and historical integration design](../architecture/local-observation-consolidation.md) describes the next implementation boundary: complete local attributed encoding, durable autonomous historical integration, finite shared spending, truthful phase monitoring, and versioned episode fidelity. At the time of this report that design is proposed, not implemented. A new independently frozen real-model evaluation is required after its deterministic and database lifecycle checks pass; the stopped experiment remains unchanged.
