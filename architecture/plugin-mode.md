# Plugin mode

Status: **implemented, verified on Linux x86_64 only**. This document is the design, acceptance contract and verification record for the lightweight plugin distribution. It does not change the server-mode guarantees described elsewhere.

## Goal

An agent can install memBrain from one instruction and use it immediately, with no Docker, no setup wizard and no extra model API key. In Claude Code the target is a single command that the agent itself can run:

```sh
claude plugin install membrain --marketplace chriswu727/membrain
```

Other MCP hosts use one stdio server entry instead.

## Product boundary

Plugin mode is an optional host adapter over the unchanged core. The core still owns storage, provenance, recall, erasure and validation. The adapter never owns the host's turn loop: it observes the host's own lifecycle events and returns optional context.

| Concern | Server mode (existing) | Plugin mode |
| --- | --- | --- |
| Database | Docker PostgreSQL + pgvector | Embedded PostgreSQL + pgvector from the `pgserver` wheel, same schema and migrations |
| Turn ownership | `BrainRuntime` generates and delivers | The host replies; hooks only record and recall |
| Before a reply | Orientation and preparation, can take minutes with reasoning models | Bounded recall only, no model calls |
| Failure behaviour | Turn fails closed with a typed error | Fail open: the host reply always proceeds |
| Model key | Required for learning | Not required; learning stays optional |
| Understanding | Background proposal and verification calls | The host model interprets recalled original sources at answer time and may save attributed notes through MCP |

## Components

1. **Embedded database engine.** A desktop profile records `database_engine` (`docker` or `embedded`). The embedded engine uses only the PostgreSQL and pgvector binaries shipped in `pgserver`, runs `initdb` once, and starts `pg_ctl` on `127.0.0.1:<port>` with password authentication. Connection strings, migrations, roles and row-level security are identical to Docker. When running as root, the cluster runs under a dedicated unprivileged system user, because PostgreSQL refuses to run as root.
2. **Zero-configuration bootstrap.** `open_client(start_service=True)` already creates a profile and starts the service. Plugin entry points select the embedded engine automatically when it is available, otherwise fall back to Docker with the existing guidance.
3. **Hook commands.** `membrain plugin hook <event>` reads the host's hook JSON from stdin and always exits 0.
   - An asynchronous `SessionStart` command installs or updates the runtime with `uv tool install` and runs `plugin start`. Only this hook may install, so a timed-out prompt hook can never interrupt an installation.
   - `session-start`: reads the user's earlier messages from other sessions through `GET /v1/brains/{id}/timeline` (newest first, deletion-safe, with a `complete` flag). When they all fit in about 8,500 characters, it injects the complete history oldest first; otherwise it injects as many recent messages as fit, each cut to 400 characters so that long pastes do not crowd out the rest, and says older messages are omitted. The first blinded A/B run showed a full-history prompt far ahead of retrieval at this scale; the [plugin-path run](../evaluations/v1-2026-10-27-plugin-path-ab.md) measured this design. It also starts the service in the background when needed.
   - `user-prompt`: skips recall when this session already received the complete history (a per-session marker), because recall would only repeat it. Otherwise it recalls records from other conversations with an explicit evidence cutoff (the current message is not yet stored), prints compact context as `additionalContext`, then records the message. If the service is not ready, the message is spooled and no context is injected.
   - `stop` (synchronous, about half a second): records `last_assistant_message`, linked to its prompt. Asynchronous hooks are cut off when a headless `claude -p` run exits, which lost replies in testing.
   - Per-message hooks launch the installed tool with `uvx --offline`, so they never download anything. Until the runtime is installed, the hook commands append the raw hook input to `$CLAUDE_PLUGIN_DATA/pending-hooks.jsonl` with plain shell; the runtime imports it on start, so a first session's messages are kept. A real installation from GitHub lost the first message before this queue existed.
   - A local spool (JSON Lines in the profile directory) holds records that could not be delivered and is flushed on the next successful call, so a slow start loses no history.
4. **Compact context rendering.** Recalled memories and original source passages are rendered as short, dated, role-attributed lines, explicitly labelled as data rather than instructions, within a character budget below the host's 10,000-character limit. The full memory-consumption protocol used by `BrainRuntime` is not injected.
5. **MCP tools.** The existing stdio bridge provides explicit recall, inspection, assistant-attributed notes (`memory_observe`) and user-requested erasure.
6. **Skill instructions.** A plugin skill tells the host when to search memory, how to treat recalled records (historical, attributed, possibly outdated), when to save an attributed note, and to honour erasure requests.
7. **Packaging.** The repository root carries a Claude Code marketplace manifest. The plugin directory contains `plugin.json`, `hooks/hooks.json`, `.mcp.json` and the skill. Commands launch the package through `uvx`.

## Deliberately deferred

- A host-authored semantic memory contract with deterministic quote validation. The existing `assertion_proposals` path only accepts exact source captures, by design.
- Background consolidation by a plugin subagent using the host's own session.
- Local multilingual embeddings for semantic recall without a provider.
- Idle shutdown of the local service.
- Linux ARM embedded binaries (no `pgserver` wheel); those machines use Docker.

## Acceptance

- Embedded engine: full source suite passes against it; setup needs no Docker on Linux x86_64, macOS and Windows wheels resolve.
- Hooks: never exit non-zero and never block a prompt; recall adds at most 1.5 s at p95 on a warm service; records survive a cold start through the spool.
- Plugin: `claude plugin validate` passes; installation from a local marketplace works; two separate Claude Code sessions demonstrate cross-session recall without manual memory calls.
- Documentation states what is and is not verified, in English and Chinese.

## Verification record (2026-10-27, Linux x86_64 container)

- **Embedded engine.** `pgserver` 0.1.4 (PostgreSQL 16.2, pgvector 0.6.2). A fresh installation initializes, migrates and becomes ready in about 2.2 s; restart takes about 1.7 s. Start, stop, restart, backup, restore into a new quarantined installation and activation were exercised with real commands. The same checks ran as root (dedicated service account, binaries copied under a shared root; these were `agent-memory-db` and `/var/lib/agent-memory` at the time and are `membrain-db` and `/var/lib/membrain` after the rename, with existing clusters left in place) and as an unprivileged user (cluster inside the profile directory).
- **Hooks.** Through the real CLI, a per-message hook takes about 0.5 to 0.7 s including interpreter start; local recall itself takes 55 to 100 ms. With the service stopped, a prompt hook returned in 0.4 s, spooled the record and started the service in the background; the next prompt replayed it and recalled it.
- **Claude Code.** An isolated Claude Code 2.1.311 configuration added the repository as a local marketplace and installed the plugin. `claude plugin validate` passed for the plugin and marketplace (the only warning is the optional icon). With a local stub standing in for the model API, three separate `claude -p` sessions showed: automatic recording of every user message; the first session's knee constraint injected into the second session's model request inside `<membrain-memory>`; the session notice in context; the MCP server connected; and, after making `Stop` synchronous, the assistant reply recorded with its parent prompt. The stub verifies integration only, not answer quality.

### Known limits

- Automatic recall is lexical. Common English word forms are folded (`run`, `running`, `ran`), but synonyms and cross-language matches are not: "wedding" does not match "getting married", and a Chinese question does not match English records. The skill asks the model to search with concrete keywords; see the [plugin-path A/B report](../evaluations/v1-2026-10-27-plugin-path-ab.md) for how often that happened.
- A query containing words such as "remember" is treated as a prior-record check. The hook supplies an evidence cutoff; the `memory_recall` MCP tool does not, so the skill tells Claude to avoid that phrasing.
- `pgserver` publishes wheels only for CPython 3.9 to 3.12 and not for Linux ARM; the plugin pins Python 3.12 and other platforms fall back to Docker. The package has few recent releases, so bundling PostgreSQL ourselves may become necessary.
- macOS and Windows were not run on real machines. Windows hook commands assume the POSIX shell that Claude Code uses for hooks.
- The plugin installs from the repository's default branch until the package is published to PyPI.
