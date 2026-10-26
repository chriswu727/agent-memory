# Frozen v1 access-age diagnostic

The [sanitized result](v1-2026-10-25-access-age-diagnostic.json) records 24 actual
service retrievals against the persisted `.local/longitudinal-life-v1` brain.
This source campaign completed **one conversation turn**: 3 persisted event rows
(2 current sources),
21 active assertions, including 7 produced by aggregation, and **no external
reinforcement records**. The diagnostic made no provider or embedding calls.

Four short queries were chosen from the known source content. Each was retrieved
with `explore` and `cue_recovery` at a shared reference instant plus 0, 365 and
3,650 days. Both intents used the same explicit historical evidence cutoff,
existing conversation scope, all existing memory worlds and a 64,000-unit
conservative context budget. These source-derived queries exercise ranking;
they are not a blind recall or semantic-accuracy benchmark.

| Elapsed days from the snapshot reference | Everyday | Cue | Deep |
| --- | ---: | ---: | ---: |
| 0 | 21 | 0 | 0 |
| 365 | 5 | 0 | 16 |
| 3,650 | 0 | 0 | 21 |

Ordinary retrieval signal sums decreased for every common returned memory.
Across the four queries, 33 positions changed at day 365 and 24 at day 3,650,
relative to day zero. Cue-recovery signal sums and ordering stayed identical.
Returned membership stayed identical for both intents at every offset: this
small, generously budgeted corpus did **not** demonstrate a memory becoming
unretrievable and subsequently being recovered. All results remained `partial`
with `partial_scope` coverage; ranking did not establish exhaustive recall or
answer quality. Access tiers are configurable engineering outputs, not measured
human forgetting rates.

The unmodified `MemoryService._retrieval` path normally writes audit traces.
Each call therefore ran inside a forced-rollback savepoint, enclosed by a
forced-rollback `REPEATABLE READ` transaction. The brain row was read with its
exact tenant, owner and brain identifiers without taking an update lock.
Canonical row hashes across **25 scoped tables**, including sources,
assertions, access state, feedback, traces and brain state, were identical after
every call and after the outer rollback in a fresh read-only transaction.
Audit timestamps stayed within the actual execution interval. No source
timestamps, memories, reinforcement credit, schema or other brains changed.

Day zero is `2026-10-26T01:55:13.896078+00:00`, a shared snapshot reference;
individual memory ages were not reset. The access clock advances only ranking
age, leaving validity, working-state expiry and infrastructure clocks unchanged.
The experiment makes no claim about years of interaction, semantic maturation,
reinforcement recovery or biological fidelity.

## Local reproduction

The bounded script is
`.local/longitudinal-v3-verification/access_age_diagnostic.py`; its SHA-256 and
the exact runtime build appear in the result. It imports this checkout's `src`
explicitly and rejects model-related environment variables. The runner passed
only the three database configuration keys (`MEMORY_DATABASE_URL`,
`MEMORY_MIGRATION_DATABASE_URL`, `MEMORY_DATABASE_APP_PASSWORD`), process path,
explicit `PYTHONPATH`, and `PYTHON_DOTENV_DISABLED=1`.

From that same restricted environment, replay with:

```sh
.venv/bin/python .local/longitudinal-v3-verification/access_age_diagnostic.py \
  --reference-time 2026-10-26T01:55:13.896078+00:00 \
  --expect-snapshot-sha256 1f513ede4d4393d1e7af2a724014599952be87b68a2ae09fcb4003be6d68459d \
  --output .local/longitudinal-v3-verification/access-age-repeat.json
```

A second 24-call execution with this fixed reference and expected snapshot
produced identical memory catalog, access states, retrieval results,
comparisons, runtime build and script fingerprint. Actual execution timestamps
appropriately differed. Public results contain query/statement hashes and
pseudonymous memory labels, not source text, owner identifiers or credentials.
