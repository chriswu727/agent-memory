# Query relevance for belief neighborhoods

The `belief-neighborhood-v2` route preserves query relevance when completing an already loaded belief component. A related assertion is needed to explain a conflict or correction; that dependency does not independently establish relevance to the user's current task.

The previous route assigned each newly introduced endpoint a fixed reciprocal-rank score of `1 / 61`. Other candidate routes already scale their scores by lexical relevance and access state. A weak query match could therefore introduce a much stronger endpoint and move an unrelated conflict to the front of context admission.

The closed source-sharing campaign exposed this on its final map-photo request. Original map clauses were present in candidate-hit traces, but an unrelated shelf-plan conflict and a later map question occupied the final context. The host safely refused to invent details and failed the requested task. The original record was still current and unchanged.

## Ranking rule

Build connected components from the exact endpoint versions of the relations already loaded by the existing bounded, scoped loader. For each component, compute the largest original seed score, summing only recognized retrieval-route signals for each seed. A newly introduced endpoint inherits that score. Existing endpoint signals remain unchanged.

Components without a positive retrieval seed do not create new relevance roots. Their loaded documents remain available for evidence and dependency validation. Recency alone supplies no query relevance. Different revisions are distinct endpoints. Explicit model-selected plans do not acquire automatic roots or new signals.

Compute component scores before inserting inherited signals, so traversal order and cycles cannot amplify them. Unrelated components do not share scores. No new database scan, relation expansion, source rewrite, model call or spending allowance is introduced. Scope filtering, exact source validation, relation snapshots, applicability and atomic conflict presentation retain their existing guards.

## Evidence boundary

The [closed campaign review](../evaluations/v1-2026-10-26-shared-communication-longitudinal.json) retains seventeen passed whole-answer cases and one failed case. It is not reclassified after a repair.

A post-stop same-brain SQL diagnostic uses the original request, access timestamp and frozen brain sequence/index/generation parameters. The unchanged installed runtime reproduces the delivered failed context byte for byte. Changing only the inherited endpoint score restores the complete map scene, taped-corner detail and expressed capability within the original 7,599-byte budget: 7,366 bytes instead of the failed context's 7,364 bytes. All brain tables match after forced rollback. Extra final projection rows still exist underneath the diagnostic; this is not an exact historical database reconstruction.

The production repair has the same zero-model source-recovery result. This demonstrates context recovery, not a new host-answer pass, useful age-dependent gist formation, a cognitive-cost reduction, or complete behavioral acceptance. Those limitations remain visible in the [repair report](../evaluations/v1-2026-10-26-belief-inheritance.json).
