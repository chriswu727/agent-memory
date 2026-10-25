# Live conversation across turns: 2026-10-24

This test connected the memory service to a real DeepSeek host conversation: user statement → automatic learning → recall in a new turn → preference change → recall of old and new arrangements, plus an independent empty-brain control. All five answers matched the factual content of the synthetic user messages, without inventing a drink or a beach agreement. However, only **1/5 stages passed every machine check**, and the last memory-backed recall answer overrode runtime uncertainty labels. The full pipeline did not pass.

The [structured results](deepseek-2026-10-24-conversation.json) preserve the complete raw baseline, sources, model inputs and outputs, verification, runtime state, usage ledger, and independent developer review. Original stage checks and pending-review fields were not rewritten; independent review is under `developer_review`. The original baseline file is 776,981 bytes, with SHA-256 `1d397a5f7faee4c5b42f52adc527c0e33f41895e1a6f8d8298817263ead91c4a`.

## Paths actually exercised

Both host and cognitive processing used `deepseek-v4-pro` with thinking enabled. The service accessed PostgreSQL through a real local TCP HTTP interface, with a new service, client, and runtime created for every turn. Answers passed through a local delivery record, and actual outputs were persisted as delivered events. This was not an external channel test such as QQ, and no operating-system process was restarted.

The host received fixed assistant instructions, compact memory from the runtime, and the current question. No test answers, scoring instructions, or complete recent conversation were appended; `recent_context_events=0`. There were no embedding calls or semantic recall controller. Only learn/change executed one new consolidation window each; consolidation for other turns remained pending.

## Five answers and pipeline results

**learn:** Accurately acknowledged the name “Origami Lamp” (original Chinese: “折纸灯”), walking before reading the textbook, the start date, and the long-term preference. It added no location, duration, or drink. This was the only stage passing every machine check.

**recall:** Accurately recalled the name and order from historical material and explicitly could not confirm a drink agreement. Orientation's inquiry and situation proposals included `invalid_proposal` rejections, leaving the result partial.

**change:** Accurately acknowledged reading the textbook before walking and the replacement relationship, while preserving that the old arrangement had existed. New user semantics were published, but an episode proposal failed verification, leaving consolidation partial.

**recall_change:** The old and new orders, dates, and name matched the original messages, and it did not assert a beach agreement. However, definite wording equivalent to “currently applicable” and “has been superseded” did not preserve the runtime's unknown state. Orientation also had `invalid_proposal` rejections and remained partial.

**empty_control:** Did not guess the name, order, or drink for the same question and acknowledged missing records. An orientation episode proposal failed verification, leaving the result partial.

A developer agent reviewed the complete answers against original sources individually. These were not keyword-based passes or an independent human benchmark. The learn/change answers directly received the current user statements, so their correctness alone cannot establish cross-turn memory. Corresponding semantic publication and later recall were verified separately.

## Did a real historical semantic chain reach the host?

The recall prompt contained the exact semantic version supported by the initial user statement. The recall_change prompt contained semantic versions supported by both the initial and changed statements, without treating the current question as historical evidence. Independent checks verified source actor=user, delivery=received, revision, actual content hash, supporting span, and existence before the current input.

All five model outputs matched both the actual delivery text and the persisted delivered output. A new runtime replayed the completed recall turn, returning identical text with `replayed=true`, without another model or delivery call.

The host also saw original excerpts, user semantic assertions, and some historical assistant statements. This establishes that the user semantic chain actually reached the prompt, but does not prove that the model used only one particular path. The empty-brain control honestly reported missing information for the same question. That provides a small observation of memory integration's effect, not a long-term reliability benchmark.

## Factual correctness and uncertainty compliance are separate requirements

The recall_change answer called the new arrangement currently applicable and the old order superseded. The original user messages did explicitly express this change, so the answer's factual content matched those statements.

However, both semantic memories supplied to the host had `applicability=unknown` and `use=belief_not_settled_current_rule`. The old memory had `belief_resolution=uncertain`, and the superseding relation itself also had unknown applicability. The claim stored an explicit legacy `valid_time` start but lacked typed validity usable by the runtime, resulting in `valid_during={"kind":"unknown"}`. Natural-language dates in the summaries could prompt the host to infer current applicability, but could not override the runtime's unknown verdict.

This run therefore records both agreement with the user's source text and failure to preserve runtime uncertainty. A coincidentally correct action order does not make the temporal-applicability pipeline pass. Ordinary recall only stated the past name and order, without asserting current applicability, and should not be conflated with this failure.

## Production fixes and unresolved problems

First, default lexical recall now consistently uses `prior_record_check`. Previously this boundary was explicit only with a recall controller; ordinary runtime now also excludes the current input and its descendants as old memory. This fix was already included in the baseline code fingerprints, and all five stages passed current-input isolation checks.

Second, compact model projection omits the changing applicability `evaluated_at`, including the same field within relation evaluations. Applicability results, reasons, evidence, and temporal boundaries remain, and the full control response stays auditable. This prevents context changes caused solely by a new evaluation timestamp. This fix also preceded the baseline, whose completed-turn replay passed; it does not establish coverage of every interruption or crash-recovery scenario.

Third, new claims must explicitly provide `valid_during` before proposal verification. An explicit source start and no scheduled end must be represented as an instant and unbounded end, respectively. Unspecified boundaries remain unknown; legacy `valid_time` null does not mean unlimited duration. The old path that automatically filled unknown validity before publication was removed, and verification must check typed validity. This fix followed the baseline. Its separate two-call follow-up is reported below; the original five turns were not fully rerun.

The final independent code review found that the default historical boundary also blocked verified historical preferences phrased as instructions, such as “please use brief replies.” A narrow exception now accepts complete, previously received user-instruction sources only for semantically verified `reported_preference` claims. Preferences with counterevidence remain contested. Ordinary facts, unverified raw instructions, question presuppositions, and current input still cannot use this exception. This fix followed the live experiment and was validated with local regression tests, without additional paid calls.

Old unknown memories were not backfilled from natural-language summaries or legacy null values; baseline failures remain intact. Rejected inquiry-oriented proposals, unstable episode judgments, and hosts potentially ignoring unknown labels also remain unresolved. Completing temporal fields alone cannot prove that hosts always honor these labels.

## Independent temporal-representation follow-up

The follow-up supplied only the same synthetic user source from the original learn stage to a standalone `SemanticProcessor`, making one proposal and one verification call. The accepted full preference retained the name, walking before reading the textbook, long-term intent, the 2026-10-20 00:00 UTC start, and no scheduled end. It explicitly generated a typed interval with that start instant and an unbounded end.

The whole claim and every citation received supporting verdicts. Accepted text and time matched the complete proposal seen by verification. Temporal probes returned false one second before the start, and true at the start and the next day. The target temporal-preference checks passed.

An additional episode-create proposal received an unknown verification verdict and was rejected. The processor's overall result remained **partial**, not completed. The complete original follow-up is under `temporal_followup` in the structured report; its independent whole-claim review is under `followup_developer_review`.

This follow-up did not publish to the original database, change old unknown memories, or ask the host to answer again. It verifies the new temporal proposal and verification path, not a complete end-to-end pass after the repair, host uncertainty compliance, or episode quality.

## Usage, reproduction, and final validation

The baseline made **18 calls**, all with confirmed usage: **121,348 input**, **73,944 output**, and **195,292 total tokens**. Input cache hits were 35,328 and misses 86,020. The reserved maximum output of 233,472 tokens differs from actual output. These are provider usage figures, not billing amounts. Independent developer review added no calls to the model under test.

The independent follow-up made **2 calls**, using **9,473 input**, **12,561 output**, and **22,034 total tokens**. The shared ledger accumulated **20 calls**, **130,821 input**, **86,505 output**, and **217,326 total tokens**, with all usage confirmed. No ledger was cleared and baseline usage was not reset to zero.

The entry point is `examples/evaluate_conversation.py`. `--plan` only displays the fixed experiment; only `--live` allows paid calls. An existing output directory is rejected before reading environment configuration or constructing a provider. `--resume` only reads saved reports; it does not automatically retry interrupted or failed paid work. The experiment had a 20-call ceiling, and the baseline was not repeated to obtain all-green results.

Checks of the public copy found no credential keys, Authorization/Bearer/API key content, or hidden `reasoning_content`. Synthetic principal/event identifiers, legitimate usage statistics, and lease-state metadata without tokens are retained.

Final local checks: **1,726 Python tests passed** in 100.57 seconds, including new imperative-preference recall, real counterevidence, and publication-path regressions. Ruff, package build, and diff checks passed. TypeScript was unchanged and its tests were not repeated. No cloud CI was added or triggered, and no Codex/Claude subscription tests were run. The baseline's partial failures and host uncertainty-compliance failure retain their original verdicts; local test success does not substitute for complete product acceptance.
