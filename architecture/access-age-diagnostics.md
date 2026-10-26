# Frozen-snapshot access-age diagnostics

`MemoryService` accepts an optional internal `access_time_provider` dependency for
controlled age-ranking diagnostics. This is not a public request field, environment
setting, or general simulated clock.

```python
service = MemoryService(
    database,
    access_time_provider=lambda: "2036-10-22T12:00:00Z",
)
```

The provider must return a timezone-aware RFC3339 string. Each retrieval calls it
once, validates and normalizes that value before reading candidates, and passes
the same instant explicitly to candidate weighting and the unindexed or otherwise
unranked recall fallback. Invalid results are rejected even with no candidates.
With no provider, retrieval samples the real clock once for these age calculations.

The dependency changes only these calculations:

- `candidates.collect_candidates(..., access_at=...)` supplies the instant to
  `dynamics.access_state`, then applies the existing rank weight. Existing direct
  retrieval intents, including `cue_recovery`, bypass this candidate age weight.
- `cognition.recall(..., access_at=...)` uses the instant for its existing fallback
  activation calculation when no candidate rank signals are available. This
  fallback remains a separate, older ranking policy; the clock does not unify the
  two policies or change which intents use them.

Audit timestamps, source timestamps, current-fact validity, conditional
applicability, working-state expiry, database leases, quota windows and monotonic
execution deadlines continue using their existing clocks. In particular,
`state["applicability_at"]` remains a separate internal validity-testing input.
The access time is not placed in the shared retrieval state or propagated into
context preparation. No schema or database migration is required.

## Diagnostic boundaries

Use a dedicated, stable snapshot to compare the same memories and access ledger
at selected instants. Record the access instant, actual execution time, snapshot
identity, code build and access-policy parameters in the evaluation artifact.
The retrieval API still performs its normal request and trace bookkeeping; the
clock does not mutate memory documents, source events or reinforcement credit.

This supports synthetic age-weight comparisons, not a compressed multi-year
interaction simulation. New observations and consolidated memories still receive
real creation timestamps. Trusted reinforcement still derives its timestamp from
the actual feedback event. Continuing to write new interactions after advancing
only the access clock would mix real and simulated coordinates. Do not interpret
that as a valid longitudinal reinforcement experiment. This mechanism also does
not establish human forgetting rates, biological fidelity or semantic answer
quality.

Do not monkeypatch the shared `now()` helper for this diagnostic. Do not rewrite
stored timestamps to simulate age. Keep any running evaluation on its frozen code
and preserve its original time-mode and aging-assessment claims.

## Verification evidence

On 2026-10-25, the targeted suite below passed **152 tests** on the isolated
`evaluation/access-aging-clock` worktree based on `282dd47`:

```text
tests/test_access_clock.py
tests/test_dynamics.py
tests/test_cognition.py
tests/test_candidates.py
tests/test_contextual_recall.py
tests/test_context_views.py
```

The new tests cover one provider read per service retrieval, the same instant
reaching indexed weighting and tail fallback, lower future ordinary rank,
unchanged indexed cue-recovery weight, equivalent timezones, rejected malformed
provider output, retained source-evidence checks, real-time validity and working
expiry, and unchanged audit timestamps and stored source/memory documents.

The database cases used new random tenants and brains on the existing local
PostgreSQL database. They ran no migration or schema writes. The test subprocess
received only the three database configuration keys
(`MEMORY_DATABASE_URL`, `MEMORY_MIGRATION_DATABASE_URL`,
`MEMORY_DATABASE_APP_PASSWORD`), process path, an explicit worktree `PYTHONPATH`,
and `PYTHON_DOTENV_DISABLED=1`. It asserted that `agent_memory.__file__` resolved
inside the worktree before running pytest. No model credentials or model calls
were used. These results are targeted local verification, not a full-suite or
paid-model evaluation result.
