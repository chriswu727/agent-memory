# Source interpretation and recall repairs

The [varied longitudinal diagnostic](../evaluations/v1-2026-10-26-longitudinal-round.md) exposed valid declarative source rejection, missed meaningful recall, assistant-origin contamination and expensive processing. This batch repairs specific mechanisms; it does not establish biological equivalence or complete longitudinal acceptance.

Apply this candidate through the normal runtime upgrade and service restart. It introduces no database migration, re-embedding requirement or brain reset; existing source and index rows remain in place, and started cognitive jobs retain their frozen contracts.

## Source interpretation without rewriting history

Newly captured events explicitly record `segmentation_rule_version=literal-episodes-v3`. Sentence boundaries retain the v2 behavior; question-word matching no longer treats every embedded English `when`, `where` or `how` as an inquiry. Actual questions and their presuppositions remain ineligible as factual premises. This is a bounded heuristic, not a complete natural-language parser.

Unversioned events retain v1/v2 literal interpretation and capture identities. Known versioned events retain their declared version. Existing source content, source hashes, literal statements and frozen cognitive requests are not rewritten. Unknown versions fail validation.

New cognitive jobs use `source-act-boundary-v1`, inheriting the existing source, citation, independent verification and publication gates. Only automatically classified legacy acts receive a current source-act interpretation; explicit restrictive source annotations remain restrictive. The new interpretation preserves original source-unit spans, including older list boundaries. Changed interpretations appear as source-bound `source_act_views` in both proposal and verification inputs. Relation capability and publication validation use the same selected contract. Older frozen contracts retain their original eligibility checks and wire bytes for unchanged old inputs.

For a literal retrieved from an older source, a changed interpretation can also appear as `source_act` in the recall item and prepared model context. This explains a legacy `(inquiry)` label without changing the stored literal. It identifies a communication act, not independent proof that the reported event occurred. Interpretation does not bypass independent model verification.

The model continues to select semantic memories and representations. The new contract asks it to separate asserted facts/preferences from adjacent questions, retain user-expressed meaning and qualifications, and summarize assistant speech concisely when exact wording is unnecessary. This is not a hand-written importance classifier.

## Bounded recall and attribution

English contractions no longer produce search terms such as `m`, `ve`, `don` or `t`; negation remains available. Within the already scoped bounded candidate corpus, inverse document frequency weights distinctive terms more strongly than repeated conversational phrasing. Repeated identical term sets do not multiply the frequency count. The scorer is request-local and introduces no model/embedding call or cross-principal cache. Frequency weighting is a ranking signal, not a probability or truth assessment; bounded retrieval remains non-exhaustive.

A matched literal can bring its immediately preceding and following source units into consideration. This uses at most four seed memories/four source events and 32 inspected exact adjacent capture rows. It checks the source version/hash/span, scope, sequence cutoff, lifecycle and superseded evidence. It follows no cross-event chain and does not manufacture a semantic summary. The complete original source remains available independently of which units fit the context budget.

Assistant-only statements and speech summaries are excluded from ordinary personal recollection before the candidate limit is consumed. Explicit requests about assistant speech, exact quotes, episode records and open commitments can still retrieve them. Their context label is `assistant_speech_not_user_experience`; delivery and semantic attribution do not make their contents evidence of the user's life or external outcomes.

Unindexed tail candidates use a rank scale comparable to fused indexed candidates. They no longer gain priority merely by combining a raw lexical score with an additive activation score. They use the same access-age weighting as indexed candidates; explicit cue recovery keeps its existing age-independent ranking behavior. This changes accessibility, not the source's truth, validity interval or stored timestamp.

## Database query behavior

The episode-neighbor query first materializes the matched episode set and then expands its members. The previous single join let PostgreSQL repeatedly nest small scoped sets under severely underestimated cardinalities. An actual retained-snapshot query exceeded a ten-second diagnostic limit. Explicit scope predicates alone produced the same plan and did not fix it. Materializing the intermediate set preserves filtering, endpoint scores, deduplication and the final candidate bound while avoiding that repeated join expansion. No global planner setting or schema migration is required.

## Host consumption and remaining limits

The host protocol is now `memory-consumption-v5`. Acknowledgments follow the current user's language; the English protocol no longer supplies Chinese acknowledgment examples. It explicitly distinguishes earlier assistant drafts from personal evidence, prohibits filling remembered scenes with plausible details, and directs the host to reuse relevant established background unless ambiguity materially changes the help. These instructions guide a fallible host model and cannot guarantee every answer.

The [repair verification report](../evaluations/v1-2026-10-26-longitudinal-repairs.md) records executed checks, source identity and bounded real-model observations. Earlier failed campaigns remain unchanged. Gist-first age-dependent forgetting, better long-term semantic representations, history-comparison utility/cost and sleep-interrupted operation recovery still require separate evidence. This batch does not solve those remaining product questions.
