# OpenClaw 的 memBrain 插件

为你的 [OpenClaw](https://github.com/openclaw/openclaw) 助手在它服务的每个渠道（WhatsApp、Telegram、Discord、iMessage 等）上提供长期记忆，并与同一台电脑上的 Claude Code、Codex 和 Hermes 共享。它与 OpenClaw 自带的记忆并行工作，不占用记忆插槽。

## 安装

需要 Gateway 的 `PATH` 中有 [uv](https://docs.astral.sh/uv/)。

```sh
curl -L https://github.com/chriswu727/memBrain/archive/refs/heads/main.zip -o membrain.zip
unzip -q membrain.zip 'memBrain-main/integrations/openclaw/membrain/*'
openclaw plugins install ./memBrain-main/integrations/openclaw/membrain --force --accept-capabilities
openclaw config set plugins.entries.membrain.hooks.allowConversationAccess true
```

OpenClaw 2026.10 在你接受本地插件声明的能力（`--accept-capabilities`）之前会拒绝安装，并要求任何读取提示词或回复的非内置插件都获得明确的会话访问授权。当前的 OpenClaw 版本需要 Node 24.22.1 或更高版本。首次加载时，插件会用 uv 安装 memBrain 运行时并在后台启动本地服务；就绪之前的对话照常进行，只是没有记忆。

## 功能

| OpenClaw hook | memBrain 行为 |
| --- | --- |
| `before_prompt_build` | 以 `appendSystemContext` 加入会话说明、已保存的笔记和早期消息（每个会话五分钟内保持不变，以便模型服务的提示词缓存生效），并以 `prependContext` 加入与本次请求相关的早期消息。暂存本次请求。 |
| `agent_end` | 对成功的运行，保存请求和最终的助手回复。 |
| 工具 | `membrain_note`、`membrain_search`、`membrain_pause`、`membrain_resume`。 |

设置插件的 `directory` 可以让这个助手的记忆与其他宿主分开。`membrain plugin ui` 可打开记忆页面；`<private>…</private>` 中的文字和看起来像凭据的内容永远不会被保存。

## 状态

插件逻辑已用 Node 针对真实的 memBrain 运行时测试：两个会话共享记忆、笔记、检索和暂停。清单文件按 OpenClaw 的插件文档和源码编写。在 Windows 11 上，真实的 OpenClaw 2026.10.8 Gateway（模型为 DeepSeek V4 Pro）用上面的命令加载了它：hook 保存了每一轮对话，模型用 `membrain_note` 保存了笔记；在关闭 OpenClaw 自带的 `memory-core` 插件、且工作区文件中没有该事实的情况下，新会话召回了之前会话中告知的车牌号和车位。尚未在消息渠道上运行。
