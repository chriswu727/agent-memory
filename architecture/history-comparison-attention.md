# Bounded attention for historical comparison

Historical retrieval and paid historical comparison have different purposes. Retrieval keeps a broad set of possible leads, including source-span bridges and weak character matches. The `bounded-comparison-attention-v1` policy admits at most **three comparison objectives per fresh learning input** from that bounded candidate set. It does not delete memories, narrow ordinary recall, or declare the other candidates irrelevant.

The policy is server-issued as `history_attention_policy` on new ordinary encoding roots. A root without that marker retains the previous discovery and continuation behavior. Existing manifests, outstanding obligations, operation reservations, and accounting are not rewritten when the software is upgraded. An explicit recovery or ranking change does not authorize comparison of the same input and endpoint again under a new objective identity.

## Selection

The existing selector still retrieves and ranks candidates with its lexical and source-span routes, scope checks, and historical cutoff. Attention operates after that bounded collection; it does not scan the complete database. The candidate audit contains at most 128 memory versions, matching the maximum assertion collection policy.

Selection has two focus slots and one exploration slot:

1. When current received user or operator input exists, its words and Chinese bigrams determine focus eligibility and ranking. Otherwise the current input sources supply the focus cue. Assistant acknowledgements remain in the exact input identity and evidence context; matches introduced only by an acknowledgement can still enter exploration. A historical assistant promise can be a focus candidate when the user's cue mentions it.
2. Ranking discounts terms repeated across distinct supporting source versions. Literal and semantic representations of one source do not count as additional independent occurrences. Whole-word matches receive more weight than individual bigrams. Selection favors a previously unused evidence contribution and then a different source origin before using the relevance score and a stable identity tie-break.
3. One remaining candidate can enter exploration. Its deterministic order is seeded by the complete current input fingerprint and cutoff. A fresh input changes that order; restarting or reconsidering the same frozen input does not. Character-only matches and assertions reached through source-span bridges remain eligible here.

Evidence diversity is a selection preference, not semantic deduplication. Two claims citing the same source or span can express different things. Their distinct memory versions remain available, retain separate objective identities, and may both be admitted. Neither an attention score nor a common source proves equivalence, truth, or irrelevance.

The scores are inexpensive heuristics. Repeated notebook formats can still outrank a meaningful but weakly connected memory, and exploration can select a routine acknowledgement. The fixed limit bounds work and improves diversity; it does not prove that each chosen comparison is useful. A useful opportunity can be missed. Ordinary recall remains able to retrieve that memory, and later fresh inputs can create different bounded opportunities.

## Obligations and coverage

Admission creates an obligation for each selected exact endpoint. Its source closure, dependency parents, exceptions, revisions, hashes, scope, and cutoff must still validate in full. If the complete package does not fit, the admitted objective remains deferred. Attention never trims its evidence to force a call through.

Candidates outside this admission are recorded as **not selected and not evaluated**. They are not deferred jobs, failed comparisons, or model-declared `no_change` results. The policy authorizes one bounded selection, so an empty remainder does not trigger another search that turns every omitted candidate into a new obligation. This avoids a queue that grows automatically with every weak lexical match.

For attention-bearing manifests, `unresolved_discovery=false` means that this authorization does not require another automatic discovery pass. It does **not** mean that history was searched exhaustively. The separate attention metadata always reports `search_exhaustive=false`; `retrieval_truncated` and `discovery_limitations` disclose known limits on the broader candidate search. Known job counts can be complete while broader historical coverage remains unknown.

The existing finite model-call and byte budgets still apply. Three objectives can share one proposal/verification package; they are not three new call allowances. Failed or uncertain provider operations retain their original recovery rules. Unused capacity is not permission to refill the three slots, replay a failed comparison, or turn archived nonselections into paid work.

Package construction reuses successful dependency-node expansions within one bounded integration selection. The key includes the exact memory version and full document hash. Overlapping targets still receive their complete parent, exception, relation and source closure, but a previously expanded identical node does not repeat its database-backed expansion. Failed node expansions are not cached. The reuse map is discarded when selection returns, so later requests recheck source changes, deletion, scope and cutoff. Each objective still performs its own endpoint-hash, complete-closure and actual-request-budget checks; rejected objectives do not contribute their extra sources to the selected package. This optimization reduces local planning work without changing model input, admitted comparisons or call allowances.

## Audit and monitoring

The private manifest preserves a bounded admission audit: exact candidate memory versions and hashes, evidence and origin fingerprints, integer scores, the deterministic selection seed, and each candidate's `focus`, `exploration`, or `not_selected` basis. It contains no copied source text. Original comparison IDs remain bound to the input fingerprint and exact endpoint, independently of the ranking policy.

Provider requests receive the exact admitted objectives and complete required evidence, plus only a compact attention summary. The unselected endpoint list, private fingerprints, scores, and selection seed are excluded from model requests and public monitoring.

The safe summary exposes the policy version, limit, candidate/admitted/not-selected counts, focus/exploration counts, source-origin count, and broader-search limitations. These are counts for the **original admission**, not counts of the currently pending subset. A selected package and its deferred remainder preserve the same audit; consumers must not sum that repeated summary across continuation jobs.

Legacy manifests have no attention envelope. Their wire representation, larger outstanding comparison ranges, and discovery refresh behavior remain unchanged. Unknown or malformed attention metadata fails validation instead of falling back to legacy behavior.

## Verification boundary

Focused tests exercise rare English and Chinese cues, meaningful corrections, user-mentioned assistant promises, assistant-added template matches, character/source-bridge exploration, multiple claims sharing exact evidence, 100 templated origins, deterministic replay, malformed audits, and private/public projection. Database tests additionally exercise complete dependency closure, preservation of an admitted incomplete package, and a delivered lifecycle using the normal selector and background worker.

These checks establish selection, accounting, provenance, and recovery behavior. They do not establish human-level salience, a semantic accuracy improvement, or exhaustive long-term consolidation. Real-provider semantic evaluation remains a separate acceptance requirement.

The package-reuse regression compares uncached and cached traversal against the same real database graph. Three overlapping targets require seven node expansions without reuse and three with reuse, with identical selected material and manifests for legacy exception edges, sourced belief relations, and both together. Separate checks retain whole-objective deferral under a source limit and remove a deleted exception from a subsequent selection while preserving independently supported facts. These are bounded local-work measurements, not token savings or a long-running latency benchmark.
