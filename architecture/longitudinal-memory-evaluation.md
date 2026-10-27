# Longitudinal memory evaluation

Status: the session runner, bounded simulated user, durable journal and session-aware learning checks are implemented. The [implementation evidence](../evaluations/v1-2026-10-25-longitudinal-harness.md) distinguishes deterministic verification from live-model results. Full longitudinal acceptance remains unpassed. This replaces the repetitive history requirement in [G4](v1-acceptance.md#g4--demonstrate-useful-memory-through-longitudinal-use). It does not erase earlier failed experiments or change the implemented memory model.

The question is whether a person can keep returning to the same agent and receive useful, appropriately remembered help without maintaining the memory themselves. A larger number of exchanges, successful writes, or correct answers to explicit recall questions cannot establish that outcome.

## Why the previous campaign is insufficient

`examples/memory_benchmark_cases.py` constructs 100 inputs from 93 instances of one note/tag/page template and seven substantive events. Its twelve final questions are six cases plus paraphrases. `examples/evaluate_conversation.py` assigns a different conversation to every input and removes recent conversation context. The next user input does not depend on the actual answer. A few dates in messages do not age the running system.

These choices isolate particular retrieval and lifecycle regressions. Preserve their fixtures, source audits, costs and failures as that evidence. Do not fill the remaining 77 exchanges merely to complete a counter, or rename another templated sequence a longitudinal test. No direct oracle leakage was found in the old runner; its unusually explicit wording and limited coverage are different weaknesses.

## A continuous user with an unfolding life

The initial scenario follows one fictional adult across work, a personal project, friendships, daily preferences, an occasional practical task and an old meaningful memory. World events have causes and consequences. Some plans change, some do not happen, and some subjects are never raised again. The agent learns only what the person actually communicates, not everything true in the fictional world.

Author the life timeline and plausible reasons to return before writing evaluation questions. Freeze event release times, important constraints, source expectations and permitted response branches before live execution. Ordinary conversations must remain useful interactions: drafting, planning, thinking through a choice, continuing unfinished work, or talking about a day. There is no filler quota, daily fact dump, or requirement to say “remember this.”

An example thread, interleaved with unrelated conversations:

| Point in the story | Natural user input or event | Behavior to inspect |
| --- | --- | --- |
| First week | “I'd like to finish that short story, but Tuesdays and Thursdays are already taken by my evening class.” | Help build a workable plan from the conversation. |
| Later that week | “The opening still isn't working.” | Resolve the reference within the session and continue the actual draft discussion. |
| Second week | “The class moved to Wednesday. Only this Friday is busy as well.” | Update the recurring constraint while keeping the Friday exception temporary. |
| After other topics and a gap | “I've got some room next week. Help me get back to the story.” | Use the relevant schedule and unfinished task without requiring the user to repeat them. |
| After a failed attempt | “I didn't finish it after all.” | Keep the task unfinished; an earlier suggestion or intention is not completion. |
| Fourth week | “Let's make next week's plan.” | Apply current constraints, avoid the expired Friday exception, and omit irrelevant old anecdotes. |

Other threads include two different people with the same first name, a cancelled visit and a separate completed visit, a mistaken detail corrected later, and a childhood recollection that becomes relevant only through a cue. Exact values and language are fixed in the eventual campaign fixture, not added retrospectively to make a failed answer correct. Personality and emotion engines remain outside this work; ordinary human expressions belong in the input without requiring those new product features.

## Sessions, interaction and time

The primary campaign follows the complete life/task timeline using one durable brain and an irregular session schedule. An initial scenario might span eight fictional weeks; the span must be justified by the changes, interruptions and delayed returns it exercises, not by reaching a round number. The initial engineering ceiling is **32 sessions**, each with one to eight user turns. This is a spending bound, not a minimum target. Freeze concrete revisit delays and intervening activities for each required case, along with the schedule and total call ceiling, before starting. Do not shorten delays or remove difficult events after seeing failures.

Within a session, preserve one conversation identity and the normal bounded recent context. Allow short messages, pronouns, interruptions, answers to clarification and topic changes. Across sessions, use deliberate stable or new conversation identities according to the same host rules a user would experience. Do not clear the brain or secretly provide the entire transcript. Identify which successful tasks depended on evidence outside the current context.

The simulated user responds to the agent's actual delivered answer. If asked a reasonable clarification, they can answer from already released personal knowledge; if misunderstood, they can naturally correct it. That correction must be recorded as assistance, not retroactively counted as unaided success. A small, bounded user model may render those branches, but it must not invent new world facts or receive scoring answers. Simulator mistakes are fixture failures requiring review, not automatically product failures.

Report fictional time span, actual elapsed operation time, active process uptime and human participation separately. An accelerated interactive campaign can test life/task continuity. Age-dependent behavior additionally requires either actual elapsed time or a validated semantic-time seam; without one, those cases remain unassessed. A separately proposed real-time soak, for example twenty-eight calendar days, can check operational endurance. That duration is not a new universal release gate and no such run is scheduled here. Calendar labels and fast-forwarded clocks cannot claim weeks of real operation, and a simulated user is not a human field trial.

Memory aging diagnostics need an explicit evaluation seam for semantic time, with tests covering the timestamps actually consumed by recall and decay. Never advance database lease clocks, request deadlines or rolling quotas to imitate memory age. The current runtime uses both application timestamps and database clocks; changing one `now()` function is not a validated whole-system time simulation.

## Observe usefulness throughout the interaction

Inspect outcomes when a person needs help, including before and after a change, rather than only at the end. Freeze a compact case ledger of concrete decisions distributed across the evolving timeline. At least two thirds must be ordinary requests or ongoing tasks rather than explicit “what did I tell you?” questions. Include late cases and irrelevant-memory controls; do not measure only the easiest early facts.

Each case specifies the permitted evidence available **at that point**, required applicable constraints, prohibited assumptions, acceptable uncertainty, and whether a prompt or clarification is needed. It scores the complete delivered answer and resulting task state, not a keyword or a model's confidence. Useful memory may shape a suggestion without being quoted verbatim.

Required behavior coverage:

- Continue an unfinished activity after a session boundary and after a long gap, without needlessly asking for its established background again.
- Apply changed information and temporary exceptions correctly; preserve the distinction between the earlier situation and the current one.
- Keep people and similar episodes separate, and distinguish proposed, cancelled, unfinished and completed actions at intermediate points.
- Retain important general meaning and useful constraints. Treat incidental details as optional unless the fixture gives them a concrete importance or use.
- Recover an older source through a meaningful reminder when available; report whether recall was spontaneous, cue-assisted or required re-teaching.
- Admit an unsupported or unavailable detail instead of manufacturing one. Hidden world facts that were never communicated are not recall targets.
- Avoid bringing irrelevant, superseded or private information into an otherwise ordinary answer.
- Preserve the above behavior across planned process restarts, an offline gap and a model-callback change without regenerating history.

For a cue ladder, snapshot the brain before the check. Ask the unaided question and each progressively informative cue on separate copies of that same state. Otherwise, the first question can rehearse the memory and contaminate the comparison. Diagnostic copies never merge back into the main life history. Actual clarification in ordinary conversation remains on the main line and counts toward user effort.

Do not reward forgetting for its own sake. Keeping an incidental detail in storage is compatible with selectively suppressing it from current context. Failure to recall it spontaneously can be acceptable; inventing it is not. Gist and detailed recall are reported separately. An explicit deletion must remain erased and must not be treated as a cue-recoverable memory.

## Separate the user, runtime and reviewer

| Component | May receive | Must not receive |
| --- | --- | --- |
| Scenario scheduler | Frozen event graph, release dates, session bounds | Authority to modify past delivered inputs or replenish spending |
| Simulated user | Released personal facts, current intent, permitted branches, actual delivered conversation | Future events, scoring rubric, required answer, internal memory contents |
| Tested agent and memory runtime | Current user messages, ordinary recent context, its own permitted memory retrieval | Private world state, future transcript, case answers, evaluator source mappings |
| Independent reviewer | Actual received sources up to the case boundary, complete answer, state transitions and rubric | Permission to teach the main brain the correct answer |

Private world truth and agent-visible evidence are distinct ledgers. A fact may be true in the fictional world without being knowable to the agent. Assistant suggestions and simulator assumptions do not prove external actions occurred. Record transcript, branches and source cutoffs so that a later correction cannot make an earlier false statement appear correct.

A model may help triage review, but the user simulator must not grade itself and a positive model verdict cannot pass a critical case. Inspect source-backed behavior independently. Selected identical-state, memory-suppressed comparisons can diagnose whether memory helped; they require separately counted calls and do not justify population-level superiority claims.

## Finite completion and reporting

Freeze the concrete case ledger before calls. For every required case, all required factual/task constraints must be satisfied; an acceptable abstention or clarification must be defined in advance. All required behavior categories above must be exercised. A blanket refusal is not success on a case with sufficient communicated evidence. Optional detail misses and writing-style differences do not become blockers after seeing answers.

Passing requires the frozen scenario and justified delays in their declared time mode, completed independent review, no unresolved critical memory/publication/isolation defect, and required practical outcomes throughout the timeline. A final correct answer cannot erase an earlier unsupported publication. Recovery-assisted outcomes remain separate from unaided outcomes. Missing or invalid cases remain unassessed. A token count, turn count, empty queue or average score cannot substitute for these conditions.

Record user re-explanation burden, unnecessary clarification, spontaneous/cued recall, stale constraints, false recollections, irrelevant intrusions and task outcomes. Also record local recall latency, end-to-end response latency, foreground/background model waiting, memory-context size, storage growth, learning debt and cumulative paid usage by role. Summarize early and late comparable cases separately to reveal deterioration. This one-person fixture cannot estimate reliability for all users.

Reserve capacity durably before every paid call. Unknown usage, provider failure, failed required processing, unsafe state, invalid simulator evidence or the cumulative ceiling stops further paid calls immediately; do not wait for the session to finish. Invalid simulator text must not be delivered to the tested agent, and dependent work must not proceed from an uncertain result. Do not spend the same failed request again or replay an entire history merely to reach its end. Normal scheduled pauses occur at safe session boundaries and resume through an append-only session journal, under the same brain, plan and cumulative ledger, with completed deliveries and settled calls verified. A late or missed session is recorded; it is not backdated. Finishing or exhausting a campaign stops it. Any repair follow-up is a distinct decision and preserves the original result.

No new experiment is launched by this document. The old campaign is closed; unused old capacity does not become the new campaign's allowance. Calculate the new finite ceiling from the selected session plan and actual host, cognition, user-simulator and optional reviewer paths before execution. Reuse deterministic evidence and avoid repeated full suites or automatic CI for documentation work.

## Implemented runner and remaining acceptance work

`examples/evaluate_longitudinal.py` runs the frozen `longitudinal-life-v1` fixture: sixteen irregular sessions, thirty-three authored turns and sixteen review cases, fifteen of which assess practical help. Story offsets span fifty-six days. They order the narrative and do not advance memory decay, leases or quotas. Relevant elapsed context is communicated in ordinary user messages; no hidden date is passed to the host as ground truth.

```sh
uv run python -m examples.evaluate_longitudinal --plan
uv run python -m examples.evaluate_longitudinal --live --sessions 1 --output .local/longitudinal-memory
uv run python -m examples.evaluate_longitudinal --inspect --output .local/longitudinal-memory
uv run python -m examples.evaluate_longitudinal --continue --sessions 1 --output .local/longitudinal-memory
```

Only `--live` and `--continue` can call providers. They use local `DEEPSEEK_API_KEY` and the configured PostgreSQL database. A fresh campaign creates its own brain and explicitly configures its frozen cognition budget; it never reuses an old campaign's brain or allowance. `--sessions` limits the next invocation, not a renewed spending budget. The default is one session. `--inspect` verifies and reads retained evidence without loading credentials or opening the database.

The current v4 global ceiling is 248 provider calls and 3,387,904 reserved output tokens: up to 33 host answers, 17 user selections and 198 cognitive calls. Cognition also has a 30-day brain ceiling of 198 calls and 19,464,192 input bytes. These are cumulative limits, not predicted usage or prices. There is no paid automatic reviewer or full baseline arm. The actor can only select an exact authored choice, using released personal knowledge and the actual preceding answers. It uses JSON mode with thinking disabled and a 512-token output bound; the host and cognition keep thinking enabled on the same Pro model, with explicit high effort for the host and low effort for cognition. This setting addresses the recorded reasoning-output exhaustion; it does not establish semantic accuracy. Clarifications and corrections remain separate from ordinary continuation.

The HTTP response envelope has an explicit 262,144-byte limit for reasoning plus content; the semantic processor still limits actual answer content to 65,536 bytes. Raising the envelope limit does not recover or settle earlier unknown provider usage.

The private campaign directory holds a frozen plan, a hash-chained journal, a durable head, a process lock and the shared transport ledger. It contains conversation and model content and must remain outside version control. Calls are reserved before network access. All three roles stop on partial, contradictory or unknown usage. Provider failure, malformed actor output, failed required learning or an operator-created `STOP` file stops dependent work. No request retries automatically.

A delivered answer is journaled before runtime settlement and learning. Its actual source prefix remains available for review if either later step fails. Review packets retain expected sources separately from the complete received conversation prefix at that point; later corrections cannot justify earlier answers. A completed run produces pending review packets, never an automatic semantic pass.

Normal pauses occur after completed sessions. Continuation requires identical code, runtime build, scenario and policy, an unchanged source/memory snapshot and cumulative ledger, and replay of the last completed turn without generation or delivery. Incomplete, failed and terminal journals cannot resume. Actual invocation duration, elapsed wall time and gaps between invocations are recorded separately from story time. This is manual bounded session continuation; no recurring scheduler is installed.

Still required for full G4: independent review of real-model behavior through the entire scenario, real-model cue comparisons, a model-callback change within the longitudinal scenario, and justified age-dependent evidence. Deterministic integration checks now cover a fresh model callback recovering source-backed history after service reconstruction. The internal [access-age diagnostic seam](access-age-diagnostics.md) supports frozen-snapshot comparisons; it does not simulate years of new interaction, consolidation or use. These checks cannot establish human field reliability.

`examples/longitudinal_cue_diagnostics.py` provides five isolated probes at exactly eleven clean completed sessions, before the main grandmother-card task. Each new brain starts from the same trusted server archive and a fresh conversation. Its durable recall graph must match; working state, access counters, embeddings and execution state are not asserted identical. It compares unaided recall, a broad cue, a specific cue with withheld details, an unknown detail and a false premise. There is no background learning, retry or merge into the main brain. Source snapshots and cumulative cognitive usage must remain unchanged.

The cue plan freezes the core `ChatCompletionsGenerator` callback instead of the main runner's evaluation host, retaining the same provider, model and generation settings. This checks an alternate callback over restored history without extra calls; it does not establish cross-vendor or cross-model compatibility. The five-copy HTTP/PostgreSQL regression and installed-runtime check each passed 18 tests, including prompt isolation, parameter equivalence and unknown-usage stopping. Real-model callback and cue outcomes remain unassessed until the eligible checkpoint is actually tested.

```sh
uv run python -m examples.longitudinal_cue_diagnostics --plan
uv run python -m examples.longitudinal_cue_diagnostics --live --source .local/longitudinal-memory --output .local/longitudinal-cues
uv run python -m examples.longitudinal_cue_diagnostics --inspect --output .local/longitudinal-cues
```

The auxiliary ceiling is fifteen provider calls and 184,320 reserved output tokens, separate from the primary campaign. Each probe permits one host answer and at most two cognitive calls. Running the auxiliary completes execution only; semantic acceptance requires independent review against the exact received source prefix. It must not silently run at a later checkpoint after missing the frozen boundary.

The implementation sequence remains the checklist for completing the protocol:

1. Author and independently review the private event graph, natural session intents, permitted response branches and separate case ledger. Confirm conversational variety and available-source cutoffs before generating paid history.
2. Implement the session journal and simulated-user interface around the public lifecycle. Persist each delivered exchange and branch decision; support safe between-session process exit without the legacy runner's prohibition on all paid resumption.
3. Reuse transport accounting, durable delivery, archive verification and source audits. Replace `PhaseLearning`'s one-conversation-per-fixture and exact-single-exchange allocation assumptions with assertions against the actual session's queued windows, inherited scope and frozen budgets. Do not bypass the runtime by inserting memories or rewriting sources.
4. Verify the new harness with deterministic providers: clarification branches, same-session context, cross-session return, mid-session interruption, session resume, absent future information, cue-copy isolation, clock separation and unchanged cumulative spend. Use the existing installed desktop path for a bounded integration check.
5. Freeze the reviewed fixture, runtime build, provider configuration, declared time mode and schedule, rubric and total ledger ceiling. Begin the longitudinal campaign only after those prerequisites pass. No run should be described as started before it actually starts.

## Release fixture

The release fixture can be selected with `--fixture release` on the same runner. It contains fourteen irregular sessions, twenty-six inputs and eighteen source-bounded cases, fifteen of which are ordinary practical tasks. The seventy-day story interweaves an illustrated zine, changing commitments, two people named Sam, an unfinished shelf plan, a fictional character and several different uses of one meaningful childhood scene. It includes counterfactual questions and unknown details without replacing the primary story with a repeated recall quiz.

The release plan freezes a callback change after seven completed sessions, from the evaluation host to the core `ChatCompletionsGenerator`, with the same provider, model and generation settings. This is a callback integration check, not cross-model evidence. Separate invocations reconstruct the service and client on the same durable brain and cumulative ledger; source review occurs at declared session boundaries. `--plan --fixture release` remains offline. Neither the fixture nor this documentation launches a paid run or grants it an acceptance pass. The bound plan, actual execution and review determine its outcome.

New release-fixture plans reserve 16,384 host output tokens per request after an actual high-effort reasoning response exhausted the earlier 4,096-token limit before producing its answer. This ceiling includes reasoning and answer output; it is not the desired reply length or actual usage. The 26 host, 156 cognitive and 12 selector call allocations cap the release fixture at 194 calls and 2,988,032 reserved output tokens. The original fixture and generic chat defaults retain 4,096 host output tokens. Old plans, failures and recorded allowances are never rewritten to acquire the new capacity.

```sh
uv run python -m examples.evaluate_longitudinal --plan --fixture release
uv run python -m examples.evaluate_longitudinal --live --fixture release --sessions 4 --output .local/release-memory
uv run python -m examples.evaluate_longitudinal --continue --fixture release --sessions 3 --output .local/release-memory
```

## Methodological references

[LoCoMo](https://github.com/snap-research/locomo) provides session-structured conversations and a persona/event-based generation approach. We borrow the idea of an unfolding event history, not a claim that synthetic stories constitute real elapsed use.

[LongMemEval](https://arxiv.org/html/2410.10813v2) covers extraction, multi-session reasoning, temporal reasoning, updates and abstention through curated questions and evidence sessions. These are useful component checks; our primary acceptance additionally requires practical help in an evolving interaction.

[RealTalk](https://arxiv.org/html/2502.13270v1) records twenty-one days of human messaging and documents differences between real and generated conversation. It motivates varied, sometimes incomplete interaction and a separate evidence label for real people. Its small, demographically limited, predominantly social sample does not validate this product or replace its task-oriented evaluation.

The proposed protocol and thresholds above are our engineering decisions, not findings established by those papers.
