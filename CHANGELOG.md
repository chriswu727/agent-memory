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

### Known limits

Real-model observations still include historical information presented as current, irrelevant retrieved material and rejected valid semantic proposals. Earlier failed campaigns remain failed. See the [acceptance contract](architecture/v1-acceptance.md) and [closeout checklist](docs/releasing.md) for the remaining work and evidence boundaries.

MCP exposes tools; installing it alone does not automatically intercept a closed AI client's conversations. Streaming, general tool loops, multimodal input, native one-click installation, personality and emotion are outside this release. Windows and Linux desktop flows have not received real-machine acceptance. Human-inspired describes the design direction, not proven biological equivalence or infallible recall.
