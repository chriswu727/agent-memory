# Agent Memory

一个独立于模型的认知记忆运行时。完整目标见 [架构与验收规范](architecture/architecture.html)。当前工程已进入实现，架构文档中的目标能力不等于当前已经实现的能力。

## 本地运行

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和可运行的 Docker。数据库使用项目独立的数据卷，不连接或修改其他项目的数据库。

```sh
uv sync --frozen
uv run agent-memory init
docker compose up -d --wait
uv run agent-memory migrate
uv run agent-memory serve
```

另开一个终端运行持久化任务处理器：

```sh
uv run agent-memory worker
```

访问 <http://127.0.0.1:8787>。访问密钥保存在 `.local/access-token`，在工作台中输入后创建或选择一个大脑。`init` 只运行一次；配置存在时不会覆盖。`.env` 与 `.local/` 包含本机凭据，已排除在版本管理之外。

数据库默认绑定 `127.0.0.1:55432`，HTTP 服务绑定 `127.0.0.1:8787`。工作台、API 与后台处理器共用一个内核。可用 `uv run agent-memory worker --once` 手动处理每个大脑的一项待处理工作。`docker compose down` 停止数据库而保留数据卷；不要使用 `down -v`，除非确实要删除整个数据库。

## 接入应用

Python 客户端位于 `agent_memory.client`，完整示例是 [examples/connect.py](examples/connect.py)。TypeScript 客户端位于 [sdk/typescript](sdk/typescript)。两者使用相同的 HTTP 契约：

1. `observe` 保存真实接收或交付的消息，使用稳定的来源 ID。
2. `prepare` 读取当前情境、工作状态和相关记忆。
3. `prepared_context` / `preparedContext` 生成实际传给模型的资料文本。
4. 模型需要进一步线索时使用 `recall`、`inspect`。
5. 输出确实交付后，再 `observe` 输出并 `commit` 本轮状态。
6. 宿主压缩或中断前使用 `checkpoint` 保存状态。

不要把完整 `ContextPacket` 控制封套注入模型。它包含权限、索引进度和诊断数据；渲染函数只交付当前所需的资料块及来源。`prepare` 默认的 2,000 token 额度指宿主扣除系统指令、当前输入和输出预留后分配给记忆的预算。当前使用 UTF-8 字节数作保守估计，不声称是目标模型的精确 tokenizer。`recall` 和 `inspect` 作为工具结果包含更多可验证来源，默认额度独立设为 16,000。

`POST /v1/actions` 接受 [JSON Schema 契约](architecture/memory-contract.schema.json)。身份由 API key 绑定，调用参数只能选择授权的大脑，不能自行指定租户或认证身份。运行中的能力声明位于 `GET /v1/capabilities`。

### MCP

MCP 使用官方 Python SDK 1.x 的 stdio 协议，版本由 `uv.lock` 固定。客户端配置示例：

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/agent-memory", "agent-memory", "mcp"],
      "env": {
        "MEMORY_BASE_URL": "http://127.0.0.1:8787",
        "MEMORY_API_KEY": "<your local access token>",
        "MEMORY_BRAIN_ID": "<your brain ID>"
      }
    }
  }
}
```

MCP 允许模型主动回忆和提交有来源标记的笔记。它不会自动读取宿主完整对话、判断消息是否送达或接管压缩。MCP 提交的笔记固定标记为 assistant 来源，不能冒充用户表达。完整的自动记录与回答前回忆需要宿主接入生命周期接口。

## 当前实现边界

| 部分 | 当前实现 |
| --- | --- |
| 保存 | PostgreSQL 原始事件、来源修订、幂等提交、事务内任务入队 |
| 工作状态 | 分支状态、修订检查、压缩交接 |
| 回忆 | 中文与关键词线索、词法特征索引、原文补读、来源检查、预算和覆盖报告 |
| 整理 | 确定性的经历捕获，保留说话人、表达性质和原文；不是模型语义提炼 |
| 更正与删除 | 版本更新、同步失效、派生依赖清理、档案恢复时检查删除记录 |
| 隔离 | 服务端身份绑定、应用查询过滤、PostgreSQL 强制行级隔离 |
| 接入 | HTTP、Python、TypeScript、MCP stdio |
| 界面 | 创建大脑、记录、回忆、来源、更正、删除、导出与诊断 |

语义模型提炼、跨经历概括、学习程序性方法、条件触发的前瞻记忆、经过校准的衰减与关联传播、多模态理解、宿主插件、云端账号与共享权限仍需实现和验证。当前仅支持拥有者私有范围；共享集合请求会明确拒绝。数据库装有 pgvector，当前候选向量是带版本标识的词法特征哈希，不是语义 embedding。

原始表达有来源，不代表其中的客观世界事实已经被独立验证。默认词法回忆返回有依据的候选，并以 `partial` 说明语义充分性尚未判断。引用原文中的一个子串，不足以证明它可以独立成为新的结论。

当前只有保持原文含义和来源归属的直接表述可进入有依据的工作状态。结构化事实提炼与计划状态验证仍待语义处理器实现；未决事项目前只接受 `unknown` 状态，不能把一条有引用的笔记直接宣布为已经完成。

当前档案导入导出和删除过滤不等于完成了生产备份体系。数据库物理备份、独立删除清单的恢复流程、迁移规模、运行 SLA 和长期对话效果，需要独立部署与实测。关闭和重开服务不会删除已提交记录。

当前单份档案最多 16 MB，每类记录最多 5,000 条；超出时明确报错，不会静默截断为不完整备份。分片导出和大规模恢复仍需实现。

## 工程结构

- `src/agent_memory/cognition.py`：经历捕获、线索回忆、证据边界和工作区组装。
- `src/agent_memory/service.py`：事务、版本、生命周期与十个记忆操作。
- `src/agent_memory/storage.py`、`migrations/`：PostgreSQL 数据模型与隔离。
- `src/agent_memory/api.py`、`web/`：认证、HTTP 与本地工作台。
- `src/agent_memory/client.py`、`mcp_server.py`、`sdk/typescript/`：宿主接入。
- `architecture/`：完整目标、机器可读契约和 48 个系统验收场景。

## 验证

先启动并迁移项目专用数据库，然后运行：

```sh
uv run pytest -q
uv run ruff check src tests examples
uv build
```

TypeScript 测试与类型检查命令见 `sdk/typescript/package.json`。没有配置数据库时，数据库相关测试会跳过；跳过不代表通过。测试使用新建的随机租户与大脑，不依赖私人对话，也不调用付费模型或 Codex／Claude 订阅。

接口结构校验、确定性回归测试、真实 MCP 协议往返和数据库测试，只验证各自覆盖的行为。完整架构中的 48 个系统场景尚不能据此宣称全部通过，也未宣称在长期记忆质量上超过任何基线。
