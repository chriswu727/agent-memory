# Agent Memory

一个独立于模型、无界面的 Agent 记忆运行时。目标是让宿主接入一次后，运行时持续管理 Agent 的工作记忆、长期认识和回忆过程。普通用户正常交流即可；主 Agent 不需要主动调用保存或搜索工具，系统也必须正常形成和使用记忆。

完整目标见 [架构与验收规范](architecture/architecture.html)，产品边界与宿主责任见 [自主记忆运行契约](architecture/autonomous-runtime.contract.json)。当前已实现底层记忆服务、同步文本自动生命周期，以及可选的情境理解、语义巩固和语义回忆：结合最近对话判断当前目标，生成有来源的认识与经历结构，按预算查找并选择记忆。完整宿主接入与长期记忆效果仍需验证，当前不能视为完整自主大脑。

后续认知内核的具体改造、实施依赖和行为评估见[认知记忆架构实施计划](architecture/cognitive-memory-plan.md)。计划中的新增能力仍待实现，不代表当前运行状态。

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

## 自动接入

Python 的 `BrainRuntime` 接管同步文本轮次。宿主初始化一次，提供模型生成函数和实际发送函数；业务层只提交收到的消息，不调用记忆工具或逐轮编排存取：

```python
from agent_memory.runtime import BrainRuntime, DeliveryReceipt

def generate(request):
    return model.generate(messages=list(request.messages))

def deliver(request):
    channel.send(request.text)
    return DeliveryReceipt("delivered")

runtime = BrainRuntime(memory, generate, deliver=deliver, instructions=agent_instructions)
result = runtime.respond(text, message_id=message_id, conversation_id=conversation_id)
```

上例的 `memory` 是绑定固定 `brain_id` 的 `MemoryClient`；`model`、`channel` 和身份来自宿主。生成函数必须使用运行时提供的 `messages`，其中保留宿主系统指令，加入有来源和预算的记忆资料及当前输入。`message_id` 是宿主收到消息时持久分配的标识，重试必须不变；新进程使用同一个大脑身份即可重新接入。可运行的无模型示例见 [examples/autonomous.py](examples/autonomous.py)。

运行时自动执行输入落盘、回答前回忆、草稿保存、交付确认、有效工作状态保留与 checkpoint。每轮使用租约和修订检查；草稿写入和阶段推进、交付记录和阶段推进各自在同一事务中。`runtime.resume(turn_id)` 从持久阶段继续，完成过的轮次直接返回已交付结果，不重新生成或发送。配置认知处理器后，回答前还会理解当前情境；实际交付后才把经过来源检查的情境推断合入工作状态，推断不会被标为已证实事实。

只有宿主确认实际发送后才能返回 `delivered`；部分发送使用 `DeliveryReceipt("partially_delivered", delivered_text=actual_text)`，取消使用 `cancelled`，发送结果无法确认使用 `unknown`。没有发送函数时停在 `generated`，草稿不进入回忆。`delivered` 指宿主确认交付，不声称用户已经读过消息。

外部模型调用或发送的结果不明确时，默认停止并报告 `generation_uncertain` / `delivery_uncertain`。仅当宿主根据 `request_id` / `delivery_id` 实现了持久去重或结果查询，才可开启 `generation_is_idempotent=True` / `delivery_is_idempotent=True`。普通 HTTP 重试、打印输出或一般模型 API 本身不能保证这个性质。租约失效可阻止后续存储与调用，但不能撤销已经进入外部系统的请求。删除或更正同样不能收回已经提供给模型或发送渠道的内容；后续生成结果会被重新检查。

`RuntimePolicy` 分别限制记忆资料与完整消息预算，使用 UTF-8 字节数保守估计；宿主应按模型的总窗口为输出和模型协议开销预留空间。当前文本输出服务上限为 64,000 字节。同步适配器不处理工具循环、流式消息、自动消息编辑、上下文压缩或多个并行轮次的分支合并，其能力明确标记为 `partial_automation`。

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

提供方需要支持 Chat Completions JSON mode。配置后，`BrainRuntime` 在回答前自动调用 `orient`：以本轮输入和同会话、同分支的近期来源为起点，加入当前访问范围允许的相关旧认识及其来源、父依赖和显式反例，提出当前目标、指代与约束，再检查这些推断是否得到所引来源支持。后台 `worker` 对新输入及实际交付的回复运行 `consolidate`，生成语义锚点、经历片段与轮廓、条件化认识和程序性候选。它们保留原始来源、版本、反例和适用条件；模型提议与检查都可能出错，结果始终标为推断。当前配置使用同一提供方执行两次独立请求，不构成客观事实认证。

服务端把情境推断放进 `current_situation` 资料块，由运行时自动传给主模型；它只解释当前上下文，不能作为历史证据。例如“你记得我们去过海边吗？”可以触发回忆，不能因此建立“我们去过海边”的事实。助手已交付的说法可支持“助手曾说过”，也不能单独证明外部事件发生或任务完成。

条件与情境观察使用共同的版本化类型定义。例如“备考时喜欢安静”保存为条件规则，“正在备考”需要另有来源支持；规则本身不能激活自己。召回结果的 `applicability` 区分成立、不成立、未知和冲突，并记录判断时刻与依据。未知、冲突或过期的规则进入 `conditional_memory`，明确标为不能直接采用的当前规则；它仍可以作为历史被回想。上下文组装时再次判断，正证据与反证据都进入输出的来源依赖。

当前核心词表覆盖活动、时段、情绪和任务状态，尚未实现动态概念治理；旧自由键条件仍可检索，但适用性保持未知。有效期明确区分未知与无限，时间不明不代表永久有效。纯时间内核支持带来源和时区的“今天”解析，但现有宿主契约未提供可信本地时区，语义写入要求相对日期保持未知，并拒绝模型自行给出的日期锚点。情境恢复还需匹配服务端已完成任务及其来源，不能只凭客户端填写的“已验证”标签。

情境计划与调用额度持久保存，恢复时复用已有结果；模型请求不占用数据库事务。来源在请求期间被更正或删除时，旧结果不能发布或重新注入。情境处理失败不会把未验证内容写入工作状态。认知配置独立于前面的 `RecallController`：前者负责理解与学习，后者负责多步回忆策略；两者都需要显式配置，都会使用配置的 API 额度。

默认每项语义任务最多调用两次模型、累计输入 196,608 字节；每个大脑的服务端认知与向量任务共享一小时滑动窗口，最多 100 次外部调用、2,000,000 字节输入。失败或结果不明的已预留调用仍计入额度。脑级额度耗尽时，本轮继续普通回答，不写入未经检查的情境推断。这些额度不包含宿主主模型或独立 `RecallController` 的调用。

后台学习使用持久的输入序列区间。尚未开始的同会话、同分支任务可以合并，但保留最早的处理起点；每批按顺序读取最多八条新输入，长消息会按请求字节预算缩小批次，再补充相关历史；单条本身超限时保留待处理状态。成功处理后，在同一事务中保存进度并创建剩余区间的续接任务。重试冻结原批次的来源、认识、经历和关系版本；来源变化时以新任务重建有效窗口。提供方故障只在剩余额度允许时有限重试，同一任务的累计额度不会重置，新任务也受大脑累计额度约束。非法结果、固定请求超限或额度不足不会推进处理进度；处理完成也不等于产生了新认识。

选材默认最多包含 32 条来源、32 条认识和 16 个经历，采用已有词面索引的有界候选查询；不是全历史扫描。附加材料同时接受实际请求字节预算，完整依赖包共同进入或退出选材。原文完整提供、断言可精确重建且没有额外关系或经历组织时，省去重复的字面捕获断言。相关认识缺少完整来源、父依赖、反例或变化关系，或整包超过预算时，停止该批次长期提议的发布，仍允许有依据的当前情境更新。任务结果的 `sampling` 记录覆盖限制，`learning_outcome` 区分无提议、无被接受提议、接受了提议及尚未评估；接受提议不等于已发布长期记忆，发布内容另见 `publication`。来源链超过预算的持续归纳仍需后续设计。

同一轮提议与核验现在也能建立有据的认识关系：不同实例的有限概括 `abstracts`、等价表述 `equivalent`、临时例外 `exception`、从明确时刻开始的改变 `supersedes`，以及未解决矛盾 `contradicts`。关系必须绑定新旧认识的精确版本、证据、条件与有效期，并独立核验整个关系是否成立。概括至少需要两个来源不重叠的实例，只描述这些实例支持的范围；等价要求语义限定相同，不能抹掉否定或未知时间。新认识未被接受时，相应关系也不能发布。这些判断仍是模型推断。

认识变化保存在 `memory_belief_relations`，旧认识与过去的经历保持原样。召回会沿已知关系补齐端点和完整来源，即使新旧说法没有词面重合；`belief_resolution` 给出原规则未改变、被长期替代、当前例外、冲突或无法确定。只有关系与新认识都明确适用时，才阻止将旧规则当作当前规则；例外过期后重新判断旧规则。缺少关系端点、证据或预算时保持不确定，不会只留下旧规则。相关组共同接受上下文预算，持久回忆计划冻结展示过的关系版本及来源；新增关系会使旧计划失效。

删除或更正关系来源会撤销相应活动关系；实际擦除还会清除服务器所持档案中的相关内容。有独立证据的旧事实不会因此被反向撤销。可信本机档案可在核验后恢复关系，上传档案中的关系继续隔离。旧档案恢复不自动合并原脑在导出后发生的普通更正，删除记录则继续约束恢复。关系查找最多 32 条边、128 个认识版本、256 条来源与八层父依赖；达到上限时明确返回不完整，不能视为已查清全部变化。

经历发布后，运行时自动组织共同内容与实例差异，不增加模型调用。`patterns.py` 比较已有断言的完整语义字段：文字、条件、时间、现实或假设世界、核验状态等完全一致，且来自不重叠的经历，才可以共用一个定义。每次经历仍保留自己的来源、观察顺序和差异；没有观察到的内容不能从共同定义补入。这种精确共享与模型提出的 `abstracts` 关系分开：后者不会授权压缩不同含义的细节，也不能替代实例原文。

共同定义和绑定存入独立的 `memory_pattern_views` 派生表，事实仍完整保存。构建上限为 64 条认识、16 个经历、256 条完整来源，缺少精确父版本或来源则跳过组织，原事实仍按原规则使用。更正、删除或经历改版会清空相关视图内容；视图不会成为其他独立事实的证据依赖。导出档案不携带这份缓存；可信本机档案恢复后从核验过的原事实与经历有界重建，上传档案继续隔离。

普通 `prepare` 只有在完整选中某个视图的事实、重新核验来源后，才尝试把重复含义呈现一次，并分别附上每个经历的绑定、差异和适用性。实际渲染没有更短时，使用完整事实；整个已选组共同接受上下文预算，避免因压缩单独丢掉差异。覆盖范围始终是所选断言，不能据此声称检索已找齐经历或例外。带持久 `recall_plan` 的路径继续使用完整事实，因为计划尚未冻结组织视图的版本。明确线索仍通过已有检索寻找对应经历的有据细节。

语义向量是另一组可选配置；服务与后台处理器须使用相同的模型、版本标识和实际输出维度：

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

候选可通过词法、已配置的语义向量、经历结构和关联路径共同获得，随后统一检查来源与适用条件。不同表示采用不同的访问衰减：细节更快沉入深层，概括与关键认识保留更久，明确线索仍可能找回。衰减参数是可调整的工程策略，尚未经长期对话校准，也不是人脑遗忘的生物定律。访问衰减不会自动擦除原始来源；永久删除仍通过 `forget` 执行。

内部检索命中或模型自称“有帮助”不会增加外部使用计数。实际交付后、同一上下文中明确关联到这次回忆的用户确认、纠正或可信工具结果，才可改变访问强度与用途评估；同一反馈事件只计一次。这些指标描述记忆的使用价值，不是真实性概率，也不会把助手自己说过的话升级成世界事实。

## 开发监控接口

接口使用与记忆服务一致的 Bearer 认证：

| GET 路径（前缀 `/v1/brains/{brain_id}/runtime`） | 内容 |
| --- | --- |
| `/status` | 持久化与索引进度、待处理任务数、轮次阶段、活动租约数 |
| `/turns?limit=100` | 最近轮次，最多 200 条 |
| `/turns/{turn_id}` | 当前阶段、修订、来源版本、上下文占用、回忆计划、累计调用额度、恢复状态 |
| `/turns/{turn_id}/history?limit=100` | 有序阶段日志，最多 200 条 |
| `/turns/{turn_id}/sources` | 单独按权限展开当前有效输入和输出 |
| `/jobs?limit=100` | 最近后台与认知任务，最多 200 条 |
| `/jobs/{job_id}` | 任务种类、状态、尝试次数、累计调用与输入额度、错误类别及安全学习摘要 |

`recall_plan` 包含选择的记忆版本、观察过的来源版本、停止原因、搜索次数、耗时、覆盖限制和结构化决策轨迹。轨迹只保存动作、查询哈希与候选数量，不复制查询文本或模型解释；`recall_model_calls` 和 `recall_input_bytes` 提供跨恢复累计的额度记录。`orientation_job_id` 可关联本轮情境任务；任务接口不返回任务正文、原始模型结果或租约凭证。向量提供方的用量可以记录，聊天语义提供方尚未统一记录实际 token；无法确定实际金额时费用为 `null`，不能当成免费。服务强制限制调用次数与输入字节，尚不保证货币金额上限；完整计费与后台指标仍待完善。

监控记录不复制正文，也不保存租约凭证或隐藏推理。来源更正后依赖输出退出有效回忆；擦除来源时递归擦除派生输出，并过滤服务器持有的导出档案。档案保留事件依赖供恢复后继续执行删除约束；运行中的租约和未完成轮次不随记忆档案迁移。

认知任务列表和详情的 `learning_summary` 只返回处理状态、提议结果、接受与发布数量、固定采样限制和拒绝原因计数。例如任务 `completed` 但 `learning_outcome=no_proposals` 表示处理结束但没有提议；`accepted_counts` 有值也不代表已写入长期记忆，需再看 `published_counts`。`published_counts.pattern_views` 统计本次可用的组织视图，`belief_relations` 统计发布的认识关系，不代表客观事实数量或累计学习次数。`learning_window` 显示输入区间、尝试与成功处理位置，以及是否仍有待处理区间；`pending_sequence_span` 是序列跨度，不能当成消息条数。摘要不包含原文、模型解释或来源 ID；原认知结果因来源失效被清空后，待处理区间仍可独立展示。

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
| 开发辅助 | 鉴权状态、轮次与阶段日志、认知任务、回忆停止原因与额度、独立来源展开；可移除的本地工作台 |

条件触发的主动提醒、经过校准的衰减与关联传播、多模态理解、完整工具循环与流式宿主适配、云端账号与共享权限仍需实现和验证。程序性候选只是有来源的步骤与条件，不会自行执行或证明任务成功。当前仅支持拥有者私有范围；共享集合请求会明确拒绝。未配置语义 embedding 时，已有词法特征哈希不会被当成语义向量；配置后只在模型、版本、维度和内容哈希一致时使用对应向量。

原始表达有来源，不代表其中的客观世界事实已经被独立验证。默认回忆返回有依据的候选，并以 `partial` 说明语义充分性尚未判断。可选控制器和认知处理器的语义判断仍可能出错，多路搜索也不能保证找齐所有相关历史。引用原文中的一个子串，不足以证明它可以独立成为新的结论。

只有保持原文含义和来源归属的直接表述可进入 `grounded` 工作状态。语义处理器生成的当前情境使用 `inferred` 状态与有效期；当前情境不能反过来作为历史证据。未决事项的通用提交接口目前只接受 `unknown` 状态，不能把一条有引用的笔记直接宣布为已经完成。

当前档案导入导出和删除过滤不等于完成了生产备份体系。数据库物理备份、独立删除清单的恢复流程、迁移规模、运行 SLA 和长期对话效果，需要独立部署与实测。关闭和重开服务不会删除已提交记录。

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
- `architecture/`：完整目标、机器可读契约和 52 个系统验收场景。

## 验证

先启动并迁移项目专用数据库，然后运行：

```sh
uv run pytest -q
uv run ruff check src tests examples
uv build
```

TypeScript 测试与类型检查命令见 `sdk/typescript/package.json`。没有配置数据库时，数据库相关测试会跳过；跳过不代表通过。测试使用新建的随机租户与大脑，不依赖私人对话，也不调用付费模型或 Codex／Claude 订阅。

GitHub Actions 仅保留手动触发，不在 push 或 pull request 时自动运行。日常开发优先执行与修改相关的本地检查；发布前按改动影响验证，避免重复运行云 CI。

接口结构校验、确定性回归测试、真实 MCP 协议往返和数据库测试，只验证各自覆盖的行为。完整架构中的 52 个系统场景尚不能据此宣称全部通过，也未宣称在长期记忆质量上超过任何基线。
