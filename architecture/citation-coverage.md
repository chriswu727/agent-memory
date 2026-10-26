# Citation coverage at semantic publication

An assertion can accurately summarize a complete source while citing an incomplete subset of that source. The [four-turn longitudinal checkpoint](../evaluations/v1-2026-10-25-longitudinal-publication-failure.json) exposed this distinction: a published summary listed five writing suggestions, but its references omitted the suggestion about the letter's location and physical details. The verifier saw the missing text in complete source context and still approved the reference set.

The `citation-coverage-v1` cognitive input contract adds a separate required `citation_coverage_verdict` for claims, current-situation updates and belief links. The runtime constructs each proposal's `citation_scope` from its own references. Positive support is limited to that proposal's numbered supporting excerpts; an abstraction additionally limits support to the excerpts selected as relevant. The coverage target is the entire proposal, including attribution, conditions, negation, uncertainty, world, time, procedural outcomes and the asserted relationship.

Complete source records remain available to detect qualifications and counterevidence. They cannot fill a missing positive premise. Other proposals cannot lend their citations to the current proposal, including an episode whose authorized member evidence covers an entire message. Episode organization retains its existing complete-member authority and fidelity checks.

Coverage approval is required independently of the existing whole-proposal, per-reference, request, relationship and instance checks. Missing coverage assessments fail schema validation; unknown or contradictory coverage prevents acceptance. Publication checks the assessment again. A positive coverage verdict cannot override a failed context or fidelity check. This adds no model call or spending allowance.

Fresh jobs use the new contract. Previously frozen jobs retain their recorded input version, and existing stored memories are not silently reverified or rewritten. The new contract inherits the prior history-link accounting and episode/source semantics. Compatibility with a prior contract is not evidence that an old failed publication has become valid.

Citation coverage is a fallible model judgment. Structural validation and an independent verdict improve the boundary but cannot prove arbitrary language entailment. Regression tests must distinguish deterministic gate behavior from actual verifier behavior, and a successful bounded diagnostic cannot substitute for the remaining longitudinal evaluation.
