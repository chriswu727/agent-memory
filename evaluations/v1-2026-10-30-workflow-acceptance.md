# Memory workflow completion — 2026-10-30

This follow-up implements the remaining core workflow, usability and performance work from the repository review. It does not claim that every future capability in the companion roadmap is implemented.

## Implemented behavior

- Notes support explicit original-message links, including an exact current-turn reference that resolves after the host records the turn. Corrections inherit those links, stale replacements fail before writing, and deletion continues through the existing dependency graph. References show provenance, not independent confirmation that the note is true.
- The local page provides structured editing, separate People and Plans views, open/completed plan filtering, original-message inspection, Save as note, and cursor-based older-message loading. Older notes without sources remain visibly unlinked. Nothing infers that a plan is done merely because its date passed.
- Service, indexing and semantic-model readiness are visible. `membrain doctor` provides read-only diagnostics and recovery commands. Pausing recording also blocks agent note tools.
- PostgreSQL connections are pooled with transaction-local principal/tenant settings. Following-message retrieval is batched. Recall budgets preserve ranked evidence before chronological rendering. Local embedding inference batches leased jobs and validates each source again before publishing; paid-provider accounting is unchanged.
- Code pull requests run Verify automatically. Plugin runtime defaults are pinned to the reviewed source revision; `MEMBRAIN_PACKAGE` remains an explicit override. The README uses the actual distribution name, `membrain-ai`.

## Verification scope

The browser journey used an isolated embedded PostgreSQL installation and synthetic messages. It covered single-use sign-in, changing a plan's date, retaining its original source, marking it done, showing completed records, the People view, saving an original message as a note, loading a 105-message conversation across the pagination boundary, and a 390-pixel viewport. The final Notes view keeps its add form collapsed; adding a note closes the form and restores the list. An injected failed background job was also verified to show an attention heading rather than a ready heading. Browser console inspection found no warnings or errors during this journey. Source erasure, isolation, retry identity, pagination under new writes, connection-pool isolation and deletion during batch inference are checked against a real database in the automated suite.

The first full run produced 5,931 passes, five skips and one failure: a compatibility regression test assumed migration 024 must remain the newest migration. The test now simulates the pre-024 runtime explicitly and still requires it to reject the upgraded database. No migration check was disabled. A second full run produced 5,935 passes, five skips and one service-start failure before the provenance test ran. The shared-bot test simulated an offline client but launched a real detached startup, which could outlive cleanup and take the next test’s API port. That simulation now suppresses its unrelated background startup, and the provenance journey uses an isolated ephemeral API port. The final focused host/plugin/note suite passed 120 tests; the database workflow/vector/timeline suite passed 37 tests, including archive provenance and rejection of an erased archive’s old manifest. TypeScript passed 43 tests and strict type checking. A later overlapping local attempt was invalidated when an auxiliary runner stopped its shared database; its errors and interruption log are retained, not counted as a pass. The final complete local run uses a separate, dedicated database. The delivery pull request’s Verify run is the full-suite merge gate. Detailed logs and browser screenshots are retained in the local review evidence folder; aggregate measurements are committed below.

## Performance evidence

[Raw measurements](v1-2026-10-30-workflow-measurements.json) preserve both the favorable and unfavorable results. The historical review and current run use the same synthetic fixture, but separate installations and run times; these are descriptive observations, not a controlled A/B or an accuracy evaluation. Each sequential recall row contains 20 warm requests; the concurrent row contains 24 requests with eight clients.

| Measurement | Historical review | Current run |
| --- | ---: | ---: |
| 100 events, recall median / p95 | 298 / 316 ms | 242 / 254 ms |
| 1,000 events, recall median / p95 | 355 / 385 ms | 270 / 307 ms |
| 1,000 events, eight concurrent clients, median / p95 | 2,246 / 2,313 ms | 2,145 / 2,180 ms |
| Add 900 events, ingestion only | 19.49 s | 10.64 s |
| Add 900 events, ingestion and all indexing settled | 52.22 s | 79.58 s |

The complete keyword-index catch-up became slower in this host fixture, despite faster ingestion and warm recall. This result is retained; the change does not establish an overall indexing-speed improvement or eliminate the existing scheduling, authority-check and per-brain serialization costs. A separate in-process control using 500 events and the same current code measured indexing at 33.33 seconds without pooling, 5.52 seconds with pooling, and 25.54 seconds with pooling but automatic statement preparation disabled. This supports retaining normal prepared connections; it does not explain or cancel the host catch-up result.

The actual pinned multilingual ONNX model processed the same 32 short documents individually and as one batch in three repetitions. Median inference decreased from 71.40 to 45.94 ms. Both produced 32 finite vectors of 384 dimensions; their minimum pairwise cosine similarity was 0.99917. Batch output is numerically close, not byte-identical. These measurements exclude model download/loading and do not measure recall quality.

The [lexical fixture runner](v1-2026-10-30-workflow-benchmark.py) accepts a new dedicated directory, explicit database/API ports and an output path. It disables semantic/model processing, creates 100 then 1,000 synthetic messages, waits for indexing, measures recall and stops its service afterward.

## Installation and delivery acceptance

The shell, JSON/MCP, Windows and OpenClaw entry points all pin runtime source commit `08695f537a07eb6221c4b17c8733a56d6635b2d5`. A regression check requires every packaged entry point to use the same immutable revision. The Windows launcher retains CRLF checkout behavior through Git attributes.

An isolated `uv tool install` first installed the previous main revision with both embedded and semantic extras, created a real embedded installation containing a source message and typed note, and stopped it. Running the normal install command again with the new pinned revision updated the same distribution version from the old source. Startup migrated the database from 24 to 25 migrations. The original message remained available, the old note could be replaced through the new API, and the replacement linked to that original message. The installed runtime build ID matched the working tree exactly: `92cb98c63d330d7305d25b975b96e3b08c6888160a6284ed0f0dfac7595063cc`. No provider was configured or called.

Ruff, browser JavaScript/launcher syntax checks, TypeScript strict type checking, 43 TypeScript tests and wheel/sdist construction passed. The corrected host/plugin/note suite passed 120 tests, the database workflow/vector/timeline suite passed 37, and the installer-pin regression passed. Verify runs the complete suite and package checks on the delivery PR before merge. The full-run journal retains the existing Python warning about `fork()` in a multithreaded process rather than suppressing it.

## Limits

No paid provider or subscription-backed Codex/Claude model evaluation was run for this change. Protocol and adapter tests do not establish real-model recall accuracy or conversation quality. The historical paid evaluations and their failures remain unchanged. No PyPI publication or publisher-account configuration was performed. Runtime source pinning does not lock every transitive dependency or imply a published package release. Performance measurements use synthetic fixtures on one machine and are not production capacity guarantees.
