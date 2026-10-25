# Episode navigation and continuous-learning verification

The new `episode-source-view-v1` input contract prevents historical episode organization labels and old verifier verdicts from entering later proposal and transition-verification contexts. It retains complete original member sources, exact versions and hashes, and explicitly fallible group structure. Raw stored labels, audit outcomes and earlier frozen request bytes remain unchanged. See the [source-navigation contract](../architecture/episode-source-navigation.md).

This contains a demonstrated propagation risk. It does not correct the false acceptances in the [real fixed-proposal diagnostic](deepseek-2026-10-25-episode-fidelity.md), repair already contaminated assertions, or prove that grouping and semantic inference are accurate.

## Local verification

The final integrated run passed **4,282 tests in 360.75 seconds**, including Docker desktop flows and chat from an isolated installed wheel. The focused source-view and compatibility group passed 406 tests. Thirty new source-view cases include actual database publication followed by ordinary worker consumption, a split-and-reload transition, and optional malformed or oversized groups that trigger explicit history omission while permitting independent new-input encoding. A falsely accepted label remains in its original audit but is absent from later model requests; its full source qualifications remain available.

Three additional PostgreSQL, public API and `BrainRuntime` tests exercise one ongoing conversation with actual durable input/output records, the normal worker, service reconstruction and completed-turn replay. Under an explicit small packaging policy, five source-grounded reports create a finite historical-comparison tail. A later notebook report supplies its unused pair to the exact old never-attempted objective, preserving its original input range, encoding parent and existing ledger. The donor's own remaining obligation stays visible. Separate invalid-response and uncertain-provider cases retain the attempted failure and its charges while allowing only untouched comparisons to proceed. These tests use deterministic providers; they establish lifecycle mechanics, not default-load performance or natural-language quality.

Source and wheel builds succeeded. All **109 runtime/schema/migration files** match both the wheel and isolated installation byte for byte, including **21 migrations**. Build identity: `f133a77a8adcf9fb8500597229cd2efdfd3203f16ec1a1dcfdc03fb37d680a09`. Global Ruff and whitespace checks passed. The TypeScript SDK did not change. No routine CI or subscription-backed tests were used.

## Remaining boundaries

Optional invalid episode views produce the fixed reason `episode_source_view_unavailable`; the public sampling sanitizer retains this category. During local fallback, the original `history_sampling` audit remains private and separate from the local batch's own sampling metadata. Directly supplied invalid required views fail before a provider call. No missing source is replaced by its old label.

Existing frozen jobs continue using their old contracts. This upgrade does not rewrite a paid request, add provider rounds or replenish its authorization. Source correction, deletion, policy checks and publication fences still apply.

Funding remains restricted to the same conversation and branch in this batch. An abandoned conversation can therefore retain deferred history after its finite allocation is exhausted; increasing the brain's rolling limit does not create another pair. Cross-conversation funding needs its own bounded authorization and isolation checks. The current real continuous-learning diagnostic uses an earlier frozen revision and separate conversations, so it neither verifies this new input projection nor closes that funding gap.
