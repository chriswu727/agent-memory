# Longitudinal evaluation harness: implementation and verification

The replacement [longitudinal protocol](../architecture/longitudinal-memory-evaluation.md) now has an executable session runner. It replaces repetitive fixture counts with an unfolding fictional life and practical tasks. Full G4 and first-release acceptance remain unpassed. The old failed campaigns and their unsettled reservations are unchanged.

## Implemented scope

The frozen scenario contains sixteen irregular sessions and thirty-three turns across fifty-six fictional days. Fifteen of sixteen review cases assess ordinary assistance; one asks about an older memory. Threads include an unfinished story, recurring class changes and a temporary exception, a corrected folder name, two people called Alex, a cancelled outing versus a completed visit, guest-specific food preferences and a meaningful childhood recollection. Hidden world facts never become agent evidence without actual disclosure.

First messages are authored. Follow-up choices depend on actual answers through a separate model selector. It receives only the current intent, released personal knowledge, current-session transcript and exact allowed choices. It cannot generate new facts, see future events or read the review rubric. Assisted and corrective choices are counted separately. Normal recent conversation remains available to the tested agent.

The runner uses the public HTTP lifecycle and one persistent brain. Its learning helper reloads actual received/delivered source versions, respects coalesced session windows and inherited historical authority, and executes only untouched work within existing allocations. It records claimed authority, operations, encoding coverage and remaining historical debt. It does not insert memories, create recovery jobs, reconsider exhausted work or refill budgets.

A private, locked, hash-chained journal records every provider request/result, branch, delivered exchange and checkpoint. Delivery is recorded before runtime settlement and learning, so later failure cannot hide an already delivered wrong answer. Review packets contain expected source references and the complete actual received prefix through the case boundary. Later information cannot retroactively justify an earlier response. Execution completion always leaves semantic review pending.

Continuation is allowed only at a clean session boundary. It requires the same plan, code fingerprints and runtime build, unchanged sources/memory and cumulative ledger, and a completed-turn replay that produces no external effects. Failed or interrupted campaigns cannot automatically retry. The CLI supports offline plan inspection, new execution, read-only inspection and bounded continuation; no scheduled task or new CI workflow is installed.

## Metering defect found and repaired

The existing evaluation transport incorrectly accepted a usage object containing only `completion_tokens` as confirmed. It also accepted contradictory totals. The core cognition parser was stricter, so the same provider response could be treated differently across roles.

The transport now shares the core usage and strict-JSON validators. Missing fields, invalid arithmetic/cache details, duplicate keys and non-finite values leave usage unknown. Sanitized observations remain in the append-only ledger, but only confirmed usage contributes to totals. Reopening an old ledger revalidates its confirmation without rewriting it. Unknown usage stops every subsequent host, actor and cognitive request; original reservations remain charged.

## Verification

Using the existing isolated installed wheel, Python `-I` from `/tmp`, the combined harness, journal, scenario, learning, metering, host and budget group passed **273 tests in 119.43 seconds**. No test failed or skipped. Two warnings were emitted: an upstream Starlette `httpx` deprecation and Python's warning for the intentional inherited-lock fork test.

This run includes the entire sixteen-session/thirty-three-turn fixture through a real loopback HTTP server and PostgreSQL with deterministic providers. It also launches two separate OS processes through the actual `live()` entry point: four turns use one brain and an unchanged cumulative ledger, and completed history is not generated or delivered again. Other cases cover same-session context, malformed simulated-user output, changed checkpoints, unknown usage and failures after delivery. The full deterministic fixture deliberately produces no semantic proposals; its completion verifies the harness, not memory quality.

The focused learning helper independently passed nineteen real PostgreSQL cases, including coalesced windows, continuation, funding scope, failure isolation and no retries. The scenario and journal groups passed fifty-two and forty-six checks respectively. These are included in the 273-test group, not additional unique coverage.

The existing conversation, lifecycle, answer-review and live-evaluation unit regressions passed **123 tests in 7.97 seconds**, with two integration cases deliberately deselected. During development, an initial runner unit test referenced a nonexistent request type, and the first full deterministic fixture failed because its fake provider omitted mandatory historical abstentions. Both fixtures were corrected without relaxing runtime validation or the stopping rule; the installed verification above includes their corrected paths.

The installed runtime build remains `80bc3130b2f73cd479c2f650dfb177693ab92f3b78430546920679cb9eb95a4c`. No production runtime, schema, migration or SDK file changed. Existing installation/recovery evidence remains applicable to that build; this batch did not repeat all desktop Docker flows or the full repository suite. Local logs and JUnit output are retained in `.local/longitudinal-harness-verification/`. Database work used the dedicated development database, not either failed acceptance database. All providers in these checks were local deterministic callbacks. No Codex or Claude subscription-backed test ran.

## Live-model status and remaining work

No new paid call has been made at this checkpoint. The next bounded diagnostic will test whether DeepSeek's actual answers, user selection and cognitive processing cooperate through the first sessions. It must use a fresh campaign directory and the separately frozen cumulative plan, not any old allowance.

The full plan permits at most 248 calls and 3,448,832 reserved output tokens across all roles; its brain cognition budget is additionally bounded. A normal invocation defaults to one session. A short pilot cannot pass the complete scenario or G4.

Still unassessed: independently reviewed full real-model behavior, isolated same-state cue comparisons, model-callback continuity within this scenario and memory aging across justified time. The latter three require additional evaluation support. Story-day offsets do not advance the memory clock. Actual elapsed time, active invocation duration and gaps are recorded separately. This bounded synthetic selector is not a real human field trial. Personality and emotion engines remain outside this batch.
