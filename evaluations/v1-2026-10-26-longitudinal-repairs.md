# Longitudinal source and recall repairs — 2026-10-26

**The diagnosed source/retrieval paths are repaired and locally verified. Full behavioral acceptance remains unpassed.** On the retained source snapshot, the original family scene and its safety meaning now reach the host without a reminder. A real two-call encoding accepts the previously rejected declarative event. However, two of five real host answers still add unsupported detail inside suggested autobiographical card text. Those failures remain visible.

The final runtime build is `bac834e69a77638fcdc2407b0a9b988934556bca1e43f2bb4537ff29e6dac810`, based on commit `6f6f998c5f196ce672be3a3bc03a9ef2638eb71c`. No stable tag or release is published. The [original varied campaign](v1-2026-10-26-longitudinal-round.md), including its unanswered timeout and unknown usage, remains closed and failed.

Evidence: [answers, source judgments and measurements](v1-2026-10-26-longitudinal-repairs.json), [runtime artifact identities](v1-2026-10-26-longitudinal-repairs-artifacts.json), [evidence manifest](v1-2026-10-26-longitudinal-repairs-evidence.json), and [raw evidence bundle](artifacts/v1-2026-10-26-longitudinal-repairs-evidence.tar.gz). The [architecture note](../architecture/longitudinal-repairs.md) explains implementation and compatibility.

## Implemented changes

- **Versioned source interpretation.** New captures record `literal-episodes-v3`; embedded English question words no longer automatically turn temporal declarations into inquiries. New jobs use `source-act-boundary-v1`. A current interpretation can recover an automatically misclassified old declaration without rewriting the event, literal, hash or original span. Proposal, verification, capability checks and publication share the chosen contract. Old frozen contracts and unchanged old-input wire goldens remain intact. Explicit restrictive source annotations stay restrictive.
- **Useful source context.** Scoped lexical ranking removes contraction fragments and weights distinctive terms using bounded document frequencies. A matched literal can recover immediately adjacent original units from the same exact source. The family example now brings back both lantern/buttons and “It made a scary dark house feel safe.” This does not create a semantic gist or infer emotion beyond what the user said.
- **Assistant attribution and tail calibration.** Ordinary personal recall excludes assistant-only speech before final candidate selection. Explicit speech/quote/episode/commitment requests remain available with `assistant_speech_not_user_experience`. Unindexed tail scores use a comparable scale and the access-age policy rather than dominating fused indexed scores with raw lexical scores and additive activation.
- **Query planning.** Episode-neighbor expansion materializes the matched episode set before joining its members. The old plan repeatedly nested small scoped sets; an actual retained-snapshot query exceeded a ten-second limit. Adding scope predicates alone yielded the same plan and still timed out, so that initial hypothesis was rejected. The materialized query took approximately 60–68 ms in the recorded six-read replay, with complete retrieval around 158–171 ms. No global planner setting or migration was introduced.
- **Host guidance.** `memory-consumption-v5` uses language-neutral acknowledgment guidance, explains corrected literal act labels, distinguishes assistant drafts from user experience and directs reuse of relevant established background. These are model instructions, not a guarantee of source-faithful generation.

## Local verification and preserved preparation failures

The final integrated source run passed **5,341 tests**, with four opt-in Docker tests skipped and one Python `fork()` deprecation warning. It ran on build `463bcd74bc8109a529ce5c7b2bbc16d6fc856aa4539a4f5df7f2371769acfb00`. A subsequent compact-DTO compatibility guard changes only the case where `derivation` is absent; complete `MemoryAssertion` behavior is unchanged. After that guard, **60 affected source tests**, **37 TypeScript tests** and strict TypeScript checking passed. An unchanged full suite was not repeated for that missing-field branch. The report distinguishes the two builds rather than attributing the earlier full run to new bytes.

The final isolated wheel passed **129 source/contract/recall checks**. Its four real macOS/Docker flows then passed separately: desktop lifecycle/MCP, direct terminal chat, upgrade/downgrade and backup/fresh-install recovery. They use deterministic callbacks or a local HTTP model stub; provider credentials and dotenv loading were disabled for that four-flow process. These exercise the four skipped source cases from the installed artifact, not four additional unique tests. The installed checks report the existing Starlette/httpx test-client deprecation warning.

Source, sdist, wheel and isolated installation match **118 runtime/schema/migration files**. Package-level hashes and per-file hashes are recorded; this is a runtime-identity claim, not a claim that a test-built archive includes subsequent documentation edits. Ruff and whitespace checks passed. No routine CI was triggered and no Codex/Claude subscription-backed test was run.

Failures during preparation were retained:

1. An initial full run had **5,299 passes, 30 failures and four skips**. Legacy wire fixtures had accidentally acquired new event metadata, explicit assistant-speech tests needed to request speech, and a ranking-version expectation needed alignment. The fixes preserve the legacy fixture's actual v2 format and old byte goldens; new v3 cases are separate. Existing golden hashes were not regenerated to hide changes.
2. The next full run had **5,338 passes, one failure and four skips**. Its access-clock test found a real regression: initial tail calibration had removed age weighting. Applying the existing access-age policy fixed it; the affected group passed 74 checks and the subsequent integrated run passed above.
3. The first TypeScript run had **36 passes and one failure**: a compact cross-language fixture lacked `derivation`, which the new attribution helper assumed existed. The missing-field guard fixed it. The original full TypeScript failure log was accidentally overwritten after a relative-path move failed; an explicit failure note preserves the observed counts, exception and cause. It is not represented as an original raw transcript.

Overlapping local groups are not added into a larger test count. The raw bundle retains the available failed/follow-up logs and the TypeScript failure note.

## Performance checks on the final installed runtime

The reused 10,000-record fixture and twenty fixed literal-label queries found **20/20** expected sources. Median local recall was **132.678 ms**, p95 **188.048 ms**, maximum **189.654 ms**; p95 remains below the fixed 500-ms budget. There were zero model/embedding calls, no discarded warmups and no cache flush. This is one ordered pass on the existing fixture, not a cold-cache SLA or whole-answer latency result. [Raw retrieval measurements](v1-2026-10-26-retrieval-repairs-performance.json).

Thirty-two complete independent user-report endpoints retained all **496** eligible abstraction pairs. Five consecutive capability projections under the new source-act contract had median **68.832 ms**, below the 100-ms budget, with 13,400 bytes of metadata and zero model/embedding calls. This is higher overhead than the older capability implementation; it is not claimed as a speedup. Raw samples are included in the report and bundle.

## Retained-snapshot replay

A trusted archive from the original amended diagnostic checkpoint was restored to an independent brain. The source graph contains 45 source events and 337 memories: the actual 22 delivered exchanges plus the unanswered input. This preserves the original checkpoint limitation; it is not eleven clean completed sessions.

Six ordinary database retrievals exercised three original queries at access ages zero and 3,650 days. Each read rolled back; scoped digests confirmed 25 tables unchanged afterward, and the source archive/file stayed unchanged. The original scene and user-expressed safety meaning appear in both family-query contexts at both ages, with no assistant-invented table/kitchen detail. Corrected `source_act` metadata explains the old literal `(inquiry)` label. Unfinished writing state remains available. Some irrelevant material and partial/index-lag coverage remain visible.

These reads ran on the integrated pre-DTO-guard build; the later guard leaves complete snapshot-memory rendering unchanged. They do not execute host generation, background learning, real elapsed years or useful gist-first forgetting. Both scene details and meaning remain available at old access ages, so this observation cannot establish the desired loss of incidental detail.

## Bounded real DeepSeek diagnostic

Before any call, five independent trusted restores were checked for the same normalized source graph. Each received only its own original probe text. The normal capture/prepare path assembled its context, and `BrainRuntime._prompt` produced the frozen host request. Real generation subsequently replayed those exact prepared requests using `ChatCompletionsGenerator`. It did not run a new full `BrainRuntime.respond` lifecycle, commit probe answers, enable a cognitive worker or merge results into the source brain.

The frozen plan digest is `1c5a2ac377096d38d80828de681e7755b4f4e08547d8eb800646f7df845dc541`. Final runtime identity was rebound before the first call following the compact-DTO guard; sources, exact prepared requests, settings, rubric and allowance stayed unchanged. The previous unexecuted plan is archived.

The plan allowed **seven calls and 53,248 reserved output tokens**: two for isolated encoding/verification and five host answers. Encoding used `deepseek-v4-pro`, enabled thinking, low reasoning, a shared 600-second allowance and 16,384 output tokens per call. Host generation used the same model with high reasoning, 4,096 output tokens and a 240-second timeout. No actor, automatic answer reviewer, retry or replacement probe ran. A temporary bounded sleep assertion was released after completion.

Whole-source and whole-answer judgments below are from the implementing Codex reviewer, independent of the tested provider. This is not a blinded external human panel, a statistical estimate or a superiority experiment.

### Encoding

The two real cognitive calls returned **three accepted attributed reports**: finding an old photo, the lantern/button/tin event, and the user's safety meaning. Their exact complete declarative spans are preserved. The old `When…` declaration now passes the new source-act contract and independent verification.

A fourth candidate describing the caption question as a reported fact was structurally rejected. The processing result remains **partial**, with the rejection visible; it is not relabeled completed. The accepted episode grouping describes only the actual message and its request. The source review authorized the remaining independent host probes after known usage and accepted-claim fidelity were checked. No accepted result was published into the original or probe brains.

The accepted records are episode fragments and largely quotations. This verifies the source boundary; it does **not** demonstrate compressed semantic anchors, gist selection or reduced cognitive cost.

### Host answers

| Probe | Recall observation | Whole answer |
| --- | --- | --- |
| Unaided card | Recovers lantern, buttons, tea tin and safety meaning without re-teaching. | **Fail:** the suggested first-person card introduces “dark, scary nights,” although no time of day was supplied. |
| Broad cue | Recovers unsupplied scene details and meaning; cue-supplied power cuts receive no recall credit. | **Pass:** no invented table, kitchen or house owner. |
| Specific cue | Recovers unsupplied details and meaning. | **Fail:** the suggested card adds “those nights” and “buttons rattling into the tea tin.” |
| Unknown photo location | Says no storage place was recorded and gives prospective search suggestions. | **Pass:** no hidden hallway/drawer claim. |
| False-premise trip/present | Separates the known scene and photo from the invented premise; missing accessible evidence is not treated as proof of non-occurrence. | **Pass.** |

All five replies are in English for English requests. All three family-card recall paragraphs are source-faithful, but a correct opening paragraph cannot excuse unsupported autobiographical detail later in the answer. “Nights” could be figurative; under the strict draft criterion, unqualified first-person use still fails. This does not establish that the real scene happened during daytime. The auditory embellishment is also absent from the user's sources.

The contexts exclude the previous assistant table/kitchen fabrication. The remaining defects are fresh generated draft details, rather than retrieval of those assistant inventions. Both are relevant to product quality. The five-answer result is **three whole-answer passes and two failures**; earlier failures remain failed and no replacement calls were made.

### Actual use

| Role | Calls | Input tokens | Output tokens | Known total |
| --- | ---: | ---: | ---: | ---: |
| Encoding and verification | 2 | 13,168 | 8,415 | 21,583 |
| Host answers | 5 | 27,719 | 5,386 | 33,105 |
| **Total** | **7** | **40,887** | **13,801** | **54,688** |

All seven calls have confirmed usage; unknown calls are **zero**. Reserved output is a ceiling, not actual use. Host inference took 7.81–18.77 seconds per frozen request, excluding the earlier preparation step. Those durations are not a complete per-message runtime benchmark and are not directly comparable to the original campaign's foreground/background timing.

## Remaining work and stop point

This repair batch and its paid execution are complete. The source boundary, meaningful-source retrieval and assistant-origin filtering are exercised, but **G4 remains unpassed**. Further release work must address the new unsupported autobiographical-draft observations, remaining irrelevant context and whether the host should opt into the existing answer-review facility with an explicit cost/latency tradeoff. An additional reviewer is not a truth guarantee and was not silently enabled.

The intended detail-to-gist aging mechanism and cognitive efficiency remain open. Local encoding currently yields episode fragments; retrieving adjacent meaning does not itself produce durable multi-level representations. Assess how the model can propose source-backed gist/importance without hand-written emotional importance rules, and how history comparisons earn their cost. The earlier 31 no-publication history jobs do not justify skipping all comparisons. Sleep-interrupted uncertain-operation recovery remains separately unverified by this batch. A new complete varied acceptance campaign should wait for the remaining repairs and its own frozen, finite plan.
