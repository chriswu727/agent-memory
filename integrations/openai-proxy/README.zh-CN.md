# memBrain 记忆代理（适用于任何可设置 `base_url` 的机器人）

很多机器人通过 OpenAI 兼容接口调用模型：基于 NoneBot、Koishi 或 LangBot 的 QQ 机器人，读取抖音或 B 站直播弹幕的 AI 虚拟主播，SillyTavern，Discord 和 Telegram 机器人，以及大多数接入 DeepSeek 的方案。`membrain proxy` 位于机器人和模型服务之间，为每一个和机器人聊天的人提供独立的长期记忆，无需修改机器人本身。

## 启动

```sh
uv tool install --python 3.12 'membrain-ai[embedded,semantic] @ https://github.com/chriswu727/memBrain/archive/refs/heads/main.zip'
membrain proxy --upstream https://api.deepseek.com/v1
```

`--upstream` 填机器人原来使用的 `base_url`（DeepSeek、OpenAI、硅基流动、本地 Ollama 或 vLLM 等）。然后只需把机器人的 `base_url` 改成 `http://127.0.0.1:8790/v1`，API key 和模型名保持不变。首次启动会创建本地数据库；记忆保存在 memBrain 的数据目录（或 `--directory` 指定的目录）。

## 区分不同的人

代理按以下方式区分记忆：

1. `X-Membrain-User` 请求头，或
2. chat-completions 请求中标准的 `user` 字段（很多机器人和 SDK 都可以设置，例如 `user="qq:12345"`）。

可选请求头：`X-Membrain-Platform`（例如 `bilibili`）、`X-Membrain-Group`（群或直播间；默认一个人在群里的记忆与私聊分开，可用 `--group-memory shared` 改为共用）、`X-Membrain-Session`（你自己的会话 ID；默认同一聊天的一天为一个会话）。没有用户标识的请求共用一份记忆，适合个人陪伴聊天。

用于角色扮演（SillyTavern 等）时，发送 `X-Membrain-Roleplay: <角色名>`：这个故事会有自己的记忆，永远不会和这个人的真实生活记忆混在一起。再加上 `X-Membrain-Share-Real-Life: 1`，可以在故事中同时看到这个人的真实生活笔记（标记为敏感的除外）。

## 对每个请求做了什么

对 `POST /v1/chat/completions`，代理在这个人的最新消息之前加入一条 system 消息，内容是记忆说明、他在其他日子发过的消息，以及与当前消息相关的早期消息。对话开头保持不变，因此模型服务（包括 DeepSeek）的提示词缓存仍然有效。回复返回后（流式或非流式），代理保存这个人的消息和回复。其他所有接口原样转发。

代理不会添加工具，因此模型无法通过它记笔记或暂停记录；`<private>…</private>` 中的文字以及看起来像密钥或密码的内容仍然不会被保存。

## 供其他机器使用

默认情况下代理只监听本机，并转发机器人发送的 API key。若要让其他机器上的机器人使用，请把模型服务的 key 保存在代理上，并给机器人一个单独的令牌：

```sh
export MEMBRAIN_UPSTREAM_KEY=sk-你的模型服务key
export MEMBRAIN_PROXY_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
membrain proxy --upstream https://api.deepseek.com/v1 --host 0.0.0.0
```

之后机器人把这个令牌当作 API key 使用。没有这两项设置时，代理拒绝监听本机以外的地址，否则任何能访问它的人都可能读取记忆。

## 状态

已使用模拟的模型服务和真实的 memBrain 服务测试（两位直播观众跨天互相隔离、流式与非流式回复、令牌模式），并作为真实进程用 HTTP 客户端测试过。在 Windows 11 上已接入真实的 DeepSeek API（`deepseek-chat` 和推理模型 `deepseek-v4-pro`）运行：流式和非流式回复都原样转发（包括推理内容分片），用 `X-Membrain-User` 区分的两个人在之后的会话中各自召回了自己的信息、看不到对方的内容，`/v1/models` 和模型服务的错误也会被转发。尚未配合具体的机器人项目运行。
