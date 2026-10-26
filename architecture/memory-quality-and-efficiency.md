# Memory quality and computational efficiency

The primary objective is useful continuity: an agent can recover a relevant experience from a cue, carry forward important preferences, distinguish similar episodes, revise its interpretation when evidence changes, and acknowledge uncertainty. Token use, local computation, storage growth and reply latency constrain that objective. A smaller context or a cheaper model call is not an improvement if it loses a correction, invents an experience, or makes the user teach the same preference again.

This document separates implemented repairs from proposed experiments. Human memory provides functional inspiration, not a claim that this engine reproduces a biological brain. Personality and emotion remain outside the current release.

## Measured starting point

The preserved [v5 campaign](../evaluations/v1-2026-10-25-longitudinal-v5-stopped.md) stopped after seven delivered answers. Reconciliation of its provider ledger with journal roles gives 33,751 tokens for seven host calls and 296,761 tokens for eighteen cognition calls. Three simulated-user calls used a further 2,353 tokens; those are evaluation overhead, excluded from the product total of 330,512. The arithmetic mean is 47,216 tokens per delivered answer, with about 90% spent on cognition. Cognition output includes 73,120 reported reasoning tokens.

These are measurements of that earlier configuration and incomplete campaign, not a steady-state estimate for the current version. Cache-hit input tokens remain counted. Token counts are not monetary charges. There is no completed, matched-quality comparison with full history or rolling summaries. The [baseline evidence](../evaluations/v1-2026-10-25-memory-efficiency-baseline.json) preserves those qualifications and the original ledger hashes.

Local lexical retrieval and deterministic packaging require no provider call. Their costs are database work, CPU, RAM and the context they later send to a model. Cognitive learning and verification must therefore be accounted for separately from retrieval and final generation. The existing cognitive usage endpoint is not a complete host bill: host generation, optional answer review and independent recall-controller calls have their own metering boundaries.

## Representation boundaries

Keep four concepts distinct:

1. **Observed sources.** Versioned text, speaker, delivery state, time, scope and evidence spans. Correction and deletion remain authoritative.
2. **Interpretations.** Source-supported beliefs, episode boundaries, relationships and their applicability. New interpretations preserve their predecessors and counterevidence rather than silently rewriting the past.
3. **Access state.** Retrieval cues, importance, stability, spacing, competition and use. Easier retrieval is not stronger proof. Lower accessibility does not mean automatic source deletion.
4. **Future appraisals.** A later personality/emotion module may attach a versioned interpretation of significance or an affective response to an exact source or memory version. A model-generated appraisal must remain distinguishable from an emotion reported by the user.

No appraisal schema is added yet. Before implementing one, define whose appraisal it is, the personality/state version that produced it, its evidence and temporal scope, and how correction, deletion and reinterpretation propagate. Subjective salience may influence attention or expression, but cannot increase epistemic authority, override access scope or revive erased material. A changed present appraisal should not rewrite a historically recorded reaction.

## Immediate engineering changes

The current batch targets redundant computation and retrieval distortion before changing the learning objective:

- Reuse an exact historical node expansion within one locked integration-package selection. Each selected objective still receives its complete source/dependency closure. Reuse ends with that selection; later work rechecks corrections and deletion. A three-target database regression reduces seven node expansions to three with identical selected and deferred packages.
- Exclude every direct seed from source-neighborhood expansion, including seeds beyond the expansion cap. A candidate cannot gain association credit merely by being found again as its own neighbor.
- Rank source support by local passage overlap rather than pooling weak words scattered across a broad citation. This changes relevance scoring, not the stored statement, its conditions, or evidence authority. Long-distance relationships remain the responsibility of the separate relation and episode routes.
- Index adjacent-event lookup by its actual conversation and branch boundary, while retaining hidden raw events as barriers. Filtering hidden records before choosing the nearest event would incorrectly bridge over them.
- Experiment with shared complete source records across verification citations under an explicit input contract. Keep every source once and preserve exact citation indices, spans, excerpts and roles. Older frozen requests retain their original format. The representation is lossless, but a paired real-model diagnostic exposed a citation-fidelity concern; fresh jobs retain the existing provenance contract.

The [evaluation report](../evaluations/v1-2026-10-25-memory-efficiency.md) records which changes passed verification and why the shared-source format was not promoted to default. Neither the storage/planning changes nor a reduction in serialized bytes establishes an end-to-end token improvement.

## Research-guided next experiments

The following are proposed mechanisms, not delivered capabilities or borrowed benchmark claims.

**Consolidate selectively.** [Go-CLS](https://pmc.ncbi.nlm.nih.gov/articles/PMC10400413/) distinguishes memorizing particular instances from learning predictable shared structure and finds that indiscriminate consolidation can impair generalization in its modeled setting. Our engineering inference is to retain distinctive episodes and exceptions while comparing supported common structure. A changed evidence fingerprint can justify reconsideration; an unchanged reminder should not restart the same expensive comparison.

**Batch coherent experience.** [LightMem](https://arxiv.org/html/2510.18866v4) studies topic-aware short-term grouping and deferred long-term updates. We should compare event-coherent batches against the existing bounded input windows, using source capture for immediate availability and durable cursors for later learning. Negation, conditions, corrections and unfinished work must survive batching. Its precompression method and reported savings cannot be adopted without testing our evidence obligations.

**Recover detail progressively.** The [Hippocam preprint](https://arxiv.org/html/2610.12124) offers intent boundaries and hierarchical access to preserved messages. Completion, resumption and context pressure are candidate triggers for our scheduler. Any experiment must expose pending learning and allow a cue to reopen the actual source. Adding an auxiliary model before every inference would need separate justification; hierarchy alone does not guarantee lower total cost.

**Use bounded associations with a direct fallback.** [HippoRAG 2](https://arxiv.org/html/2502.14802v2) combines direct passages and graph-based retrieval, including Personalized PageRank. A suitable experiment is a bounded graph route alongside direct lexical/vector candidates, with independently measured recall and interference. It should not replace direct retrieval, invent edges from simple adjacency, or treat a graph path as proof. Approximate vector indexing likewise needs a recall comparison before replacing the current exact candidate path.

**Compare sparse and dense context policies.** The repaired frozen packet still includes unrelated memories. Ranking alone may not remove low-value filler when the budget can hold it. Test a relevance/diversity admission policy against simple budget filling, including short replies whose meaning depends on an adjacent source. Calibrate on held-out episodes and paraphrases; a threshold fitted to one failed example is insufficient.

## Acceptance and stopping criteria

Evaluate complete paths: capture, background learning, durable storage, cue retrieval, context preparation and actual delivered answer. Count all provider inputs, outputs and reasoning tokens; separate simulated-user and scoring overhead. Preserve unknown usage and interrupted work rather than recording them as free.

Use the same source timeline and equal behavioral requirements for the current engine, each candidate algorithm, full-history baseline and rolling-summary baseline. Include ordinary ongoing tasks, changing preferences, temporary exceptions, interruptions, delayed returns, distractors, cue-assisted recovery, corrections and deletion. Preserve complete failures; do not replace the longitudinal gate with repeated short quizzes.

Quality comes first: no unsupported published memory or disclosure; necessary qualifications and corrections remain; important task continuity and cue-assisted recall do not regress. Then compare total provider tokens, useful recalled material per context budget, local p50/p95, database rows examined, learning backlog and storage/index growth. Report simulated time separately from actual elapsed time. Keep an optimization only when its measured benefit survives these constraints. Stop the experiment at its frozen budget or failure boundary; do not retry until a diagnosis justifies a separate change.
