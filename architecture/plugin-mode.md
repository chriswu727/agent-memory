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
   - Per-message hooks launch the installed tool with `uvx --offline`, so they never download anything. Until the runtime can run, hooks skip recording. Earlier versions queued raw hook JSON in the plugin data directory; this bypassed privacy filtering, so shell fallback queues have been removed. Legacy queues are still filtered and imported on start. After installation, the runtime filters messages before spooling them when the service is unavailable.
   - A local spool (JSON Lines in the profile directory) holds records that could not be delivered and is flushed on the next successful call, so a slow start loses no history.
4. **Compact context rendering.** Recalled memories and original source passages are rendered as short, dated, role-attributed lines, explicitly labelled as data rather than instructions, within a character budget below the host's 10,000-character limit. The full memory-consumption protocol used by `BrainRuntime` is not injected.
5. **MCP tools.** The existing stdio bridge provides explicit recall, inspection, assistant-attributed observations (`memory_observe`) and user-requested erasure, plus `memory_note` for host-curated notes.
6. **Host-curated notes.** A blinded run on a fresh scenario showed two limits of retrieval: about seventy messages exceed what a hook may inject (Claude Code caps hook context at 10,000 characters), and similarity search cannot connect facts related only by implication (a seafood menu and a shellfish allergy; checking in at a clinic and a parent who needs an interpreter). The host model can make those connections, so it keeps the durable facts: when the user states one, it saves a one-line note with `memory_note`, and replaces the note when the fact changes (the old note is suppressed, not erased). Session start injects all notes first, then the user's recent messages with pasted code and logs compacted. Per-prompt recall shows complete source messages around recalled statements, oldest first, with the session's previous prompt as a cue.
7. **Skill instructions.** A plugin skill tells the host when to search memory, how to treat recalled records (historical, attributed, possibly outdated), when to save an attributed note, and to honour erasure requests.
8. **Local semantic recall.** With the `semantic` extra, `plugin start` downloads a pinned, checksum-verified int8 ONNX export of `multilingual-e5-small` (MIT, 384 dimensions, about 135 MB) into the installation directory in a detached process, then restarts the service. The service passes an in-process `LocalEmbeddingProvider` to the existing embedding path: new memories get embedding jobs, older ones are backfilled when the worker is idle, and each hook or MCP recall embeds the query (with the model's query prefix) and fuses vector and keyword rankings. No key, network call per message or GPU is involved. The desktop worker now drains queued jobs for up to two seconds per round instead of one job per second.
9. **Packaging.** The repository root carries a Claude Code marketplace manifest. The plugin directory contains one manifest per host (`.claude-plugin/`, `.codex-plugin/`, `.codebuddy-plugin/` for WorkBuddy and CodeBuddy), each pointing explicitly at its own hooks file in `hooks/` and its own MCP file, plus the shared skill. Nothing sits at the default `hooks/hooks.json` or `.mcp.json` locations, so no host loads another host's hooks. Commands launch the package through `uvx`.

## Deliberately deferred

- A host-authored semantic memory contract with deterministic quote validation. The existing `assertion_proposals` path only accepts exact source captures, by design.
- Background consolidation by a plugin subagent using the host's own session.
- Idle shutdown of the local service.
- Linux ARM embedded binaries (no `pgserver` wheel); those machines use Docker.

## Acceptance

- Embedded engine: full source suite passes against it; setup needs no Docker on Linux x86_64, macOS and Windows wheels resolve.
- Hooks: never exit non-zero and never block a prompt; recall adds at most 1.5 s at p95 on a warm service; after runtime installation, records survive a service cold start through the privacy-filtered spool. Messages sent before the runtime can run are skipped.
- Plugin: `claude plugin validate` passes; installation from a local marketplace works; two separate Claude Code sessions demonstrate cross-session recall without manual memory calls.
- Documentation states what is and is not verified, in English and Chinese.

## Verification record (2026-10-28, Linux x86_64 container)

- **Claude Code after the per-host file split.** Claude Code 2.1.312 with an isolated configuration and a stub model API: `claude plugin validate` passed, and of two `claude -p` sessions the second's model request contained the first session's message in `<membrain-history>`. Child processes must not inherit `CLAUDE_CODE_SESSION_ID`; when they did (running inside another Claude Code session), every run shared one session ID and memory correctly treated them as the same conversation.
- **Codex.** Codex CLI 0.165 with an isolated `CODEX_HOME` and a stub Responses API: the plugin installed from the local marketplace, its four hooks were trusted in `/hooks`, and two `codex exec` sessions stored both turns and carried the first session's message into the second.
- **WorkBuddy and CodeBuddy.** No host available; `bin/membrain-hook` was driven with WorkBuddy's documented hook input (no `last_assistant_message` on `Stop`), the reply was recovered from the transcript, and the next session received the earlier message.

## Verification record (2026-10-27, Linux x86_64 container)

- **Embedded engine.** `pgserver` 0.1.4 (PostgreSQL 16.2, pgvector 0.6.2). A fresh installation initializes, migrates and becomes ready in about 2.2 s; restart takes about 1.7 s. Start, stop, restart, backup, restore into a new quarantined installation and activation were exercised with real commands. The same checks ran as root (dedicated service account, binaries copied under a shared root; these were `agent-memory-db` and `/var/lib/agent-memory` at the time and are `membrain-db` and `/var/lib/membrain` after the rename, with existing clusters left in place) and as an unprivileged user (cluster inside the profile directory).
- **Hooks.** Through the real CLI, a per-message hook takes about 0.5 to 0.7 s including interpreter start; local recall itself takes 55 to 100 ms. With the service stopped, a prompt hook returned in 0.4 s, spooled the record and started the service in the background; the next prompt replayed it and recalled it.
- **Claude Code.** An isolated Claude Code 2.1.311 configuration added the repository as a local marketplace and installed the plugin. `claude plugin validate` passed for the plugin and marketplace (the only warning is the optional icon). With a local stub standing in for the model API, three separate `claude -p` sessions showed: automatic recording of every user message; the first session's knee constraint injected into the second session's model request inside `<membrain-memory>`; the session notice in context; the MCP server connected; and, after making `Stop` synchronous, the assistant reply recorded with its parent prompt. The stub verifies integration only, not answer quality.

### Known limits

- Until the semantic model has downloaded, or without the `semantic` extra, automatic recall is lexical: common English word forms are folded (`run`, `running`, `ran`), but synonyms and cross-language matches are not ("wedding" does not match "getting married"; a Chinese question does not match English records). See the [plugin-path A/B report](../evaluations/v1-2026-10-27-plugin-path-ab.md).
- Notes depend on the host model saving them. With DeepSeek V4 Pro under the skill guidance it saved dated notes and replaced outdated ones unprompted; other hosts and models have not been measured. Claude Code asks once before a plugin MCP tool is used.
- Vector similarity has no relevance floor: in a small memory every record is a candidate, so a few loosely related records can be injected. They are labelled as historical data and limited to the recall budget.
- The model download needs access to Hugging Face or a mirror (`HF_ENDPOINT`). ONNX Runtime wheels do not exist for every platform; there recall stays lexical.
- A query containing words such as "remember" is treated as a prior-record check. The hook supplies an evidence cutoff; the `memory_recall` MCP tool does not, so the skill tells Claude to avoid that phrasing.
- `pgserver` publishes wheels only for CPython 3.9 to 3.12 and not for Linux ARM; the plugin pins Python 3.12 and other platforms fall back to Docker. The package has few recent releases, so bundling PostgreSQL ourselves may become necessary.
- macOS was not run on a real machine. On Windows 11 the embedded database, service start from a hook, recording and recall ran for real through both launchers: `bin/membrain-hook` under the Git Bash bundled with WorkBuddy 5.8.3 (the shell WorkBuddy uses for hooks; it converts `CODEBUDDY_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` to `/c/...` form, and Git Bash converts them back for the runtime; older releases imported records queued before uv existed; current launchers skip these messages to avoid writing unfiltered input), and `bin/membrain-hook.cmd` under `cmd.exe`. When no bash is found WorkBuddy runs hooks with PowerShell, which cannot run the quoted hook command; that fallback is unsupported. Claude Code, Codex and the WorkBuddy app itself were not run on Windows.
- The plugin installs from the repository's default branch until the package is published to PyPI.
