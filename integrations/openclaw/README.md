# memBrain for OpenClaw

Long-term memory for your [OpenClaw](https://github.com/openclaw/openclaw) assistant on every channel it serves (WhatsApp, Telegram, Discord, iMessage...), shared with Claude Code, Codex and Hermes on the same computer. It works alongside OpenClaw's own memory; it does not take the memory slot.

## Install

Requires [uv](https://docs.astral.sh/uv/) on the Gateway's `PATH`.

```sh
curl -L https://github.com/chriswu727/memBrain/archive/refs/heads/main.zip -o membrain.zip
unzip -q membrain.zip 'memBrain-main/integrations/openclaw/membrain/*'
openclaw plugins install ./memBrain-main/integrations/openclaw/membrain --force --accept-capabilities
openclaw config set plugins.entries.membrain.hooks.allowConversationAccess true
```

OpenClaw 2026.10 refuses to install a local plugin until you accept its declared capabilities (`--accept-capabilities`), and requires the explicit conversation-access grant for any non-bundled plugin that reads prompts or replies. Current OpenClaw releases need Node 24.22.1 or later. On first load the plugin installs the memBrain runtime with uv and starts its local service in the background; turns before it is ready proceed without memory.

## What it does

| OpenClaw hook | memBrain behaviour |
| --- | --- |
| `before_prompt_build` | Adds the session notice, saved notes and earlier messages as `appendSystemContext` (kept stable for five minutes per session so provider prompt caches work), and related earlier messages for this request as `prependContext`. Stages the request. |
| `agent_end` | Stores the request and the final assistant reply of a successful run. |
| Tools | `membrain_note`, `membrain_search`, `membrain_pause`, `membrain_resume`. |

Set the plugin's `directory` setting to keep this assistant's memory apart from your other hosts. `membrain plugin ui` opens the memory page, and `<private>…</private>` text and credential-like values are never stored.

## Status

The plugin logic is tested with Node against the real memBrain runtime: two sessions sharing memory, notes, search and pause. The manifest follows OpenClaw's plugin documentation and source. On Windows 11 a real OpenClaw 2026.10.8 Gateway with DeepSeek V4 Pro loaded it with the commands above: the hooks stored each turn, the model saved notes with `membrain_note`, and with OpenClaw's own `memory-core` plugin disabled and no workspace file holding the fact, a new session recalled a licence plate and parking space told in an earlier one. It has not yet been run on a messaging channel.
