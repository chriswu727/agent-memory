# Changelog

## Unreleased — 0.1.0 candidate

Agent Memory is a human-inspired memory engine for AI agents. It keeps persistent memory outside the model's current prompt and supplies relevant context through an explicit agent lifecycle. This candidate is not yet a stable release: longitudinal behavioral acceptance remains incomplete.

### Included

- Persistent source-backed memory, working context, background learning, consolidation and cue-guided retrieval.
- Python lifecycle integration, HTTP API, TypeScript client, optional MCP memory tools and direct API-key terminal chat.
- Scoped identities, corrections and deletion handling, bounded cognitive spending, and inspectable processing outcomes.
- Local setup, restart, upgrade, backup and recovery with an independently installed package; the verified desktop target is macOS with uv and Docker.
- Local retrieval and preparation optimizations without adding a separate model for memory admission or promoting experimental verification formats.
- English and Chinese entry-point documentation, integration references and retained evaluation reports.
- Explicit semantic time allowances up to 600 seconds, with desktop response waits of 615 seconds for retained frozen jobs. The default 30-second allowance and existing 240-second reasoning preset are unchanged; proposal and verification still share one deadline.
- Versioned source-act interpretation for new captures and cognitive jobs, with unchanged old source/capture identities and legacy frozen request contracts.
- Scoped frequency-weighted lexical ranking, bounded adjacent source units, and comparable indexed/tail scores with access-age weighting.
- Assistant speech remains available for explicit speech/quote/episode/commitment requests with its own attribution label; it is excluded from ordinary personal recollection.
- Materialized episode-neighbor discovery to avoid a pathological nested join, plus language-neutral acknowledgment guidance and explicit remembered-detail boundaries.

### Known limits

Real-model observations still include historical information presented as current, irrelevant retrieved material and rejected valid semantic proposals. Earlier failed campaigns remain failed. See the [acceptance contract](architecture/v1-acceptance.md) and [closeout checklist](docs/releasing.md) for the remaining work and evidence boundaries.

The [completed varied diagnostic](evaluations/v1-2026-10-26-longitudinal-round.md) attempted all 33 inputs on one brain, with 32 replies and one preserved timeout. Meaningful recall and source-faithful recollection still fail in some cases. Access-age tier changes did not demonstrate the intended gist-first forgetting behavior. These remain release blockers, not validated human-memory equivalence.

MCP exposes tools; installing it alone does not automatically intercept a closed AI client's conversations. Streaming, general tool loops, multimodal input, native one-click installation, personality and emotion are outside this release. Windows and Linux desktop flows have not received real-machine acceptance. Human-inspired describes the design direction, not proven biological equivalence or infallible recall.
