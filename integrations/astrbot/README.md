# memBrain for AstrBot

Long-term memory for every person who talks to your [AstrBot](https://github.com/AstrBotDevs/AstrBot) bot, on QQ (NapCat / aiocqhttp, QQ official), WeChat, Telegram, Feishu, DingTalk, Discord, Slack and the other platforms AstrBot supports, with any model provider, DeepSeek included. Memory stays on the bot's machine; no extra API key.

## Install

Copy the plugin into AstrBot's plugin directory and restart AstrBot (or reload plugins in the dashboard). AstrBot installs the Python dependency from `requirements.txt` on load.

```sh
cd AstrBot/data/plugins     # in Docker: the mounted data/plugins directory
curl -L https://github.com/chriswu727/memBrain/archive/refs/heads/main.zip -o membrain.zip
unzip -q membrain.zip 'memBrain-main/integrations/astrbot/astrbot_plugin_membrain/*'
mv memBrain-main/integrations/astrbot/astrbot_plugin_membrain . && rm -rf memBrain-main membrain.zip
```

The first start creates a local PostgreSQL database inside AstrBot's `data/plugin_data/membrain`, so memory survives container upgrades and is included in AstrBot data backups. It needs Linux x86_64, macOS or Windows; ARM Linux (for example a Raspberry Pi) is not supported yet.

## What it does

- **Before each model request** it appends to the system prompt: the notes the bot keeps about this person, their messages from earlier days, and earlier messages related to the current one.
- **After each reply** it stores the person's message and the reply.
- **Tools** for the model: `membrain_note` (keep a lasting fact, replacing an outdated one), `membrain_search`, `membrain_pause` and `membrain_resume` (when someone says "don't remember this").

Each person has their own memory: what Alice told the bot is never shown in a reply to Bob. By default what someone says in a group is also kept apart from their private chats, so a group reply cannot reveal a private conversation.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `group_memory` | `separate` | `shared` lets a person's group messages and private chats share one memory. |
| `record_groups` | on | Off: group messages are neither recalled nor stored. Only messages that reach the model (for example when the bot is mentioned) are seen. |
| `roleplay_character` | empty | If the bot plays a fictional character, its name: the story's memory is kept apart from each person's real-life memory. |
| `share_real_life` | off | Show each person's non-sensitive real-life notes in the role-play, marked as real life. |
| `directory` | empty | Where memory is kept; empty means `data/plugin_data/membrain`. |

Wrap text in `<private>…</private>` to keep it out of memory; values that look like passwords, API keys or tokens are removed before storage.

## Status

Tested with stand-ins for AstrBot's plugin API and a real memBrain service (two QQ users kept apart, a group kept apart from a private chat, pause), and the tool docstrings are checked against AstrBot's parameter format. On Windows 11 it ran inside AstrBot 4.29.3 (source install, Python 3.12) with DeepSeek through AstrBot's own WebChat: AstrBot installed the dependency from `requirements.txt`, the first start created the memory in `data/plugin_data/membrain`, the model saved notes and searched with the plugin's tools, each of two users recalled their own facts in a new conversation, and neither saw the other's. That run found a bug that stopped every first start (fixed). QQ through NapCat has not been tried yet.
