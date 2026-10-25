# Learning phase monitoring

The current implementation separates local encoding from historical integration in diagnostics. The broader [local observation and historical integration contract](local-observation-consolidation.md) remains an implementation contract pending integrated acceptance. These diagnostics have focused offline and database coverage; they do not establish the semantic quality of model outputs.

## Inspecting progress

With the desktop service already running:

```sh
agent-memory desktop learning-status
agent-memory desktop learning-status --job JOB_ID
```

Both commands accept `--directory PATH`. They make authenticated read requests and do not start the service, call a model, deliver a message, or retry work. JSON stays on standard output. An exact job inspection may additionally print a fixed phase notice on standard error.

API integrations use `GET /v1/brains/{brain_id}/runtime/learning-progress` for the aggregate or `GET /v1/brains/{brain_id}/runtime/jobs/{job_id}` for an exact job. Python's `RuntimeClient.learning_progress()` and `RuntimeClient.job(job_id)` use these routes.

Desktop chat reads aggregate progress once at startup. Its post-delivery situation-understanding notice concerns the turn's separate orientation job. The public turn does not currently identify its encoding job, so chat does not claim that a particular reply's local encoding or historical integration has finished.

## Reading the counts

`learning_phases.version` is `encoding-integration-v1`. Its counters are:

| Field | Meaning |
| --- | --- |
| `encoding_open_count` | Encoding jobs with remaining work or invalid coverage. |
| `encoding_assessed_count` | Valid retained encoding results assessed as completed or partial. |
| `history_integration_open_count` | Historical obligations with unfinished work or unresolved lineage. |
| `history_integration_deferred_count` | Valid open historical obligations whose current leaf is deferred. |
| `history_integration_running_count` | Valid open historical obligations whose current leaf has a live running lease. |
| `history_integration_assessed_count` | Valid historical lineages with assessed objectives or an empty assessed manifest. |
| `history_integration_unassessed_objective_count` | Admitted comparison objectives still unassessed, including valid frozen manifests before a result exists, partial results and known failed attempts. Valid delegation avoids counting the same pending work twice. |
| `legacy_unclassified_count` | Jobs predating explicit phase metadata. |
| `unknown_count` | Jobs whose phase metadata or required lineage cannot be validated. |

The aggregate examines all retained learning-window metadata in the authorized brain, including old jobs beyond the latest 200. It reads bounded admitted/selected manifests and unsettled-operation flags to compute progress, without selecting conversation text or provider responses. Public summaries do not expose those manifests. Missing children, cycles, incompatible phases, invalid scopes, and malformed metadata make `counts_complete=false`; their work is not silently treated as finished. A paid historical attempt with missing manifest/coverage evidence is incomplete, not a trustworthy zero. Do not read incomplete zero counters as proof that nothing remains.

A valid recovery or continuation transfers the corresponding remaining work to its child without double-counting that lineage. Historical integration never substitutes for encoding progress. Its original pending creation time survives validated continuation and recovery links. An assessed historical prefix and an open historical tail can both be counted: `history_integration_assessed_count` is not a count of fully closed obligations.

An encoding replan after optional evidence changes is recognized only by its explicit `unreserved-encoding-replan-v1` marker, exact same-root recovery chain, unchanged scope and range, and unprocessed predecessors with zero recorded calls and input bytes. Runtime authorization additionally checks the durable operation ledger. This is not a fresh allocation or permission to retry a paid or uncertain attempt.

When source or context invalidation leaves a paid encoding prefix blocked, a validated `encoding-window-snapshot-v1` boundary can identify later inputs that were never attempted. A separate linked encoding allocation may process only that untouched tail. Monitoring keeps the original prefix blocked, checks the exact boundary and reverse link, and does not infer a safe tail from missing or malformed snapshot metadata.

The existing `window_count` and scheduling counts remain available. Deferred historical work contributes to `blocked_count` with the fixed reason `history_integration_deferred`. Here, blocked means it is not currently claimable; deferred integration may be reconsidered automatically when complete evidence and existing authorized capacity become available. Provider failures and uncertain paid attempts are separate failure/recovery cases.

For a frozen `no_unsettled_retry-v1` job, an expired lease with a retained reserved operation contributes to `blocked_count` with reason `provider_uncertain`, even before a worker's reclaim terminalizes it. A live lease remains running; legacy jobs retain their original policy. Reading progress does not settle or cancel the job. An actual lease conflict during provider settlement retains error code `provider_uncertain` and exposes only a fixed `lease_conflict` diagnostic; other settlement exceptions keep the generic message without raw exception text.

## Exact status and semantic outcomes

An encoding result preserves the historical integration job originally created with it. Exact job inspection validates that link, scope, original input range, allocation identity, and every recovery or integration continuation before reporting:

- `history_integration_job_id`: the original linked historical obligation.
- `history_integration_current_job_id`: its validated current leaf.
- `history_integration_state` and `history_integration_resolved`: the current state and whether lookup succeeded.
- `history_integration_reason` and `history_integration_category`: fixed deferred diagnostics when applicable.
- `history_integration_open` and `history_integration_unassessed_objective_count`: whether the chain retains work, including unassessed selected objectives on a partial ancestor even when its current leaf completed.
- `history_funding`: the validated donor authorization root and original unattempted obligation when private history uses another conversation's unused allocation. Logical progress retains the original conversation and encoding parent.
- `history_attention`: bounded admission counts for the original manifest. Descendants retain this same summary; their counts must not be added together. Nonselected opportunities are not admitted debt, and `search_exhaustive` remains false.

The safe deferred categories are `package_incomplete`, `shared_allocation_exhausted`, and `brain_budget_unavailable`. A complete bounded package can be assessed without publishing a new memory. A valid partial semantic result can likewise record assessment with rejected proposals. Neither job completion nor an assessment counter promises publication.

Individually verified relations may be published while the whole selected comparison remains unassessed. Published and assessed objective counts are independent. Failed selected comparisons stay visible under `history_integration_unassessed`; a continuation of untouched work cannot erase them or authorize an automatic paid replay.

Exact lookup follows at most 256 lineage edges. Missing, cyclic, malformed, or longer lineages report unresolved status. Job-list responses do not perform this per-job traversal and therefore do not claim that a retained historical state is current. Inspect the exact job to resolve it.

`semantic_outcomes.by_phase` separates `encoding`, `history_integration`, `legacy`, and `unknown` results. These totals describe retained latest job results, not every attempt and not the number of memories currently usable for recall. Source invalidation can make a retained result unavailable. Accepted proposal counts and published version counts remain separate.

Public history coverage includes only bounded objective, assessed, and published counts. Objective identifiers, source contents, private manifests, provider text, and raw result bodies are not exposed through these summaries. These monitoring calls are read-only; they neither replenish allocations nor authorize another provider attempt.

The optional summary shapes are `HistoryFundingSummary`, `HistoryAttentionSummary`, and `LearningHistorySummaries` in the JSON Schema and TypeScript contract. See [private historical learning capacity](cross-conversation-learning-funding.md) for grant boundaries and resource accounting.
