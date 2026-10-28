# memBrain memory proxy (any bot with a `base_url`)

Many bots reach their model through an OpenAI-compatible API: QQ bots built on NoneBot, Koishi or LangBot, AI VTubers that read Douyin or Bilibili live comments, SillyTavern, Discord and Telegram bots, and most DeepSeek integrations. `membrain proxy` sits between such a bot and its model provider and gives every person who talks to the bot their own long-term memory, without changing the bot.

## Start it

```sh
uv tool install --python 3.12 'membrain-ai[embedded,semantic] @ https://github.com/chriswu727/memBrain/archive/refs/heads/main.zip'
membrain proxy --upstream https://api.deepseek.com/v1
```

`--upstream` is the `base_url` your bot used before (DeepSeek, OpenAI, SiliconFlow, a local Ollama or vLLM...). Then change only the bot's `base_url` to `http://127.0.0.1:8790/v1`; keep its API key and model name. The first start creates a local database; memory is kept in your memBrain data directory (or `--directory`).

## Telling people apart

The proxy separates memories by:

1. the `X-Membrain-User` header, or
2. the standard `user` field of the chat-completions request (many bots and SDKs can set it, e.g. `user="qq:12345"`).

Optional headers: `X-Membrain-Platform` (for example `bilibili`), `X-Membrain-Group` (a group or live room; by default a person's group memory is kept apart from their private chats, change with `--group-memory shared`), and `X-Membrain-Session` (your own conversation ID; by default one day of one chat is a session). Requests with no user share one memory, which suits a personal companion chat.

## What it does to each request

For `POST /v1/chat/completions` it adds one system message just before the person's latest message, containing the memory notice, their earlier messages from other days, and older messages related to the current one. The start of the conversation is left untouched so the provider's prompt cache (DeepSeek's included) keeps working. After the reply arrives (streamed or not) it stores the person's message and the reply. Every other endpoint is forwarded unchanged.

The proxy does not add tools, so the model cannot write notes or pause recording through it; text inside `<private>…</private>` and values that look like keys or passwords are still never stored.

## Serving other machines

By default the proxy listens only on this computer and forwards the API key your bot sends. To let bots on other machines use it, keep the provider key on the proxy and give the bots a separate token:

```sh
export MEMBRAIN_UPSTREAM_KEY=sk-your-provider-key
export MEMBRAIN_PROXY_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
membrain proxy --upstream https://api.deepseek.com/v1 --host 0.0.0.0
```

Bots then use the token as their API key. The proxy refuses to listen beyond this computer without both settings, because anyone who can reach it could otherwise read memories.

## Status

Tested with a stand-in provider and a real memBrain service (two live-stream viewers kept apart across days, streaming and non-streaming replies, token mode), and as a running process with an HTTP client. On Windows 11 it ran against the real DeepSeek API (`deepseek-chat` and the reasoning model `deepseek-v4-pro`): streaming and non-streaming replies passed through unchanged (reasoning chunks included), two people told apart by `X-Membrain-User` each recalled their own facts in a later session and not the other's, and `/v1/models` and provider errors were forwarded. Not yet run with a specific bot project.
