# First-release lifecycle smoke: 2026-10-25

The fresh `deepseek-v4-pro` thinking run completed all five stages. Independent review of the complete delivered answers found **5/5 correct for these synthetic fixtures**. The stored episode contains one minor unsupported specificity change, described below. This short experiment is a prerequisite for sustained-history testing, not first-release acceptance.

The [evidence record](deepseek-2026-10-25-lifecycle-smoke.json) retains the actual prompts, structured cognitive outputs, delivered answers, source and memory snapshots, machine checks, provider ledger, and independent review. The frozen working-tree snapshot was based on `f716f0fb3ea2cb43eb1e473d074ca78ecac1b205`; its manifest SHA-256 is `6802acac455e6ac22bb2d42228226666cbc0a0d512d50881c22704c193da9513`. It is not represented as a committed release. A separate isolated database and source directory prevented ongoing development from changing the experiment.

## What the answers demonstrate

The first exchange reports a named study routine, initially walking before reading. The next retrieves its name and order while leaving an unspecified drink unknown. A later report explicitly reverses the order from a stated date. The following answer distinguishes the new applicable routine from the historical one, and does not accept the question's invented beach agreement. The empty-brain control does not invent any of those memories.

The changed-order answer is supported by a persisted, source-bound `supersedes` relation effective on October 23. The new preference reaches the host as applicable; the old preference is marked superseded. Source IDs, exact revisions, source hashes and quoted spans match the preceding user reports. Host prompts contain the declared instructions, compact memory and current input, with no recent-dialogue shortcut. Actual delivery text matches the recorded model response. Resuming the completed recall turn causes no new model request or delivery.

Both statement-only turns produce reported background context rather than an invented current task. All machine checks passed, but the answer verdict above comes from inspecting the complete evidence, not counting those checks or matching answer keywords.

One stored episode organization statement expands “revision/study” to “exam preparation.” The user did not establish an exam. This narrowing does not enter the delivered answer or semantic preference statement, but it is a real bounded quality issue and is retained in the record. No paid retry was used to replace it.

## Cost and latency

The frozen limit was 24 requests and 290,816 reserved output tokens. All **24 calls** completed with confirmed provider-reported usage: **122,002 input + 50,903 output = 172,905 total tokens**. No usage was unknown. These are token counts, not a currency invoice. The durable server ledger also records all 14 cognitive calls as known; it intentionally excludes the five host calls and five answer-review calls.

| Stage | Foreground seconds | Orientation provider seconds | Host provider seconds | Extra review provider seconds | Background learning seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Learn | 81.79 | 60.03 | 6.05 | 15.03 | 88.18 |
| Recall | 59.35 | 31.46 | 3.55 | 23.42 | — |
| Change | 73.36 | 44.36 | 4.54 | 23.51 | 135.78 |
| Recall change | 58.22 | 31.72 | 6.37 | 18.93 | — |
| Empty control | 48.81 | 26.57 | 7.08 | 14.27 | — |

Model requests account for almost all foreground time. The remaining difference includes local HTTP, database, preparation, and delivery work; it is not an isolated retrieval measurement. Ordinary desktop chat does not enable this extra answer reviewer. This experiment therefore does not establish ordinary desktop reply latency.

The explicit cognitive policy allowed 240,000 ms for each complete proposal-and-verification job, 16,384 output tokens per request, and 65,536 decoded response bytes. Four of five orientations exceeded the library's default 30-second total allowance; one background proposal used 10,662 output tokens, above the default 8,192. These observations justify an explicit reasoning-model preset and visible degraded-learning diagnostics. They do not justify silently changing existing installations or cumulative budgets.

## Boundaries and next evidence

This frozen runner constructs its semantic processor directly. Subsequent work routes evaluation through `configured_service` with transport-only metering; that change is not part of this result. Installed desktop behavior, conditional ambiguity, long-history retrieval, abstraction differences, calibrated forgetting, and years of operation remain separate acceptance questions. The next sustained synthetic comparison must preserve the failed detail above, freeze a new plan, record its explicit server allowance, and stop on uncertain paid work.

After this live run, the newer evaluator performed a zero-call archive preflight on its retained database. Eight sources, ten assertions, two episodes, one supersession relation, and twelve event dependencies survived export and fresh-brain restore with matching normalized graph hashes; the original brain's graph remained unchanged. That separately recorded preflight excludes working state and execution/access caches. It validates the next experiment's graph-preservation check, not a retrospective change to the paid run's scope.
