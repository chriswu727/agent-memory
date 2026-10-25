# Agent Memory

[English](README.md) | 简体中文

一个独立于模型、无界面的 Agent 记忆运行时。目标是让宿主接入一次后，运行时持续管理 Agent 的工作记忆、长期认识和回忆过程。普通用户正常交流即可；主 Agent 不需要主动调用保存或搜索工具，系统也必须正常形成和使用记忆。

完整目标见 [架构与验收规范](architecture/architecture.html)，产品边界与宿主责任见 [自主记忆运行契约](architecture/autonomous-runtime.contract.json)。当前已实现底层记忆服务、同步文本自动生命周期，以及可选的情境理解、语义巩固和语义回忆：结合最近对话判断当前目标，生成有来源的认识与经历结构，按预算查找并选择记忆。完整宿主接入与长期记忆效果仍需验证，当前不能视为完整自主大脑。

后续认知内核的具体改造、实施依赖和行为评估见[认知记忆架构实施计划](architecture/cognitive-memory-plan.md)。计划中的新增能力仍待实现，不代表当前运行状态。

## 产品与接入目标

接入单位是一个具有稳定身份的 Agent。大脑身份独立于模型、进程和某个聊天窗口；参与者、关系与任务分别限定记忆范围。宿主通过适配器交出消息生命周期和记忆上下文的管理权，适配器负责调用底层操作，业务代码不逐轮手动编排 `observe / prepare / commit`。

每轮运行时自动接收真实输入和工具结果，理解当前目标与指代，激活相关记忆，必要时继续寻找，并为模型组装有预算的当前工作区。真实送达或实际结果到达后，自动更新工作状态和长期记忆候选。后台按新证据、冲突与任务结果触发巩固、关联更新和活跃度变化。

当前上下文仍然有容量限制，可以压缩临时内容；长期记忆由原始来源和版本化认知状态承担，不再依赖层层覆盖的滚动摘要。接入成功不能只用“多注入了一段检索结果”判断，需要验证持久状态、主动联想、理解更新和重启后的连续性。

完整接入必须在主 Agent 的记忆工具被禁用时仍能运作。MCP 是补充操作接口；仅支持工具调用的连接按实际能力报告。没有消息、推理或上下文生命周期控制权的宿主，不能标为完整接入。模型/API 代理若无法确认最终送达，也不能把生成完成当作用户已收到回复。

产品无需管理页面。开发诊断通过经过鉴权的状态、指标和结构化运行日志接口提供：能追踪本轮使用了哪些记忆及其来源、为何继续或停止回忆、发生了什么版本变化、成本与失败原因。日志默认不复制聊天正文，也不记录模型隐藏推理。现有本地工作台只是可移除的开发辅助，不是产品入口或验收前提。

### 独立产品与可选接入

产品首先面向通过 API key 使用模型的 Agent。直接接入和第三方机器人插件共用同一个记忆核心；AstrBot、LangBot、NapCat 等是可选接入途径，不是安装、运行或发展核心能力的前提。依赖方向固定为「宿主适配器 → 公共 API / SDK → 记忆核心」，核心不导入聊天框架，也不按框架分叉记忆算法。第三方专用依赖应放在可选适配包中。

核心拥有持久记忆、来源、身份与范围、回忆、学习、衰减和恢复规则。适配器负责真实事件转换、稳定身份映射、生命周期接线和交付回执；通过公共接口访问记忆，不直接访问数据库或维护另一份权威记忆。每轮应只有一个生命周期编排者，避免插件和模型代理同时捕获、注入或生成。

更换模型、渠道或聊天框架时，应能继续绑定原有大脑；跨平台参与者与会话的关联必须由可信配置明确映射，不能仅凭昵称或模型猜测合并，也不能将私聊记忆自动带入群聊。卸载适配器不应删除核心数据。独立使用和适配器替换都需经过重启恢复、重试去重、范围隔离及更正删除的验收，不能把目标写成已完成能力。

核心、HTTP API 和 Python 同步文本运行时不依赖上述聊天框架。桌面聊天命令与 `open_agent()` 会话入口可以直接连接已配置的 Chat Completions 模型。外部宿主仍需提供真实发送回执和稳定消息身份。模型兼容网关及 QQ 框架适配插件尚未交付；MCP 继续作为补充接口，不能代替缺失的生命周期接入。

## 电脑版使用

先安装 [uv](https://docs.astral.sh/uv/)，并启动 Docker Desktop，然后执行：

```sh
uv tool install --python 3.12 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
agent-memory desktop setup
```

此安装方式不需要 Git 或克隆项目，也不需要分别启动数据库、API 和 worker。如果安装提示命令目录不在 `PATH`，执行 `uv tool update-shell` 后重新打开终端。首次设置会询问用于记忆学习的模型服务地址、模型名称和 API key；输入密钥时不回显。当前支持兼容 Chat Completions 的提供方。模型名称和端点由用户提供，不默认使用任何订阅或调用模型试探配置。暂时跳过模型配置也可运行持久存储和词面回忆，但不会启用模型驱动的语义学习；状态中会标记 `cognition: not_configured`。

设置会创建固定大脑身份及本机凭据，准备专属数据库，完成迁移，并在后台启动记忆服务和任务处理器。重复执行不会重新创建大脑或清空记忆。API 默认使用 `127.0.0.1:8788`，数据库默认使用 `127.0.0.1:55433`；首次设置遇到端口占用时选择空闲端口并保存。已有安装遇到端口冲突会报告问题，不停止其他进程或悄悄换到另一个大脑。

推理模型需要超过默认认知额度的时间或输出时，可以显式使用 `desktop setup --cognition-preset reasoning`。该预设允许提议与核验合计 240 秒、每次请求 16,384 个输出 token、65,536 字节解码内容；它不选择模型、不启用思考模式，也不提高累计额度。已有安装的修改方式及 DeepSeek 实测取舍见[认知策略与恢复](architecture/cognition-policy.md)。

完成后，设置结果会给出不含密钥的 MCP 配置，并将它保存到本机配置目录的 `mcp.json`。也可以重新获取：

```sh
agent-memory desktop mcp-config
```

将这份配置添加到支持 stdio MCP 的宿主即可连接记忆工具。不同宿主的配置入口不同，当前不会自动修改外部客户端配置。宿主启动这条 MCP 连接时会自动启动或连接本机服务；退出 MCP 连接后，服务继续在后台处理任务。电脑重启后，下次 MCP 连接或 SDK 的 `open_client()` 会重新启动服务并沿用原大脑，不必重新设置。

**MCP 连接仍是 `tools_only`。** 它不会自动读取宿主全部聊天、接管 compact 或确认消息送达。需要自动记忆的 Agent 应使用下文的 Python 运行时，并由宿主提供消息生命周期。安装成功、认知模型已配置和宿主已自动接入是三个不同状态。

### 直接聊天与 Python 会话

完成 setup 后，可明确选择复用记忆学习的模型来回答，或不带参数运行 `configure-chat`，单独填写聊天模型：

```sh
agent-memory desktop configure-chat --use-cognition
agent-memory desktop chat
```

聊天使用同一个持久大脑，自动捕获输入、回忆、生成回答、记录真实终端交付并更新工作状态，不需要聊天框架或主动调用记忆工具。下次启动继续连接原大脑及默认会话；用 `--conversation <稳定标识>` 和 `--branch <稳定标识>` 选择会话。新会话不是新大脑，也不是租户隐私隔离边界；无关用户应分别使用获授权的大脑身份。

回答成功交付不代表学习成功。聊天在完成后只查询本轮及对应的情境分析任务，并将不可用或未完成的分析提示到 stderr。交互聊天启动时另查一次后台进度；脚本单次消息不扫描这些历史。诊断失败不会重试模型，也不会把已交付的回答改成失败操作。

回答配置保存在私有 `chat.json`，与 `profile.json` 中的认知配置分开。它是受文件权限保护的本地明文，不是系统钥匙串。切换回答模型不重启记忆服务、不改变大脑。`configure-chat` 支持 `--base-url`、`--model`、`--api-key-env`、可选 `--thinking` 和 `--non-interactive`。单次请求默认最多 4,096 个输出 token、180 秒网络超时与经过时间检查、1 MiB 响应体；可用 `--max-output-tokens`、`--timeout-seconds`、`--max-response-bytes` 修改。这些不是累计费用上限，也不能保证远端请求在硬截止时间被取消。运行时另外限制实际交付文本为 64,000 字节。只配置回答模型不会启用模型驱动的记忆学习，聊天入口会提示这一状态。

Python 入口复用同一模型配置及会话保护：

```python
from agent_memory.agent_session import open_agent
from agent_memory.runtime import DeliveryReceipt

def deliver(request):
    channel.send(request.text)
    return DeliveryReceipt("delivered")

with open_agent(conversation_id=conversation_id, deliver=deliver) as agent:
    result = agent.respond(text, message_id=message_id)
```

宿主的 Python 环境也需要安装此包；上例的 `channel`、输入和稳定身份由宿主提供。`open_agent()` 自动启动或连接本机服务，支持 `directory`、`branch_id`、`instructions`、`policy` 及 `start_service=False`。自定义 `generate` 回调还须提供稳定的 `generator_fingerprint`，标识实际模型和配置。没有 `deliver` 时结果仍是草稿，会话保持待处理；之后带发送回调重新打开并调用 `resume()`，可交付符合条件的现有草稿而不重新生成。需要自定义回忆控制器或回答核验时，可用下文更底层的 `BrainRuntime`；桌面聊天不会暗中启用这些付费调用。

本地会话在打开期间持有按大脑、会话和分支划分的跨进程锁，同一安装下相同范围的第二个会话不能并行运行另一轮。这不覆盖其他电脑、更底层的 `BrainRuntime` 实例或直接 API 调用。小型持久恢复文件只保存待处理身份及校验值，不复制消息和回答正文，数据库保持权威来源。未完成轮次会阻止另一条消息；仍可能生成或发送时，模型配置、指令和运行策略保持冻结。已经送达或完成的轮次可在配置改变后收尾或重放，仍保留数据库身份和来源检查，不重新生成或发送。仅轮换密钥不会改变生成配置指纹。重启时应保留安装目录。

交互命令为 `/quit`、`/resume`、`/retry <原始输入>`，恢复需显式触发。如果输入捕获中断，重交完全相同的原文，CLI 会复用已保存的消息身份。脚本调用方式：

```sh
agent-memory desktop chat --conversation example --message 'Hello' --message-id event-001
agent-memory desktop chat --conversation example --resume
```

脚本重试保持消息 ID 与正文不变。用旧 ID 提交不同输入时，会在新建待处理状态前拒绝；恢复时也会将输入校验值与数据库核对。已完成轮次的重放不会再次输出或发送回答。模型调用或终端写入结果不确定时保留阻塞，避免自动重复付费或发送；`/resume` 无法解决未知外部结果。终端回执只证明输出已写入并 flush，不证明用户已经阅读。

需要明确结束阻塞轮次时，使用 `/cancel <准确轮次ID>` 或 `chat --cancel <准确轮次ID>`；来源与失败记录会保留，之后可使用新的消息 ID 继续。生成或发送结果未知时，须显式使用 `/cancel-uncertain <准确轮次ID>`，或在脚本取消命令上加 `--acknowledge-uncertain`。取消不重试模型、不发送回答，也无法撤销可能已经发生的付费或发送；日志保留 `generation_uncertain` 或 `delivery_uncertain`。已经确认交付但尚未提交的轮次应恢复，不能取消。Python 对应 `agent.cancel(turn_id=..., acknowledge_uncertain=False)`。取消遇到网络失败时保留待处理文件，可再次依据持久日志协调同一次取消。需要更换被冻结的策略或模型时，也应先明确取消未交付轮次，不要手动删除恢复文件。

选择恢复操作前，可以先查看未完成轮次：

```sh
agent-memory desktop turn-status --conversation example --branch main
```

使用原来的会话、分支与安装目录；默认会话和分支分别为 `desktop-chat`、`main`。此命令只从已经运行的服务读取有界元数据，不生成、发送、恢复或取消操作。`turn: null` 只表示检查时没有找到占位的未完成轮次，不证明所有消息均已交付。提供方错误、会话被占用和外部结果不确定时的处理见[接入与恢复诊断](architecture/integration-troubleshooting.md)。

此入口仅支持 Chat Completions 同步文本，保留运行时提供的消息，拒绝截断回复及工具调用，不自动重试提供方请求。它尚不支持流式、工具循环、多模态、模型代理或自动接管其他客户端。`ChatCompletionsGenerator.last_usage` 与 `last_request_id` 提供最近一次调用经过校验的有界诊断；缺失或无效用量保持未知，它们不是持久账单账本。完整认知质量仍需单独评估。默认情境分析策略保持 `always`，`chat --orientation on_demand` 显式选择下文说明的取舍。`--instructions-file` 接受最多 64,000 字节的 UTF-8 文件，实际仍受更小的完整输入预算约束。

### 本机状态与配置

```sh
agent-memory desktop status
agent-memory desktop logs
agent-memory desktop stop
agent-memory desktop start
agent-memory desktop restart
```

`status` 检查认证后的服务、数据库、大脑身份和后台处理器；模型配置存在不代表已验证提供方可用或记忆质量。`logs` 返回有界的结构化服务事件，不包含聊天正文和 API key。`stop` 和 `restart` 保留数据库卷及配置，不删除记忆。停止通过已认证的服务实例完成，不根据遗留 PID 杀进程。使用 `setup` 更新认知配置时，会自动重启需要更新的服务；指定 `--no-start` 则只保存配置，稍后执行 `restart` 应用。更新安装的程序后，也应执行 `desktop restart`，让后台实例加载新代码。

通过上面的源码 URL 安装后，可重新安装包并重启服务，更新到当前 `main` 分支：

```sh
uv tool install --python 3.12 --force --reinstall-package agent-memory-runtime 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
agent-memory desktop restart
```

`--reinstall-package` 会刷新源码压缩包，即使包版本号没有改变。非默认安装应继续使用同一个 `--directory`，无需创建新的配置或大脑。如果另一个 Python 宿主会导入 SDK，重新连接前也应更新它的环境；将下面的解释器路径替换为该宿主实际使用的路径：

```sh
uv pip install --python /absolute/path/to/host/python --reinstall-package agent-memory-runtime 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
```

服务启动时冻结基于内容的构建标识，覆盖运行时代码、契约和迁移。即使包版本号未变，构建内容改变也会报告 `restart_required`，`start` 不会悄悄复用旧后台。启动和迁移会拒绝未知或非连续前缀的迁移历史，降级检查会在停止现有服务之前执行。这些检查不证明历史迁移 SQL 从未被修改。真实双版本 Docker 测试已验证大脑身份与来源保留，以及不兼容降级不会停掉新版服务。

配置默认位于 macOS 的 `~/Library/Application Support/Agent Memory`、Windows 的 `%LOCALAPPDATA%\Agent Memory`，或 Linux 的 `$XDG_DATA_HOME/agent-memory`（未设置时为 `~/.local/share/agent-memory`）。所有桌面子命令都接受 `--directory <绝对目录>`；同一安装应始终使用同一目录。目录内包含本机凭据，记忆正文则持久存放在该安装的 Docker 数据卷中，复制配置目录不等于备份数据库。

使用 `desktop backup --output 私有目录 --request-id 稳定标识` 创建流式完整数据库检查点。受支持的恢复需要该备份和独立保存的最新删除登记，目标必须是新的隔离安装，并需明确确认恢复点才能激活；旧的未完成操作不会自动继续。[备份与恢复指南](architecture/desktop-recovery.md) 说明了当前删除登记的保存、敏感明文备份、新凭据以及外部宿主必须确认的恢复 epoch。

自动部署可以先只保存配置，稍后启动：

```sh
agent-memory desktop setup --non-interactive --no-start
agent-memory desktop start
```

需要非交互配置认知模型时，用 `--base-url`、`--model` 和 `--api-key-env` 指定服务地址、模型及**已设置的密钥环境变量名称**；不要将密钥值写进命令参数。可选的 `--thinking enabled|disabled` 仅用于支持该参数的提供方。桌面服务使用自己的配置，不自动读取仓库 `.env`，也不会把原开发数据库里的大脑迁移到新安装。

当前交付是本机命令行安装和服务管理，仍需要 uv 和 Docker Desktop；尚无原生一键安装包或特定宿主的完整生命周期适配。实现包含 Windows、Linux 的路径与进程处理，当前桌面端到端验收以 macOS 为范围，其他系统尚未实机验收。具体覆盖与限制见[桌面安装与恢复验收](evaluations/desktop-2026-10-24.md)。

### 开发方式

已有开发环境可继续使用以下路径。需要 Python 3.12、uv 和可运行的 Docker；在克隆的仓库目录执行。开发数据库使用项目独立的数据卷，与上述桌面安装分开。

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

`init` 只生成配置。开发接入还需要在 API 启动后创建一个大脑，并持久保存返回的 `id`；以后始终绑定这个 ID，不能每次启动都创建新大脑。例如首次创建：

```python
from pathlib import Path
from agent_memory.client import MemoryClient

with MemoryClient("http://127.0.0.1:8787", Path(".local/access-token").read_text().strip()) as memory:
    brain = memory.create_brain("My Agent")
    Path(".local/brain-id").write_text(brain["id"] + "\n")
```

数据库默认绑定 `127.0.0.1:55432`，HTTP 服务绑定 `127.0.0.1:8787`。工作台、API 与后台处理器共用一个内核。可用 `uv run agent-memory worker --once` 手动处理每个大脑的一项待处理工作。`docker compose down` 停止数据库而保留数据卷；不要使用 `down -v`，除非确实要删除整个数据库。

## 自动接入

Python 的 `BrainRuntime` 接管同步文本轮次。宿主初始化一次，提供模型生成函数和实际发送函数；业务层只提交收到的消息，不调用记忆工具或逐轮编排存取。下例面向宿主开发者，其 Python 环境也需安装 `agent-memory-runtime`（可使用同一源码 URL）；`uv tool install` 安装的命令行工具有独立环境，不会修改宿主的 Python 依赖。接入代码可以直接读取本机配置并连接固定大脑：

```python
from agent_memory.desktop_connection import open_client
from agent_memory.runtime import BrainRuntime, DeliveryReceipt

def generate(request):
    return model.generate(messages=list(request.messages))

def deliver(request):
    channel.send(request.text)
    return DeliveryReceipt("delivered")

with open_client() as memory:
    runtime = BrainRuntime(memory, generate, deliver=deliver, instructions=agent_instructions)
    result = runtime.respond(text, message_id=message_id, conversation_id=conversation_id)
```

`open_client()` 自动启动或连接本机服务，返回绑定固定 `brain_id` 的 `MemoryClient`；可传 `directory=Path(...)` 选择安装目录，或用 `start_service=False` 仅连接已运行的服务。它不要求复制访问密钥，也不读取当前工作目录的 `.env`。开发部署仍可手动构造 `MemoryClient`，沿用原数据库中的大脑。

上例的 `model`、`channel` 和消息身份来自宿主；`open_client()` 本身不捕获宿主消息。生成函数必须使用运行时提供的 `messages`，其中保留宿主系统指令，加入有来源和预算的记忆资料及当前输入。`message_id` 是宿主收到消息时持久分配的标识，重试必须不变；新进程使用同一个大脑身份即可重新接入。[examples/autonomous.py](examples/autonomous.py) 用固定回复代替回复模型，但服务端已配置的认知与 embedding 提供方仍可能在情境分析和后台处理时产生付费调用。若要让此示例不调用模型，应确保服务与后台处理器均未配置这两类提供方。

运行时自动执行输入落盘、回答前回忆、草稿保存、交付确认、有效工作状态保留与 checkpoint。每轮使用租约和修订检查；草稿写入和阶段推进、交付记录和阶段推进各自在同一事务中。`runtime.resume(turn_id)` 从持久阶段继续，完成过的轮次直接返回已交付结果，不重新生成或发送。配置认知处理器后，回答前还会理解当前情境；实际交付后才把经过来源检查的情境推断合入工作状态，推断不会被标为已证实事实。

只有宿主确认实际发送后才能返回 `delivered`；部分发送使用 `DeliveryReceipt("partially_delivered", delivered_text=actual_text)`，取消使用 `cancelled`，发送结果无法确认使用 `unknown`。没有发送函数时停在 `generated`，草稿不进入回忆。`delivered` 指宿主确认交付，不声称用户已经读过消息。

连接同一已升级服务的多个宿主，在同一个大脑、会话和分支内只能有一个未完成轮次，由持久占位保证。竞争的新轮次会在输入落库和模型调用之前收到 `scope_conflict`。释放执行租约或租约到期不会释放会话占位：已保存草稿或结果不确定的外部操作仍属于原轮次。提交另一条消息前，需要恢复原轮次或显式关闭它。其他会话和分支可独立运行；旧的已完成消息仍可在新轮次占位期间重放。`scope_reserved` 和 `reserved_scope_count` 将会话占用与活动执行租约分别展示。

交付记录区分宿主确认发送和记忆状态提交成功。在有效发送授权之后，`record_output` 保存有界的 `delivery_receipt`，包含实际文本的 SHA-256 哈希、字节数、授权元数据及固定处理和失败代码，不把回复正文复制进监控。如果发送期间其他记忆或工作状态发生变化，可以保留实际已发送内容，同时将轮次以 `failed` 结束，不应用过期情境推断。如果关联来源被更正或擦除，则只保留回执元数据，不复活已删除正文。因此回执的 `state` 可以是 `delivered`，而轮次的 `stage` 是 `failed`。回执保存后、`finish` 前再发生失效，也会保留回执并关闭未提交轮次，不覆盖较新的工作状态。

运行时会报告该失败，不重新发送。本地会话只有在核实匹配的终态回执后才清理 pending 标记；网络响应不确定时继续保留恢复状态。明确的会话冲突且远端确实没有本轮记录时，也可清理本地标记，不会取消另一宿主的轮次。租约已转移给其他 worker 的情况仍需核对处理；这并不保证发送后、回执保存前进程崩溃时的恰好一次交付。生成前和发送前的原有检查仍然执行。

依赖跨宿主互斥之前，应升级全部 API 服务进程；不支持旧版与新版服务混连同一数据库。迁移保留历史未完成轮次，为每个会话中最早的轮次分配占位，其他旧轮次按顺序恢复。迁移角色必须能够跨越行级安全策略读取全部数据；权限不足时会失败，不会悄悄漏掉不可见轮次。正常桌面迁移连接已具备此权限。会话占位和交付回执属于运行记录，不复制进记忆档案。

对于没有回执的旧版已送达轮次，来源被删除后仍可通过恢复操作关闭过期轮次并释放占位。如果恢复返回终态失败，需要显式取消本地 pending 标记后再发送下一条消息；会话无法自动核实一条从未保存过的回执。

外部模型调用或发送的结果不明确时，默认停止并报告 `generation_uncertain` / `delivery_uncertain`。仅当宿主根据 `request_id` / `delivery_id` 实现了持久去重或结果查询，才可开启 `generation_is_idempotent=True` / `delivery_is_idempotent=True`。普通 HTTP 重试、打印输出或一般模型 API 本身不能保证这个性质。租约失效可阻止后续存储与调用，但不能撤销已经进入外部系统的请求。删除或更正同样不能收回已经提供给模型或发送渠道的内容；后续生成结果会被重新检查。

`RuntimePolicy` 分别限制记忆资料与完整消息预算，使用 UTF-8 字节数保守估计；宿主应按模型的总窗口为输出和模型协议开销预留空间。当前文本输出服务上限为 64,000 字节。同步适配器不处理工具循环、流式消息、自动消息编辑、上下文压缩或多个并行轮次的分支合并，其能力明确标记为 `partial_automation`。

### 按需进行回复前分析

默认 `RuntimePolicy(orientation_policy="always")` 保留每轮的情境提议和独立核验。迁移至 `011_orientation_route.sql` 后，可对以普通对话、已知事实和偏好回忆为主的接入显式选择：

```python
from agent_memory.runtime import RuntimePolicy

runtime = BrainRuntime(
    memory, generate, deliver=deliver,
    policy=RuntimePolicy(orientation_policy="on_demand"),
    answer_reviewer=answer_reviewer,
)
```

服务器检查本次检索在适用性过滤之前的候选依赖和关系。没有情境依赖时，复用同一个上下文包直接回答，省去两次前台语义调用；有条件记忆、条件关系、工作项、未完成事项或不完整依赖时仍运行完整分析。已有定向任务继续恢复；语义回忆控制器和向量回忆暂时也保留完整分析。判断不增加分类模型调用，不按关键词猜测，也不声称全脑不存在其他相关记忆。

`orientation_route` 持久记录策略、跳过或分析的决定、固定原因、输入和依赖指纹。跳过不是语义核验成功。服务器保存内部预检，在进入准备与生成阶段时持锁重查；不能只依赖版本计数，因为后台新发布认识可能尚未推进该计数。生成后再检查跳过前提、当前上下文和来源，即使未配置回答核验器也检查；恢复不能静默更换策略。预检记录不进入普通监控响应，路由只含标识、计数与哈希，不复制消息正文。

这两个策略不完全等价：跳过的轮次不会即时派生或提交新的情境摘要、目标和待办状态。原始输入仍独立传给宿主并落盘；配置认知处理器时后台学习仍排队，须由 worker 执行。依赖即时目标跟踪的 Agent 应保留 `always`。答案核验由原来的 `answer_reviewer` 独立决定，快速路径不会自动关闭它。

按需路径用额外的本地读取换取减少前台模型等待，完整交付通常会准备或复核五次上下文。快照仍保守绑定全脑事件与索引进度；其他会话或后台活动可能触发 `context_stale`，即使最终相关内容相同。该并发可用性边界尚未优化，不能将当前小样本延迟外推为高并发性能。

### 交付前的回答核验

记忆的适用性计算正确，不代表宿主一定会正确回答。可在接入时启用独立的回答核验：

```python
import os

from agent_memory.answer_review import AnswerReviewer, AnswerReviewPolicy
from agent_memory.answer_review_provider import ChatCompletionsAnswerReviewProvider

with ChatCompletionsAnswerReviewProvider(
    base_url=os.environ["MEMORY_REVIEW_BASE_URL"],
    api_key=os.environ["MEMORY_REVIEW_API_KEY"],
    model=os.environ["MEMORY_REVIEW_MODEL"],
) as provider:
    runtime = BrainRuntime(
        memory, generate, deliver=deliver, instructions=agent_instructions,
        answer_reviewer=AnswerReviewer(
            provider, reviewer_id="my-review-provider-and-model-config-v1",
            policy=AnswerReviewPolicy(max_output_tokens=4096),
        ),
    )
    result = runtime.respond(text, message_id=message_id, conversation_id=conversation_id)
```

启用前须迁移数据库至 `010_response_review.sql`。这些环境变量由上例显式读取；没有配置 `answer_reviewer` 的原有接入保持关闭，不会暗中增加模型调用。`reviewer_id` 必须稳定标识实际端点、模型与推理配置；更换提供方时也应改变它，运行时无法自动辨认任意自定义回调的内部实现。

每份草稿最多核验一次：先持久化草稿与调用预留，再把生成时的完整消息和原始草稿作为资料交给核验器。它分别检查当前输入、历史依据、适用性、不确定性与动作完成声明；所有检查明确一致且覆盖完整才准许返回草稿或进入发送。发现问题、不确定、非法响应或提供方失败都会抛出 `RuntimeBlocked`，草稿保留但不交付，也不进入回忆。宿主须处理这个状态；当前没有自动改写或付费重试来生成替代答案。

策略、请求和草稿摘要及记忆版本绑定在轮次上，服务端也检查交付许可。进程中断留下 `pending` 时，恢复不会重新调用核验器；已通过的草稿可以继续，但先重建并核对原上下文和当前适用性。准备后的策略不能通过关闭核验降级；已完成轮次仍重放原交付事实，不追认旧答案通过了新核验。发送结果未知时，即使恢复实例没有发送回调，也保持 `delivery_uncertain`。

`GET /v1/brains/{brain_id}/runtime/turns/{turn_id}` 提供 `response_review_policy`、`response_review` 与预留调用数；`runtime/status` 提供各状态数量。诊断只保存指纹、状态和固定原因码，不复制草稿或对话正文。审核完成数、预留次数与实际 token 用量不同，调用方仍须独立计量实际提供方用量。

核验增加一次模型请求和相应延迟；预算分别限制输入字节、输出 token 与响应字节。内置 HTTP 适配器实施网络超时，自定义同步回调必须自行遵守请求时限。语义一致和完整覆盖仍是模型判断，不是事实证明。许可只绑定交给发送回调的草稿；渠道截断或改写后的实际文本照实记录，不冒充已经通过同一核验的文本。

宿主解释协议 `memory-consumption-v3` 区分自然承接与完成存储的声明：没有相应操作的可信完成证据时，不提前宣称已经存好或永久记住；原始消息捕获也不证明语义巩固完成。这是含义约束，不是词语黑名单。回答被拒绝时，已收到的用户输入仍然落盘；配置语义处理器时，其后台学习窗口已在采集事务中排队，须由 worker 实际执行，排队本身不等于学习成功。

### 语义回忆

宿主可在初始化时为回忆配置一个模型。主 Agent 仍然只接收组装好的上下文，不必决定何时调用记忆工具。以下代码沿用前面的 `memory`、`generate`、`deliver`；实际模型请求会使用宿主提供的 API 凭据：

```python
import os

from agent_memory.recall_control import RecallController, RecallPolicy
from agent_memory.recall_provider import ChatCompletionsRecallReasoner

with ChatCompletionsRecallReasoner(
    base_url=os.environ["MEMORY_REASONER_BASE_URL"],
    api_key=os.environ["MEMORY_REASONER_API_KEY"],
    model=os.environ["MEMORY_REASONER_MODEL"],
) as reason:
    runtime = BrainRuntime(
        memory, generate, deliver=deliver,
        recall_controller=RecallController(reason, RecallPolicy(
            max_model_calls=3, max_search_calls=4,
            max_input_bytes=48000, max_request_bytes=16000,
        )),
    )
    result = runtime.respond(text, message_id=message_id, conversation_id=conversation_id)
```

适配器使用 Chat Completions 的 JSON mode，要求提供方支持 `response_format: {"type": "json_object"}`。`base_url` 是 API 根路径，适配器追加 `chat/completions`；模型名称由宿主显式选择。协议参考 [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 和 [Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion)。也可直接传入自己的 `reason(RecallReasoningRequest) -> str` 函数，按照请求中的 JSON Schema 返回决定。未配置控制器时继续使用词法回忆，不调用额外模型。

每轮自动保留同一会话、同一分支最近的已接收或已交付消息，默认最多六条，可用 `RuntimePolicy(recent_context_events=...)` 调整为零到八条。它们按时间顺序提供情境，明确标记为对话资料；问题中的假设、助手的说法和梦境不会因此变成客观事实。最近消息和长期候选共同受上下文预算约束。

同步运行时的默认词法回忆与语义回忆都使用本轮输入之前的历史证据边界。本轮陈述仍作为当前输入参与回答和情境理解，但不能被重新检索为“早已记得”的独立证据。恢复时重新判断记忆的适用性；纯诊断字段 `evaluated_at` 不进入模型投影，避免仅因评估时刻刷新改变同一请求。完整诊断保留该时刻，真正的生效、到期或来源变化仍会阻止复用旧请求。

此前用户的“请用简短回答”等指令，在独立语义核验后可以作为偏好被回忆；来源必须是已接收的用户消息，并引用完整指令片段。这个规则也保留有反证的偏好状态，不会让原始指令直接变成事实，或放开提问预设、本轮输入及其后代的历史证据限制。

控制器可以结束回忆，也可以提出新线索继续查找；结束时只能选择已经展示过的记忆 ID。所有搜索沿用本轮输入之前的证据边界，最终由服务端重新检查来源、版本、更正、删除和范围。无法区分多个候选时保留 `ambiguous`，找不到充分依据时保留 `insufficient`；`sufficient` 只表示模型认为内容相关，不代表事实已经验证或历史已穷尽。

选定的版本、最近对话引用和覆盖限制在生成前作为 `recall_plan` 持久保存。恢复使用同一计划重新构造上下文；来源失效时停止，不静默改用另一份记忆。模型调用次数和输入字节数在每次请求前持久预留，进程中断后不会归零。搜索次数与耗时限制作用于每次规划尝试；它们不是跨重启累计计数。失败请求也消耗已预留额度，适配器不会自动重试。

默认每次规划最多 15 秒，单次模型输出最多 1,024 token，并检查返回内容的字节上限。超时采用网络操作超时和返回后检查，不能强制中断任意宿主回调。调用与字节预算限制的是资源使用量，尚未接入提供方实际 token 计费，不等于金额上限。提供方故障、非法决定或预算耗尽会记录停止原因，并让回答保留不确定性。

### 情境理解与长期学习配置

所有额外模型提供方默认关闭。要启用服务端认知处理，在 `.env` 中设置以下完整配置，并重启服务和后台处理器：

```dotenv
MEMORY_COGNITION_BASE_URL=https://your-provider.example/v1
MEMORY_COGNITION_API_KEY=<your API key>
MEMORY_COGNITION_MODEL=<your chat model>
```

`MEMORY_COGNITION_THINKING` 可选，值为 `enabled` 或 `disabled`；不设置时沿用提供方默认行为。仅对支持 `thinking.type` 参数的提供方使用，仍须配置上面三项；单独配置开关会在启动时拒绝。输出 token 上限与响应字节上限分别控制，截断内容不能作为成功提议。

可选的 `MEMORY_COGNITION_MAX_WALL_TIME_MS`、`MEMORY_COGNITION_MAX_OUTPUT_TOKENS` 与 `MEMORY_COGNITION_MAX_RESPONSE_BYTES` 默认分别为 30,000、8,192 和 32,768，上限分别为 300,000、32,768 和 65,536。总时间包含提议与核验两阶段；输出 token 按单次请求限制，解码后的正文上限与传输层响应包上限分别计算。桌面安装可用 `agent-memory desktop cognition-policy` 查看，或用 `desktop configure-cognition-policy --max-wall-time-ms 240000 --max-output-tokens 16384 --max-response-bytes 65536` 同时保存三项后重启。示例显式提高了额度，默认值不变。

新的学习与情境任务快照保存完整的实际语义策略。更换限制后重启不会改写已启动任务的冻结策略，也不重置调用预留。没有策略字段的旧快照保留原行为，继续使用当前处理器参数；请求指纹不一致时仍不能复用已预留请求。桌面连接最多等待响应 315 秒，以覆盖冻结策略上限，建立连接和连接池等待分别限制为五秒。其他宿主须让 HTTP 响应超时覆盖服务端策略；Python SDK 通用默认值仍为 30 秒。

提供方需要支持 Chat Completions JSON mode。配置后，`BrainRuntime` 在回答前自动调用 `orient`：以本轮输入和同会话、同分支的近期来源为起点，加入当前访问范围允许的相关旧认识及其来源、父依赖和显式反例，提出当前目标、指代与约束，再检查这些推断是否得到所引来源支持。后台 `worker` 对新输入及实际交付的回复运行 `consolidate`，生成语义锚点、经历片段与轮廓、条件化认识和程序性候选。它们保留原始来源、版本、反例和适用条件；模型提议与检查都可能出错，结果始终标为推断。当前配置使用同一提供方执行两次独立请求，不构成客观事实认证。 核验器分别判断每条引用承载的前提，再用必填的 `proposal_verdict` 判断所有证据是否共同支持完整命题；缺少整体判定、整体不确定或任意引用不匹配都会拒绝发布。所有核验项还必须带 `link_verdict`：普通命题为 `null`，认识关系需明确判定，不能只凭局部证据有效就接受变化方向或有限概括。自定义核验提供方须遵守请求中的完整响应 schema。

服务端把情境推断放进 `current_situation` 资料块，由运行时自动传给主模型；它只解释当前上下文，不能作为历史证据。例如“你记得我们去过海边吗？”可以触发回忆，不能因此建立“我们去过海边”的事实。助手已交付的说法可支持“助手曾说过”，也不能单独证明外部事件发生或任务完成。

新前台任务使用专用 `orient-v1` 契约，只提议和核验当前情境；长期认识、经历组织和认识关系留给后台。空情境合法，不强迫每条闲聊都产生推断；有情境提议时仍独立核验。模式和完整工作区随任务冻结，重试不刷新工作区时间戳；真实工作区版本变化会阻止复用。旧快照继续使用原通用契约，不自动升级。选材仍保留完整来源依赖并沿用保守预算；可选的 `on_demand` 策略及其能力取舍见前面的运行时说明。

新前台任务冻结 `communication-intent-v1`。新的编码阶段任务使用 `episode-fidelity-v1`，继承沟通意图与经历范围规则，并要求经历组织分别通过归属、不确定性与否定、具体程度、边界四项核验。缺失、不确定或矛盾的判定会阻止该经历发布；模型判定仍可能出错。模型会收到明确的经历操作范围、按现有资格规则计算的可用成员，以及后台学习窗口的 `learning_input_refs`。历史来源仍可用于认识比较，但不能因此跨会话组织经历；空的经历列表只表示当前范围没有可编辑经历。学习引用不授予 `current_request` 权限，当前请求仍须单独绑定真实轮次。合法空文本记录可计入已处理窗口，但不能作为经历引文。选材按实际契约计算标签大小，避免长标识使批次超限。恢复时校验准确来源版本、窗口、会话与分支；旧快照缺少该契约版本时保持旧输入和提示词，不在重试中改写请求。

新输入契约明确区分“可以引用本轮消息”和“消息确实要求助手做某事”。偏好、变化和个人计划的自述不必产生任务；真实问题、隐含请求和混合输入仍须独立核验请求意图。被引用或假设的请求不会变成本轮授权。旧快照保留原有输入和提示词；这是协议修缮，真实模型的分类质量仍须另行评估。

当前问题或请求使用 `evidence_use: current_request` 单独表示。服务端从真实运行轮次绑定 `current_input_refs`；模型只能据此提出当前目标或待回答事项，并须通过独立的 `request_verdict`、整体命题及每条来源核验。这一路径不生成情境观察，也不能授权长期事实或把提问预设当作历史。快照恢复、发布和复用都会重查轮次绑定与原始判定，旧快照不会自动获得新权限。保留到后续工作区的请求标为此前请求背景，不继续冒充本轮请求。

条件与情境观察使用共同的版本化类型定义。例如“备考时喜欢安静”保存为条件规则，“正在备考”需要另有来源支持；规则本身不能激活自己。召回结果的 `applicability` 区分成立、不成立、未知和冲突，并记录判断时刻与依据。未知、冲突或过期的规则进入 `conditional_memory`，明确标为不能直接采用的当前规则；它仍可以作为历史被回想。上下文组装时再次判断，正证据与反证据都进入输出的来源依赖。

带反证或认识状态未定的内容，即使条件成立，也不会标成可直接采用的当前规则。`BrainRuntime` 在宿主原有指令之后加入固定的记忆解释协议，要求主模型区分历史陈述与当前适用性、保留未知和冲突、避免仅按日期或记录先后选择结论。协议计入输入预算及生成指纹；协议变化会阻止复用已准备或生成结果未明的旧上下文；已完成轮次仍可重放。已生成的回复沿用原交付流程，启用回答核验时还须通过核验及上下文绑定检查。解释协议本身是模型指导，不能保证任意模型都遵守，实际回答需单独验收。

当前核心词表覆盖活动、时段、情绪和任务状态，尚未实现动态概念治理；旧自由键条件仍可检索，但适用性保持未知。有效期明确区分未知与无限，时间不明不代表永久有效。纯时间内核支持带来源和时区的“今天”解析，但现有宿主契约未提供可信本地时区，语义写入要求相对日期保持未知，并拒绝模型自行给出的日期锚点。情境恢复还需匹配服务端已完成任务及其来源，不能只凭客户端填写的“已验证”标签。

新认知提议的每条 claim 都必须显式给出 `valid_during`，完全未知时也要输出 `{"kind":"unknown"}`。核验器检查的时间结构与最终保存的结构一致；旧 nullable `valid_time` 不能代替它，`end=null` 不能自动推断为无限。旧持久记录和冻结父快照继续保守读取，不自动补造有效期。自定义提供方需遵守更新后的响应 schema。

新语义提议不再接受旧式 `eq/ne` 自由键条件。无法映射到已注册定义的概念用 `{"op":"unresolved","label":"银色状态"}` 保留原标签，在复合条件中继续按未知求值；标签碰巧等于某个已知词也不能激活条件。旧记录继续兼容读取，不自动迁移或猜测其含义。

情境计划与调用额度持久保存，恢复时复用已有结果；模型请求不占用数据库事务。来源在请求期间被更正或删除时，旧结果不能发布或重新注入。情境处理失败不会把未验证内容写入工作状态。认知配置独立于前面的 `RecallController`：前者负责理解与学习，后者负责多步回忆策略；两者都需要显式配置，都会使用配置的 API 额度。

默认每项语义任务最多调用两次模型、累计输入 196,608 字节；每个大脑的服务端认知与向量任务共享一小时滑动窗口，最多 100 次外部调用、2,000,000 字节输入。失败或结果不明的已预留调用仍计入额度。脑级额度耗尽时，本轮继续普通回答，不写入未经检查的情境推断。这些额度不包含宿主主模型或独立 `RecallController` 的调用。

新的后台批次声明 `encoding-integration-v1`：编码阶段最多两次调用、196,608 字节输入，历史整合阶段的一对提议与核验调用有相同上限。两个阶段共享不可重置的四次调用、393,216 字节额度，并继续受原有脑级滑动窗口限制。完整历史路径通常只用原来的两次调用。额外一对调用可以用于处理较早尚未付费尝试的完整对照，但不能自动重试失败或结果未知的付费任务。重启、检查容量或提高脑级预算都不会补充批次额度。

可选历史不完整时，新的编码任务在调用模型前切换到 `local-observation-v1`，只提供完整的新输入。它可以保留带说话者归属的报告和暂定经历片段，不能据此宣布当前规则、历史胜者或外部行动完成。本地评估与持久历史整合义务在同一事务提交。完整历史包会自动处理；证据或额度不足时保留明确的延期状态。新的独立输入、来源变化或滑动窗口额度恢复可以重新检查尚未付费的任务；已尝试的失败仍须明确恢复。

历史整合冻结有限的对照清单，包含完整来源、父依赖、例外与关系闭包。核验器接收一份完整上下文，再按准确版本解析引用；选择性引用不能隐藏未引用的限定条件。候选发现和依赖范围的限制保持可见。已知上下文会提前检查预算，但模型生成的提议过大时仍可能无法进行核验。超出有界扫描的未见候选继续标为未完成。详见[学习契约](architecture/local-observation-consolidation.md)与[阶段监控](architecture/learning-monitoring.md)；确定性测试不能替代持续真实模型验收。

用 `agent-memory desktop budget` 查看实际策略和当前预留用量；`desktop configure-budget` 使用稳定请求 ID 和刚读到的修订号调整额度，认证 HTTP API 与两种 SDK 提供相同操作。显式配置可提高或降低脑级额度，但不会重置用量或放宽既有任务上限。调用上限设为零会停止新的认知与向量调用预留。命令、取值边界和响应丢失后的处理见[认知额度配置](architecture/cognitive-budgets.md)。

后台学习使用持久的输入序列区间。尚未开始的同会话、同分支任务可以合并，但保留最早的处理起点；每批按顺序读取最多八条新输入，长消息会按请求字节预算缩小批次，再补充相关历史；单条本身超限时保留待处理状态。成功处理后，在同一事务中保存进度并创建剩余区间的续接任务。重试冻结原批次的来源、认识、经历和关系版本；从未预留提供方调用的新编码额度，可以通过经过校验的后继任务重建失效窗口，继续使用同一份额度。已付费失败或结果未知的任务不能通过自动来源恢复取得新额度；旧任务保留原有的有限重试行为。同一额度的累计用量不会重置，新任务也受大脑累计额度约束。非法结果、固定请求超限或额度不足不会推进处理进度；处理完成也不等于产生了新认识。

选材默认最多包含 32 条来源、32 条认识和 16 个经历，获取数量有界的词面候选。数据库过滤和排序实际检查的行数仍可能超过返回候选的上限。附加材料同时接受实际请求字节预算，完整依赖包共同进入或退出选材。原文完整提供、断言可精确重建且没有额外关系或经历组织时，省去重复的字面捕获断言。相关认识缺少完整来源、父依赖、反例或变化关系时，不完整历史批次不能发布长期提议。新的编码任务可在生成前改用上文独立的新输入路径；旧冻结任务不会自动取得这种权限。任务结果的 `sampling` 记录覆盖限制，`learning_outcome` 区分无提议、无被接受提议、接受了提议及尚未评估；接受提议不等于已发布长期记忆，发布内容另见 `publication`。来源链超过预算的持续归纳仍需后续设计。

同一轮提议与核验现在也能建立有据的认识关系：不同实例的有限概括 `abstracts`、等价表述 `equivalent`、临时例外 `exception`、从明确时刻开始的改变 `supersedes`，以及未解决矛盾 `contradicts`。关系必须绑定新旧认识的精确版本、证据、条件与有效期，并独立核验整个关系是否成立。概括至少需要两个来源不重叠的实例，只描述这些实例支持的范围；等价要求语义限定相同，不能抹掉否定或未知时间。新认识未被接受时，相应关系也不能发布。这些判断仍是模型推断。

后台另有明确的比较计划：从已选认识中寻找来源主体、世界和条件相容、支持事件不重叠的实例，每批最多三个比较目标。新计划最多选择两个强词面线索目标和一个弱线索探索目标；没有弱候选时可选三个强目标。弱候选仍需实词交集，先去重再排序，零交集不会进入探索。新 v4 计划在每类名额内优先覆盖不同的完整支持来源对，再优先使用前面目标尚未使用的来源，避免同一对经历拆出的多个片段占满名额。排序变化沿用 v3 目标身份，因此已评估的来源对不会仅因升级获得新的付费调用。词面相似只用于选择候选，共同含义仍由模型提出并核验。模型需要逐目标提出有限概括及关系，或者明确说明本次不改变认识；只记录“用户想起往事”不算完成归纳。目标随任务快照冻结；v3 比较策略要求每条概括只表达一个最小共同命题，实例差异保留在原记录，缺失信息不能证明相同或不同。运行时把共同命题的引文逐一对应到每个父实例，核验模型必须分别确认各实例及整个命题、关系；缺失、重复或未知的逐实例判断都不能发布该概括。共同命题可只引用各实例相关的完整片段，抽象关系仍保留所有端点的完整来源；普通父依赖规则保持严格。完成有效核验或明确决定不变的相同实例版本组合不会因新的提醒反复规划，新实例、版本或比较策略变化可形成新目标。旧 v1/v2/v3 冻结计划保持兼容，已记录的失败不会被删除。网络错误、缺失核验或非法提议不阻止后续重试。前台情境理解不自动承担整理目标，没有增加额外模型轮次，仍受原有两次调用预算约束。抽象核验另外区分“无关”与“未知”：只有核验明确标为 `irrelevant`、且不涉及任何结论限定或反证的引文，才可退出共同命题的支持集合。核验必须给出精确支持索引，每个实例仍须有留下的支持证据，整个命题和关系都须通过；文字、时间、条件、世界及父版本不改变。完整父引文仍保留在关系中，结构化核验和来源选择写入任务审计，普通监控只显示固定原因与数量。普通记忆不开放这条筛选路径。

认识变化保存在 `memory_belief_relations`，旧认识与过去的经历保持原样。召回会沿已知关系补齐端点和完整来源，即使新旧说法没有词面重合；`belief_resolution` 给出原规则未改变、被长期替代、当前例外、冲突或无法确定。只有关系与新认识都明确适用时，才阻止将旧规则当作当前规则；例外过期后重新判断旧规则。缺少关系端点、证据或预算时保持不确定，不会只留下旧规则。相关组共同接受上下文预算，持久回忆计划冻结展示过的关系版本及来源；新增关系会使旧计划失效。

删除或更正关系来源会撤销相应活动关系；实际擦除还会清除服务器所持档案中的相关内容。有独立证据的旧事实不会因此被反向撤销。可信本机档案可在核验后恢复关系，上传档案中的关系继续隔离。旧档案恢复不自动合并原脑在导出后发生的普通更正，删除记录则继续约束恢复。关系查找最多 32 条边、128 个认识版本、256 条来源与八层父依赖；达到上限时明确返回不完整，不能视为已查清全部变化。

经历发布后，运行时自动组织共同内容与实例差异，不增加模型调用。`patterns.py` 比较已有断言的完整语义字段：文字、条件、时间、现实或假设世界、核验状态等完全一致，且来自不重叠的经历，才可以共用一个定义。每次经历仍保留自己的来源、观察顺序和差异；没有观察到的内容不能从共同定义补入。这种精确共享与模型提出的 `abstracts` 关系分开：后者不会授权压缩不同含义的细节，也不能替代实例原文。

新经历提议必须给出 `organization_statement`，明确说明这些交互记录为何构成一次分组或边界变化。核验包含旧经历、完整新旧成员来源、各来源角色和变更后的分组；缺少旧来源、版本不符或超过 32 条来源上限时拒绝该提议，不截断后声称核验完整。组织命题保存为整个状态转换的说明，不证明对话中提及的外部事件真实发生。旧经历记录可以没有此字段。

归档保留当前经历的组织信息和完整来源依赖，恢复时映射来源版本；`organization_source_version` 仅记录转换前的经历身份，不能作为独立证据，归档不保证包含该旧组织快照。它不会被误映射成来源事件版本。旧格式继续兼容，外部上传的经历仍先隔离。

共同定义和绑定存入独立的 `memory_pattern_views` 派生表，事实仍完整保存。构建上限为 64 条认识、16 个经历、256 条完整来源，缺少精确父版本或来源则跳过组织，原事实仍按原规则使用。更正、删除或经历改版会清空相关视图内容；视图不会成为其他独立事实的证据依赖。导出档案不携带这份缓存；可信本机档案恢复后从核验过的原事实与经历有界重建，上传档案继续隔离。

普通 `prepare` 只有在完整选中某个视图的事实、重新核验来源后，才尝试把重复含义呈现一次，并分别附上每个经历的绑定、差异和适用性。实际渲染没有更短时，使用完整事实；整个已选组共同接受上下文预算，避免因压缩单独丢掉差异。覆盖范围始终是所选断言，不能据此声称检索已找齐经历或例外。带持久 `recall_plan` 的路径继续使用完整事实，因为计划尚未冻结组织视图的版本。明确线索仍通过已有检索寻找对应经历的有据细节。

语义向量是基于环境变量部署时的另一组可选配置，也适用于上面的开发环境。桌面配置目前尚未提供 embedding 设置；设置这些环境变量不会在受管理的桌面服务中启用向量。基于环境变量部署时，服务与后台处理器须使用相同的模型、版本标识和实际输出维度：

```dotenv
MEMORY_EMBEDDING_BASE_URL=https://your-provider.example/v1
MEMORY_EMBEDDING_API_KEY=<your API key>
MEMORY_EMBEDDING_MODEL=<your embedding model>
MEMORY_EMBEDDING_MODEL_VERSION=<your stable model revision>
MEMORY_EMBEDDING_DIMENSIONS=1024
```

`1024` 仅为示例，必须替换为所选模型的实际维度，范围为 1–8192。后台为新发布的记忆创建向量；查询只有在请求预算中显式设置 `max_embedding_calls > 0` 才调用向量提供方。默认预算为零，仅设置环境变量不会启用每次查询的向量调用。例如：

```python
from agent_memory.client import default_budget

budget = {**default_budget(16000), "max_embedding_calls": 1}
result = memory.recall(
    "以前我偏爱哪种香味？",
    conversation_id=conversation_id, branch_id="main", budget=budget,
)
```

词面检索结合原有特征哈希通道，以及针对归一化 ASCII 查询词元的有界 PostgreSQL 词匹配通道。每个通道在召回时最多保留 512 条候选，学习时最多 256 条，实际可能因请求预算而更少；每次查询额外读取一行判断是否截断。合并后去重，并按实际词面重合重新排序。下文的来源桥接仍是独立的有界通道，最终来源、依赖和上下文预算不变。这能减少哈希碰撞造成的漏召回，但不代表穷尽检索或数据库工作量恒定。

候选可通过词法、已配置的语义向量、经历结构和关联路径共同获得，随后统一检查来源与适用条件。词面命中保留的原始来源后，也会沿支持证据找到对应的语义认识：即使原文是中文、概括是英文，只要查询命中有效支持片段，仍可进入候选。桥接检查精确来源版本、内容哈希、范围、分支及删除状态，不能通过反例或背景引用冒充支持；之后仍检查完整依赖与当前适用性。来源桥接最多读取 64 条源事件，召回时最多保留 512 条关联认识，学习时最多 256 条，实际额度可能更小，并额外读取一条候选判断是否截断；达到上限会报告覆盖限制。这不等于任意跨语言理解，也无法从已经没有词面命中的来源自动找回所有改写。不同表示采用不同的访问衰减：细节更快沉入深层，概括与关键认识保留更久，明确线索仍可能找回。衰减参数是可调整的工程策略，尚未经长期对话校准，也不是人脑遗忘的生物定律。访问衰减不会自动擦除原始来源；永久删除仍通过 `forget` 执行。

内部检索命中或模型自称“有帮助”不会增加外部使用计数。运行时现会自动检查紧随其后的原始用户消息是否为完整、明确的确认或纠正，例如“没错”“不对”。只有前一条真实交付的回答仅含一条可唯一识别的已选记忆原句或引用，且记忆版本与全部支持来源仍有效，才会影响访问强度。原始解释提示保持不变，不增加模型请求，也不提升认识等级。改写、额外问题或陈述、内容相同但归属不明的多条记忆、导入反馈，以及没有结果关联的工具观察，都只保留轨迹。刚召回但尚未正式发布为断言的原文尾部记忆也只留痕。

准备阶段持久绑定最终服务器资料包、精确上下文哈希、所选版本和来源哈希；之后的校验准备或请求缓存清除不能替换该绑定。自定义上下文明示退出归因，自动反馈和旧版手动回退均不能奖励。没有绑定的旧轮次保留保守的手动检查，包括拒绝归属不明的多次准备，不获得自动反馈贡献。交付顺序由状态转换与事件序列确定，不要求应用与数据库的墙上时钟一致。

每个可归属的反馈事件及其版本，只向独立来源账本贡献一次。更正或擦除会让受影响的贡献失效，再由仍有效的证据重建访问状态。延迟处理旧反馈不会把它变成最近反馈；负面结果降低用途评估，但不会提高激活度或刷新强化时间。迁移 014 会清空无法证明真实交付用途的旧访问聚合，原始来源和记忆保留。这些指标描述用途，不是真实性概率，也不会把助手自己说过的话升级成世界事实。

[第一版基础修缮验收记录](evaluations/v1-foundations-2026-10-25.md) 记录了升级、反馈归属、语义结果、提供方边界及调用中断的验证；它不是第一版最终验收。

新的[10 月 25 日生命周期实测](evaluations/deepseek-2026-10-25-lifecycle-smoke.md) 完成五个短场景，实际回答均符合来源，24 次调用用量已知，偏好变更得到正确解析。报告保留了一处经历摘要的无依据细化，以及 `always` 模式较高的延迟；长历史和第一版最终验收仍是独立关卡。

## 开发监控接口

接口使用与记忆服务一致的 Bearer 认证：

| GET 路径（前缀 `/v1/brains/{brain_id}/runtime`） | 内容 |
| --- | --- |
| `/status` | 持久化与索引进度、学习进度、旧版未完成任务计数、轮次阶段、活动租约数及占位会话数 |
| `/learning-progress` | 整个大脑的学习区间、可运行/等待重试/运行中/受阻计数及最早缺口 |
| `/cognitive-usage` | 保留的认知与向量调用预留、分阶段结果、提供方报告的 token 及未知用量计数 |
| `/cognitive-budget` | 实际策略、修订、已用额度与剩余额度 |
| `/scope?conversation_id=...&branch_id=...` | 精确范围内占位的回合，或最早未结束的旧回合；无待处理回合时为 `turn: null` |
| `/turns?limit=100` | 最近轮次，最多 200 条 |
| `/turns/{turn_id}` | 当前阶段、修订、来源版本、上下文占用、回忆计划、累计调用额度、恢复状态 |
| `/turns/{turn_id}/history?limit=100` | 有序阶段日志，最多 200 条 |
| `/turns/{turn_id}/sources` | 单独按权限展开当前有效输入和输出 |
| `/jobs?limit=100` | 最近后台与认知任务，最多 200 条 |
| `/jobs/{job_id}` | 任务种类、状态、尝试次数、累计调用与输入额度、错误类别及安全学习摘要 |

Python 可用 `RuntimeClient(memory).active_scope(conversation_id, branch_id="main")`，TypeScript 可用 `memory.activeScope(conversationId, branchId)` 查找阻塞当前会话的回合。查询不受最近轮次列表限制，只读取元数据，不领取或取消回合。旧版排队回合的 `scope_reserved` 可为 `false`，直到前一个回合结束后被领取。

`recall_plan` 包含选择的记忆版本、观察过的来源版本、停止原因、搜索次数、耗时、覆盖限制和结构化决策轨迹。轨迹只保存动作、查询哈希与候选数量，不复制查询文本或模型解释；`recall_model_calls` 和 `recall_input_bytes` 提供跨恢复累计的额度记录。`orientation_job_id` 可关联本轮情境任务；任务接口不返回任务正文、原始模型结果或租约凭证。服务强制限制调用次数与输入字节，但不保证货币金额上限。

Python 的 `RuntimeClient(memory).cognitive_usage()` 和 TypeScript 的 `memory.cognitiveUsage()` 读取持久化认知与向量操作账本。提取与核验调用分别结算：后一次核验失败，不会把前一次成功返回的调用改成不确定。这里 `completed` 表示提供方返回了响应，不表示提议通过语义核验。即使响应不可用，经过校验的提供方输入、输出与总 token 仍会保留。未实际发出的预留、中断调用、旧记录以及没有有效 `last_usage` 和本线程新 `last_call_id` 的自定义提供方，用量保持未知。总 token 只累加已知记录，`counts_complete=false` 提醒不能把缺失用量当成免费。此接口覆盖情境分析、后台学习与向量任务，不包含主模型生成、回答核验、回忆控制器调用，也不是整个账户的账单。向量调用的输入与总 token 必须一致；其输出 token 按向量协议为零。共用的同步提供方适配器按线程隔离最近一次调用的诊断，不推算价格或总金额。

新认知快照冻结 `no_unsettled_retry-v1`：结算丢失时保留已占用额度，重新领取会以 `provider_uncertain` 关闭原任务，不再次发送同一个未改变的请求。这也覆盖提供方返回后进程崩溃或数据库故障的情况。显式学习恢复可以授权新的有界子任务，原有用量保留；旧快照继续遵守此前的有界重试契约。若提供方不确定结果已单独结算，仍可使用此前授权的剩余重试额度；来源变更也可以授权新的来源修复任务。这些路径均不重置先前预留。

监控记录不复制正文，也不保存租约凭证或隐藏推理。来源更正后依赖输出退出有效回忆；擦除来源时递归擦除派生输出，并过滤服务器持有的导出档案。档案保留事件依赖供恢复后继续执行删除约束；运行中的租约和未完成轮次不随记忆档案迁移。

认知任务列表和详情的 `learning_summary` 只返回处理状态、提议结果、接受与发布数量、固定采样限制和拒绝原因计数。例如任务 `completed` 但 `learning_outcome=no_proposals` 表示处理结束但没有提议；`accepted_counts` 有值也不代表已写入长期记忆，需再看 `published_counts`。`published_counts.pattern_views` 统计本次可用的组织视图，`belief_relations` 统计发布的认识关系，不代表客观事实数量或累计学习次数。`learning_window` 显示输入区间、尝试与成功处理位置，以及是否仍有待处理区间；`pending_sequence_span` 是序列跨度，不能当成消息条数。摘要不包含原文、模型解释或来源 ID；原认知结果因来源失效被清空后，待处理区间仍可独立展示。

`learning_summary.consolidation` 单独统计比较目标、尝试、核验成立、明确不变、未解决和去重数量，并只公开固定原因枚举。`no_change` 是模型本次决定，不代表已经证实“没有共同点”；`satisfied` 要求存在通过核验的概括关系，不能仅靠任务完成或模型自报。

`learning_progress` 汇总所有后台输入区间，不受最近 200 个任务的列表限制。它分别报告模型配置与处理状态，在有效续接或恢复任务已接手时避免重复计数，并提供最多八个最早受阻示例及其任务 ID 和 fence。失效或缺失的交接会被报告为覆盖不完整。这些是处理诊断，不是消息条数，也不衡量模型理解了多少。原有 `pending_job_count` 仍包含历史取消和失效任务；应通过 `learning_progress` 查找可处理的工作。索引已追上最新输入时，语义学习仍可能存在缺口。

其中独立的 `semantic_outcomes` 汇总保留的任务最新结果，分别报告已评估和未评估任务、接受与发布数量，以及固定拒绝原因。已评估区间可能拒绝了全部提议；处理覆盖向前推进，不代表成功发布记忆。这些是历史任务结果，不是每次尝试、去重来源数量或当前有效记忆数量。来源擦除、结果失效、缺失或格式错误会产生未知计数和 `semantic_outcomes.counts_complete=false`，不会被当成可信的零成功结果。最多返回八个示例，不包含原文或模型解释。

排除受阻原因后，可向 `POST /v1/brains/{brain_id}/runtime/jobs/{job_id}/retry-learning` 提交 `{"request_id":"recover-1","expected_fence":1}`。fence 必须匹配受阻任务；重复相同 request ID 和 fence 返回同一个恢复任务，给同一个父任务提交不同授权会冲突。响应是恢复任务当前的公开元数据。服务必须配置认知模型。此操作只排队，本身不发起模型请求；普通排队中、运行中或仍可自动重试的任务不能被手动重复创建。

恢复会在原区间尚未处理的部分重新读取有效来源，并保留父任务的结果、调用次数、输入字节预留及不确定结果。每个恢复批次最多两次模型调用、196,608 输入字节，同时受整个大脑的滚动额度约束。成功批次可在原区间内按相同单批限制继续处理，因此两次调用**不是**多批区间的总授权。后来的消息不能扩大恢复区间。提供方失败后，单批额度不足以安全重试时会停止，需再次显式请求恢复；来源变化仍走正常的失效与重建流程。恢复已排队不代表学习成功或产生了新认识。

桌面服务可用以下命令读取已保存的配置；服务需要已经运行，命令不会隐式启动 worker：

```sh
agent-memory desktop learning-status
agent-memory desktop learning-status --job JOB_ID
agent-memory desktop retry-learning JOB_ID --expected-fence 1 --request-id recover-1
```

使用监控返回的任务 ID 和 fence。若恢复响应丢失，应复用完全相同的 request ID 和 fence。Python 提供 `RuntimeClient(memory).learning_progress()`、`.job(job_id)` 和 `.retry_learning(job_id, request_id="recover-1", expected_fence=1)`；TypeScript 提供 `memory.learningProgress()` 和 `memory.retryLearning(jobId, {requestId: "recover-1", expectedFence: 1})`。两个 SDK 都不会自动重试此写入操作。

## 当前底层接口

Python 客户端位于 `agent_memory.client`，底层流程示例是 [examples/connect.py](examples/connect.py)。TypeScript 客户端位于 [sdk/typescript](sdk/typescript)。以下是适配器需要编排的底层 HTTP 契约，并非目标产品要求用户或主 Agent 逐步操作的方式：

1. `observe` 保存真实接收或交付的消息，使用稳定的来源 ID。
2. `prepare` 读取当前情境、工作状态和相关记忆。
3. `prepared_context` / `preparedContext` 生成实际传给模型的资料文本。
4. 模型需要进一步线索时使用 `recall`、`inspect`。
5. 输出确实交付后，再 `observe` 输出并 `commit` 本轮状态。
6. 宿主压缩或中断前使用 `checkpoint` 保存状态。

不要把完整 `ContextPacket` 或 `RecallResult` 控制封套注入模型。`prepared_context` / `preparedContext` 与 `recalled_context` / `recalledContext` 输出 `compact-memory-v1`：资料直接作为 JSON 对象，来源与关系各呈现一次，`memory_versions` 将短引用映射回可供 `inspect` 使用的精确记忆 ID 与版本。MCP 的 `memory_recall` 也返回这个紧凑 `context`，不再返回完整 HTTP 封套；需要完整记录时使用 `inspect`。

服务端的 `model_context` 保存按预算生成的权威文本。TypeScript SDK 优先原样使用它，避免科学计数法、大整数等在跨语言解析后改变文本、数值或上下文长度。兼容旧服务时仍可由字段重建，但如果重建结果超过服务端计量会明确拒绝，不能静默超预算。需要调整资料时应重新请求服务端组装，而不是修改封套字段后沿用旧的权威文本。

`prepare` 默认的 2,000 单位额度指宿主扣除系统指令、当前输入和输出预留后分配给记忆的预算；`recall` 默认 16,000。两者按上述实际模型资料的 UTF-8 字节数保守计量，不声称是精确 tokenizer 或整个 HTTP 响应的字节上限。HTTP 仍提供完整记录和诊断。相关认识组共同进入或退出预算，无法容纳时返回覆盖限制。原文字面记忆标为 `historical_report_not_current_rule`，不能绕过语义认识的时效、例外和冲突判断；详细的 `inspect` 输出继续独立计量。

`POST /v1/actions` 接受 [JSON Schema 契约](architecture/memory-contract.schema.json)。身份由 API key 绑定，调用参数只能选择授权的大脑，不能自行指定租户或认证身份。运行中的能力声明位于 `GET /v1/capabilities`。

### MCP

MCP 使用官方 Python SDK 1.x 的 stdio 协议，版本由 `uv.lock` 固定。桌面安装直接使用 `agent-memory desktop mcp-config` 生成的无密钥配置。下面是开发部署的手动客户端配置示例：

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

MCP 允许模型主动回忆和提交有来源标记的笔记。它不会拦截宿主轮次、自动读取宿主完整对话、判断消息是否送达或接管压缩；配置服务端认知模型也不会改变这一边界。MCP 提交的笔记固定标记为 assistant 来源，不能冒充用户表达。完整的自动记录与回答前回忆需要宿主接入生命周期接口。

## 当前实现边界

| 部分 | 当前实现 |
| --- | --- |
| 保存 | PostgreSQL 原始事件、来源修订、幂等提交、事务内任务入队 |
| 工作状态 | 分支状态、修订检查、压缩交接；可选情境推断在实际交付后合入 |
| 回忆 | 可选模型驱动的线索改写与候选选择、多路候选融合、适用条件、来源复核、持久计划与额度 |
| 整理 | 原文捕获；可选语义提炼、经历边界、跨来源认识与程序性候选，保留推断标记和版本依赖 |
| 共同内容与差异 | 精确语义共享、独立经历绑定、有据有限概括、完整组上下文预算 |
| 认识变化与持续学习 | 有条件的例外、替代、等价与矛盾；持久学习区间、冻结快照、受预算约束的续接 |
| 访问变化 | 按表示与时间计算访问层级；可信外部反馈更新使用计数和用途评估 |
| 更正与删除 | 版本更新、同步失效、派生依赖清理、档案恢复时检查删除记录 |
| 隔离 | 服务端身份绑定、应用查询过滤、PostgreSQL 强制行级隔离 |
| 接入 | Python 同步文本自动运行时；HTTP、Python、TypeScript、MCP 底层操作 |
| 桌面运行 | 固定本机 profile、大脑与凭据，自动启动数据库/API/worker，MCP 与 SDK 自动连接，停止保留数据 |
| 开发辅助 | 鉴权状态、轮次与阶段日志、认知任务、回忆停止原因与额度、独立来源展开；可移除的本地工作台 |

条件触发的主动提醒、经过校准的衰减与关联传播、多模态理解、完整工具循环与流式宿主适配、云端账号与共享权限仍需实现和验证。程序性候选只是有来源的步骤与条件，不会自行执行或证明任务成功。当前仅支持拥有者私有范围；共享集合请求会明确拒绝。未配置语义 embedding 时，已有词法特征哈希不会被当成语义向量；配置后只在模型、版本、维度和内容哈希一致时使用对应向量。

原始表达有来源，不代表其中的客观世界事实已经被独立验证。默认回忆返回有依据的候选，并以 `partial` 说明语义充分性尚未判断。可选控制器和认知处理器的语义判断仍可能出错，多路搜索也不能保证找齐所有相关历史。引用原文中的一个子串，不足以证明它可以独立成为新的结论。

在[一万条合成记录检索诊断](evaluations/retrieval-scale-2026-10-25.md)中，候选与范围投影修改后，同样 20 个标签线索查询由 18/20 命中变为 20/20，测得召回中位时间从 487.009 ms 变为 92.3195 ms，p95 从 503.284 ms 变为 123.173 ms。这是本地进程内测量，不含 HTTP 或模型耗时；修复后复用了已预热的数据，并未控制同机并发负载。报告保留了精确数据、因缺少查询边界而排除的首次尝试，以及本次比较的适用限制。

只有保持原文含义和来源归属的直接表述可进入 `grounded` 工作状态。语义处理器生成的当前情境使用 `inferred` 状态与有效期；当前情境不能反过来作为历史证据。未决事项的通用提交接口目前只接受 `unknown` 状态，不能把一条有引用的笔记直接宣布为已经完成。

档案导入导出与已实现的桌面逻辑备份、新安装恢复是不同功能。后者使用独立且最新的删除登记、操作隔离及明确激活，并已通过真实 Docker 和安装包验证。数据库物理备份、规模化迁移、运行 SLA 和长期对话效果仍需独立部署与实测。关闭和重开服务不会删除已提交记录。

当前单份档案最多 16 MB，每类记录最多 5,000 条；超出时明确报错，不会静默截断为不完整备份。分片导出和大规模恢复仍需实现。

## 工程结构

- `src/agent_memory/cognition.py`：经历捕获、线索回忆、证据边界和工作区组装。
- `src/agent_memory/runtime.py`、`turns.py`：自动文本生命周期、持久阶段与调用额度。
- `src/agent_memory/recall_control.py`、`recall_provider.py`：语义回忆决策与可选模型适配器。
- `src/agent_memory/semantic.py`、`learning.py`、`consolidation.py`：语义提议与检查、当前情境、后台认知发布。
- `src/agent_memory/belief_store.py`、`belief_resolution.py`：认识关系、完整证据闭包与当前适用判断。
- `src/agent_memory/learning_windows.py`、`neighborhood.py`：持久处理区间、按条数与字节预算选择完整材料。
- `src/agent_memory/patterns.py`、`pattern_store.py`：共同语义与经历差异的精确绑定、验证、持久化和失效。
- `src/agent_memory/candidates.py`、`retrieval.py`、`dynamics.py`：候选融合、版本依赖、访问层级与外部反馈。
- `src/agent_memory/jobs.py`、`embeddings.py`、`vector_jobs.py`：持久任务、调用预算与可选向量处理。
- `src/agent_memory/service.py`：事务、版本、生命周期与十个记忆操作。
- `src/agent_memory/storage.py`、`migrations/`：PostgreSQL 数据模型与隔离。
- `src/agent_memory/api.py`、`web/`：认证、HTTP 与本地工作台。
- `src/agent_memory/client.py`、`mcp_server.py`、`sdk/typescript/`：宿主接入。
- `src/agent_memory/local_profile.py`、`desktop.py`、`desktop_connection.py`：本机配置、服务生命周期与自动连接。
- `src/agent_memory/agent_session.py`、`chat_config.py`、`chat_provider.py`、`desktop_chat.py`：直接模型接入、持久本机会话、显式恢复与终端聊天。
- `architecture/`：完整目标、机器可读契约和 52 个系统验收场景。

## 验证

项目文档和开发者界面统一使用英文，README 同时维护中文版本。多语言测试夹具、语言识别规则及原始评估输入输出保留来源语言；引用证据的译文单独标注，不替换原始记录。语言规范见[项目约定](AGENTS.md)。

先启动并迁移项目专用数据库，然后运行：

```sh
uv run pytest -q
uv run ruff check src tests examples
uv build
```

TypeScript 测试与类型检查命令见 `sdk/typescript/package.json`。没有配置数据库时，数据库相关测试会跳过；跳过不代表通过。测试使用新建的随机租户与大脑，不依赖私人对话，也不调用付费模型或 Codex／Claude 订阅。

GitHub Actions 仅保留手动触发，不在 push 或 pull request 时自动运行。日常开发优先执行与修改相关的本地检查；发布前按改动影响验证，避免重复运行云 CI。

[独立聊天验收记录](evaluations/standalone-chat-2026-10-24.md) 包含安装包、真实数据库和本地模拟提供方的验证，覆盖直接聊天、重启恢复、去重及显式取消。确定性提供方不用于衡量语义学习质量。

[后台学习恢复验收记录](evaluations/learning-recovery-2026-10-24.md) 记录了受阻区间监控、显式恢复、预算与不确定状态的保留，以及尚待解决的架构缺口。

[跨宿主会话与交付恢复验收记录](evaluations/runtime-concurrency-2026-10-25.md) 记录了持久会话占位、记忆失效期间的交付回执、升级恢复及仍然存在的外部交付不确定性。

接口结构校验、确定性回归测试、真实 MCP 协议往返和数据库测试，只验证各自覆盖的行为。完整架构中的 52 个系统场景尚不能据此宣称全部通过，也未宣称在长期记忆质量上超过任何基线。


### 手动 DeepSeek 实测

`examples/evaluate_deepseek.py` 通过真实来源捕获、后台学习、入库、召回和上下文准备检查合成会话，使用 DeepSeek-V4-Pro 思考模式。它只创建隔离的测试身份和大脑，不读取已有用户大脑。项目专用 PostgreSQL 须已启动并迁移，密钥放在忽略提交的 `.env` 中的 `DEEPSEEK_API_KEY`。

```sh
# 明确允许付费；先检查一个阶段，失败即停止后续依赖阶段
uv run python -m examples.evaluate_deepseek --live --scenario temporal --stage-limit 1
uv run python -m examples.evaluate_deepseek --live --resume --scenario temporal --stage-limit 3
uv run python -m examples.evaluate_deepseek --live --resume --scenario conditional --stage-limit 3
uv run python -m examples.evaluate_deepseek --live --resume --scenario abstraction --stage-limit 2

# 仅重新校验保存的结果，不连接数据库或模型
uv run python -m examples.evaluate_deepseek --recheck
```

默认把报告与调用记录存到忽略提交的 `.local/deepseek-evaluation/`。整个目录共享最多 16 次调用和 262,144 个预留输出 token；每阶段最多两次调用、每次最多 16,384 个输出 token，阶段总时间最多 120 秒。先落盘预留再发请求，不自动重试；恢复时保留已有额度、阶段和任务身份。未完成的已预留调用仍计入上限，不能当作没有花费。不同输出目录分别记账，做修复对照时需合并统计。保留失败报告，不能删除调用记录绕过预算。

慢速思考模型可显式传 `--max-wall-time-ms 240000`，上限 300,000 毫秒；不增加调用次数或输出额度。执行策略随阶段保存，服务端认知租约覆盖允许的请求时限及小量收尾时间。默认遇到失败停止；只有查明原因、确认仍有可用前置证据时，才用 `--live --resume --continue-after-failure "诊断原因"` 继续后续阶段。它会保留原失败和诊断记录，不重跑已完成阶段，也不能把整个场景改称通过。

调用记录只保存安全用量字段、延迟、模型名和最终 JSON 内容，不保存请求正文、密钥、错误正文或隐藏思考内容。压缩响应以有上限的方式解码；不完整或无法解析的响应明确记为用量未知。当前计量示例使用 POSIX 文件锁，适合 macOS/Linux 开发环境。

已保存[首轮实测](evaluations/deepseek-2026-10-24.md)与[紧凑上下文、主动归纳后续实测](evaluations/deepseek-2026-10-24-compact-planning.md)。后者包含 8,000 单位预算的零调用重放、14 次共享计量请求及未通过的归纳与跨语言场景。

来源桥与逐实例归纳的[后续修缮报告](evaluations/deepseek-2026-10-24-memory-repairs.md)另保留一次失败和一次通过的独立认知处理器诊断。`examples/evaluate_memory_repairs.py --replay` 复用本地保存的合成数据做免费重放并冻结输入，`--live` 才允许最多两次调用；只有诊断完成且失败时，`--live --followup "具体原因"` 才能在同一账本中追加唯一一次两调用补测。原报告不改写，整个账本最多四次调用，`--resume` 只读报告。独立处理器测试不等于完成了数据库发布和宿主回答的端到端真实模型验收。

验收针对固定来源、关系方向、条件、有效期及实际召回/上下文输出；时间测试注入领域时钟，不代表已有宿主历史情境 API。内容关键词只是辅助护栏，还需人工检查生成命题。小样本诊断不等于长期效果基准，也没有使用主聊天模型回答来代替记忆质量判断。

### 完整同步对话实测

`examples/evaluate_conversation.py` 将真实 DeepSeek 主模型接入 `BrainRuntime`，通过本机 TCP HTTP API 与 PostgreSQL 执行输入捕获、情境理解、上下文准备、真实生成和交付确认；交付目标是持久化的本地测试收件箱。每轮重建服务、客户端和运行时，切换会话并关闭近期对话补充。使用固定合成偏好验证初次学习、跨会话回忆、偏好改变及无依据的提问，并增加独立空大脑对照。没有注入隐藏聊天记录或标准答案，也没有连接真实聊天渠道。

```sh
uv run python -m examples.evaluate_conversation --plan
uv run python -m examples.evaluate_conversation --live
uv run python -m examples.evaluate_conversation --resume
```

仅 `--live` 读取本地密钥和执行付费请求。共享账本最多 20 次调用、249,856 个预留输出 token；五个轮次最多各两次情境处理和一次主模型请求，仅两次新增信息执行各一次巩固任务，因此计划最多 19 次请求。主模型每次输出上限 4,096 token，认知请求上限 16,384 token。模型超时不自动重试；已有输出目录拒绝再次付费运行，`--resume` 只读报告，中断也不能自动重发。

报告分开记录实际进入主模型的持久语义版本、此前用户支持来源、最终回答、实际交付、任务完整程度及调用用量。管线检查通过不等于回答含义正确；自然回答须按预先固定的标准另行审阅。探测消息也正常产生后台任务，但本次预算不执行这些待处理窗口。此测试覆盖同步文本小样本，不代表长期稳定性、工具循环、流式输出、OS 进程崩溃恢复或任意 Agent 的完整接入。

已保存[完整同步对话诊断](evaluations/deepseek-2026-10-24-conversation.md)。原始阶段、部分失败和后续专项补测分开记录；回答内容符合来源也不代表主模型遵守了运行时的不确定性标记。

该次基线暴露的有效期缺失可用 `uv run python -m examples.evaluate_temporal_repair --live` 做唯一一次专项诊断。它要求本地原始五阶段报告、固定来源版本和 18 次已确认调用的账本完全匹配，只重处理初次学习的用户原文；最多追加两次、每次 8,192 输出 token，仍受原 20 次调用和 249,856 预留输出 token 总额约束。结果另存 `temporal-followup.json`，不发布进数据库、不生成宿主回答、不改写原始失败；文件一旦存在就拒绝再次执行。`--resume` 只读专项结果。

当前请求、经历组织和记忆解释协议的修复使用独立实验入口：

```sh
uv run python -m examples.evaluate_interaction_repair --plan
uv run python -m examples.evaluate_interaction_repair --live
uv run python -m examples.evaluate_interaction_repair --resume
```

该实验重跑同样的五轮真实链，并增加一次独立宿主诊断：冻结旧公开报告中适用性为未知的完整上下文，只插入新的通用记忆解释协议，再检查回答有没有把未知说成已确定。旧答案和评分标准不发送给被测模型；诊断不发布记忆、不交付用户，也不参与新五轮的学习。两部分共用最多 20 次调用、253,952 个预留输出 token，输入与代码指纹在首次请求前落盘；用量不明时阻止所有后续付费调用。默认目录 `.local/deepseek-interaction-repair/` 已存在就拒绝重跑，`--resume` 只读。机器检查与回答语义审阅分开保存。

已保存[当前请求、经历组织与记忆消费实测](evaluations/deepseek-2026-10-24-interaction-repair.md)：五轮流程 3/5 全过，原独立诊断触及输出上限；v2 的三项对照目标 2/3 通过，未知适用性仍被宿主越过。工程回归通过不代表此项真实验收通过；本批按用户后续要求将当前实现与失败报告一并提交，验收结果保持原判。

`examples/evaluate_consumption_repair.py --plan / --live / --resume` 是独立三项宿主对照入口，分别检查当前自述、已成立适用性和未知适用性。它冻结原 learn/recall/独立诊断输入，仅替换协议消息，不把原答案或评分标准发送给模型。来源须有完整已知用量；仅额外允许上述“前五轮完成，末次诊断恰好触及输出上限”的严格失败状态，不放行其他中断。新账本最多 3 次调用、12,288 个预留输出 token，须加到原实验用量中；不重试，已有目录拒绝重跑，`--resume` 只读。执行完成和语义通过分别记录。

回答核验与前台分工的验证分别使用以下入口：

```sh
uv run python -m examples.evaluate_answer_review --plan
uv run python -m examples.evaluate_answer_review --live
uv run python -m examples.evaluate_memory_lifecycle --plan
uv run python -m examples.evaluate_memory_lifecycle --live
uv run python -m examples.evaluate_memory_lifecycle --orientation-policy on_demand --output .local/lifecycle-on-demand --live
uv run python -m examples.evaluate_memory_lifecycle --profile repair --orientation-policy on_demand --plan
uv run python -m examples.evaluate_memory_lifecycle --profile long --history-turns 104 --plan
```

前者冻结三个旧错误答案和三个正常对照，只评估核验器；最多六次、49,152 个预留输出 token，标签不发送给模型。生命周期 v2 保留原来的五轮 smoke 夹具。每轮最多两次前台认知、一次回答和一次核验；两个学习轮各授权最多四次后台调用，由局部编码与历史整合共享，每个阶段最多两次，总上限为 28 次、356,352 个预留输出 token。历史延续任务不会增加授权额度。执行器检查核验绑定、持久交付、当前来源的编码覆盖、记忆发布及重启回放，并记录准确的任务与授权根标识、保留的操作账本、实际模型请求和响应及延迟。历史评估、发布、延期工作与已选但未评估的目标分别保留，编码完成不代表历史整合完成。两者 `--resume` 均只读，已有输出目录拒绝再次付费执行，不自动重试已尝试的付费工作。

生命周期执行器支持在所选输出目录内创建名为 `STOP` 的文件，请求有序停止。它会在初始化前、前台历史或探针轮次之间、每个学习任务之前，以及摘要更新和探针准备前后检查；已经开始的前台轮次或学习任务先运行到安全边界。每轮最多执行八个尚未尝试且已就绪的历史任务，每个任务仍受既有共享授权限制，延期工作继续保留。报告记录 `operator_stop`，保留实际用量并关闭连接。如果启动前目录内只有 `STOP`，只写入停止报告，不读取凭据、不连接数据库或模型。已有证据不会覆盖，`--resume` 仍然只读。学习任务失败或取消、`sampling.publication_restricted=true` 或 `learning_window.processing_succeeded=false` 时，以 `learning_processing_incomplete` 停止；用量不明、运行时失败以及模型或预算失败优先保留。合法且已完成处理的部分结果仍可继续，未解决的历史工作独立保留供审阅。

独立的 `repair` 模式固定运行带种子的 100 轮数据集中原始前 16 轮，包含先前出错的 stage-003。它只运行记忆组，不含留出探针或基线对照，冻结上限为 128 次调用、1,769,472 个预留输出 token。与原执行器一致，各夹具仍使用独立会话，因此该诊断不测试后续同一会话输入为旧历史工作补充授权。诊断通过不等于长历史验收通过。获授权后执行真实测试时必须使用新的输出目录，旧停止证据保持不变。

已保存[回答核验与前台分工实测](evaluations/deepseek-2026-10-24-answer-review.md)：六个固定回归均符合预期；首次链路第三轮因含糊的存储完成措辞被拦，保留原判。仅明确宿主承接规则后的唯一补测中，五个回答符合来源并交付，两个陈述回合仍误标为请求，完整流程为 3/5。共 44 次真实调用、302,914 token，全部用量已知；该历史版本前台仍需约 31–78 秒。该报告保留原始失败，不作为按需路由的测试结果。

[按需分析与延迟实测](evaluations/deepseek-2026-10-24-orientation-routing.md) 使用相同五轮夹具，五个回答均符合来源并交付，完整流程为 4/5。前台模型等待平均从历史记录的 56.47 秒降到 20.88 秒，本次完整回复平均 21.68 秒；这是历史前后比较，不是同版本成对 A/B。一次后台提议越过经历操作范围被正确拒绝；补齐范围契约后，基于原完整输入的独立两调用处理器补测通过，未重新执行五轮端到端链，原判不改写。按需模式不即时派生目标和待办，不能视为与完整模式等价。

`long` 执行器准备 100–128 轮真实输入、生成和交付（默认 104），再从同一可信历史快照恢复六个隔离探针大脑。记忆内核、滚动摘要和无记忆三组使用相同宿主、上下文上限及各自相同的总调用上限，实际花费单独计量；摘要更新也计费。问题、期望内容和来源映射不进入历史编码或宿主输入。摘要只标为未核验的历史报告，不伪造来源，因此比较的是整套接入行为，不能归因于单一存储结构。

长历史实验尚未通过真实模型验收。v2 的 `long` 与 `repair` 计划会在每个全新且未使用的记忆大脑开始首轮前，明确冻结有限的 30 天服务端额度。默认 104 轮历史加六个探针对应 636 次认知调用、62,521,344 输入字节；每个对照组各自的总上限为 856 次调用、11,771,904 个预留输出 token。实际消耗分别计量，每个授权根及每个阶段的服务端限制继续生效。已使用的预算不能替换，用量不明时停止后续付费调用。这些数字是资源上限，不是预计消耗或货币价格。压缩执行一百轮不等于连续运行数月，模拟跨度与实际跨日运行须分别报告。
