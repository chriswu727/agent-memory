# Local observation encoding and historical integration

Status: proposed implementation contract, 2026-10-25. The behavior described below is **not implemented**. This document records the next engineering decision; it does not change the stopped evaluation, existing jobs, or published memories.

## Evidence and failure boundary

The stopped on-demand long evaluation preserved its original requests, responses, results, and operator-stop record. At the completed `history-003` stage, the user reported attending a named primary school, a ginkgo tree beside its gate, and not having supplied the school's city. The proposal preserved all three points. Every proposed claim cited only that stage's new inputs, had no parent memory versions, and its episode proposed only those local input members.

Nevertheless, the job rejected all four proposals as `incomplete_history_package` before verification. Its sampling report contained `request_bytes_budget`, eight selected sources, eleven assertions, six omitted packages, and `publication_restricted=true`. The job became `cancelled` with `dependency_unavailable`. An offline replay using the recorded payload and response produced byte-identical proposal messages and the same rejection, without a provider call. Completed stages 003 through 009 showed the same blockage. This is a learning failure; source persistence, literal captures, and answer delivery remained available.

The relevant current boundaries are:

- [neighborhood.py](../src/agent_memory/neighborhood.py): omitting a relevant historical package sets a global publication restriction.
- [semantic.py](../src/agent_memory/semantic.py): that restriction rejects every non-situation proposal, including independently sourced new reports.
- [learning.py](../src/agent_memory/learning.py): a restricted batch cannot advance its processing window.
- [consolidation.py](../src/agent_memory/consolidation.py): every published claim and episode retains all source roots from the supplied snapshot. In the recorded run, a note with two episode members acquired four roots, and the following note acquired six.

The last rule is conservative provenance protection, not permission to drop roots. A model that saw historical context may have depended on it even when its citations omit it. Removing the global guard, deleting contextual roots after generation, or increasing the context budget alone would not establish safe independent learning.

## Recommended decision

Separate **encoding observed communication** from **integrating it with historical memory**. A source-grounded report can remain useful when a historical comparison cannot fit. Its publication must not assert that comparison has happened.

For a fresh batch, perform deterministic packaging before the first provider reservation:

1. If the existing historical package is complete within its declared bounded scope, retain the full organization path and its existing proposal/verification budget.
2. If only optional historical packages are incomplete, select a new `local-observation-v1` proposal mode. Rebuild its model input from the exact required local source closure. Do not reuse a proposal made with incomplete history.
3. If the required local closure is unavailable, invalid, or too large even after the normal finite input-window split, block the batch without a model call. A historical omission and a missing required source are different cases.

The new mode is a new frozen contract, not a flag that overrides `publication_restricted` on the old mode. The old guard remains unchanged. Existing public per-brain budgets remain authoritative. Fresh jobs declare their finite phase allocation before any call; neither a phase transition nor a retry resets cumulative usage.

The minimal durable implementation reuses `memory_jobs` with `kind='consolidate'`. It introduces an explicit phase discriminator, a shared authorization root, and a durable integration job; it does not introduce another queue or a new generic workflow engine. Integration is automatic when its complete evidence package and declared allocation are available. Normal use must not require the user to manage learning jobs.

## Finite autonomous allocation

Recommend a new, explicit `encoding-integration-budget-v1` policy for fresh batches: at most two calls and 196,608 input bytes for encoding, plus at most one integration proposal/verification pair with the same limits. The shared cumulative ceiling is therefore four calls and 393,216 input bytes for one finite encoding batch and its authorized integration phase. Each phase is further limited by its frozen `SemanticPolicy`; the configured per-brain rolling budget applies to every reservation across both phases.

This is an engineering choice to permit up to twice the old per-batch ceiling, not a claim that the cost is unchanged. The complete-history path still needs at most its existing two calls and creates no second paid phase. The extra pair is available only to the declared local-encoding/integration path. It is a ceiling, not a requirement to spend: absent complete useful comparisons, no call occurs. Per-brain defaults and user-configured rolling limits do not increase. A lower phase policy can only reduce this allocation.

Freeze the allocation on the encoding root before its first reservation. Descendant phase jobs reference the same immutable authorization root. Use the existing operation ledger as the charge source of truth: within a transaction, lock the authorization root, check all descendant reservations against its shared ceiling, then apply the phase and per-brain limits and insert the reservation. Add a typed root reference/index if needed for this bounded lookup; do not introduce an independent usage ledger. Concurrent workers cannot each spend the same remaining pair. Unknown and unsuccessful reserved operations remain charged.

An ordinary restart or reconsideration cannot mint another allocation. A genuinely new independent input batch receives its own declared allocation and may use its one integration pair for a complete package that also advances a relevant older unresolved obligation. That assignment is frozen before reservation, each objective retains its semantic identity, and no one operation may be charged to multiple roots or claimed by multiple workers. Already assessed or failed/uncertain objectives are not automatically retried under a newer root. A configuration or budget revision can unblock existing remaining allocation; it does not replenish an exhausted root.

With only the old two-call cap, successful local proposal and verification leave no capacity for autonomous integration. Keeping that cap while promising automatic integration would be misleading. The four-call ceiling is the recommended bounded tradeoff; manual-only integration is rejected as inconsistent with the autonomous-memory product.

## Restricted local encoding contract

The local request contains the exact `learning_input_refs`, their validated source closure, the operation scope, and the fixed contract metadata. Historical assertions, historical episodes, belief relations, consolidation objectives, and historical working-state text are absent. Empty context must be labeled as intentionally withheld, never evidence that no history exists. Source records remain untrusted data.

Required closure includes source ancestry necessary to establish origin, delivery state, revision, and allowed scope. Every source hash and exact revision is checked before reservation and again before publication. Source corrections, superseded evidence, deletion, suppression, quarantine, and ownership checks continue to apply. A source selected merely because it overlaps a cue is optional history, not required local closure.

Use a separate restricted proposal schema, with these runtime-enforced limits:

- Claims describe what a bound speaker reported or what the assistant actually delivered. Restrict them to `memory_kind='episodic'`, `representation='episode_fragment'`, and `claim_kind='reported_fact'` or `agent_said`; they do not establish an external event or an effective standing preference.
- Claims cite complete authorized utterance segments from the exact new inputs. `source_memory_versions` is empty, `procedure` is null, conditions are `always`, and applicability is unknown. Temporal details may remain inside the attributed report; they do not become executable validity or an override of history.
- Every supplied local qualifier, contradiction, correction, negation, uncertainty, and hypothetical scope remains available to independent verification. Selecting a supportive segment cannot hide a conflicting segment of the same supplied records.
- No belief links, schemas, procedures, situation updates, success claims, historical absence claims, supersession, exception resolution, or inferred user intentions are accepted. A claim that a user reported a preference is not published as an instruction to apply that preference now.
- Episode actions may only create provisional local interaction groups from authorized new members. They cannot continue, reopen, close, or split an existing episode, claim that no earlier group exists, or claim an external event completed. Existing member ownership must prevent duplicate creation on replay.

These are schema and provenance boundaries plus fallible semantic verification, not a deterministic proof of natural-language meaning. Retrieval must retain the attributed-report distinction. This mode must not promote a report into current action guidance simply because historical integration is pending.

All sources actually shown to this restricted model remain publication roots. The gain comes from not showing unrelated historical sources in the first place. Historical source roots from old publications are never retroactively removed.

## Durable phase and state contract

New jobs persist `learning_contract_version='encoding-integration-v1'`, `learning_phase='encoding'` or `history_integration`, and the shared `authorization_root_job_id`. The snapshot additionally freezes the chosen proposal mode, semantic policy, input contract version, exact source versions, source-set fingerprint, and history cutoff. Invalid present metadata fails closed; missing metadata retains legacy behavior.

When restricted encoding publishes successfully, the **same database transaction** must:

1. Publish the verified local outputs and settle the encoding batch's processing result.
2. Insert one integration job for exactly the attempted input range and exact input version set, bound to the already declared remaining allocation.
3. Store its identifier in the encoding result as `history_integration_job_id`. Its initial state is `queued` only when a complete useful package and capacity are ready; otherwise it is `deferred`.
4. Advance the encoding window and create an ordinary encoding continuation when the original range has a remaining tail.

If any step fails, none of these changes commits. A deterministic integration identifier derived from the encoding job, attempted range, and source-set fingerprint makes retries idempotent. An integration obligation covers one finite encoding batch, not the entire ever-growing conversation.

The integration job has the following proposed metadata. Identifiers and fingerprints are private bounded metadata, not copied conversation text:

```json
{
  "learning_contract_version": "encoding-integration-v1",
  "learning_phase": "history_integration",
  "encoding_parent_job_id": "consolidate:<encoding-id>",
  "authorization_root_job_id": "consolidate:<allocation-root-id>",
  "from_sequence": 10,
  "through_sequence": 12,
  "encoding_input_fingerprint": "<sha256-of-canonical-exact-input-versions>",
  "history_cutoff_sequence": 12
}
```

Add `deferred` as an explicit scheduler state for a known obligation that is not currently claimable. Do not overload `cancelled`, which currently implies terminal/recovery handling. A deferred integration job carries the fixed reason `history_integration_deferred` plus a safe category: `package_incomplete`, `shared_allocation_exhausted`, or `brain_budget_unavailable`. Its safe result records `processing_status='not_evaluated'`; the exact strict shape must be added to the job sanitizer rather than passed through arbitrary JSON. Every state allowlist, claim query, monitor, recovery path, and backup/quarantine path must handle the new state explicitly.

Deterministic reconsideration can move `deferred` to `queued` under a row lock only when source identities, complete packaging, objective ownership, and remaining allocation are validated. A provider failure or unknown paid outcome moves into existing failure/recovery handling, never back to an automatically recyclable deferred state.

`encoding_parent_job_id` is not `continuation_parent_job_id` or `recovery_parent_job_id`. Integration does not delegate or replace encoding coverage. Reusing those existing links would incorrectly let the monitor erase one obligation when the other finishes.

| Encoding outcome | Historical integration outcome | Durable meaning |
| --- | --- | --- |
| Full bounded organization assessed | Assessed in the same batch | Existing bounded processing completed; published counts may still be zero. |
| Restricted local outputs assessed | Queued or deferred integration job exists | Local encoding completed; historical integration remains open until its bounded work is assessed. |
| Restricted proposals rejected semantically | Queued or deferred integration job exists | The inputs were assessed locally, no local output is implied, and historical integration remains open. |
| Required local closure or provider operation failed | Not yet eligible | Encoding remains blocked; do not create a false successful encoding record. |
| Integration later assessed without accepted changes | Assessed with explicit no-change outcome | The bounded comparison was assessed; this is not evidence that a new memory was published. |
| Integration lease/provider/budget/dependency failed | Deferred or blocked | Preserve its remaining work and all cumulative usage. |

A malformed restricted proposal is not an assessment of its intended meaning. Existing distinctions between malformed output, semantic rejection, unknown provider outcome, and accepted publication remain intact.

## Visibility and monitoring

Keep the existing counts explicitly defined as job/window counts, never message counts or a learned watermark. Both encoding jobs and integration jobs remain visible. A completed encoding parent plus a deferred integration child must still produce open blocked work; the child is not deduplicated against its encoding parent.

Add a strict `learning_phases` summary to `learning_progress`, with `version='encoding-integration-v1'` and these fixed counters:

- `encoding_open_count` and `encoding_assessed_count`.
- `history_integration_open_count`, `history_integration_deferred_count`, `history_integration_running_count`, and `history_integration_assessed_count`.
- `legacy_unclassified_count`, `unknown_count`, and `counts_complete`.

Assessed counts describe retained historical results, not present source validity or truth. Deferred counts are a subset of open integration obligations. Authorized queued/waiting work remains open; running is a separate subset. Every logical integration root is counted once across its valid retry/continuation lineage. Missing children, cross-scope references, duplicate ownership, cycles, or invalid phase metadata make counts incomplete rather than disappearing from the totals. Legacy jobs are not inferred to have completed a newly introduced phase.

Expose `history_integration_state`, its exact linked job ID, and fixed reason in the sanitized encoding job result. Exact-job CLI/chat monitoring can then report “local encoding assessed; historical integration deferred” without a history scan or a provider call. The linked job is authoritative after a retry; the parent's original result remains a historical record. The exact-job API resolves that one validated link when producing current safe status.

`semantic_outcomes` continues to count assessments and publications separately. Add the phase dimension; do not rename a processed range to “remembered,” count a deferred placeholder as evaluated, or conceal zero accepted proposals. Oldest pending examples sort by original input sequence and retain the original obligation age across retries. Public summaries contain no source bodies, targets, exact source IDs, or provider error text.

## Later bounded historical integration

The worker performs the ready integration phase automatically under the fresh root's declared allocation. `retry-learning` is reserved for explicit recovery after failed/unknown paid operations or an intentional extra authorization; it is not a normal prerequisite for integrating every overflowed input. Its existing request ID, expected fence, scope checks, replay identity, and finite authorization semantics remain in force. A retry child must preserve its phase and cannot silently become another local encoding batch.

Reconsider deferred obligations cheaply on a new independent input, an authoritative source/dependency change, or a configuration/budget revision. Persist a canonical trigger fingerprint and the last reconsidered version per obligation; duplicate event delivery and process restarts do not repeat work or grant calls. Drain a bounded number of obligations per trigger, oldest relevant first, with persisted continuation for the finite scan. There is no timer-driven paid loop. A scheduled wake at the known rolling-budget availability boundary may recheck capacity once for the same trigger; it does not renew the allocation or repeat an attempted provider operation.

Reconsideration is SQL/validation/packaging work, not model work. It queues a job only after a complete package fits an existing unspent integration allocation. If capacity or required evidence is still unavailable, retain the deferred reason and age. Unrelated new inputs cannot transfer unlimited budget to a repeatedly failing objective; at most one pair from each new batch can be assigned, and an objective with a paid failed/unknown attempt requires explicit reconciliation rather than fresh automatic assignment.

Before any integration provider reservation, build and persist a finite comparison manifest:

1. Bind the original input versions and an explicit cutoff for this attempt. A later independent trigger may select a newer durable cutoff in a new frozen plan; it preserves the original encoding cutoff as provenance and cannot mutate a reserved plan. Include known contradiction/counterexample edges and complete source/parent closures for each selected comparison objective. The manifest distinguishes candidate discovery limits from missing required evidence.
2. Partition independent objectives into bounded packages. A package is complete for its objective only when all of its declared endpoints, supporting sources, counterevidence, and relevant relation closures are present and valid. A relation cannot be divided so that its contradictory endpoint is omitted from the model's view.
3. Freeze the objective IDs, source identities, policy, strategy version, and remaining manifest before calling the model. Preserve current objective semantic identities and assessed-objective deduplication; changing ranking or encoding versions does not authorize reassessing the same pair.
4. Run proposal and independent verification only for a complete package. Supported operations remain local to those supplied endpoints. No package may infer global absence, universal validity, or a historical winner from omitted candidates.
5. Atomically publish, mark the assessed objectives, and persist the remaining finite manifest. A continuation can use only the root's remaining declared allocation. The one integration pair cannot turn into a new pair per objective or per continuation. After that pair is used, unresolved independent objectives stay deferred until a future independent batch assigns its own finite integration phase or an operator explicitly authorizes recovery. New context requires a new frozen plan identity; it never mutates a previously reserved request.

If even one required objective closure does not fit, retain that objective as deferred with a fixed budget/dependency reason and make no call for it. Other independent complete objectives may progress; they cannot close the incomplete objective. If discovery itself was truncated or some intended comparisons could not be enumerated, retain an explicit unresolved discovery marker. It must not become an empty manifest interpreted as complete.

When every enumerated objective is assessed and no discovery marker remains, label the obligation `assessed_bounded_manifest`. This means the declared finite work was assessed, not that the whole brain's history was searched exhaustively. A legitimate empty manifest must retain a valid completed discovery assessment; missing or malformed manifests are unknown. Publish counts and no-change counts remain separate.

This resolves the current budget dead end through bounded comparison packages, rather than increasing an input limit until the entire history happens to fit. The first implementation must demonstrate automatic local encoding followed by later integration when complete evidence and capacity become available, with no user job-management step. Shipping only the local fallback and a permanently inert label is incomplete.

## Source changes, recovery, and compatibility

Validate exact input/source identities before every reservation and publication. Correcting a source, deleting it, changing an authoritative scope, or restoring into quarantine cannot reuse an old proposal, verification, or manifest. Preserve the failed attempt and its usage. Any replacement must have a new identity bound to the new source set and a link to the superseded obligation.

Source correction can create a replacement deferred obligation under existing invalidation handling. It may use remaining unspent allocation, or a genuinely new correction input's declared integration phase, only with a newly frozen source identity before reservation. It cannot reset spent or uncertain allocation. If all required sources have been erased, retire the obligation as source unavailable, not semantically assessed; apply existing erasure/redaction rules to its stored metadata and results. No recovery path may reconstruct deleted text from a prior model response.

Keep old snapshot semantics exact. Snapshots with no phase/version fields continue to use their original general mode, global publication guard, policy behavior, and retry contract. Existing failed jobs are not migrated into successful local encoding. A user can explicitly authorize a fresh recovery child using the new contract; its old parent's usage and failure remain visible. Frozen old proposal/schema bytes, request identities, policy snapshots, and comparison identities must remain reproducible across the change.

## Independent episode fidelity contract

The recorded episode descriptions also expose a separate semantic defect: “cannot confirm it was saved” became “stated it was not saved,” and “study/review arrangement” became “exam preparation.” Both an authentic source and a positive verifier verdict can coexist with that error.

Introduce `episode-fidelity-v1` independently of the local/history phase choice. It inherits the communication-intent and episode-operation-scope rules for new snapshots. Keep all older input-contract branches and serialized requests unchanged.

The new proposal instructions explicitly preserve the scope of attribution, negation, and uncertainty and prohibit an unsupported more-specific domain. The new verification schema adds an `organization_fidelity` object only to episode assessments, with fixed `attribution`, `modality`, `specificity`, and `boundary` verdicts. Each verdict is `supports`, `contradicts`, or `unknown`. Acceptance requires all four to support, in addition to the existing whole-proposal and numbered-evidence checks. Missing or malformed required verdicts fail closed. Verification cannot repair the proposal text.

Persist these assessments in the existing verification ledger. Do not relabel old episodes as having passed the new contract. This extra structure makes the acceptance obligation explicit; it remains model assessment, not a truth certificate. Independent blinded provider acceptance is required before claiming that it improves real semantic quality.

## Acceptance mapping and delivery boundary

| ID | Deterministic acceptance and required observation |
| --- | --- |
| LO-01 | Replay the recorded school input under a crowded optional history. The fresh restricted request contains complete local input and no historical text; faithful reports publish and a durable deferred integration obligation remains. |
| LO-02 | Omitted old counterevidence says the opposite of a new report. Preserve only that the new speaker reported it; reject supersession, current action guidance, historical absence, and inferred completion. |
| LO-03 | A required source/ancestor is missing, stale, cross-scope, suppressed, or over budget. No local publication or paid call; preserve a visible encoding failure. |
| LO-04 | Conflicting clauses within the same new inputs cannot be dropped by selecting a supportive citation. Unsupported combinations fail independent verification. |
| LO-05 | Crash before/after publication, linked-job insertion, window advancement, and response return. Encoding output and deferred obligation commit atomically and replay once. |
| LO-06 | Eight-input batching and finite continuation preserve exact ranges; encoding and integration never cover one another's progress. Sequence span is not interpreted as message count. |
| HI-01 | A fresh restricted encoding batch automatically runs one ready integration pair, without operator action. Restart preserves the exact request, manifest, policy, shared allocation, and cumulative ledger. |
| HI-02 | Two independent complete objectives plus one oversized contradiction closure. Progress the two within authorization; retain the third and its unresolved state without partial evidence or extra calls. |
| HI-03 | Discovery truncation, missing manifest, missing child, duplicate owner, wrong scope, and cycles remain visible as incomplete/unknown. A latest-200 cutoff cannot hide old debt. |
| HI-04 | No accepted changes is an assessed no-change outcome; malformed output, provider timeout, uncertain reservation, and expired lease do not become assessments or trigger a new authorization. |
| HI-05 | A correction/deletion between packaging, reservation, and publication prevents stale publication, preserves charges, and cannot resurrect prior output. Test restored/quarantined state as well. |
| HI-06 | Exact retry authorization is idempotent. A changed source fingerprint or objective set requires a distinct validated attempt; an old request ID cannot bind a new input. |
| HI-07 | A deferred obligation becomes ready after an independent input, source change, or budget revision. Deduplicated cheap reconsideration queues it once; no user job-management step or timer-paid loop occurs. |
| HI-08 | Concurrent phase workers and multiple continuations cannot exceed four calls/393,216 input bytes under one fresh allocation, or the smaller phase/brain limits. Failed and unknown reservations remain charged; a new input cannot automatically retry an uncertain objective. |
| MON-01 | Completed encoding plus deferred integration yields open blocked work and an exact-job warning. Completed bounded integration resolves only its own obligation; publication and assessment totals remain distinct. |
| COMP-01 | Golden old requests for absent, episode-operation-scope-v1, and communication-intent-v1 input versions are byte-identical. Old policy snapshots and assessed comparison IDs retain behavior. |
| EF-01 | Unknown saved status cannot become a negative saved fact; explicit negative remains negative. Study does not become exam preparation; unspecified city/date/cause stays unspecified. |
| EF-02 | Each missing, unknown, or contradicted fidelity facet independently vetoes an episode. Correct attributed uncertainty and supported boundaries can pass. |
| LIVE-01 | After deterministic and database lifecycle checks pass, run a separately frozen, bounded real-provider acceptance through public configuration. Independently review outputs; keep the stopped old evaluation unchanged and report call/usage limits and every failure. |

Implementation ownership should remain narrow: semantic mode/fidelity in `semantic.py` and `episode_semantics.py`; pre-call selection in `neighborhood.py` and `learning.py`; phase-aware obligations/reconsideration/recovery in `learning_windows.py` and `learning_recovery.py`; shared reservation enforcement in `jobs.py`; publication/invalidation in `consolidation.py`; safe status in `learning_progress.py`, API/client/SDK and exact-job desktop monitoring. The scheduler-state migration, queue, API, monitor, and invalidation tests are part of the same delivery boundary as local encoding.

## Alternatives considered

Removing the global restriction is unsafe because the existing model has already seen incomplete historical context. Dropping uncited contextual roots after generation weakens the existing provenance boundary. Raising context limits delays the failure and increases cost without fixing indefinite growth. A lexical threshold cannot prove that omitted material is irrelevant or non-contradictory. Treating source-only encoding as fully learned hides unfinished integration. Automatically retrying old frozen jobs or silently minting integration budgets violates the current spending and uncertainty contracts. Making every normal overflow require a manual retry is also rejected: the configured brain budget already authorizes bounded autonomous work, and the new finite phase policy should use that authorization transparently.

A new dedicated obligation table could make aggregation more direct, but would duplicate queue state, leases, recovery, and erasure handling. Reusing scoped jobs with a strict phase contract is the recommended first implementation. A future migration is justified only if measured query or lifecycle complexity requires it; the public phase semantics should not depend on that storage choice.
