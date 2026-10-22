# Agent Memory

一个独立于模型、无界面的 Agent 记忆运行时。目标是让宿主接入一次后，运行时持续管理 Agent 的工作记忆、长期认识和回忆过程。普通用户正常交流即可；主 Agent 不需要主动调用保存或搜索工具，系统也必须正常形成和使用记忆。

完整目标见 [架构与验收规范](architecture/architecture.html)，产品边界与宿主责任见 [自主记忆运行契约](architecture/autonomous-runtime.contract.json)。当前已实现底层记忆服务；自动生命周期接入和完整认知控制器仍待实现，不能把当前低层接口描述为已经完成的自主大脑。

## 产品与接入目标

接入单位是一个具有稳定身份的 Agent。大脑身份独立于模型、进程和某个聊天窗口；参与者、关系与任务分别限定记忆范围。宿主通过适配器交出消息生命周期和记忆上下文的管理权，适配器负责调用底层操作，业务代码不逐轮手动编排 `observe / prepare / commit`。

每轮运行时自动接收真实输入和工具结果，理解当前目标与指代，激活相关记忆，必要时继续寻找，并为模型组装有预算的当前工作区。真实送达或实际结果到达后，自动更新工作状态和长期记忆候选。后台按新证据、冲突与任务结果触发巩固、关联更新和活跃度变化。

当前上下文仍然有容量限制，可以压缩临时内容；长期记忆由原始来源和版本化认知状态承担，不再依赖层层覆盖的滚动摘要。接入成功不能只用“多注入了一段检索结果”判断，需要验证持久状态、主动联想、理解更新和重启后的连续性。

完整接入必须在主 Agent 的记忆工具被禁用时仍能运作。MCP 是补充操作接口；仅支持工具调用的连接按实际能力报告。没有消息、推理或上下文生命周期控制权的宿主，不能标为完整接入。模型/API 代理若无法确认最终送达，也不能把生成完成当作用户已收到回复。

产品无需管理页面。开发诊断通过经过鉴权的状态、指标和结构化运行日志接口提供：能追踪本轮使用了哪些记忆及其来源、为何继续或停止回忆、发生了什么版本变化、成本与失败原因。日志默认不复制聊天正文，也不记录模型隐藏推理。现有本地工作台只是可移除的开发辅助，不是产品入口或验收前提。

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

服务地址为 <http://127.0.0.1:8787>，访问密钥保存在 `.local/access-token`。当前开发工作台也在此地址，可用于手工检查底层行为；目标产品不要求打开它。`init` 只运行一次；配置存在时不会覆盖。`.env` 与 `.local/` 包含本机凭据，已排除在版本管理之外。

数据库默认绑定 `127.0.0.1:55432`，HTTP 服务绑定 `127.0.0.1:8787`。工作台、API 与后台处理器共用一个内核。可用 `uv run agent-memory worker --once` 手动处理每个大脑的一项待处理工作。`docker compose down` 停止数据库而保留数据卷；不要使用 `down -v`，除非确实要删除整个数据库。

## 当前底层接口

Python 客户端位于 `agent_memory.client`，底层流程示例是 [examples/connect.py](examples/connect.py)。TypeScript 客户端位于 [sdk/typescript](sdk/typescript)。以下是适配器需要编排的底层 HTTP 契约，并非目标产品要求用户或主 Agent 逐步操作的方式：

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
| 开发辅助 | 可移除的本地工作台；当前诊断接口尚不构成完整 monitoring/logging 接口 |

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
- `architecture/`：完整目标、机器可读契约和 52 个系统验收场景。

## 验证

先启动并迁移项目专用数据库，然后运行：

```sh
uv run pytest -q
uv run ruff check src tests examples
uv build
```

TypeScript 测试与类型检查命令见 `sdk/typescript/package.json`。没有配置数据库时，数据库相关测试会跳过；跳过不代表通过。测试使用新建的随机租户与大脑，不依赖私人对话，也不调用付费模型或 Codex／Claude 订阅。

接口结构校验、确定性回归测试、真实 MCP 协议往返和数据库测试，只验证各自覆盖的行为。完整架构中的 52 个系统场景尚不能据此宣称全部通过，也未宣称在长期记忆质量上超过任何基线。
