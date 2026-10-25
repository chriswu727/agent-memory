# Answer review and foreground responsibilities: 2026-10-24

This is a historical record of the preceding experiment batch. References below to “local,” “not pushed,” and “final code” describe its state at the end of that batch. See the [on-demand orientation report](deepseek-2026-10-24-orientation-routing.md) for subsequent implementation, latency measurements, and independent repairs. This batch's failure verdicts remain unchanged.

This work implemented durable answer review before delivery and `orient-v1`, which handles only the current situation. All six fixed regression samples received the expected review verdict. In the complete synchronous conversation follow-up, all five answers were grounded in sources and actually delivered, but two statement turns were still misclassified by foreground processing. **Only 3/5 complete stages passed; overall acceptance had not passed.** Code and reports remained local, without commit or push, under the repository's then-current requirement to report validation failures first.

The [structured evidence](deepseek-2026-10-24-answer-review.json) preserves raw reports from three experiments, call ledgers, follow-up reasons and budgets frozen before execution, read-only database auditing, and independent developer review. Original failures, pending-review markers, and execution states were not rewritten; conclusions are stored separately under `developer_review`. A developer agent checked complete inputs, outputs, sources, and durable state. This is neither a hidden human benchmark nor cross-validation between different model families.

## Implementation and engineering boundaries

- New foreground jobs use `orient-v1` to propose and independently verify only the current situation. Long-term beliefs, episode organization, and belief relations stay in the background. An empty situation is valid. Jobs freeze mode, sources, and complete working state; recovery does not refresh timestamps and change the original request. Real version changes still reject reuse. Old snapshots retain their original contract.
- The optional `AnswerReviewer` checks the entire answer for fidelity to current input and memory, applicability, uncertainty, and action-completion claims. Each draft permits at most one call, with the draft persisted and a reservation made before review. Unknown, rejected, invalid, or failed reviews block delivery. Review is disabled by default and adds no automatic cost to existing integrations.
- Server approval binds the draft, request, policy, memory, and working-state versions. Recovery does not spend again, and review cannot be disabled to bypass an existing turn's policy. Context is checked again after review; applicability expiring during review can block sending. Completed-turn replay returns the actual delivered result without retrospectively certifying an old answer under new review rules.
- If a delivery channel truncates or rewrites an answer, the actual delivered text is recorded. Approval only establishes that the original draft passed this model review, not that changed text did. The review model's own claims of consistency or complete coverage are not proof of truth.
- Migration `010_response_review.sql`, API and type contracts were added. Monitoring exposes state, fingerprints, call reservations, and fixed reason codes, without copying chat text, drafts, or provider credentials.

There was no automatic answer repair. A rejected or uncertain review returns `RuntimeBlocked` to the host, potentially leaving the user without an answer. This remains a usability limitation; rejection rate cannot substitute for memory quality.

## Six fixed regressions: 6/6 matched expectations

The first three samples were observed real answer failures: ignoring an explicit current user self-report, inventing a date to override established applicability, and acknowledging uncertainty before calling the latest record the current rule. All three were rejected with locations in the original draft.

Three valid controls were approved: faithfully acknowledging a new self-report, sourced recall, and correctly preserving unknown applicability. The first two came from earlier real model answers; the last was a predefined valid control. Complete original host messages and drafts were sent to the reviewer, without expected labels.

These six cases are developer regressions, not estimates of general accuracy, false rejection, or missed-error rates. Request-object tamper checks were added afterward. A zero-call replay using saved responses confirmed unchanged actual request content, policy, and request fingerprints, with the same six outcomes. That replay is not new model evidence.

## The first complete chain stopped at turn three

The initial `learn` correctly restated and published the user's preference and an episode. After changing conversations, rebuilding the client and runtime, and disabling recent dialogue, `recall` still recovered the name and order and said only that no drink record was found.

The third turn, `change`, accurately restated the new order, but its opening “好的，已记下” (original Chinese; “Okay, noted”) was judged an unconfirmed completed action. The draft remained `generated`, with a durable rejection audit and no delivery. The last two turns did not run. The near-equivalent first-turn wording “好的，我记下了” (original Chinese; “Okay, I've noted it”) had been approved, demonstrating inconsistent handling of acknowledgment versus completed storage. Available evidence cannot uniquely establish whether the third turn was over-conservatively blocked or the first was a missed error. No unreturned rationale should be invented for the model.

Read-only database auditing confirmed that the third turn's real user input, sequence 7, was persisted. Its background learning window `[7,7]` was `queued`, with 0 attempts and 0 calls. User input was queued before answer review. The experiment stopped without processing that window; this does not mean production code deleted the correction or failed to queue it because the answer was rejected. Without a running worker, queued does not mean learned.

## One bounded follow-up: answers 5/5, complete pipeline 3/5

Before the follow-up, only the host interpretation protocol changed to `memory-consumption-v3`: use natural acknowledgment without claiming completed durable storage when no corresponding completion evidence exists; raw message capture does not establish semantic consolidation or permanent retention. This constrains meaning rather than blacklisting words. Reviewer rules, thresholds, model, output limits, and fixtures remained unchanged.

The reason, original report hash, complete code fingerprints, and budget of at most 24 calls were frozen before paid execution. One five-turn chain ran on new isolated data, without resetting the original ledger or continuing to rerun. DeepSeek-V4-Pro thinking mode, local TCP API, PostgreSQL, and local durable delivery all participated. Every turn rebuilt the service, client, and runtime and changed conversations, with 0 recent-context events and no semantic recall controller, embeddings, or hidden answers.

| Stage | All pipeline checks | Answer and delivery | Result |
| --- | --- | --- | --- |
| learn | Failed | Passed | Correctly acknowledged and published the preference; the statement was misclassified as a request |
| recall | Passed | Passed | Recovered the name and order without inventing a drink |
| change | Failed | Passed | Correctly acknowledged and published the new preference and superseding relation; misclassification recurred |
| recall_change | Passed | Passed | Distinguished old and new orders without confirming an unsupported beach agreement |
| empty_control | Passed | Passed | No history available; did not guess a name, order, or drink |

Both failures were `request_intent_unconfirmed`. `learn` proposed the preference statement as a `current_request` `open_loop`; `change` proposed the change statement as a requested goal. Restated content can be supported without establishing request intent. Independent verification returned unknown, and the system rejected both under the original rules rather than adding them to the current situation. Final answers still read the current original messages directly, and background learning independently learned the preferences. The rejection safeguard worked, but classification remained unreliable; these turns cannot be relabeled as passing.

The verdicts also differed in detail: `learn` received supports for the whole claim and unknown for request intent; `change` received unknown for both the complete request claim and its intent. Source excerpts supporting some content do not establish the whole claim that the user requested it.

In the actual post-change recall input, the new preference had explicit validity and was marked usable with source attribution. The old preference was marked superseded through a published `supersedes` relation. The answer distinguished them correctly without treating assistant restatement as independent evidence. Initial learning created one interaction episode; the change stage proposed and published no new episode. The retained old episode must not be counted as a second publication.

The follow-up's `finished` status means all five turns executed. Its command still exited with **1** because of the two pipeline failures. Five approved reviews and five answers matching this fixture's intent do not establish that every semantic module passed, or general quality under unknown conditions, long history, or other natural conversations.

## Usage and latency

| Experiment | Actual calls | Input tokens | Output tokens | Total tokens |
| --- | ---: | ---: | ---: | ---: |
| Fixed review regressions | 6 | 24,248 | 23,159 | 47,407 |
| First chain, stopped at turn three | 14 | 52,734 | 35,054 | 87,788 |
| v3 five-turn follow-up | 24 | 113,251 | 54,468 | 167,719 |
| Batch total | **44** | **190,233** | **112,681** | **302,914** |

Usage for all 44 calls was confirmed. Reserved output caps totaled 507,904, distinct from actual output. New ledgers did not reset old usage, and token counts are not billing amounts. This table excludes other earlier experiment batches.

The v3 turns accumulated approximately **69.5, 51.9, 78.1, 52.1, and 30.7 seconds** of foreground provider time. Answer review accounted for approximately **9.0, 15.1, 9.1, 17.7, and 4.9 seconds**, respectively. The two background learning stages took another approximately 63.7 and 64.2 seconds. Foreground figures exclude database and other non-provider overhead; complete stage durations are separately stored in the structured report.

This remains unsuitable for instant-chat speed. The dedicated foreground format narrowed responsibilities and input structure, but does not prove faster responses. Old and new experiments had stochastic outputs and new state; a single timing difference cannot establish a causal improvement.

## Validation, delivery, and remaining work

Before the v3 wording change, the full Python regression run had **2,206 passing tests** in 127.16 seconds. After only protocol wording and version changed, **127 related runtime, consumption-protocol, and review tests passed** in 13.05 seconds. Those tests overlap the full suite and must not be added as independent totals. All 12 TypeScript tests and type checking passed, as did Ruff, package build, and diff checks. No CI was added or triggered, and Codex/Claude subscriptions were not used as models under test.

The raw six-case report SHA-256 is `9d33aaf6349d1674258e2535583dbb3366cbd89ba8bc7b4d0be5470ec7c7b5a1`; the first chain's is `b63cf00cfbaf7e2e32791a915f2cd036ff7d152771af11b2f0b9b87e660af34e`; the v3 follow-up's is `58a59d7d57e83cd73f1993a17bae38c146c31b82c8896121be9fe2f1ca95986a`. All 48 follow-up code fingerprints matched the final code. Raw reports can be reconstructed byte for byte in their original format from the wrapper objects, with hashes checked separately.

Next priorities are foreground routing and type selection for statements, questions, and action requests, followed by false rejection of normal acknowledgment, overhead when inference is unnecessary, and usability after blocked answers. Verification must not be automatically relaxed to turn misclassification into success.

The hundred-turn runner has history, control groups, and isolated probes ready, but has not completed a live run. It first needs sliding-quota preflight before each whole turn, safe waiting, and resumption, then three small comparison groups. There is no real long-term comparison yet between no memory, rolling summaries, and the current kernel. Months of use, spaced reminders, interference between similar experiences, and forgetting-parameter calibration remain unfinished. Personality and emotion remain later research topics.

Because live acceptance did not fully pass, this batch was not committed or pushed at the time; the earlier `8d745d9` was already on `origin/main`. Passing local engineering checks and preserving complete raw evidence do not change the failed acceptance items.
