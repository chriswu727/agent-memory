# memBrain for Claude Code, Codex, WorkBuddy and CodeBuddy

Automatic, local long-term memory across Claude Code, Codex, WorkBuddy and CodeBuddy sessions, shared on the same computer.

```sh
claude plugin install membrain --marketplace chriswu727/membrain
```

In Codex:

```sh
codex plugin marketplace add chriswu727/memBrain
codex plugin add membrain@membrain
```

Codex asks you to review and trust the hooks on the next start (or later with `/hooks`); untrusted hooks never run.

In WorkBuddy, add the marketplace `https://raw.githubusercontent.com/chriswu727/memBrain/main/.codebuddy-plugin/marketplace.json` under **Plugins → +** and install **membrain**. In CodeBuddy Code:

```sh
codebuddy plugin marketplace add chriswu727/memBrain
codebuddy plugin install membrain@membrain
```

Requires [uv](https://docs.astral.sh/uv/). No Docker, setup wizard or extra model API key.

## What it does

| Hook | Behaviour |
| --- | --- |
| `SessionStart` (asynchronous) | Installs or updates the runtime with `uv tool install` and starts the local service with an embedded PostgreSQL database. |
| `SessionStart` | Adds Claude's saved notes about you and your recent messages from other sessions. |
| `UserPromptSubmit` | Recalls related earlier messages by keywords and meaning, in any language, adds them as dated, attributed messages, then records the message. |
| `Stop` | Records Claude's final reply. |

Every hook fails open: if memory is unavailable the prompt and reply proceed unchanged, and records are queued locally until the service is ready. The bundled MCP server adds `memory_note` (Claude saves short lasting facts, such as health limits, people and dates, and replaces them when they change), `memory_recall`, `memory_inspect`, `memory_observe` and `memory_forget`; the skill tells Claude when to use them. Claude Code may ask you once to allow these tools.

## Data and control

- Memory is stored under your user data directory (`~/.local/share/membrain` on Linux, `~/Library/Application Support/memBrain` on macOS, `%LOCALAPPDATA%\memBrain` on Windows). Set `MEMBRAIN_DIRECTORY` to use another location.
- `membrain plugin ui` opens your memory page in the browser with a single-use link: review, correct and delete notes, browse and delete conversations, search, pause recording, export or erase everything.
- `membrain plugin status` shows the service state, queued records and whether semantic recall is ready; `membrain desktop stop` stops the service.
- Ask Claude to forget something, or use the `memory_forget` tool, to suppress or erase records.
- Say "don't remember this" (Claude calls `memory_pause`) or run `membrain plugin pause [--hours N]` to stop recording; `membrain plugin resume` turns it back on. A turn in which recording is paused is not stored at all.
- Wrap anything in `<private>…</private>` to keep it out of memory. Values that look like passwords, API keys, tokens or private keys are replaced with `[secret removed]` before storage.

## Limits

- Semantic recall uses a local multilingual model (about 135 MB) downloaded from Hugging Face after the first start; until then recall matches words only. Set `HF_ENDPOINT` to use a mirror, or `MEMBRAIN_SEMANTIC=0` to skip it.
- Model-driven background learning is off unless a provider is configured with `membrain desktop setup`.
- The embedded database needs CPython 3.12 (installed automatically by uv) on macOS, Windows or Linux x86_64. Linux ARM uses Docker instead.
- Verified on Linux x86_64. On Windows 11 the runtime, the embedded database and both hook launchers were run on a real machine: WorkBuddy runs hook commands with the Git Bash it bundles (PowerShell if no bash is found, never `cmd.exe`), so it uses `bin/membrain-hook`; `bin/membrain-hook.cmd` serves hosts that run hooks with `cmd.exe`. The WorkBuddy and CodeBuddy apps themselves, Claude Code and Codex have not yet been run on Windows, and macOS has not been tested on a real machine.

See [plugin mode](../../architecture/plugin-mode.md) for the design and verification record.
