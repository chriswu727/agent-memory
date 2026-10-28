# memBrain for OpenClaw

Long-term memory for your [OpenClaw](https://github.com/openclaw/openclaw) assistant on every channel it serves (WhatsApp, Telegram, Discord, iMessage...), shared with Claude Code, Codex and Hermes on the same computer. It works alongside OpenClaw's own memory; it does not take the memory slot.

## Install

Requires [uv](https://docs.astral.sh/uv/) on the Gateway's `PATH`.

```sh
curl -L https://github.com/chriswu727/memBrain/archive/refs/heads/main.zip -o membrain.zip
unzip -q membrain.zip 'memBrain-main/integrations/openclaw/membrain/*'
openclaw plugins install ./memBrain-main/integrations/openclaw/membrain --force
openclaw config set plugins.entries.membrain.hooks.allowConversationAccess true
```

OpenClaw requires the explicit conversation-access grant for any non-bundled plugin that reads prompts or replies. On first load the plugin installs the memBrain runtime with uv and starts its local service in the background; turns before it is ready proceed without memory.

## What it does

| OpenClaw hook | memBrain behaviour |
| --- | --- |
| `before_prompt_build` | Adds the session notice, saved notes and earlier messages as `appendSystemContext` (kept stable for five minutes per session so provider prompt caches work), and related earlier messages for this request as `prependContext`. Stages the request. |
| `agent_end` | Stores the request and the final assistant reply of a successful run. |
| Tools | `membrain_note`, `membrain_search`, `membrain_pause`, `membrain_resume`. |

Set the plugin's `directory` setting to keep this assistant's memory apart from your other hosts. `membrain plugin ui` opens the memory page, and `<private>…</private>` text and credential-like values are never stored.

## Status

The plugin logic is tested with Node against the real memBrain runtime: two sessions sharing memory, notes, search and pause. The manifest follows OpenClaw's plugin documentation and source. It has not yet been loaded by a real OpenClaw Gateway.
