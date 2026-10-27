# memBrain for Claude Code

Automatic, local long-term memory across Claude Code sessions.

```sh
claude plugin install membrain --marketplace chriswu727/membrain
```

Requires [uv](https://docs.astral.sh/uv/). No Docker, setup wizard or extra model API key.

## What it does

| Hook | Behaviour |
| --- | --- |
| `SessionStart` (asynchronous) | Installs or updates the runtime with `uv tool install` and starts the local service with an embedded PostgreSQL database. |
| `SessionStart` | Adds one line telling Claude that memory is active. |
| `UserPromptSubmit` | Recalls matching records from earlier sessions, adds them as dated, attributed lines, then records the message. |
| `Stop` | Records Claude's final reply. |

Every hook fails open: if memory is unavailable the prompt and reply proceed unchanged, and records are queued locally until the service is ready. The bundled MCP server adds `memory_recall`, `memory_inspect`, `memory_observe` and `memory_forget`; the skill tells Claude when to use them.

## Data and control

- Memory is stored under your user data directory (`~/.local/share/membrain` on Linux, `~/Library/Application Support/memBrain` on macOS, `%LOCALAPPDATA%\memBrain` on Windows). Set `MEMBRAIN_DIRECTORY` to use another location.
- `membrain plugin status` shows the service state and queued records; `membrain desktop stop` stops the service.
- Ask Claude to forget something, or use the `memory_forget` tool, to suppress or erase records.

## Limits

- Automatic recall matches words; related records with different wording may need a deliberate search.
- Model-driven background learning is off unless a provider is configured with `membrain desktop setup`.
- The embedded database needs CPython 3.12 (installed automatically by uv) on macOS, Windows or Linux x86_64. Linux ARM uses Docker instead.
- Verified on Linux x86_64. macOS and Windows have not yet been tested on real machines.

See [plugin mode](../../architecture/plugin-mode.md) for the design and verification record.
