# First-release local acceptance checkpoint

This checkpoint covers the bounded capability, comparison-attention, private funding and conditional-speech repairs. It does not pass the [complete release gates](../architecture/v1-acceptance.md) or the real-memory campaign.

The final runtime build identity is `0c770b1de1ee3c0cfc4ed8e3c3f7a1869f348ebf9d5046e81b79b8fd9c92d520`. The source archive, wheel and isolated installed package match all 113 runtime, contract, resource and migration files, including twenty-two migrations. The [artifact verification](v1-2026-10-25-artifact-verification.json) records the distribution and per-file hashes. This identity covers shipped runtime bytes; it does not identify the evaluator or every development test.

## Installed package

The final installed-artifact run passed 30 tests in 45.98 seconds: four real Docker flows and twenty-six speech-frame regressions, running pytest through the isolated wheel interpreter from an unrelated directory. The Docker flows cover desktop lifecycle and MCP, standalone text chat, upgrade/downgrade safety, and streamed backup/fresh-install recovery. The standalone flow executes `desktop setup`, changes the saved model between completed conversations, recalls the original source, and preserves completed-turn replay without another generation. The recovery flow retains the existing 5,007-event and greater-than-16-MiB archive checks, deletion authority, interruption, quarantine and explicit activation. The upgrade flow constructs two package copies from the candidate runtime and adds a synthetic migration to the second.

An earlier four-flow check passed in 50.11 seconds with one Starlette deprecation warning recommending `httpx2` for its test client. The final test environment includes that existing development dependency and reported no warning. These flows use deterministic callbacks or a local HTTP model stub and make no paid model calls. macOS is the verified desktop target; these are not Windows/Linux certification or months of endurance evidence.

## Local overhead

The measurements used an Apple M4, macOS 15.7.4 arm64 and Python 3.12.13. No samples were discarded.

| Gate | Result | Budget |
| --- | --- | --- |
| 10,000-record lexical fixture, twenty fixed cues | 20/20 expected source labels; median 91.564 ms; p95 178.788 ms | 20/20; p95 ≤ 500 ms |
| Thirty-two complete eligible relation endpoints, five calls | Median 24.091 ms; 496 eligible pairs; 13,400 bytes of capability metadata | Median < 100 ms |

The lexical measurement reused the preserved synthetic fixture and exact twenty queries from [the earlier retrieval diagnostic](retrieval-scale-2026-10-25.md). Database/OS caches were not flushed; there were no additional warmups. All current samples, including the first, are in [the retrieval evidence](v1-2026-10-25-retrieval-performance.json). Timing encloses the in-process public recall action, not provider waiting, HTTP transport or answer generation. A matching unique label does not establish semantic recall accuracy.

The [capability evidence](v1-2026-10-25-capability-performance.json) records all five consecutive offline samples. Each endpoint had thirty-one structurally eligible partners. The complete metadata remains inside existing request accounting; this operation makes no model calls. These are finite development regression budgets, not a production SLA.

Both performance artifacts identify the immediately preceding build, `7336fbd4a2b087cbe73e2e7d572b7fee76ea05e2aea734b742248c552f195274`. The only subsequent runtime change adds a rejection guard for assistant conditional speech containing other fictional/quoted lexical markers. It does not change retrieval or the all-user-source capability fixture's executed path. These unchanged measurements are retained rather than relabeled as a new run.

## Integrated regression

The full source suite ran once: **4,450 passed, one failed and four skipped**, in 341.90 seconds. The four opt-in Docker tests were separately executed from the final installed wheel above. The failure was `test_evaluator_drains_actual_delivered_source_phases_and_retains_unfunded_history`: its old four-target fixture expected one deferred comparison, while the new policy admits only three and reports the fourth as not selected, so actual debt was zero. This failure is preserved; it is not reported as a green full-suite run.

The follow-up initially reported 40 passed and two failed: the seed helper directly published four semantic targets, then redundant extraction doubled the candidate population to eight; the deterministic fake provider also assumed metadata absent from a literal candidate. The final fixture explicitly settles extraction only for those already-published historical seeds, without deleting sources or changing production selection. Actual current-turn capture, delivery and all three current literal-extraction jobs still execute.

The final targeted evaluator/phase run passed **42 tests in 3.51 seconds**. Its parameterized database test preserves the legacy three-assessed/one-unpaid-deferred path and checks that the latest path admits and assesses three, leaves the fourth explicitly not selected, and never counts it as assessed or debt. Both verify four settled calls, two per phase, exact source delivery, publication, authority snapshots and index completion. No runtime code changed after the full run; only this fixture and its assertions changed, so unchanged passing checks were not rerun.

The TypeScript SDK's 37 tests and strict type checks passed. Ruff and diff checks passed. The dedicated acceptance-profile offline group passed 233 tests with four database tests deliberately excluded from that focused run; the database evaluator paths are covered by the integrated/follow-up results above. No paid model calls were made during this repair and progress audit, and no routine CI was triggered.

## Remaining acceptance

The real-memory gate remains unpassed: no new 100-exchange/12-probe campaign has been run for this candidate. The dedicated acceptance profile passed its offline tests and its `--plan` invocation; it does not construct paid reviewer or baseline providers. Earlier failed real-model results remain preserved. Independent source-and-answer review cannot be replaced by passing engineering tests or these timing measurements. This commit delivers the verified local repair and finite acceptance tooling, not a declaration that the first release is mature.
