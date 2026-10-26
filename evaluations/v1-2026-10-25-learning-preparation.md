# Reuse learning preparation without changing model judgment

This follow-up removes redundant local work before cognitive inference. The configured model still decides what interpretations to propose or whether a comparison warrants no change. There is no new importance heuristic, selection model, provider call, database migration or input contract. Existing source, scope, correction, deletion and publication checks remain in place.

## Changes and evidence

Learning snapshot preparation previously enumerated eligible historical comparison pairs twice: once to query completed objective IDs and again to select the remaining candidates. It now discovers the scoped, ordered candidates once, reads the same completed-objective records, and selects from those candidates. Objective identities, versions, ranking policy and suppression rules are unchanged. Independent validation at semantic processing and publication remains separate; the reuse does not authorize a previously failed or unverified comparison as completed.

Sixteen frozen plan digests from baseline commit `2f08735191983004f2cd37bb61aef17222b1be81` cover planner versions v1–v4, completed-objective suppression and restricted publication. The new path matches those digests. The database regression also checks one discovery per fresh snapshot and that a completed model no-change decision continues to suppress the same comparison after another reminder.

The [local preparation benchmark](v1-2026-10-25-learning-preparation.json) uses 32 independent synthetic statements, 496 candidate comparisons and 12 previously attempted objectives. Fifteen alternating old/new timing pairs retain identical selected plans. Median preparation time is 14.32 ms before and 9.60 ms after. This excludes the database deduplication lookup, inference and publication. It is a local computation observation, not an end-to-end latency, token or currency saving.

Window sizing previously reread the same working-state row for every eligible input prefix. It now reads that state once inside the existing locked selection and independently projects it against each prefix's complete sources. An eight-input regression reduces these working-state reads from eight to one. Each subsequent selection reads again; no cross-job cache is introduced. Unavailable sources still cannot support state. A state item or open loop whose evidence appears later can reappear in that later prefix because projection never mutates the reusable snapshot. Exact byte-budget checks, oversized-first-input behavior, source order and complete text are retained.

## Verification

The initial existing focused group passed 107 tests. The first added regression run had one failure and 60 passes: the new test named the schema `WorkingMemoryState`, while the actual schema is `WorkingState`. Correcting that fixture name retained schema validation and all assertions; the group then passed 61 tests. No failing runtime assertion was removed.

The full source run finished with **5,250 passed, two failed, four skipped and one warning** in 523.56 seconds. Both failures were the long-learning-ID budget cases in `test_episode_operation_scope.py`: their `BudgetSources` test double lacked the extracted `_working_from_events` method. It now inherits the real service implementation while retaining its fake database and all exact-budget assertions. The entire affected file then passed **54 tests**. No production code changed for this fixture repair, and the full suite was not rerun after that test-only change. The original failed run remains recorded rather than being described as an all-green run.

The isolated installed wheel passed **131 non-integration checks**, followed by **87 database integration checks** covering windows, model no-change deduplication, phase allocations, recovery and historical attention. The four Docker-gated desktop setup, upgrade, recovery and standalone-chat flows also passed separately. The upgrade test constructs independent packages from the source runtime; the [artifact manifest](v1-2026-10-25-learning-preparation-artifacts.json) checks matching runtime/schema/migration bytes in source, sdist, wheel and installation. All 23 existing migrations remain unchanged. The verified build is `4f9655bd59349144ec973c9747661edcab55018ac248da5576ccf952462bf3dc`.

Ruff and diff whitespace checks passed. The full run retained the existing multithreaded-fork deprecation warning. Test groups overlap and are not summed. These checks use deterministic local callbacks and **zero paid provider requests**. No CI workflow was added or dispatched. The prior real-model shared-source experiment remains experimental; this change does not promote it or rerun that experiment.

## Remaining boundaries

Longitudinal acceptance and real elapsed-time aging are still incomplete. Candidate retrieval can still include irrelevant material or miss relevant material. A larger hand-written admission policy is deferred; future work should improve useful continuity without taking semantic decisions away from the configured model. Personality and emotion remain later source-bound appraisal mechanisms.
