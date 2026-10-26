# Agent Memory

**受人类记忆机制启发的 AI Agent 记忆引擎。**

[English](README.md) | 简体中文

通过分层回忆、选择性遗忘和可追溯来源，让 Agent 在不同对话之间保留并使用有用的记忆。

Agent Memory 以本地服务运行。将 Agent 的对话生命周期接入一次后，运行时会自动捕获消息，在回复前准备相关记忆，并在后台学习。进程重启或切换已配置的回复模型后，Agent 仍使用同一个大脑。

[快速开始](#quickstart) · [Python 接入](#python-integration) · [记忆如何工作](#how-memory-works) · [状态与验证](#status-and-verification) · [完整参考](docs/reference.md)

> **预发布阶段。** 同步文本运行时已实现，长期记忆质量仍在验证中。桌面安装已在 macOS 上验证。MCP 目前仅提供记忆工具。详见[支持范围与已知限制](#status-and-verification)。

<a id="desktop-setup"></a>
<a id="quickstart"></a>

## 快速开始

安装 [uv](https://docs.astral.sh/uv/)，并启动 Docker Desktop。准备好兼容 Chat Completions 的模型服务地址、模型名称和 API key；记忆学习还要求模型支持 JSON 模式。

```sh
uv tool install --python 3.12 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
agent-memory desktop setup
agent-memory desktop configure-chat --use-cognition
agent-memory desktop chat
```

在 `setup` 中填写用于记忆学习的模型配置。`configure-chat --use-cognition` 明确选择复用这份配置来生成回复。如需使用不同的回复模型，请运行不带该参数的 `agent-memory desktop configure-chat`。

设置会创建一个持久大脑，并启动数据库、API 和后台任务处理器。无需克隆仓库，也无需分别启动这些服务。如果终端找不到安装后的命令，请运行 `uv tool update-shell`，然后重新打开终端。

**数据存储在本地；模型处理取决于你配置的提供方。** 聊天和语义学习可能产生 API 费用，并将相关内容发送给该提供方。跳过认知模型配置后，仍可使用存储和词面回忆，但模型驱动的学习会被禁用。较慢的推理模型可能需要显式启用[推理预设并调整提供方配置](docs/reference.md#desktop-setup)。

### 体验跨会话的连续性

先聊一个普通任务，例如：

> 我正在写一篇短篇小说。每次写半小时最适合我。

继续交流，然后输入 `/quit`。查看后台学习状态，再在同一安装中打开另一段对话：

```sh
agent-memory desktop learning-status
agent-memory desktop chat --conversation writing-followup
```

请 Agent 帮你安排下一次写作。这个练习可以让你观察早先的反馈是否帮助了新的请求，无需手动调用保存或搜索工具。新会话沿用同一个大脑，不是不同用户之间的隐私边界。如果学习仍在等待或已被阻塞，请先检查状态，再解读结果。

这是一个建议亲自尝试的练习，不是预先录制的结果，也不保证一定能回忆成功。[最新真实模型诊断](evaluations/v1-2026-10-25-preference-use.md)记录了一个相关案例：模型成功使用了半小时偏好，但整段回答仍存在质量问题。

<a id="automatic-integration"></a>
<a id="python-integration"></a>

## Python 接入

完成桌面设置和聊天配置后，还需在应用自己的 Python 3.12+ 环境中安装此包。请在已激活虚拟环境或包含 `.venv` 目录的项目中运行以下命令。CLI 使用的隔离环境不会让应用自动获得可导入的 SDK。

```sh
uv pip install 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
```

下面的终端示例会使用已保存的模型配置和持久大脑：

```python
from agent_memory.agent_session import open_agent
from agent_memory.runtime import DeliveryReceipt


def deliver(request):
    print(request.text, flush=True)
    return DeliveryReceipt("delivered")


with open_agent(conversation_id="writing", deliver=deliver) as agent:
    agent.respond(
        "Help me plan my next writing session.",
        message_id="writing-request-001",
    )
```

请在 Python 3.12+ 环境中运行。在实际宿主中，将 `deliver` 替换为真实的发送操作，并只在确认发送成功后返回成功回执。此示例确认的是终端写入，不代表有人已经阅读回答。每条新输入都需要一个稳定且唯一的消息 ID；只有在处理同一条消息的恢复或核对时，才复用该 ID。重放已完成的消息不会再次生成或交付回复。

如需自己的模型回调、交付渠道、恢复处理或可选的回答核验器，请参阅[自动接入参考](docs/reference.md#automatic-integration)。HTTP 和 TypeScript 客户端提供底层操作；目前开箱即用的自动文本运行时是 Python 版本。

<a id="mcp"></a>

## 通过 MCP 连接

完成设置后，为 stdio MCP 宿主生成配置：

```sh
agent-memory desktop mcp-config
```

将生成的配置添加到宿主。桥接进程会启动或连接同一个本地服务，并提供记忆工具。

**MCP 当前为 `tools_only`。** 它不会捕获每一段对话、确认消息交付或接管上下文压缩。自动捕获和回复前回忆需要通过 Python 运行时或宿主适配器接入对话生命周期。QQ／聊天框架专用插件和兼容 OpenAI 的记忆网关尚未交付。详见 [MCP 参考](docs/reference.md#mcp)。

<a id="how-memory-works"></a>

## 记忆如何工作

“受人类记忆机制启发”描述的是设计方向：不同形式的记忆、随时间和使用而变化的可访问性、线索引导的回忆，以及根据新证据进行学习。目前尚未证明其与生物记忆等价，也未达到经过验证的人类级可靠性。

```mermaid
flowchart LR
    A[收到消息] --> B[捕获来源]
    B --> C[在预算内回忆]
    C --> D[生成回复]
    D --> E[确认交付]
    E --> F[记录已交付来源]
    B -.-> G[后台学习]
    F -.-> G
    G --> H[持久记忆]
    H --> C
```

| 机制 | 运行时的行为 |
| --- | --- |
| 工作记忆与长期记忆 | 将有界的近期上下文与持久保存的情节、语义和程序性记忆表示结合。 |
| 选择性遗忘 | 根据记忆年龄、表示形式、保留优先级和已记录的使用情况调整可访问性。重要记忆可以受到保护。降低可访问性与删除数据是不同操作。 |
| 线索引导的回忆 | 通过词面检索、可选的语义嵌入和有边界的上下文关联查找候选记忆。相关的简短回复也可通过相邻来源被找到。 |
| 修订与不确定性 | 保留来源版本、条件、冲突和适用性标记，使新信息能够限定旧记忆的含义与适用范围。 |
| 可追溯的学习 | 保留支持记忆的来源引用，并在发布前检查记忆提议。模型判断仍可能出错。 |
| 持久身份 | 将大脑与回复模型、进程分离，并显式处理作用范围、重启和恢复。 |

当前上下文仍有容量上限。运行时会在预算内选择记忆；长期状态独立于当前模型提示保存。详细行为见[架构](architecture/architecture.html)、[检索设计](architecture/conversation-retrieval.md)和[运行时参考](docs/reference.md)。

## 本地运行

```sh
agent-memory desktop status
agent-memory desktop logs
agent-memory desktop learning-status
agent-memory desktop stop
agent-memory desktop start
```

停止服务会保留已提交的记忆。下一次聊天、MCP 或 SDK 连接可以重新启动服务。配置位于安装目录，记忆内容位于该安装的 Docker 数据库卷。仅复制配置目录不等于备份数据库。

参阅[更新与配置](docs/reference.md#local-status-and-configuration)、[备份与恢复](architecture/desktop-recovery.md)和[故障排查](architecture/integration-troubleshooting.md)。无需管理界面。

<a id="current-implementation-boundaries"></a>
<a id="verification"></a>
<a id="status-and-verification"></a>

## 状态与验证

当前候选版本支持**本地同步文本 Agent**，提供 Python 运行时、HTTP API、TypeScript 客户端、MCP 工具和直接终端聊天入口。核心记忆行为独立于聊天框架。桌面端到端验证目前覆盖 **macOS、uv 和 Docker** 的组合。

[最新验证报告](evaluations/v1-2026-10-25-preference-use.md)记录了：

- 完整源码测试中 **5,147 项通过**，四项受 Docker 条件限制而跳过的测试已另行执行。
- 隔离安装 wheel 后的重点测试中 **346 项通过**，另有四项已安装版本的桌面／聊天流程通过。这些测试组有重叠，不能相加作为独立测试总数。
- 三项有界的 DeepSeek 宿主观察满足了六项范围有限的标准，但整段回答仍有缺陷。这不代表通过了长线验收。

已知限制仍然存在：模型可能把历史信息当作当前事实，检索可能包含无关内容，语义核验也可能拒绝有效提议。完整长线验收和真实时间流逝下的老化验证尚未完成。目前尚未交付流式输出、工具循环、多模态输入、原生一键安装，以及对任意 AI 客户端的完整接入；Windows／Linux 桌面端尚未进行实机验收。性格与情绪建模属于后续研究方向。

<a id="manual-deepseek-evaluation"></a>

[验收标准](architecture/v1-acceptance.md)、[已关闭的长线失败记录](evaluations/v1-2026-10-25-longitudinal-v5-stopped.md)和[评估参考](docs/reference.md#manual-deepseek-evaluation)提供可复现的方法、保留的失败结果与证据边界。所有历史报告继续保留。

## 文档

| 入口 | 内容 |
| --- | --- |
| [运行时与接入参考](docs/reference.md) | 详细设置、模型配置、SDK／API／MCP、限制、监控和评估命令。 |
| [架构与验收规范](architecture/architecture.html) | 记忆结构、系统职责和完整设计。 |
| [自动运行时契约](architecture/autonomous-runtime.contract.json) | 生命周期职责、接入要求和实现边界。 |
| [认知预算控制](architecture/cognitive-budgets.md) | 明确的模型调用额度、用量记录和配置。 |
| [开发设置](docs/reference.md#development-setup) | 克隆仓库后的设置、数据库迁移和后台任务处理器。 |
| [本地验证](docs/reference.md#verification) | 测试命令和现有证据的覆盖范围。 |

## 反馈与贡献

正在为 Agent 加入持久记忆？欢迎[分享使用场景或安装问题](https://github.com/chriswu727/agent-memory/issues)。可复现步骤、已脱敏的日志和你使用的接入方式尤其有帮助。仓库文档及面向开发者的贡献使用英文。本页是中文概览；[英文首页](README.md)为默认入口。

如果项目对你有帮助，欢迎 **Star 仓库**，并帮助我们确定下一项值得支持的接入方式。优先使用本地检查；GitHub Actions 保持仅手动运行。
