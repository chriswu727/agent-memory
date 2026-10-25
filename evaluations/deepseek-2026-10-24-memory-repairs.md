# Source bridging and shared-claim repair evaluation: 2026-10-24

These repairs address two specific failures in the [previous report](deepseek-2026-10-24-compact-planning.md). Source bridging passed a free recall replay using existing data. The independent live DeepSeek abstraction diagnostic failed first, then passed one follow-up after evidence roles were clarified. The original failure remains recorded; no old records were rewritten and no `unknown` verdict was reinterpreted as a pass.

The [structured results](deepseek-2026-10-24-memory-repairs.json) include both rounds of proposals, verification, source selection, actual acceptance results, free replay, and a shared usage ledger. All sources were existing synthetic test texts; no real user-brain content was sent.

## Recalling English abstractions from Chinese sources

Previously, lexical retrieval primarily matched assertions themselves. When a model summarized Chinese source text in English, a Chinese query could find the original text but miss the corresponding semantic assertion.

The system now performs a bounded search for derived beliefs from matching supporting source spans. It checks source versions, hashes, span boundaries, scope, and deletion/correction state, then applies the existing complete dependency and applicability checks. Background citations, counterexamples, and unrelated spans cannot supply a match. Neither assertions nor sources are regenerated or rewritten, and no model or embedding call is made.

The old conditional stage recovered the English conflicting self-report at all three evaluation times, changing `published_memories_recalled_and_prepared` from false to true. The unknown “silver state” and conflict handling retained their original boundaries. The original task's `job_completed=false`, caused by other rejected proposals, did not change.

An additional query used the original Chinese “备考 同时 互相冲突 更正” (English gloss: “exam preparation, simultaneously, mutually conflicting, correction”). The target English assertion had a direct lexical score of zero, yet its exact original version reached recall and prepare unchanged, passing budget and source-bridge path checks. The bridge reads at most 64 events; recall scans at most 512 related assertions, and learning at most 256, with potentially smaller actual allowances. Reaching a limit reports incomplete coverage. This is not arbitrary cross-language semantic understanding: a retained source must still be discoverable through a matching cue.

## Shared content, irrelevant sources, and unknown information

The new comparison policy requires each abstraction to express one bounded shared claim, leaving instance differences in the original records. The runtime computes supporting-span mappings for each exact parent instance. Verification must independently confirm every instance, the complete claim, and the abstraction relation. The comparison policy is part of objective identity: a new policy can make one fresh attempt on an old failed instance group, while repeated reminders under the same policy remain deduplicated. Old frozen plans remain compatible.

The first two-call test generated a concise claim that both records showed the user walking before studying, without adding an unobserved difference in duration. However, it still copied clothing spans into the supporting citations. Verification supported the complete claim and relation but marked both clothing spans `unknown`. The system rejected the proposal; that result remains a failure.

This exposed another protocol gap: complete sources retained to trace parent instances do not necessarily all support the shared claim. The repair adds an explicit `irrelevant` verdict, meaning only that a span is unrelated to any material content, negation, condition, qualifier, or counterevidence for the claim. This differs from `unknown`.

Only for the structurally valid abstraction path must verification provide support indices exactly matching every `supports` verdict. The runtime may remove explicitly irrelevant citations from the shared claim, but may not rewrite its conclusion, conditions, time, world, or parent versions. Each instance must retain supporting evidence, and the entire claim and relation must pass. Unknown, counterevidential, or incomplete judgments cannot be discarded. Ordinary memories cannot use this path. Complete parent sources remain in the relation; pre-selection citations and structured judgments enter the job audit, and sources and structure are checked again before publication.

The single two-call follow-up passed. The accepted claim was, in the original Chinese:

> 在两个已比较的实例中，用户在开始复习前先散步，随后开始查看复习材料。

English translation: “In the two compared instances, the user took a walk before starting to study, then began reviewing study materials.”

Verification selected two action spans and explicitly marked two clothing spans irrelevant. The shared claim retained two supporting citations, while the abstraction relation kept all four citations. Both instances, the whole claim, and the relation received explicit support. The accepted claim text exactly matched the proposal. No duration, location or clothing difference, causal benefit, or permanent habit was invented.

This is a small sample checked by developer review and model verification, not an independent human benchmark. The live provider test ran only a standalone `SemanticProcessor`; it did not publish this live model result to the database or observe a host's final answer.

## Independent validation of publication, auditing, and invalidation

A separate deterministic database integration test exercised the complete publication path: four actual test source events formed two multi-event instances; a reminder led to publication of the filtered shared claim and complete relation. Verification and source selection were persisted. Ordinary monitoring did not expose source text or citation identities, and recall/prepare retained the source and relation closure.

Even correcting only a clothing source removed from the shared claim's support set invalidated the corresponding parent-instance version and dependent abstraction. Sources retained in the relation did not escape correction propagation through citation filtering. This test used controlled model responses and is reported separately from the live DeepSeek diagnostic; together they are not described as a complete live-model end-to-end acceptance test.

## Usage, reproduction, and limits

The two diagnostics shared one ledger and made **4 calls**, all returning usage: **38,319 input**, **14,819 output**, and **53,138 total tokens**. The initial failure used 25,077 tokens and the follow-up 28,061; these are not billing amounts. Each call allowed at most 16,384 output tokens; each round retained a two-call, 240-second limit, with four calls overall and no automatic retries. No further paid calls were made to obtain a complete set of passes.

The entry point is `examples/evaluate_memory_repairs.py`, which requires the corresponding synthetic baseline saved locally. `--replay` replays for free and freezes the original instances; only `--live` permits paid calls. After a completed failed run, `--live --followup "specific reason"` permits one follow-up. It checks the old report, frozen batch, and complete ledger, preserves `live.json`, writes a separate `followup.json`, and rejects a third attempt. `--resume` reads the report only; `--resume --followup inspect` reads the follow-up only.

Long-running abstraction stability, discovery without a lexical cue in distant history, costs at scale, and final-answer quality remain unproven. Source bridging is still a bounded candidate mechanism, and model judgments about individual instances and irrelevance can be wrong. The successful follow-up does not remove these limitations.

Final local checks: **1,562 Python tests passed**, including the new database publication and correction-propagation tests. Ruff, package build, and diff checks passed. TypeScript was unchanged and its tests were not repeated. No cloud CI was added or triggered, and no Codex/Claude subscription tests were run.
