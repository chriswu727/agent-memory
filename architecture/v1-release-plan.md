# First-release completion plan

Status: active development; this document is not release acceptance.

The [finite first-release acceptance](v1-acceptance.md) now defines the supported scope, measurable gates, spending limits and explicit success stop. It governs release completion; the broader architecture and implementation sequence below do not authorize an ever-expanding first release.

The [current local acceptance checkpoint](../evaluations/v1-2026-10-25-local-acceptance.md) records the subsequent bounded-attention, private-funding, capability and speech-attribution repairs, installed artifact, regression follow-up and measured overhead. The historical results below remain evidence for their original candidates. Actual 100-exchange/twelve-probe acceptance is still open.

The first release must be a usable independent memory runtime for API-backed text agents, with a desktop installation and a public integration surface. It must form and retrieve memories automatically through the declared lifecycle, preserve continuity across restarts and model changes, retain qualifications and corrections, and make failures and costs inspectable. MCP remains an additional tools interface; installing MCP alone cannot intercept a closed host's conversations.

Human memory is a functional inspiration. A passing software test or synthetic time advance does not establish biological fidelity or reliable performance over years. Personality and emotion remain subsequent research, as requested. Existing restrictions on streaming, tools, multimodal input, shared accounts, and unsupported operating systems must remain explicit until their own acceptance is executed.

## Completion gates

| Gate | Required evidence | Current action |
| --- | --- | --- |
| Installation and upgrades | Isolated installed-package startup; stable identity; changed builds replace stale daemons; incompatible schema and downgrade attempts fail before destructive changes | Repeated in the 4,282-test integrated run; final wheel matches 109 runtime/schema/migration files and 21 migrations |
| Integration and continuity | A documented standalone API-key model path and Python adapter; durable input/turn identity, cross-host exclusion, restart recovery, honest delivery uncertainty | Preserve existing lifecycle tests; evaluate remaining generic API integration friction |
| Memory feedback | Only attributable, trusted external evidence changes access strength; corrections cannot positively reinforce an incorrect memory; retained contributions remain auditable through correction/deletion | Ledger, durable prepared selection, conservative received-input feedback and custom-context opt-out implemented; focused regressions passed |
| Situation and learning | Statements do not invent tasks; real requests retain actionable intent; assessed rejection, no proposals, accepted proposals, publication, and unassessed work remain distinguishable | Gate failed in sustained real-model evaluation: incomplete optional history blocked eight consecutive learning windows. Local encoding, bounded historical integration, phase allocation, monitoring, and recovery now pass integrated local checks; real-model episode fidelity and sustained-learning acceptance remain pending |
| Recovery and portability | Users can back up and recover their installation with explicit treatment of deletion manifests, operational turns, and uncertain external effects; archive size limits cannot silently lose data | Streamed backup, independent deletion authority, quarantine and epoch activation implemented; 5,007-event Docker and installed-wheel recovery passed; integrated regression and distribution checks passed |
| Provider boundaries and usage | Common bounded HTTP parsing, strict responses, no hidden retries, known versus unknown actual token use, content-free diagnostics | Shared transport, per-phase settlement and usage monitoring passed; explicit cumulative budget configuration implemented and tested |
| Long-term behavior | Frozen source ledger, restart and model-independent continuity, conditional changes, independent episodes, generalization differences, misleading cues, and deletion in a sustained synthetic history | Three deterministic combined lifecycle cases passed. A frozen 100-history experiment stopped after 11 stages and 38 settled calls because learning stalled; held-out probes and comparison arms were not run. Repair and a new separately recorded sustained evaluation are required |
| Release delivery | Relevant local regressions, full suite, installed artifact checks, bilingual setup documentation, English developer material, and verified publication to the default branch | Commit verified batches without routine CI; publish final acceptance separately |

The historical 52-scenario architecture specification remains the broader target. Its unexecuted scenarios must not be marked passed merely because a related unit test exists. Release evidence must identify the exact behavior, implementation revision, provider settings, fixtures, budgets, and limitations that were exercised.

The [stopped long-history evaluation](../evaluations/deepseek-2026-10-25-long-history-stopped.md) remains blocking evidence. The subsequent [fixed episode diagnostic](../evaluations/deepseek-2026-10-25-episode-fidelity.md) completed sixteen known-usage calls but found the same two false acceptances under both verification contracts. Four positive fidelity labels did not establish correct meaning. The [local encoding and historical integration contract](local-observation-consolidation.md) records the repair boundary, including autonomous deferred work and finite shared allocation. The subsequent [continuous-learning repair diagnostic](../evaluations/deepseek-2026-10-25-learning-repair-stopped.md) encoded all eight completed inputs, then stopped on an invalid historical abstraction. It also exposed excessive comparison debt, two inaccurate organization labels and a speech-event/content-world false rejection. Semantic and sustained-learning acceptance remain open. The [source-navigation repair](episode-source-navigation.md) is locally verified and published, but was absent from that frozen real run. The [retrieval scale diagnostic](../evaluations/retrieval-scale-2026-10-25.md) separately verifies a local retrieval repair; it does not resolve the learning failure or establish a production latency guarantee.

## Implementation sequence

1. Repair trust and operation foundations in parallel: feedback provenance and access dynamics, build/schema compatibility, semantic-outcome monitoring, and consistent provider transport behavior.
2. Integrate those changes through public HTTP/SDK and installed desktop flows. Reconcile source invalidation, upgrades, recovery, and metadata boundaries before expanding automation.
3. Add conservative autonomous feedback where a delivered use and later external outcome can both be established. Improve current-request classification and bounded cross-episode selection without relaxing evidence or applicability checks.
4. Complete the supported backup/recovery and developer integration path. Report unsupported behavior explicitly rather than accepting a request and silently omitting memory.
5. Freeze an evaluation revision and fixtures. Start with targeted real-model diagnostics, then run a sustained history and isolated probes only after prerequisites pass. Correct concrete failures and run a separately recorded follow-up; never overwrite earlier verdicts.
6. Run release checks, build and inspect the distribution, update the acceptance report, and publish verified changes. Remaining failures block the applicable gate and stay visible.

## Verification and resource policy

Ordinary development uses local tests, real isolated PostgreSQL tenants, and deterministic providers. Database-mutating suites share one coordinated test environment and do not run concurrently against mutable schema. Desktop end-to-end tests create and remove only their own installations and volumes.

The user has authorized bounded DeepSeek evaluations and increased usage when useful. Every real-model run must have an immutable fixture/plan, persistent call and output-token ceilings, known-usage accounting, and a stopping rule for uncertain results. Credentials stay in ignored local configuration. Tests must not consume Codex or Claude subscription usage. No automatic CI is added or triggered.

Engineering and semantic outcomes remain separate. A verified rejection is not a learned belief; a publication count is not the count of currently true or usable memories; an empty queue is not proof of good understanding. A retained original source is not proof that its claim about the world is true. Real-model fluency must not replace an independent source-and-answer audit.
