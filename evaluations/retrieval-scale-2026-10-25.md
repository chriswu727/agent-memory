# Bounded lexical retrieval at 10,000 records: 2026-10-25

Twenty literal-cue queries against the same 10,000-record synthetic fixture found 18 expected labels before the retrieval changes and all 20 afterward. Median measured recall time changed from 487.009 ms to 92.3195 ms; p95 changed from 503.284 ms to 123.173 ms. This is a local, in-process storage diagnostic. It does not measure model response time, HTTP latency, semantic memory quality, or a production service-level guarantee.

## Results and retained evidence

| Run | Records | Expected labels found | Median (ms) | p95 (ms) | Maximum (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline b | 100 | 20/20 | 26.978 | 35.412 | 39.300 |
| Baseline b | 1,000 | 20/20 | 64.226 | 67.294 | 85.780 |
| Baseline b | 10,000 | 18/20 | 487.009 | 503.284 | 523.786 |
| Repaired replay c | 10,000 | 20/20 | 92.3195 | 123.173 | 149.567 |

The baseline missed `zeta00705` and `zeta00940`, returning `no_match` for those requests. All other baseline samples and every repaired sample returned `partial`: the literal source was available, but semantic sufficiency was not assessed. Timings include all measured requests, including the two baseline misses; they are not statistics restricted to successful requests. With 20 samples, p95 is the nineteenth sorted observation, not a confidence bound.

The public evidence files are byte-for-byte copies of the original local output:

- [Attempt a](retrieval-scale-2026-10-25-a.json): retained but excluded from the successful-recall comparison.
- [Baseline b](retrieval-scale-2026-10-25-b.json): all three corpus sizes, individual queries, timings, and outcomes.
- [Repaired replay c](retrieval-scale-2026-10-25-c.json): the same baseline brain, its source-file hash, and all 20 replay measurements.
- [Provenance manifest](retrieval-scale-2026-10-25.json): source hashes, private harness hashes, migration history, changed retrieval file hashes, and interpretation limits.

Attempt a omitted an explicit pre-query evidence boundary. Its historical records were therefore unavailable to the query, and every recorded hit check was false. Its faster timings cannot establish successful retrieval performance. Baseline b added `evidence_cutoff={"mode":"through_sequence","through_sequence":size}` and checked returned memory statements instead of searching the entire serialized response. The original attempt remains available rather than being replaced by the corrected run.

Baseline b discarded two warmup queries at each corpus size. Replay c reused the already-warm 10,000-record fixture after migration and executed the exact 20 measured baseline queries once. It did **not** discard two additional warmup queries. The unchanged `plan.warmups=2` in c is inherited baseline metadata, not a record of replay execution; all 20 replay measurements, including the first, are included above.

## Dataset and measurement method

The baseline creates a new tenant, owner, and brain, then grows that one brain through 100, 1,000, and 10,000 observations. The source text is synthetic:

```python
rng = random.Random(20261025)
tokens = ["quartz", "garden", "lantern", "notebook", "forest",
          "bridge", "harbor", "willow", "silver", "copper"]
label = f"zeta{number:05d}"
text = f"My {rng.choice(tokens)} inventory label {label} refers to shelf {number % 17 + 1}."
```

`number` ranges from zero through 9,999. Each record is a received user message with `real_world_report` interpretation, an unknown occurrence time, source revision 1, and its numeric source-event key. All records use conversation `seed`, branch `main`. Observation goes through `MemoryService.handle`; fixture construction then directly publishes `cognition.extract_event` results and advances the brain's indexed sequence to its durable sequence. This deliberately bypasses worker throughput and invokes no cognitive or embedding provider. The `seed_seconds` fields measure incremental seeding between sizes, not total ingestion time for independent fresh databases.

At each size, baseline b executes 22 recall requests, discarding repetitions zero and one. Query identity is `zeta{(repetition * 47) % size:05d}`. Requests use `intent="cue_recovery"`, no extra cues, no cue event IDs, `default_read_fence()`, `default_budget(16000)`, and an explicit evidence cutoff through the current size. The budget is the service's conservative context estimate; no model tokenization occurs. Timing encloses the in-process public `recall` action using a monotonic clock, excluding fixture construction.

The hit predicate checks whether any returned memory statement contains the expected unique label. It does not independently assess semantic inference or establish that all relevant history was found. For the 10,000-record case, the measured labels run from `zeta00094` through `zeta00987` in steps of 47. These are 20 selected older records, not a query of every stored record or broad sampling of the entire corpus.

For a comparable new run, use a dedicated disposable PostgreSQL database with the selected revision's migrations and application grants already applied. Construct `MemoryService(Database(dedicated_dsn))` directly without configuring model providers, create a new isolated principal and brain, and follow the seeding and query procedure above. Save results to a new output path; preserve earlier failures. For a repair comparison, retain the baseline fixture and exact query order, apply the documented migration, and state whether additional warmups were run. Do not point a benchmark at a normal desktop or production database. No standalone public benchmark runner is included in this batch; the original local harnesses and their hashes are identified in the provenance manifest.

## Code and schema boundary

The baseline artifacts record commit `019ec6ad4b50d22ef309d88f199d1fab99dddf6b` with migrations through `017_cognitive_budget_configuration.sql`. Replay c used the same fixture with `018_retrieval_scope_projection.sql` and the then-uncommitted retrieval changes on that base commit. The manifest records SHA-256 values for the relevant baseline and repaired files; it does not pretend that the baseline commit already contained the repair or that these five file hashes identify every byte of the running package.

The change adds a second bounded lexical candidate route. The existing feature-hash route remains, including for Chinese cues and queries unsuitable for the added route. The new route uses PostgreSQL's `simple` full-text matching expression to require a match against at most 128 normalized ASCII query terms, each at most 256 characters, before ranking those matches by the existing hash distance. An existing full-text index does not guarantee that PostgreSQL selects an index scan for this query. PostgreSQL tokenization is not identical to the application's term extraction, so final application lexical scoring still decides whether a candidate has actual overlap. Many weak real matches can still crowd out a stronger candidate before that final scoring; retrieval remains non-exhaustive.

Each lexical route retains at most 512 candidates for recall or 256 for learning, often fewer under the selected budget. Each SQL query requests one extra row to detect truncation. The deduplicated lexical pool can therefore be twice the per-route limit. The source-evidence bridge remains a separate bounded route: at most 64 source events and at most 512 linked beliefs for recall or 256 for learning. Final root, source-dependency, and context budgets are unchanged. Return-size limits do not bound all database scan or sort work.

Migration 018 stores generated visibility, conversation, and branch projections from the assertion document. Scope filters use those projections with the same prior comparisons. Tenant, owner, brain, lifecycle, deletion, suppression, and learning sequence checks remain in both candidate queries; source and applicability checks still run afterward. Stored recall plans and frozen learning/orientation snapshots retain their original selected versions. The projection and candidate changes were measured together, so this diagnostic cannot assign their separate contributions to the observed latency difference.

## Verification and limits

The implementation owner reported seven focused retrieval regressions passing, including real hash collisions beyond the original candidate limit, generated-scope updates and deletion, retained Chinese fallback, bounded query terms, and learning's frozen source sequence. The related 92-test group also passed. This report does not assert the outcome of the final integrated suite, which was separate work when the report was written.

The runs used synthetic literal captures and no model, HTTP server, vector provider, or external delivery host. They did not measure large relation fanout, cold caches, long-running maintenance, worker throughput, realistic multilingual paraphrases, or sustained interactive conversation. A separate isolated DeepSeek experiment shared the machine, and execution order and concurrent host load were not controlled or randomized. Replay c covers only the 10,000-record case; no repaired 100- or 1,000-record performance curve is claimed. The observed timings and 20/20 labeled-cue result are diagnostic evidence, not a general speedup promise, exhaustive recall proof, or production SLA.
