# Source-backed episode navigation

Status: implemented with integrated local verification. The [verification report](../evaluations/v1-source-navigation-2026-10-25.md) records 4,282 passing tests and an inspected installed artifact. This boundary follows the [failed fixed-proposal diagnostic](../evaluations/deepseek-2026-10-25-episode-fidelity.md), which found that four positive fidelity judgments could still accept unsupported meaning. It contains reuse of an erroneous episode label; it does not turn semantic verification into a truth certificate.

## Separate organization from factual support

An episode groups interaction records so that the runtime can find related contributions. Its membership, open/closed state and boundary are inferred organization. They do not prove a shared external event, agreement, cause or completed task. A model-generated organizing statement is a fallible interpretation, even after another model accepts it.

For newly frozen `episode-source-view-v1` requests, the runtime exposes historical episodes through a fixed navigation projection. It includes the episode identity, version, bound scope, state, exact member versions, boundary reference, and member content hashes. Fixed metadata labels the group as inferred and denies that its state establishes external completion. Free-form organization text, old verifier verdicts, and job diagnostics are absent from this projection.

The model still receives the complete original member sources, including negation, uncertainty, quoted speakers and other qualifications. These records remain untrusted communication rather than established world facts. The projection references the source records already present in the request; it does not duplicate their text or substitute a shortened label for them. A missing, superseded, corrupted or inaccessible member cannot produce a source-backed view.

The same projection applies to the proposal's historical episode list and the verifier's `existing_episode` context for a transition. The current proposed organizing statement remains unchanged in verification. Its original text, assessments and outcome stay in the audit. This prevents an earlier erroneous label from becoming a later factual premise without pretending that the earlier acceptance was correct.

## Storage, publication and compatibility

Raw episode documents and frozen snapshots retain their original bytes and exact source dependencies. Archive export and restore preserve the audit record. The navigation view is constructed at model-consumption boundaries; it does not rewrite old organization text or delete source ancestry. Normal host memory context and pattern construction continue to use source-backed member memories, not the episode's free-form label.

Before a provider reservation, a directly supplied episode with invalid or oversized source closure fails closed. During optional neighborhood selection, an unusable historical episode is instead recorded as an omitted relevant package with `episode_source_view_unavailable`. The complete new inputs may then enter the existing local-observation path before generation. The runtime cannot silently drop unavailable history after a model has already seen it and publish the result as an independent local interpretation.

Publication reconstructs and validates the projection from the bound snapshot again. Existing `None`, operation-scope, communication-intent and episode-fidelity contracts retain their prior serialized requests, schemas and policies. Newly frozen jobs use the new input version; restarting or recovering an old job does not upgrade its frozen request. The change adds no provider rounds and creates no new authorization or database migration.

## Verification boundaries

Regression coverage must include a previously accepted but misleading organization statement whose four fidelity verdicts all say `supports`, then show that future proposal and verification requests contain its complete source qualifiers but none of its old free-form text. The raw stored audit must remain available. Missing and changed sources, scope mismatches, malformed boundaries, oversized optional groups, public omission diagnostics, and old request hashes need separate checks.

The same-conversation lifecycle regression complements this boundary. Under an explicit small packaging policy, a deterministic provider creates source-backed reports, triggers local encoding, leaves a never-attempted historical comparison, and repays that exact comparison using a later input's unused allocation after service reconstruction. Separate returned-invalid and uncertain-call cases preserve the failed selected comparison while allowing only its untouched tail to proceed. Actual input capture, source versions, delivery, normal worker scheduling and public monitoring participate; this establishes mechanics, not real-model semantic quality or default-load performance.

Grouping itself can still be wrong. Derived member assertions can also misinterpret a source. Candidate discovery remains bounded, and projected organization is still a model-visible cue. These limitations require source-and-answer evaluation; removing a label is not evidence of accurate recollection, stronger generalization or human-like forgetting.
