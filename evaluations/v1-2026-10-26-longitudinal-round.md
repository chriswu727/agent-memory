# Varied longitudinal memory diagnostic — 2026-10-26

**The authored life sequence was exercised to its end, but behavioral acceptance failed.** Sixteen sessions and all 33 inputs were attempted on one persistent brain: 32 replies and their required learning completed; one input timed out and remains unanswered. Fifteen sessions completed, with one partial session. The final fourteen inputs ran in a separately declared diagnostic tail after the original campaign stopped. This is not a clean 33-turn acceptance pass.

The scenario follows writing, work, two people named Alex, a cancelled outing, a later museum visit, family recollection, cooking and a household task. Its 56 fictional days are narrative context, not 56 days of actual operation. Primary-to-tail calendar duration was **3 hours, 19 minutes, 28.62 seconds**, including macOS sleep, review gaps and isolated diagnostics. Instrumented primary/tail invocations totaled **8,679.70 seconds**; monotonic measurements across sleep are not a certified active-uptime measurement.

## Evidence and frozen conditions

- [Full answers, source references and judgments](v1-2026-10-26-longitudinal-round.json).
- [Evidence manifest and SHA-256 checksums](v1-2026-10-26-longitudinal-evidence.json).
- [Compressed raw journals, ledgers, plans, isolated archives, diagnostic executors and verification logs](artifacts/v1-2026-10-26-longitudinal-evidence.tar.gz).

The archive contains synthetic scenario evidence, including the original Chinese answer. Credential values and key patterns were checked before compression; local credentials and personal configuration are excluded. Local execution paths appear in frozen metadata. Diagnostic executors are archived for inspection with their original paths and dependencies, not advertised as a standalone installed command.

The candidate runtime build is `eceb600da38d0607ec065540e4cc0ffd6f49422616fe2322c96d201cd0d7ee73`, based on `f1daa5b3e92a88a666b78f9410be1f3dbbdc7f80` plus the archived time-allowance patch. Runtime and scenario fingerprints remained frozen throughout primary and tail execution. Main plan digest: `444e026ea47b58a693ebb02c3c1ebd455c29abe402b7f615e410300eb322af15`; combined pre-execution plan digest: `6dac212c4f37d94c5dd9a8dde29c22848d281637e79a67ee65fe2676026fc81e`.

Real API generation used `deepseek-v4-pro`: high reasoning for host answers, low reasoning for cognition and a bounded non-thinking JSON branch selector. Host output was capped at 4,096 tokens, cognition at 16,384, and selector output at 512. The host's generation allowance remained 240 seconds; semantic processing explicitly used a shared 600-second proposal/verification allowance. Context used a 16,000 conservative byte-based token estimate and at most six recent messages; this estimate is not measured provider token usage.

The simulated user selected only frozen authored branches based on actual delivered responses. It could not see the answer rubric or future events. Three checkpoint replays checked persistent continuity across process reconstruction. Required encoding completed for every delivered main-line reply. Full answers and new derived claims/episode descriptions were reviewed against available source prefixes by the implementing Codex agent, independently of the tested host and actor. This was **not a blinded external human panel**, a human field trial, a baseline superiority experiment or a cross-vendor reliability test.

## Main-line findings

Across all 33 inputs, source review records **27 passes, two partial results and four failures**. Three passes required user assistance and are not unaided successes. The four failures were an unexpected language switch, an unsupported caption detail, the unanswered timeout and failed unaided family-memory recall. Among the sixteen predefined cases, twelve passed, two were partial and two failed; the caption defect and timeout occurred outside those named cases and remain counted in the whole sequence.

| Predefined case | Result | Observation |
| --- | --- | --- |
| `initial-practical-plan` | pass | Respected Tuesday/Thursday classes and unfinished outline. |
| `same-session-opening` | pass | Used Lea, observatory, letter and the requested half hour. |
| `changed-schedule` | fail language | Updated the schedule correctly but answered in Chinese to an English request. |
| `cancelled-outing` | pass | Did not turn the cancelled walk into a completed outing. |
| `first-return-without-reteaching` | pass | Recovered writing premise, half-hour preference and updated Wednesday class. |
| `corrected-work-detail` | pass | Used Harbor instead of Juniper, with the analyst and unsent handoff. |
| `separate-similar-episodes` | pass | Kept the actual museum visit separate from the cancelled walk. |
| `guest-preference-scope` | partial dietary qualification | Preference ownership passed; vegetarian qualification was incomplete. |
| `plan-is-not-completion` | pass | Kept the 250-word opening unfinished and the letter unopened. |
| `unrelated-task-focus` | pass | Helped rearrange the lamp without irrelevant personal history. |
| `delayed-meaningful-memory` | fail recall | Asked for a reminder; relevant family memory was absent from the supplied context. |
| `cue-assisted-card` | pass assisted | After the tea-tin/power-cut reminder, recovered lantern and buttons; no unaided credit. |
| `late-project-continuity` | partial redundant clarification | Useful accurate plan, but unnecessarily asked for the class schedule again. |
| `no-guest-preference-leak` | pass | Used mushrooms for the user without importing the guest’s dislike. |
| `uncommunicated-location` | pass | Admitted the photo location was unknown; search suggestions stayed hypothetical. |
| `new-completion-not-delivery` | pass | Accepted completed draft and correct friend without claiming sending or reading. |

The dietary caveat is a general host-answer issue, not a preference-attribution failure: an unqualified Parmesan option was treated as vegetarian if dairy was acceptable. Parmigiano Reggiano specifies calf rennet; generic Parmesan products can differ, and the actual product was unspecified. [Official product specification](https://backend.parmigianoreggiano.com/uploads/0325_disciplinare_docx_EN_2eda3456af.pdf).

### Reproduced defects and limits

1. **A local speech-act heuristic blocks valid declarative memory.** `classify_segments` matches question words anywhere. “When our power went out, Grandma would light a lantern and put buttons in a tea tin for us to sort.” is classified as an inquiry. An offline proposal containing only that declarative span is rejected by `_check_claim_sources` before semantic verification. Splitting off the later actual question does not solve this example. Separately, combined user-report/question proposals repeatedly fail as a whole. Literal sources and episodes survive, but reliable semantic encoding should not depend on their rescuing rejected valid facts.
2. **Relevant family memory loses to generic lexical overlap.** The raw lexical diagnostic ranks “I don't want it to sound like I'm chasing him” above the actual Grandma statement. Contraction fragments such as `m`, `ve`, `don` and `t` contribute to unweighted matching. The actual family statement ranks 81st in this diagnostic and its emotional safety meaning ranks 223rd. This is an identified ranking weakness, not a controlled proof that lexical matching alone caused the final packed context; fusion, recency, tail coverage and packing also affect selection.
3. **Assistant-invented detail can return as autobiographical recollection.** The first family caption invents a lantern “on the table.” Stored derived speech remains attributed to the assistant, but a later isolated broad-cue answer reuses the table and adds a kitchen. Attribution in storage alone did not prevent unsupported recollection at generation. The main sequential reminder answer avoided those invented locations; that success does not erase the earlier defect.
4. **Language continuity and unnecessary re-teaching need repair.** One answer switches to Chinese; the English runtime protocol includes Chinese acknowledgment examples, a plausible contributor rather than established causation. Another late writing answer retrieves the correct schedule but asks the user to repeat it anyway.
5. **Age tiers do not yet demonstrate useful gist-first forgetting.** The age diagnostic changes activation classifications but largely returns the same meanings, misses the family meaning even at day zero and includes irrelevant material. It does not validate the intended transition from accessible detail to durable gist and cue-dependent recovery.
6. **Cognitive processing is expensive relative to its observed changes.** All 31 history-integration jobs publish no memories, relations or episode actions in this scenario. Encoding publishes 61 derived memories and performs 28 episode actions, with rejected candidates retained in evidence. Many accepted records quote entire assistant replies. Relevance, representation size and when to compare history require attention; zero-change jobs alone do not establish that comparison is always unnecessary.

These algorithmic issues are **not repaired by this commit**. The only runtime change made before freezing this evaluation is the explicit time-allowance repair below.

## Isolated cue and access-age diagnostics

At the amended eleven-attempted-session boundary, the actual history consisted of ten completed sessions, one partial session, 22 replies and one unanswered input. This does not meet the original eleven-clean-session boundary. The diagnostics are exploratory and cannot establish G4 acceptance.

Five independent restored brains received the same normalized durable snapshot and actual conversation prefix. Probe answers were not merged into the main brain; no probe learned from another. They used the core `ChatCompletionsGenerator` callback with the same provider/model settings, not another vendor.

| Probe | Result | Source review |
| --- | --- | --- |
| Unaided recall | Fail | Requests a reminder; the injected context lacks the family memory. Appropriate uncertainty does not pass required recall. |
| Broad cue | Fail | Recovers some details but claims a table and kitchen unsupported by the user. |
| Specific cue | Fail | Recovers safety/kindness meaning and some unsupplied details, but claims whose house it was without evidence. Facts supplied in the cue receive no recall credit. |
| Unknown photo location | Pass | Does not invent the uncommunicated location; search suggestions are conditional. |
| False-premise childhood trip | Pass | Does not accept the invented trip/present; separates known scene details from the unknown premise. |

These are five single observations, **two whole-answer passes and three failures**, not a statistical accuracy estimate.

The access-age comparison performed **18 direct retrievals with zero model/embedding calls**: three queries, two recall intents and three access ages (0, 365 and 3,650 days), using a fixed 16,000 conservative context allowance. Repeatable-read rollback and scoped digests verified that 25 database tables and source journals were unchanged. Local durations were 32–38 ms. Responses explicitly reported partial scope and index lag.

The checkpoint catalog held 337 memories: 295 direct captures and 42 episode fragments, with no semantic-anchor/schema representation. All 337 computed activation tiers changed from everyday at day zero to deep at later ages, but surfaced meaning was substantially unchanged. The grandmother-card query missed family meaning even at day zero. The broad cue primarily recovered an assistant caption containing the unsupported table. Useful unfinished-writing state remained available alongside irrelevant desk/museum material. This direct retrieval seam differs from normal runtime preparation and packing; it neither simulates decades of interaction nor establishes real elapsed-time aging.

## Usage and response time

All usage below is provider-reported where known. Reserved output ceilings are not actual consumption. Unknown calls are not counted as zero tokens, estimated or silently retried.

| Segment | Calls | Known tokens | Unknown-usage calls |
| --- | ---: | ---: | ---: |
| Closed initial 240-second attempt | 3 | 21,482 | 1 |
| Original frozen primary segment | 82 | 906,508 | 1 |
| Same-brain diagnostic tail | 63 | 746,451 | 0 |
| Isolated cue probes | 5 | 35,942 | 0 |
| **This round** | **153** | **1,710,383** | **2** |

The previously closed plan-review attempt is a separate historical run and is not included in these round totals. There were no paid answer-review calls and no Codex/Claude subscription-backed tests.

For the primary plus tail, 144 calls have known usage: host answers used **210,775 tokens**, cognitive processing **1,417,549**, and authored-branch selection **24,635**, totaling **1,652,959**. One additional host call has unknown usage. Cognition accounts for approximately **85.8%** of known primary/tail tokens. This is a reasoning-model diagnostic configuration, not a promised default cost per user message.

| Completed primary/tail measurements | Samples | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Foreground request to delivered answer | 32 | 24.33 s | 60.61 s | 69.90 s |
| Non-provider foreground remainder | 32 | 4.29 s | 7.20 s | 7.48 s |
| Required background learning | 32 | 212.95 s | 368.90 s | 394.88 s |

The non-provider remainder includes orchestration and runtime work, not just database retrieval. Background learning is separate from the already delivered answer; this sequential diagnostic waits for it before proceeding. Completed-answer statistics exclude the failed 240.55-second host request, which remains a failure rather than a successful latency sample. Raw samples and role-specific timings are archived. Sleep affects interpretation; these measurements are not an SLA.

## Preserved failures and disclosed amendments

The initial 240-second attempt delivered one response but completed no turn: proposal took 171.54 seconds, leaving about 68.27 seconds for verification, which timed out with unknown usage. That journal remains closed. The repair permits explicitly configuring up to **600,000 ms** and gives desktop connections a **615-second** read timeout so frozen jobs remain accessible. The default semantic allowance remains 30 seconds, the existing reasoning preset remains 240 seconds, and SDK callers still configure their own response timeout. Proposal and verification share one allowance; other call/input/output limits and no-retry behavior remain intact. This accommodates slow inference; it does not accelerate it.

After the seventh primary answer switched language, a written amendment at sequence 204 permitted continuing the frozen story despite semantic failures, while preserving their failed judgments. It predates the unsupported table answer. Its digest is `3016139947b1e813c27841cf0a81faddbb201433be056d37db1cce87c00485a4`. Provider failure, unknown usage, failed required processing or integrity failure still stopped execution.

The primary segment later stopped at the nineteenth input, `dinner-shopping`, after eighteen completed replies. macOS sleep overlapped host generation. The provider-side outcome is unknown; overlap does not prove the provider failed. A read-only audit found unchanged earlier sources, the unanswered user input and an expired `generating` lease, with no assistant response. No cleanup, retry, fabricated reply or usage reconciliation was performed.

A separately frozen same-brain tail then started at original session ten. It skipped the unanswered input permanently and inherited the actual prefix with that answer explicitly null. It kept the runtime, model, policies and source brain, used only new conversations, and subtracted all 82 earlier reservations including the unknown call. The original primary ceiling remained 248 calls / 3,387,904 reserved output tokens; the tail could use at most the remaining 166 calls / 2,436,608 tokens. Actual primary-plus-tail reservations were 145 calls / 1,700,352 output tokens. Auxiliary allowance remained separate at fifteen calls / 184,320 output tokens; only five calls / 20,480 output tokens were reserved. No reset or ceiling increase was used. A temporary bounded sleep inhibitor protected the tail without changing permanent power settings.

Tail plan digest: `418c786564ca79ac574b25722094ae7ee2e4819c8cf44e8c217f6e24382eca26`. The original stopped campaign is not represented as successfully resumed. At the earlier six-session boundary, semantic review preceded continuation, but the standalone programmatic review artifact was written afterward against the preserved prefix following local helper errors; the runtime had already verified the checkpoint before calls. These procedural deviations remain visible.

## Local verification and next acceptance work

The scoped source run initially had **187 passes and two failures** in the newly added timing fixture. A first fixture correction still failed its two cases; correcting its text to match the existing semantic fixture then passed both. The isolated installed-package run passed **41 checks**, including deadline sharing, retained frozen policy, lease allowance, desktop timeout and database behavior. Source, sdist, wheel and isolated installation matched across **117 runtime/schema/migration files**. Ruff and whitespace checks passed. No fresh full regression suite, routine CI or stable release was run for this batch; overlapping check groups are not added as independent tests.

G4 remains failed. Before another paid acceptance run, reproduce and repair the declarative-span rejection, family-memory retrieval/packing and assistant-origin contamination paths offline; address language continuity and redundant clarification; measure history-comparison utility and representation cost on this retained evidence. Reassess how access-age policies preserve meaning rather than merely changing tier labels. Preserve the sleep-interrupted turn as uncertain and test explicit recovery separately rather than treating it as settled. Each repair needs behavior-focused local checks before a bounded real-model follow-up on a newly frozen candidate. Do not rerun the same long sequence simply to accumulate more rounds.

This diagnostic is complete and its paid execution has stopped. It does not establish first-release maturity. No stable tag or release is published.
