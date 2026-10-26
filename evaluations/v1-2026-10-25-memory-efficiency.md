# Memory processing, retrieval priority and a rejected default promotion

This batch improves local history planning, source-cue ranking and adjacent-event storage access. A lossless shared-source verification format also reduced measured model input, but its paired observations exposed a citation-fidelity concern. The shared format remains an explicit experimental input contract. Fresh jobs retain `citation-provenance-v1`; the observed savings are not a default product-wide improvement. Longitudinal acceptance remains unpassed.

## Local algorithm changes

Historical integration now reuses successful exact-document dependency expansions within one locked package selection. Three overlapping targets require three expansions instead of seven, with byte-equivalent complete packages and identical selected/deferred manifests in the database regression. Failed expansions are not cached; reuse does not survive a selection or hide a later correction/deletion. This reduces local computation and queries, not model calls or tokens.

Source-neighborhood expansion excludes all direct seeds before the neighbor limit, including seeds beyond the expansion cap. Source bridging additionally uses the strongest sentence/line passage rather than combining weak words across a broad supporting span. It preserves full stored statements and evidence, with the prior weak character-cue fallback. Direct assertion and learning-neighborhood scoring retain their previous behavior.

The [source-frozen priority replay](v1-2026-10-25-retrieval-priority.json) retains the exact query, budget, fifteen memory versions and content/evidence. The duration preference moves from positions 5/6 to 2/3, the story premise from 13 to 6, and the original class schedule from 14 to 7. The broad unrelated river summary moves from 2 to 11. All seven unrelated blocks remain and the context stays 15,920 conservative byte units. This is an ordering repair, not a successful host response or token saving. Candidate-local information weighting was explored but not adopted because it did not improve selected membership.

Migration 023 indexes current-event neighbors by tenant, owner, brain, conversation, a collision-safe nullable branch key and sequence. Hidden current events remain barriers. In each null/main/empty-branch variant of a disposable 40,009-row fixture, the natural query plan reduces filtered unrelated rows from 40,003 to zero while preserving exact neighbor rows. The extra index occupies about 2.9 MB with short synthetic identifiers and adds write maintenance. These isolated measurements do not establish whole-recall latency or production storage ratios. See the [baseline and query plans](v1-2026-10-25-memory-efficiency-baseline.json).

## Shared-source experiment

The new `shared-verification-sources-v1` contract retains complete source records once in a shared table for general/orientation verification. Each citation retains its exact source/version/hash, span, excerpt, index and role. Conflicting envelopes and incorrect bindings fail validation. Bounded local/history requests already share their complete source context and do not use this extra projection. Existing frozen contracts remain byte-identical.

Two fixed general-verifier cases each received one current-provenance baseline and one shared-source request from the same installed candidate. The complete-options case retained all original proposals and sources; its clean assistant-options claim was the primary positive target. The missing-letter case required non-support when positive citations omitted the letter detail. Expected results, complete requests, source hashes, model settings, a four-call ceiling and the supplementary pronoun issue were frozen before paid execution.

All four primary target checks passed. Actual provider-reported use was:

| Format, two calls each | Input tokens | Output tokens | Total tokens |
| --- | ---: | ---: | ---: |
| `citation-provenance-v1` | 46,749 | 12,607 | 59,356 |
| `shared-verification-sources-v1` | 23,255 | 6,997 | 30,252 |

The two shared-source inputs used approximately 50.3% fewer tokens; total tokens were approximately 49.0% lower in these observations. Four calls together used 89,608 tokens, with zero unknown usage and no paid retry. Input cache hits differed substantially, so those percentages are not currency savings. Output/reasoning use is model-dependent; two cases cannot establish a typical per-turn improvement.

The preregistered supplementary case prevents a whole-packet pass. Its claim named Alex, while the positive excerpt contained only a pronoun. The baseline marked citation coverage unknown; the shared format marked it supports. Complete context contained the name, but the contract does not authorize uncited positive premises beyond the narrow speaker-role/delivery metadata exception. One pair does not prove a deterministic causal regression. It does establish insufficient evidence to promote the shared format to default. The exact statement, excerpt, verdicts, usage and hashes are in the [paired diagnostic record](v1-2026-10-25-memory-efficiency-verifier.json).

The first harness invocation failed local request validation because JSON-deserialized messages were a list rather than the provider's required tuple. It reserved and sent zero calls. That failed record remains intact. The corrected envelope passed a local mock transport preflight and used a separate frozen plan/output directory; this was not a paid retry. No diagnostic published a memory or changed the preserved campaign database. Hidden model reasoning was not recorded.

## Verification notes

An initial full suite recorded one failure, 5,233 passes and four Docker-gated skips. The failed episode-continuation test expected complete sources inline in each citation while the experimental default supplied the shared table. Updating the test to resolve every citation and check source hashes produced 24 passing related tests. This original failed run is retained. After the real-model finding, the default and its production test expectation were restored to the original provenance contract.

Earlier targeted history package and integration groups passed 40 and 14 tests respectively. The first new deletion regression incorrectly expected removal of an exception to suppress independently supported facts; the corrected regression checks actual source/relation removal and refreshed scope generation. Retrieval targeted groups passed, including a final 34-test source-neighborhood run after the finalized index was applied. These overlapping groups are not summed into a unique total.

The expanded index test initially retained a branch update in the same transaction as expression-index creation. PostgreSQL's HOT-chain visibility restriction temporarily kept that newly created index out of the plan. Inserting the fixture's final branch values directly preserved all semantic and plan assertions; all three variants passed. Committed migration behavior was checked separately. No planner assertion was removed or forced index setting added to manufacture a pass.

The subsequent full experimental-candidate suite finished with **5,234 passed, four skipped and one warning** in 525.19 seconds. The four Docker-gated tests were run separately against the installed candidate: desktop setup, upgrade, recovery and standalone chat all passed. This candidate used build `0f70a9b8191a86bcae5030864a415cd991fc67a19e024d9cc2400b0e44f7fa1b` and the experimental default. These desktop tests use local synthetic callbacks, not paid models.

After restoring the existing provenance default, the six affected source test files passed **311 tests**. A newly built, isolated installed wheel then passed **440 focused tests** covering both input contracts, citation and episode behavior, history packaging, lexical/source retrieval, source neighborhoods, migration plans and desktop upgrade. The full eight-minute suite and four desktop flows were not repeated after this restoration. The [artifact manifest](v1-2026-10-25-memory-efficiency-artifacts.json) verifies that the final runtime differs from the full-suite candidate only in `semantic.py`'s default constant and its import. It also verifies identical runtime/schema/migration bytes across source, sdist, wheel and installation; migrations 001–022 remain unchanged and 023 is included. The final build is `fe045d0727cafa2fa86363048d7f62d861f19e881e20d23cac85615a64dfdb13`.

One [fixed installed-wheel retrieval pass](v1-2026-10-25-memory-efficiency-performance.json) reused the existing 10,000-record fixture after the test suites finished. All 20 expected literal-label sources were found, with median 92.62 ms, P95 113.11 ms and maximum 125.51 ms against the existing 500 ms P95 gate. There were no provider or embedding calls. This measures local in-process `cue_recovery`, with existing caches and no discarded warmups; it excludes HTTP, generation latency and the conversational-neighborhood route. It is a regression check, not a production latency guarantee or a matched speedup measurement.

`uv run ruff check src tests examples` and `git diff --check` passed. The full suite reported the existing multithreaded-fork deprecation warning; the independently resolved installed environment reported Starlette's `httpx` test-client deprecation warning. Neither warning was hidden or converted into a passing assertion. No automatic CI workflow was added or dispatched. These overlapping test groups are not summed.

## Remaining work

The [quality/efficiency design](../architecture/memory-quality-and-efficiency.md) prioritizes memory continuity and evidence fidelity over context size. The next unresolved areas are selective context admission, coherent deferred learning, mode-specific fixed prompt overhead, and a matched-quality whole-lifecycle comparison with full history and rolling summaries. The stopped longitudinal campaign, untested elapsed-time aging and cross-provider limits remain unchanged. Personality and emotion are future source-bound appraisal mechanisms, not implemented fields or a reason to weaken current memory authority.
