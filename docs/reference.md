# Runtime and integration reference

[English overview](../README.md) | [简体中文概览](../README.zh-CN.md)

Latest verification: [historical feedback use and remaining acceptance work](../evaluations/v1-2026-10-25-preference-use.md). The detailed history and earlier failures below remain preserved; full longitudinal acceptance has not passed.

A model-independent, headless memory runtime for agents. The goal is to let a host integrate once, then let the runtime continuously manage the agent's working memory, long-term beliefs, and recall. Users should be able to converse normally; memory must form and be used even when the main agent does not explicitly call save or search tools.

See the [architecture and acceptance specification](../architecture/architecture.html) for the complete target, and the [autonomous runtime contract](../architecture/autonomous-runtime.contract.json) for product boundaries and host responsibilities. The current implementation provides the underlying memory service, an automatic synchronous text lifecycle, and optional situation understanding, semantic consolidation, and semantic recall: it uses recent conversation to identify the current goal, creates beliefs and episode structures with traceable sources, and searches and selects memories within a budget. Complete host integration and long-term memory quality still need validation. This is not yet a complete autonomous brain.

First-release work is bounded by the [finite acceptance and stop criteria](../architecture/v1-acceptance.md). Memory quality now requires [longitudinal evaluation](../architecture/longitudinal-memory-evaluation.md) of realistic use across multiple sessions: continuity in daily life and ongoing tasks, and whether remembered information helps the user. The new runner supports sixteen irregular sessions, answer-dependent user choices, normal recent context and durable continuation. Its [verification report](../evaluations/v1-2026-10-25-longitudinal-harness.md) separates executed checks from unassessed memory quality. Simulated story time does not prove real elapsed operation or aging, and the release gates have not yet all passed.

The [continued live investigation](../evaluations/v1-2026-10-25-longitudinal-followup.md) repaired provider envelope and reasoning controls, then completed a two-session, four-turn checkpoint. Independent review found a published memory with an incomplete citation set and unnecessary literal fragments. The [citation-coverage contract](../architecture/citation-coverage.md) adds a separate support-coverage gate; segmentation preserves time expressions and old-source identities. The report distinguishes local verification, bounded real-model diagnostics and the still-incomplete longitudinal evaluation. Earlier failures and unknown usage remain preserved; no full longitudinal pass is claimed.

The subsequent [v5 continuation](../evaluations/v1-2026-10-25-longitudinal-v5-stopped.md) stopped when a stored half-hour preference did not reach the host and an older, longer schedule resurfaced. The [retrieval repair](../architecture/conversation-retrieval.md) retains match strength and adds bounded source-neighborhood navigation for short replies. It preserves the context ceiling and citation boundaries; it does not establish completed longitudinal acceptance.

The current learning repair adds [source-derived relation capabilities](../architecture/relation-capabilities.md), [at most three admitted historical comparisons per fresh input root](../architecture/history-comparison-attention.md), and [private cross-conversation continuation within existing allowances](../architecture/cross-conversation-learning-funding.md). Unselected opportunities, admitted pending work, assessed no-change and actual publications remain distinct. The [local acceptance checkpoint](../evaluations/v1-2026-10-25-local-acceptance.md) records installation, regression and performance evidence. These changes still require the revised longitudinal acceptance; they do not establish human-like recall accuracy.

The [first synthetic campaign](../evaluations/v1-2026-10-25-acceptance-attempt-1.md) stopped after twelve delivered exchanges and 47 known-usage calls because a historical proposal contradicted its own relation coverage. The runtime correctly withheld that publication. The [versioned accounting repair](../architecture/history-link-coverage.md) derives coverage from exact relation endpoints and retains source and semantic verification. The former hundred-exchange/twelve-probe protocol and its failures remain regression evidence; that protocol is no longer the maturity gate.

The [single permitted follow-up under the former protocol](../evaluations/v1-2026-10-25-acceptance-attempt-2.md) passed its sixteen-exchange source/accounting/replay checkpoint, then stopped after twenty-three exchanges on a provider response timeout with unknown usage. All twenty-three new inputs were encoded; the last historical comparison and all twelve probes remain incomplete. There were 90 known-usage calls and one unknown-usage call. That campaign stays closed; the monitoring defects exposed by the timeout have a [locally verified repair](../evaluations/v1-2026-10-25-timeout-monitoring.md), separate from the preserved failed result.

The [cognitive memory implementation plan](../architecture/cognitive-memory-plan.md) describes proposed changes to the cognitive core, implementation dependencies, and behavioral evaluation. Its additional capabilities remain planned work, not a description of current behavior.

## Product and integration goals

The unit of integration is an agent with a stable identity. Its brain identity is independent of the model, process, or chat window; participants, relationships, and tasks separately constrain memory scope. The host delegates management of the message lifecycle and memory context through an adapter. The adapter invokes the underlying operations, so application code does not manually orchestrate `observe / prepare / commit` on every turn.

On each turn, the runtime should automatically receive actual inputs and tool results, understand the current goal and references, activate relevant memories, continue searching when needed, and assemble a budgeted working context for the model. Once actual delivery or an actual outcome is confirmed, it should update working state and long-term memory candidates. Background processing should trigger consolidation, association updates, and activation changes based on new evidence, conflicts, and task outcomes.

The current context still has a capacity limit, and temporary content may be compacted. Long-term memory rests on original sources and versioned cognitive state rather than repeatedly overwritten rolling summaries. Successful integration requires more than injecting a retrieved passage: it must demonstrate persistent state, spontaneous associations, revised understanding, and continuity after a restart.

Complete integration must work even when the main agent's memory tools are disabled. MCP is a supplementary operations interface; connections that only support tool calls must report that actual capability. A host without control over the message, reasoning, or context lifecycle cannot be labeled fully integrated. A model/API proxy that cannot confirm final delivery must not treat generation completion as proof that the user received the response.

The product does not require an administration UI. Authenticated status, metrics, and structured runtime log interfaces provide development diagnostics: which memories and sources a turn used, why recall continued or stopped, which versions changed, costs, and failure reasons. Logs do not copy conversation bodies by default and do not record hidden model reasoning. The existing local workbench is a removable development aid, not the product entry point or an acceptance prerequisite.

### Independent product and optional integrations

The primary audience is agents that access models through API keys. Direct integrations and third-party bot plugins share the same memory core. AstrBot, LangBot, NapCat, and similar projects are optional entry points, not prerequisites for installing, running, or developing core capabilities. Dependencies flow in one direction: **host adapter → public API / SDK → memory core**. The core does not import chat frameworks or fork memory algorithms for individual frameworks. Framework-specific dependencies belong in optional adapter packages.

The core owns persistent memories, provenance, identity and scope, recall, learning, decay, and recovery rules. Adapters translate actual events, map stable identities, connect lifecycle hooks, and supply delivery receipts. They access memory through public interfaces rather than accessing the database directly or maintaining another authoritative memory store. Each turn should have one lifecycle orchestrator, avoiding duplicate capture, injection, or generation by a plugin and a model proxy.

Changing models, channels, or chat frameworks should allow the agent to retain its existing brain. Trusted configuration must explicitly map participants and conversations across platforms; display names or model guesses must not merge identities, and private-chat memories must not automatically enter group chats. Uninstalling an adapter should not delete core data. Independent operation and adapter replacement both require acceptance checks for restart recovery, retry deduplication, scope isolation, corrections, and deletion. These goals must not be presented as completed capabilities.

The core, HTTP API, and Python synchronous text runtime operate independently of these chat frameworks. The desktop chat command and `open_agent()` session entry point connect directly to a configured Chat Completions model. External hosts still supply actual delivery and stable message identities. A model-compatible gateway and QQ framework adapters have not been delivered. MCP remains supplementary and cannot replace missing lifecycle integration.

## Desktop setup

Install [uv](https://docs.astral.sh/uv/) and start Docker Desktop, then run:

```sh
uv tool install --python 3.12 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
agent-memory desktop setup
```

This installation does not require Git or a repository clone, or separate startup commands for the database, API, and worker. If installation reports that the command directory is not in `PATH`, run `uv tool update-shell` and reopen your terminal. First-time setup asks for the service URL, model name, and API key used for memory learning; key input is hidden. Chat Completions-compatible providers are currently supported. The user supplies the model name and endpoint. Setup does not select a subscription or make model calls to probe the configuration. You can skip model configuration and still use persistent storage and lexical recall, but model-driven semantic learning remains disabled and status reports `cognition: not_configured`.

Setup creates a stable brain identity and local credentials, prepares a dedicated database, applies migrations, and starts the memory service and worker in the background. Repeating setup does not recreate the brain or clear its memories. The default API address is `127.0.0.1:8788`, and the database defaults to `127.0.0.1:55433`. On first setup, occupied ports are replaced with available ports and saved. An existing installation reports a port conflict rather than stopping another process or silently switching to a different brain.

For a reasoning model that needs more time or output than the default cognitive limits, explicitly use `desktop setup --cognition-preset reasoning`. The preset permits 240 seconds for proposal plus verification, 16,384 output tokens per request, and 65,536 decoded-content bytes. It does not select a model, enable thinking mode, or increase cumulative spending allowances. See [cognitive policy and recovery](../architecture/cognition-policy.md) for existing installations and the measured DeepSeek tradeoffs.

Setup prints an MCP configuration containing no secrets and saves it as `mcp.json` in the local configuration directory. You can retrieve it again with:

```sh
agent-memory desktop mcp-config
```

Add this configuration to a host that supports stdio MCP to connect the memory tools. Configuration locations vary by host; setup does not currently modify external client configuration automatically. Starting this MCP connection automatically starts or connects to the local service. Closing the MCP connection leaves the service processing background tasks. After a computer restart, the next MCP connection or SDK `open_client()` call starts the service again with the same brain; no new setup is required.

**The MCP connection is still `tools_only`.** It does not automatically read all host conversations, take over compaction, or confirm message delivery. Agents that need automatic memory should use the Python runtime below and supply the host message lifecycle. Successful installation, a configured cognition model, and automatic host integration are three distinct states.

### Direct chat and Python sessions

After setup, explicitly reuse the configured memory learning provider for replies, or run `configure-chat` without flags to enter a separate provider:

```sh
agent-memory desktop configure-chat --use-cognition
agent-memory desktop chat
```

Chat uses the same persistent brain and automatically captures input, recalls memory, generates an answer, records actual terminal delivery, and updates working state. No chat framework or memory-tool calls are required. The next invocation reconnects to the same brain and default conversation. Use `--conversation <stable-id>` and `--branch <stable-id>` to select a session. A new conversation is not a separate brain or a tenant privacy boundary; unrelated users need separate authorized brain identities.

Successful delivery does not prove successful learning. Chat reports unavailable or incomplete situation processing on stderr after checking only the completed turn and its orientation job. Interactive startup also checks background progress once; scripted single-message invocations do not scan that history. Diagnostic failures do not retry the model or turn an already delivered answer into a failed operation.

Reply settings live in private `chat.json`, separate from the cognition settings in `profile.json`. They are protected local plaintext, not a system keychain. Changing the reply model does not restart the memory service or change the brain. `configure-chat` supports `--base-url`, `--model`, `--api-key-env`, optional `--thinking`, and `--non-interactive`. The default per-request limits are 4,096 output tokens, 180 seconds of network timeout and elapsed-time checks, and 1 MiB of response bytes. Override them with `--max-output-tokens`, `--timeout-seconds`, and `--max-response-bytes`. These are not cumulative spending limits or a hard cancellation deadline for a remote request. The runtime separately caps delivered text at 64,000 bytes. Configuring only replies leaves model-driven memory learning disabled, and chat reports that state.

The Python entry point uses the same saved model configuration and session protection:

```python
from agent_memory.agent_session import open_agent
from agent_memory.runtime import DeliveryReceipt

def deliver(request):
    channel.send(request.text)
    return DeliveryReceipt("delivered")

with open_agent(conversation_id=conversation_id, deliver=deliver) as agent:
    result = agent.respond(text, message_id=message_id)
```

Install the package in the host's Python environment as well. The host supplies `channel`, input text, and stable identities. `open_agent()` starts or connects to the local service; `directory`, `branch_id`, `instructions`, `policy`, and `start_service=False` provide explicit overrides. A custom `generate` callback also requires a stable `generator_fingerprint` identifying its actual model and configuration. Without `deliver`, the result remains a draft and the session remains pending; reopening with a delivery callback and calling `resume()` can deliver an eligible draft without generating it again. The lower-level `BrainRuntime` below remains available for custom recall controllers and answer reviewers; desktop chat does not enable those paid calls implicitly.

One local session holds a cross-process lock for its brain, conversation, and branch. Other sessions using that same installation cannot run a second turn in that scope concurrently. This does not serialize clients on other machines, lower-level `BrainRuntime` instances, or direct API calls. A small durable sidecar holds pending identities and fingerprints, never message or answer bodies; the database remains authoritative. Pending work blocks a different message and freezes the model configuration, instructions, and runtime policy while generation or delivery remains possible. Already delivered or completed turns can finish or replay after a configuration change, with database identity and source checks retained and no new generation or delivery. Rotating a key alone does not change the generation fingerprint. Keep the installation directory intact when restarting.

Interactive commands are `/quit`, `/resume`, and `/retry <original input>`. Recovery is explicit. If input capture was interrupted, retry the exact original input; the CLI reuses its saved message identity. For scripted use:

```sh
agent-memory desktop chat --conversation example --message 'Hello' --message-id event-001
agent-memory desktop chat --conversation example --resume
```

Keep the same message ID and text when retrying a scripted send. Reusing an existing ID with different input is rejected before creating new pending state. Recovery checks the pending input fingerprint against the database. A completed replay does not print or send the answer again. An uncertain model call or terminal write stays blocked rather than automatically charging or sending again; `/resume` cannot resolve an unknown external outcome. A successful terminal receipt means output was written and flushed, not that a person read it.

To deliberately close a blocked turn, use `/cancel <exact-turn-id>` or `chat --cancel <exact-turn-id>`. This preserves its sources and failure record and permits a new message with a new ID. For an unknown generation or delivery outcome, explicitly use `/cancel-uncertain <exact-turn-id>` or add `--acknowledge-uncertain` to the scripted cancellation. Cancellation neither retries the model nor sends a reply, and cannot undo a possible charge or send; the journal retains `generation_uncertain` or `delivery_uncertain`. A confirmed delivery still awaiting commit must be resumed, not cancelled. The Python equivalent is `agent.cancel(turn_id=..., acknowledge_uncertain=False)`. A cancellation network failure keeps the pending record so the same cancellation can be reconciled against the durable journal. Cancelling is also the way to close an undelivered turn before changing a frozen policy or model; do not manually delete its sidecar.

Inspect the unfinished turn before choosing a recovery action:

```sh
agent-memory desktop turn-status --conversation example --branch main
```

Use the original conversation, branch, and installation directory; defaults are `desktop-chat` and `main`. This command reads bounded metadata from an already-running service without generating, sending, resuming, or cancelling anything. `turn: null` means no unfinished owner was found at that moment, not that all messages were delivered. See [integration and recovery diagnostics](../architecture/integration-troubleshooting.md) for provider errors, occupied conversations, and uncertain outcomes.

This entry point supports synchronous text Chat Completions only. It preserves runtime messages, rejects truncated replies and tool calls, and does not retry provider requests. It does not implement streaming, tool loops, multimodal input, a model proxy, or automatic integration into another client. `ChatCompletionsGenerator.last_usage` and `last_request_id` expose bounded, validated diagnostics for its most recent call; missing or invalid usage remains unknown, and these fields are not a persistent billing ledger. Full cognitive quality remains a separate evaluation target. The default orientation policy stays `always`; `chat --orientation on_demand` explicitly selects the tradeoffs described below. `--instructions-file` accepts a UTF-8 file up to 64,000 bytes, subject to the smaller effective full-input budget.

### Local status and configuration

```sh
agent-memory desktop status
agent-memory desktop logs
agent-memory desktop stop
agent-memory desktop start
agent-memory desktop restart
```

`status` checks the authenticated service, database, brain identity, and worker. The presence of model configuration does not verify provider availability or memory quality. `logs` returns bounded, structured service events without conversation bodies or API keys. `stop` and `restart` retain the database volume and configuration; they do not delete memories. Shutdown is requested through the authenticated service instance rather than by killing a process using a stale PID. Updating cognition configuration through `setup` automatically restarts the services that need the update. With `--no-start`, setup only saves configuration; run `restart` later to apply it. After updating the installed program, also run `desktop restart` so the background instance loads the new code.

To update the source-URL installation above to the current `main` branch, reinstall the package and restart the service:

```sh
uv tool install --python 3.12 --force --reinstall-package agent-memory-runtime 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
agent-memory desktop restart
```

`--reinstall-package` refreshes the source archive even when the package version is unchanged. Keep using the same `--directory` for a non-default installation; there is no need to create a new profile or brain. If a separate Python host imports the SDK, update that environment before reconnecting it too, replacing the interpreter path below with the host's actual interpreter:

```sh
uv pip install --python /absolute/path/to/host/python --reinstall-package agent-memory-runtime 'agent-memory-runtime @ https://github.com/chriswu727/agent-memory/archive/refs/heads/main.zip'
```

The service freezes a content-based build identity at startup, covering runtime code, contracts, and migrations. A changed build reports `restart_required` even when the package version is unchanged; `start` will not silently reuse the old daemon. Startup and migration reject unknown or non-prefix schema histories, and a downgrade check runs before stopping a live service. These checks do not prove that historical migration SQL was unmodified. The real two-build Docker test verifies retained brain identity and sources, plus refusal to stop a newer service during an incompatible downgrade.

The default configuration directory is `~/Library/Application Support/Agent Memory` on macOS, `%LOCALAPPDATA%\Agent Memory` on Windows, or `$XDG_DATA_HOME/agent-memory` on Linux (`~/.local/share/agent-memory` when unset). All desktop subcommands accept `--directory <absolute-directory>`; use the same directory consistently for one installation. This directory contains local credentials. Memory content is persisted in the installation's Docker data volume, so copying the configuration directory does not back up the database.

Use `desktop backup --output PRIVATE_DIRECTORY --request-id STABLE_ID` for a streamed full-database checkpoint. Supported recovery requires that bundle and the independently current deletion registry; it restores into a fresh quarantined installation and needs explicit checkpoint activation. Old pending work does not automatically resume. The [backup and recovery guide](../architecture/desktop-recovery.md) explains current-authority preservation, sensitive plaintext artifacts, new credentials, and the required external-host recovery epoch.

For automated deployment, save configuration first and start the service later:

```sh
agent-memory desktop setup --non-interactive --no-start
agent-memory desktop start
```

To configure cognition non-interactively, use `--base-url`, `--model`, and `--api-key-env` to specify the service URL, model, and **name of an already-set environment variable containing the key**. Do not put the key value in command arguments. The optional `--thinking enabled|disabled` flag is only for providers that support it. The desktop service uses its own configuration; it does not automatically read the repository's `.env` or migrate brains from the development database into the new installation.

The current delivery provides local command-line installation and service management and still requires uv and Docker Desktop. There is no native one-click installer or complete lifecycle adapter for a specific host yet. The implementation includes Windows and Linux path and process handling, but desktop end-to-end acceptance currently covers macOS; the other operating systems have not been tested on actual machines. See [desktop installation and recovery acceptance](../evaluations/desktop-2026-10-24.md) for coverage and limitations.

### Development setup

Existing development environments can continue using the following workflow. It requires Python 3.12, uv, and a working Docker installation. Run these commands from a cloned repository. The development database has its own project volume, separate from the desktop installation above.

```sh
uv sync --frozen
uv run agent-memory init
docker compose up -d --wait
uv run agent-memory migrate
uv run agent-memory serve
```

Run the persistent worker in another terminal:

```sh
uv run agent-memory worker
```

The service runs at <http://127.0.0.1:8787>, with its access token saved in `.local/access-token`. The development workbench is available at the same address for manually inspecting underlying behavior; the target product does not require opening it. Run `init` once; it does not overwrite existing configuration. `.env` and `.local/` contain local credentials and are excluded from version control.

`init` only creates configuration. A development integration must also create a brain after the API starts and persist the returned `id`. Reuse that ID thereafter rather than creating a new brain on every startup. For example, create it once with:

```python
from pathlib import Path
from agent_memory.client import MemoryClient

with MemoryClient("http://127.0.0.1:8787", Path(".local/access-token").read_text().strip()) as memory:
    brain = memory.create_brain("My Agent")
    Path(".local/brain-id").write_text(brain["id"] + "\n")
```

The database binds to `127.0.0.1:55432` by default, and HTTP binds to `127.0.0.1:8787`. The workbench, API, and worker share the same core. Use `uv run agent-memory worker --once` to process one pending job per brain manually. `docker compose down` stops the database while retaining its volume; do not use `down -v` unless you intend to delete the entire database.

## Automatic integration

Python's `BrainRuntime` manages synchronous text turns. The host initializes it once with a model generation function and an actual delivery function. Application code submits received messages without calling memory tools or orchestrating reads and writes on each turn. The following example is for host developers. Install `agent-memory-runtime` in the host's Python environment as well, using the same source URL if needed; the CLI installed by `uv tool install` has an isolated environment and does not change the host's Python dependencies. Integration code can read the local configuration and connect to the stable brain directly:

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

`open_client()` starts or connects to the local service and returns a `MemoryClient` bound to the stable `brain_id`. Pass `directory=Path(...)` to select an installation directory, or `start_service=False` to connect only to an already-running service. It does not require copying an access token and does not read `.env` from the current working directory. Development deployments can still construct `MemoryClient` manually and retain the brain in their original database.

The host supplies `model`, `channel`, and message identities in this example; `open_client()` does not capture host messages itself. The generation function must use the runtime-provided `messages`, which preserve host system instructions and add sourced, budgeted memory material and the current input. The host must persistently assign `message_id` when receiving a message and keep it unchanged across retries. A new process reconnects using the same brain identity. [examples/autonomous.py](../examples/autonomous.py) uses a fixed reply instead of a reply model. Configured server-side cognition and embedding providers can still make paid calls for situation analysis and background processing. To run this example without model calls, use a service and worker with neither provider configured.

The runtime automatically persists inputs, recalls before answering, saves drafts, confirms delivery, retains valid working state, and checkpoints. Each turn uses leases and revision checks. Draft persistence and phase advancement share one transaction, as do delivery recording and phase advancement. `runtime.resume(turn_id)` continues from the persistent phase; completed turns return their delivered result without generating or sending again. With a cognition processor configured, the runtime also interprets the current situation before answering. Source-checked situation inferences are merged into working state only after actual delivery, and remain labeled as inferences rather than verified facts.

Return `delivered` only after the host confirms actual sending. For partial delivery, use `DeliveryReceipt("partially_delivered", delivered_text=actual_text)`; use `cancelled` for cancellation and `unknown` when the sending outcome cannot be confirmed. Without a delivery function, the turn stops at `generated`, and its draft is excluded from recall. `delivered` means host-confirmed delivery, not proof that the user has read the message.

A durable reservation allows one unfinished turn per brain, conversation, and branch across hosts connected to the same upgraded service. A competing new turn receives `scope_conflict` before its input is captured or its model is called. Releasing or expiring the worker lease does not release that reservation: a saved draft or uncertain external operation still belongs to its original turn. Resume that turn or explicitly close it before submitting a different message. Independent conversations and branches remain available. A completed message can still replay while a newer turn owns the scope. `scope_reserved` and `reserved_scope_count` expose occupancy separately from active worker leases.

Delivery recording distinguishes the host's confirmed send from successful memory-state commit. After a valid sending authorization, `record_output` preserves a bounded `delivery_receipt` containing the actual-text SHA-256 digest and byte count, authorization metadata, and fixed disposition and failure codes. It never copies response text into monitoring. If unrelated memory or working state changes during sending, the actual delivered content can be retained while the turn closes as `failed`; stale situation updates are not applied. If a bound source was changed or erased, only receipt metadata is retained and no deleted text is revived. The receipt's `state` can therefore be `delivered` while the turn's `stage` is `failed`. A later invalidation before `finish` also preserves the receipt and closes the uncommitted turn without overwriting newer working state.

The runtime reports that failure without sending again. A local session clears its pending marker only after verifying the matching terminal receipt; an uncertain network response keeps recovery pending. A confirmed scope rejection with no remote turn can also clear a local marker, without cancelling the other host's turn. A lease lost to another worker still requires reconciliation: this does not guarantee exactly-once delivery across a process crash between sending and recording its receipt. Existing checks before generation and before sending remain enforced.

Upgrade all API service processes before relying on cross-host exclusion; mixing older servers against the same database is unsupported. Migration preserves historical unfinished turns and grants each scope's oldest turn the reservation, leaving later legacy turns available for ordered recovery. Migration requires a database role that can see all rows despite row-level security and fails rather than silently skipping hidden turns. The normal desktop migration connection already has that role. Runtime reservations and delivery receipts are operational records and are not copied into memory archives.

For an older delivered turn with no receipt, resume can still close a stale turn and release its reservation after source deletion. If this returns a terminal failure, explicitly cancel its local pending marker before sending the next message; the session cannot automatically verify a receipt that was never recorded.

When an external model call or delivery has an uncertain outcome, the default is to stop and report `generation_uncertain` / `delivery_uncertain`. Enable `generation_is_idempotent=True` / `delivery_is_idempotent=True` only if the host implements durable deduplication or result lookup using `request_id` / `delivery_id`. Ordinary HTTP retries, printing output, or a typical model API do not establish this property. Lease expiration can block later writes and calls, but cannot cancel a request already accepted by an external system. Similarly, deletion and correction cannot retract content already provided to a model or delivery channel; subsequent generation results are checked again.

`RuntimePolicy` separately limits memory material and the full message budget, using UTF-8 bytes as a conservative estimate. The runtime reserves host/protocol/current-input overhead and JSON escaping before requesting memory; the final complete prompt remains bounded. The host should reserve space within the model's total context window for output and protocol overhead. The current service limit for text output is 64,000 bytes. The synchronous adapter does not handle tool loops, streaming messages, automatic message editing, context compaction, or merging branches from concurrent turns. Its capability is explicitly reported as `partial_automation`.

### On-demand analysis before answering

The default `RuntimePolicy(orientation_policy="always")` retains a situation proposal and an independent verification on every turn. After applying `011_orientation_route.sql`, integrations focused on ordinary conversation and recall of known facts and preferences can explicitly select:

```python
from agent_memory.runtime import RuntimePolicy

runtime = BrainRuntime(
    memory, generate, deliver=deliver,
    policy=RuntimePolicy(orientation_policy="on_demand"),
    answer_reviewer=answer_reviewer,
)
```

The server examines candidate dependencies and relations from the current retrieval before applicability filtering. When there are no situation dependencies, it reuses the same context packet to answer directly, avoiding two foreground semantic calls. Conditional memories or relations, working items, unresolved items, or incomplete dependencies still require full analysis. Existing orientation jobs continue to resume; semantic recall controllers and vector recall also retain full analysis for now. Routing adds no classifier-model call, does not guess from keywords, and does not claim that the entire brain contains no other relevant memories.

`orientation_route` persistently records the policy, the decision to skip or analyze, fixed reasons, and input and dependency fingerprints. Skipping is not successful semantic verification. The server stores an internal preflight and rechecks it under a lock before preparation and generation. A version counter alone is insufficient because newly published background beliefs may not yet have advanced it. After generation, the skip prerequisites, current context, and sources are checked again even without an answer reviewer. Recovery cannot silently change the policy. Preflight records are excluded from ordinary monitoring responses; routing exposes only identifiers, counts, and hashes, without copying message bodies.

The two policies are not fully equivalent: skipped turns do not immediately derive or commit new situation summaries, goals, or pending-task state. The original input is still independently passed to the host and persisted. With a cognition processor configured, background learning is still queued and must be executed by the worker. Agents that depend on immediate goal tracking should retain `always`. The existing `answer_reviewer` independently controls answer review; the fast path does not disable it automatically.

The on-demand path trades extra local reads for less foreground model waiting. Complete delivery typically prepares or rechecks context five times. Snapshots remain conservatively bound to brain-wide event and index progress. Other conversations or background activity may trigger `context_stale` even when the ultimately relevant content is unchanged. This concurrency limitation has not been optimized; current small-sample latency must not be extrapolated to high-concurrency performance.

### Answer review before delivery

Correctly calculating a memory's applicability does not guarantee a correct host response. An integration can enable independent answer review:

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

Apply `010_response_review.sql` before enabling review. The example explicitly reads these environment variables. Existing integrations without `answer_reviewer` remain disabled and do not incur hidden model calls. `reviewer_id` must stably identify the actual endpoint, model, and reasoning configuration; change it when changing providers. The runtime cannot automatically identify the internal implementation of an arbitrary custom callback.

Each draft is reviewed at most once. The runtime first persists the draft and call reservation, then supplies the reviewer with the full messages used for generation and the original draft as material. The reviewer separately checks the current input, historical support, applicability, uncertainty, and claims that actions have completed. Returning or sending the draft is permitted only when all checks explicitly agree and coverage is complete. Problems, uncertainty, invalid responses, or provider failures raise `RuntimeBlocked`. The draft is retained but neither delivered nor included in recall. Hosts must handle this state; there is currently no automatic rewriting or paid retry to generate a replacement answer.

The policy, request and draft digests, and memory versions are bound to the turn; the server also checks delivery permission. Recovery does not call the reviewer again if a process interruption leaves review `pending`. An approved draft can continue after reconstructing and checking its original context and current applicability. A prepared policy cannot be downgraded by disabling review. Completed turns still replay the original delivery fact, without retroactively declaring that an old answer passed a new review. An unknown sending outcome remains `delivery_uncertain`, even if the recovery instance has no delivery callback.

`GET /v1/brains/{brain_id}/runtime/turns/{turn_id}` exposes `response_review_policy`, `response_review`, and reserved call counts; `runtime/status` exposes counts by state. Diagnostics store only fingerprints, states, and fixed reason codes, without copying drafts or conversation bodies. Completed review counts, reservations, and actual token usage are different quantities; callers must meter actual provider usage separately.

Review adds one model request and its latency. Separate budgets limit input bytes, output tokens, and response bytes. The built-in HTTP adapter enforces network timeouts; custom synchronous callbacks must honor request deadlines themselves. Semantic consistency and complete coverage remain model judgments, not factual proof. Permission is bound only to the draft passed to the delivery callback. Text truncated or rewritten by a channel is recorded as actually delivered, without claiming it passed the same review.

Historical user feedback may guide a reversible suggestion for the same task without certifying a permanent current rule. The host and optional answer reviewer share this [bounded permission](../architecture/historical-suggestions.md); current changes, false applicability and unresolved conflicts still constrain use. An earlier assistant proposal does not establish user adoption.

The host interpretation protocol, `memory-consumption-v5`, distinguishes a natural conversational acknowledgment from a claim that storage has completed. Without trustworthy completion evidence for the corresponding operation, the host must not claim that something has already been saved or permanently remembered. Capturing the original message does not establish that semantic consolidation is complete. This is a semantic constraint, not a word blacklist. If an answer is rejected, the received user input remains persisted. With a semantic processor configured, its background learning window was already queued in the capture transaction and must actually run in the worker; queuing alone is not successful learning.

Assistant speech exposed for an explicit speech, quotation, episode or commitment request carries `assistant_speech_not_user_experience`; it cannot prove details of the user's life or external outcomes. An older literal's optional `source_act` explains a corrected speech-act interpretation while preserving its original statement. New events explicitly record their segmentation version. New cognitive jobs use `recall-resolution-v1`, inheriting `source-act-boundary-v1`; older frozen requests retain their prior contract. See the [source and recall repair architecture](../architecture/longitudinal-repairs.md) for scope and ranking, and [source-preserving recall resolution](../architecture/recall-resolution.md) for verified short expressions and compatibility.

`recall` accepts optional `detail_level="auto" | "summary" | "full"`; MCP exposes the same option. Auto can use a verified short expression for an older memory. Concrete detail cues and explicit recollection intents keep full detail. `summary` is a preference, not permission to omit necessary qualifications. `inspect(memory_ids=[...])` returns the full assertion and original sources. Compact context marks short expressions as `summary_expandable`; raw results retain complete memory documents and presentation diagnostics. Use the SDK context helpers for model injection rather than dumping that raw result. Existing assertions without a verified summary remain detailed, and upgrade performs no automatic paid archive rewrite.

### Semantic recall

The host can configure a model for recall during initialization. The main agent still receives assembled context and does not need to decide when to call memory tools. This example reuses the earlier `memory`, `generate`, and `deliver`; actual model calls use API credentials supplied by the host:

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

The adapter uses Chat Completions JSON mode and requires support for `response_format: {"type": "json_object"}`. `base_url` is the API root; the adapter appends `chat/completions`. The host explicitly selects the model. See [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/) and the [Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion) for the protocol. You can also supply your own `reason(RecallReasoningRequest) -> str` function, returning a decision according to the request's JSON Schema. Without a controller, recall remains lexical and makes no additional model calls.

Each turn automatically retains recent received or delivered messages from the same conversation and branch: up to six by default, configurable from zero to eight through `RuntimePolicy(recent_context_events=...)`. They provide chronological context and are explicitly labeled as conversation material. Assumptions in questions, assistant statements, and dreams do not thereby become objective facts. Recent messages and long-term candidates share the context budget.

Both default lexical recall and semantic recall in the synchronous runtime use a historical evidence boundary preceding the current input. The current statement still participates in answering and situation understanding as the current input, but cannot be retrieved again as independent evidence that it was already remembered. Recovery reassesses memory applicability. The diagnostic-only field `evaluated_at` is excluded from the model projection so that merely refreshing the evaluation time does not alter the same request. Full diagnostics retain that timestamp; actual changes in validity, expiration, or sources still prevent reuse of an old request.

Earlier user instructions such as “Please keep your answers brief” can be recalled as preferences after independent semantic verification. Their source must be a received user message, and the full instruction fragment must be cited. This rule also preserves preferences with counterevidence. It neither turns the original instruction directly into a fact nor relaxes historical-evidence restrictions for question presuppositions, the current input, or its descendants.

The controller can stop recalling or propose another cue and continue searching. At completion, it can select only memory IDs it has already seen. All searches retain the evidence boundary preceding the current input. The server ultimately rechecks sources, versions, corrections, deletions, and scope. Indistinguishable candidates remain `ambiguous`; inadequate support remains `insufficient`. `sufficient` means only that the model considers the material relevant, not that facts are verified or history has been exhausted.

Selected versions, recent-conversation references, and coverage limits are persisted as a `recall_plan` before generation. Recovery reconstructs context from the same plan and stops if a source becomes invalid, rather than silently substituting another memory. Model-call counts and input bytes are durably reserved before each request and do not reset after a process interruption. Search-count and elapsed-time limits apply to each planning attempt; they do not accumulate across restarts. Failed requests still consume their reservations, and the adapter does not retry automatically.

By default, each planning attempt has a 15-second limit, and each model output is limited to 1,024 tokens, with a response-byte limit checked as well. Timeouts use network-operation limits and checks after return; they cannot forcibly interrupt arbitrary host callbacks. Call and byte budgets limit resource use but are not connected to actual provider token billing and are not monetary caps. Provider failures, invalid decisions, or budget exhaustion record a stop reason and preserve uncertainty in the answer.

### Configuring situation understanding and long-term learning

All additional model providers are disabled by default. To enable server-side cognition in a development deployment, set the complete configuration below in `.env`, then restart the service and worker:

```dotenv
MEMORY_COGNITION_BASE_URL=https://your-provider.example/v1
MEMORY_COGNITION_API_KEY=<your API key>
MEMORY_COGNITION_MODEL=<your chat model>
```

`MEMORY_COGNITION_THINKING` is optional and accepts `enabled` or `disabled`; when unset, the provider's default behavior applies. Use it only with providers that support `thinking.type`. All three settings above remain required; configuring only the switch is rejected at startup. Output-token and response-byte limits are separate, and truncated content cannot count as a successful proposal.

`MEMORY_COGNITION_REASONING_EFFORT` optionally forwards a supported provider effort (`minimal`, `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`); unset leaves the provider default, and an explicit effort cannot accompany disabled thinking. Provider support varies. `MEMORY_COGNITION_MAX_ENVELOPE_BYTES` separately bounds the complete HTTP response, including hidden reasoning, from 1 to 1,048,576 bytes; its default is 65,536. Neither setting changes the semantic content limit or retries a failed request. On desktop, `agent-memory desktop configure-cognition-policy --reasoning-effort low --max-envelope-bytes 262144` changes only these options and preserves existing time/output/content limits. Restart to apply. Use `--reasoning-effort default` to remove the override. Existing policy files remain readable; saves use policy format v2.

Optional `MEMORY_COGNITION_MAX_WALL_TIME_MS`, `MEMORY_COGNITION_MAX_OUTPUT_TOKENS`, and `MEMORY_COGNITION_MAX_RESPONSE_BYTES` default to 30,000, 8,192, and 32,768. Their upper bounds are 600,000, 32,768, and 65,536 respectively. Wall time covers both proposal and verification; output tokens apply per request, and decoded-content bytes are distinct from the transport's bounded response envelope. For a desktop installation, inspect `agent-memory desktop cognition-policy`, or save all three with `desktop configure-cognition-policy --max-wall-time-ms 240000 --max-output-tokens 16384 --max-response-bytes 65536`, then restart. These example values explicitly increase the allowance; defaults are unchanged.

New learning and orientation snapshots freeze the complete effective semantic policy. Restarting with different limits does not rewrite a started job's frozen policy or reset its reservations. Legacy snapshots without a policy field retain their earlier behavior of using the current processor settings; incompatible reserved requests still cannot be reused with a different fingerprint. Desktop connections allow a bounded 615-second response wait for the maximum frozen policy, with separate five-second connection/pool limits. Other hosts must choose an HTTP response timeout that covers their server policy; the Python SDK's general default remains 30 seconds.

The provider must support Chat Completions JSON mode. Once configured, `BrainRuntime` automatically calls `orient` before answering. Starting with the current input and recent sources from the same conversation and branch, it adds relevant earlier beliefs allowed by the current access scope, along with their sources, parent dependencies, and explicit counterexamples. It proposes current goals, references, and constraints, then checks whether the cited sources support those inferences. The background `worker` runs `consolidate` on new inputs and actually delivered replies, producing semantic anchors, episode fragments and outlines, conditional beliefs, and procedural candidates. They retain original sources, versions, counterexamples, and applicability conditions. Both proposals and verification can be wrong, so results remain labeled as inferences. The current configuration uses the same provider for two independent requests and does not certify objective facts. The verifier assesses the premise carried by each citation, then uses the required `proposal_verdict` to determine whether the evidence jointly supports the complete proposition. A missing or uncertain overall verdict, or any mismatched citation, blocks publication. Every verification item must also include `link_verdict`: `null` for an ordinary proposition and an explicit verdict for a belief relation. Locally valid evidence alone cannot justify a change direction or bounded generalization. Custom verification providers must follow the request's complete response schema.

The server places situation inferences in a `current_situation` material block that the runtime automatically passes to the main model. It interprets the current context and cannot serve as historical evidence. For example, “Do you remember when we went to the beach?” can trigger recall but cannot establish that a beach visit happened. A delivered assistant statement can support “the assistant previously said this,” but cannot independently prove an external event or task completion.

New foreground jobs use the dedicated `orient-v1` contract to propose and verify only the current situation. Long-term beliefs, episode organization, and belief relations remain background work. An empty situation is valid; ordinary conversation is not forced to produce inferences. Any proposed situation is still independently verified. The mode and complete workspace are frozen with the job, and retries do not refresh workspace timestamps. An actual workspace version change prevents reuse. Older snapshots continue using their original general-purpose contract without automatic upgrades. Material selection still preserves complete source dependencies and conservative budgets. See the runtime section above for the optional `on_demand` policy and its tradeoffs.

Fresh foreground and background jobs freeze the input contract `citation-provenance-v1`, which retains the separate coverage verdict introduced by `citation-coverage-v1`. Verification of `claims`, `situation_updates`, and `belief_links` must include `citation_coverage_verdict`; publication requires `supports` alongside the existing whole-proposal, citation, request, instance, and relationship checks where applicable. The server-derived `citation_scope` limits positive support to that proposal's selected numbered excerpts plus a narrow provenance allowance: each selected reference's own source `observed.actor_role` and `observed.delivery_state`, bound by exact event ID, revision and content hash, may support only recorded speaker role and communication status. These fields cannot establish a human identity, external truth or completion, or uncited text. A list heading cannot support uncited list items. Remaining source content, surrounding context, and parent assertions may detect qualifications, conflicts, and counterevidence but cannot fill a missing positive premise. Another proposal's citations, including an episode's complete member sources, cannot supply that premise either. For abstractions, coverage and provenance use only the selected positive references. Missing, uncertain, or contradicted coverage blocks publication without adding a model round.

The provenance clarification is covered by deterministic request, guard and restart regressions. A [bounded real-model diagnostic](../evaluations/v1-2026-10-25-citation-provenance-verification.json) accepted two fully cited attribution claims and withheld a summary with missing citation coverage; saved responses also passed independent installed-processor replay. These observations do not establish general accuracy or longitudinal acceptance. Frozen `citation-coverage-v1` jobs retain their exact messages, payloads and response schemas, and existing longitudinal evidence keeps its recorded contract. See the [citation coverage contract](../architecture/citation-coverage.md) for the permission boundary and limitations.

The contract retains the communication and episode-scope rules and separate attribution, modality, specificity, and boundary verdicts for episode organization. Episode proposals retain complete member-source authority and do not return `citation_coverage_verdict`. Missing, uncertain, or contradicted organization facets block the episode; model verdicts remain fallible. The model receives the explicit scope for episode operations, eligible members computed under the existing eligibility rules, and the background learning window's `learning_input_refs`. Historical sources can support belief comparison but do not authorize organizing episodes across conversations. An empty episode list means only that the current scope has no editable episodes. Learning references do not grant `current_request` authority; a current request must still be bound separately to an actual turn. Valid empty text records can count as processed within a window but cannot serve as episode citations. Material selection measures labels using the actual contract so that long identifiers do not overflow a batch. Recovery checks exact source versions, windows, conversations, and branches. Older snapshots, including those without an input-contract version, retain their original inputs and prompts; retries do not upgrade the contract or rewrite the request.

New requests consume historical episodes as [source-backed navigation views](../architecture/episode-source-navigation.md): exact member versions, hashes and structural boundaries, explicitly marked as fallible grouping. Complete member sources remain available, while old free-form organization labels and verifier verdicts stay outside future model context. Raw audit records and old frozen requests remain unchanged. This contains label propagation; it does not correct a false verifier judgment or an already contaminated assertion. The [fixed diagnostic](../evaluations/deepseek-2026-10-25-episode-fidelity.md) found two false acceptances under both older verification contracts.

The new input contract explicitly separates permission to cite a current message from evidence that it asks the assistant to do something. Self-reported preferences, changes, and plans need not create tasks; actual questions, implicit requests, and mixed input still receive independent intent verification. Quoted or imagined requests do not become current authorization. Frozen older contracts keep their exact original payloads and instructions. This is a protocol repair whose real-model classification quality must still be evaluated.

Current questions or requests are represented separately with `evidence_use: current_request`. The server binds `current_input_refs` from the actual runtime turn. The model may use these only to propose current goals or questions to answer, subject to an independent `request_verdict`, overall-proposition verification, and verification of every source. This path does not create situation observations, authorize long-term facts, or treat question presuppositions as history. Snapshot recovery, publication, and reuse all recheck the turn binding and original verdicts. Older snapshots do not acquire new permissions automatically. Requests retained in later workspaces are labeled as earlier request context, not the current request.

Conditions and situation observations share versioned type definitions. For example, “prefers quiet when preparing for exams” is stored as a conditional rule; “is preparing for exams” needs separate source support. A rule cannot activate itself. Recall results use `applicability` to distinguish established, not established, unknown, and conflicting applicability, recording the evaluation time and supporting evidence. Unknown, conflicting, or expired rules enter `conditional_memory` and are explicitly labeled as unavailable for direct use as a current rule, although they can still be recalled as history. Context assembly reassesses applicability and includes both supporting and opposing evidence in its source dependencies.

Content with counterevidence or an unresolved belief state is not labeled as a directly usable current rule even when its condition holds. After the host's existing instructions, `BrainRuntime` adds a fixed memory interpretation protocol requiring the main model to distinguish historical statements from current applicability, retain uncertainty and conflict, and avoid choosing conclusions solely by dates or record order. The protocol counts toward the input budget and generation fingerprint. A protocol change prevents reuse of old context that was prepared or whose generation outcome is uncertain; completed turns remain replayable. Already-generated replies retain their original delivery flow and, when answer review is enabled, must also pass review and context-binding checks. The interpretation protocol is model guidance, not a guarantee that every model follows it; actual answers require separate acceptance evaluation.

The current core vocabulary covers activities, time periods, emotions, and task states; dynamic concept governance is not implemented. Older free-key conditions remain retrievable but have unknown applicability. Validity explicitly distinguishes unknown from unbounded time; an unspecified time does not mean forever. The pure temporal core supports source- and timezone-aware interpretation of “today,” but the current host contract supplies no trusted local timezone. Semantic writes therefore require relative dates to remain unknown and reject model-supplied date anchors. Restoring situation state also requires a matching completed server-side job and its sources; a client-provided “verified” label is insufficient.

Every claim in a new cognitive proposal must explicitly provide `valid_during`, including `{"kind":"unknown"}` when validity is entirely unknown. The verifier checks the same temporal structure that is ultimately stored. The older nullable `valid_time` cannot substitute for it, and `end=null` cannot automatically imply an unbounded interval. Older persistent records and frozen parent snapshots remain conservatively readable without fabricated validity periods. Custom providers must follow the updated response schema.

New semantic proposals no longer accept legacy `eq/ne` free-key conditions. A concept that cannot map to a registered definition retains its original label through an unresolved expression such as `{"op":"unresolved","label":"silver state"}` and continues to evaluate as unknown within compound conditions. A label that happens to match a known term still cannot activate the condition. Older records remain readable without automatic migration or guessed meanings.

Situation plans and call budgets are durable, and recovery reuses existing results. Model requests do not hold database transactions open. If sources are corrected or deleted during a request, the old result cannot be published or reinjected. Failed situation processing does not write unverified content into working state. Cognition configuration is independent of the earlier `RecallController`: cognition handles understanding and learning, while the controller handles multi-step recall strategy. Both require explicit configuration and consume the configured API allowance.

By default, each semantic job can make at most two model calls with 196,608 cumulative input bytes. Server-side cognition and vector jobs share a one-hour rolling budget per brain, capped at 100 external calls and 2,000,000 input bytes. Reserved calls with failed or uncertain outcomes still count. When the brain-level budget is exhausted, the turn continues with an ordinary answer without writing unchecked situation inferences. These budgets exclude the host's main model and the independent `RecallController`.

Fresh background batches declare `encoding-integration-v1`: encoding has at most two calls and 196,608 input bytes; one historical integration pair has the same limit. Both phases share an immutable ceiling of four calls and 393,216 bytes, plus the unchanged per-brain rolling limits. Complete-history encoding usually needs only its existing pair. The extra pair can fund a complete older unpaid comparison; it cannot automatically retry a failed or uncertain paid attempt. Restarting, rechecking capacity, or increasing the brain budget does not replenish a batch allocation.

When optional history is incomplete, fresh encoding switches to `local-observation-v1` before any model call. Only the complete new inputs enter this request. It can retain attributed reports and provisional local episode fragments, without declaring a current rule, historical winner, or external completion. Local assessment and a durable historical integration obligation commit together. Ready history packages run automatically; unavailable evidence or capacity leaves a visible deferred obligation. New independent inputs, source changes, and renewed rolling capacity can reconsider unpaid work. Already attempted failures remain explicit recovery cases.

Historical integration freezes a finite comparison manifest, including complete source, parent, exception, and relation closures. The verifier receives the complete context once and resolves exact references into it; selective citations cannot hide uncited qualifications. Discovery and dependency limits remain explicit. Known context is preflighted, but an unexpectedly large generated proposal can still exceed the verification budget. Unseen candidates beyond a bounded scan remain unresolved. See [the learning contract](../architecture/local-observation-consolidation.md) and [phase monitoring](../architecture/learning-monitoring.md); deterministic checks do not replace sustained real-model acceptance.

Use `agent-memory desktop budget` to inspect the effective policy and current reservations. `desktop configure-budget` changes it with a stable request ID and the revision just read; the authenticated HTTP API and both SDKs expose the same operation. Explicit configuration can raise or lower the brain allowance without resetting usage or increasing existing job limits. Zero calls stops new cognitive and embedding reservations. See [cognitive budget configuration](../architecture/cognitive-budgets.md) for commands, bounds, and lost-response handling.

Background learning uses persistent input-sequence windows. Jobs from the same conversation and branch that have not started may merge while retaining the earliest processing position. Each batch reads up to eight new inputs in order, shrinks for long messages to fit the request-byte budget, and adds relevant history. An individually oversized record remains pending. Successful processing saves progress and creates continuation jobs for the remaining window in the same transaction. Retries freeze the original batch's source, belief, episode, and relation versions. A fresh encoding allocation that has never reserved a provider call can rebuild a stale window through a validated successor under the same allocation. Paid failures and uncertain results cannot gain another allocation through automatic source recovery. Older jobs retain their original bounded retry behavior. A job's cumulative budget never resets, and new jobs are also constrained by the brain's cumulative budget. Invalid results, oversized fixed requests, or insufficient budget do not advance progress. Completed processing does not necessarily produce a new belief.

Material selection defaults to at most 32 sources, 32 beliefs, and 16 episodes, fetching bounded lexical candidate sets. Database filtering and sorting can still inspect more rows than the returned candidate limit. Additional material must also fit the actual request-byte budget, with complete dependency bundles selected or excluded together. Redundant literal-capture assertions are omitted when the full original text is present, the assertion can be reconstructed exactly, and there are no additional relations or episode structures. Missing complete sources, parent dependencies, counterexamples, or change relations blocks publication from the incomplete historical batch. A fresh encoding job may instead take the separate local-only path described above before generation; older frozen jobs do not acquire that permission. The job result's `sampling` records coverage limits. `learning_outcome` distinguishes no proposals, no accepted proposals, accepted proposals, and not yet evaluated. Accepted proposals do not imply published long-term memory; see `publication` for published content. Continuous generalization whose source chains exceed the budget still needs further design.

The same proposal-and-verification cycle can now establish supported belief relations: bounded generalization across distinct instances (`abstracts`), equivalent formulations (`equivalent`), temporary exceptions (`exception`), changes beginning at an explicit time (`supersedes`), and unresolved contradictions (`contradicts`). Relations must bind exact old and new belief versions, evidence, conditions, and validity, and independently verify the complete relation. Generalization requires at least two instances with non-overlapping sources and may describe only the scope they support. Equivalence requires matching semantic qualifications and cannot erase negation or unknown time. A relation cannot publish if its new belief is not accepted. These judgments remain model inferences.

Background processing also has an explicit comparison plan. It looks among selected beliefs for instances with compatible source subjects, worlds, and conditions, and non-overlapping supporting events, choosing at most three comparison targets per batch. New plans select up to two strong lexical-cue targets and one weak-cue exploration target; without a weak candidate, they may select three strong targets. Weak candidates still require an intersection of content words and are deduplicated before ranking. Zero-overlap candidates do not enter exploration. New v4 plans first cover different complete supporting-source pairs within each slot class, then prefer sources unused by earlier targets. This prevents several fragments from one pair of events from occupying every slot. The ranking change keeps v3 objective identities, so previously assessed pairs do not receive new paid work merely because of the upgrade. Lexical similarity only selects candidates; the model still proposes and verifies their shared meaning. For each target, the model must propose a bounded generalization and relations or explicitly decide to leave the beliefs unchanged. Merely recording that “the user recalled the past” does not complete generalization. Targets are frozen in the job snapshot. The v3 comparison strategy requires each generalization to express one minimal shared proposition, preserving instance differences in the original records; missing information cannot prove sameness or difference. The runtime maps citations for the shared proposition to each parent instance. The verification model must separately confirm each instance and the complete proposition and relation. Missing, duplicate, or unknown per-instance judgments prevent publication. The shared proposition may cite only the relevant complete fragments of each instance, while the abstraction relation retains all endpoints' complete sources; ordinary parent-dependency rules remain strict. The same instance-version combination, once validly verified or explicitly left unchanged, is not replanned merely because of a new reminder. A new instance, version, or comparison strategy can create a new target. Older frozen v1/v2/v3 plans remain compatible, and recorded failures are not deleted. Network errors, missing verification, and invalid proposals remain subject to the versioned recovery rules below; they do not authorize automatic repetition of paid phase attempts. Foreground situation understanding does not automatically take on consolidation targets. No extra model rounds were added, and the original two-call budget still applies. Abstraction verification additionally distinguishes irrelevant from unknown evidence: a citation may leave the shared proposition's support set only when verification explicitly marks it `irrelevant` and it carries no qualification or counterevidence for the conclusion. Verification must supply exact support indices; every instance must retain supporting evidence, and the complete proposition and relation must pass. Text, time, conditions, world, and parent versions remain unchanged. Complete parent citations remain in the relation. Structured verification and source selection enter the job audit, while ordinary monitoring exposes only fixed reasons and counts. This filtering path is not available to ordinary memories.

Belief changes are stored in `memory_belief_relations`, leaving earlier beliefs and past episodes intact. Recall follows known relations to include endpoints and complete sources even without lexical overlap between old and new statements. `belief_resolution` reports whether the original rule remains unchanged, is durably superseded, has a current exception, is conflicting, or cannot be resolved. An old rule is prevented from acting as the current rule only when both the relation and the new belief are explicitly applicable. The old rule is reassessed after an exception expires. Missing endpoints, evidence, or budget preserve uncertainty rather than leaving only the old rule. Related groups share the context budget, and persistent recall plans freeze displayed relation versions and sources. New relations invalidate old plans.

Deleting or correcting a relation's sources revokes the corresponding active relation. Erasure also removes relevant content from archives held by the server. Earlier facts supported by independent evidence are not revoked in reverse. Trusted local archives can restore relations after verification; relations from uploaded archives remain quarantined. Restoring an older archive does not automatically incorporate ordinary corrections made in the original brain after export, while deletion records continue to constrain restoration. Relation traversal is capped at 32 edges, 128 belief versions, 256 sources, and eight levels of parent dependencies. Reaching a cap explicitly reports incomplete coverage, not a complete account of all changes.

After episode publication, the runtime automatically organizes shared content and instance differences without additional model calls. `patterns.py` compares assertions' complete semantic fields: text, conditions, time, actual or hypothetical world, verification state, and more must match exactly, and the assertions must come from non-overlapping episodes, before they can share one definition. Each episode retains its own sources, observation order, and differences. Unobserved content cannot be filled in from a shared definition. This exact sharing is separate from model-proposed `abstracts` relations; those relations do not authorize compressing details with different meanings or replacing the original instance text.

New episode proposals must provide an `organization_statement` explaining why the interaction records form a grouping or boundary change. Verification includes the old episode, complete sources for old and new members, each source's role, and the resulting grouping. Missing older sources, version mismatches, or exceeding the 32-source limit cause rejection rather than truncation followed by a claim of complete verification. The organization statement explains the whole state transition; it does not prove that an external event mentioned in the conversation happened. Older episode records may lack this field.

Archives retain the current episode's organization information and complete source dependencies, remapping source versions during restoration. `organization_source_version` records only the episode identity before the transition and cannot serve as independent evidence; the archive does not guarantee inclusion of that older organization snapshot. It is not misinterpreted as a source-event version. Older formats remain compatible, and externally uploaded episodes remain quarantined initially.

Shared definitions and bindings are stored in the separate derived table `memory_pattern_views`, while facts remain fully stored. Construction is capped at 64 beliefs, 16 episodes, and 256 complete sources. Missing exact parent versions or sources skip organization; the original facts remain usable under their original rules. Corrections, deletions, or episode revisions clear affected views. A view does not become an evidentiary dependency for independent facts. Exported archives omit this cache. After restoration from a trusted local archive, it is rebuilt within bounds from verified original facts and episodes; uploaded archives remain quarantined.

Ordinary `prepare` attempts to present repeated meaning once only when it has selected all facts in a view and reverified their sources. It separately includes each episode's bindings, differences, and applicability. If rendering is not actually shorter, it uses the complete facts. The entire selected group shares the context budget so compression cannot independently discard differences. Coverage always refers to the selected assertions and does not establish that all episodes or exceptions were found. Paths with a persistent `recall_plan` continue to use complete facts because plans do not yet freeze organization-view versions. Explicit cues still use existing retrieval to find supported details of the corresponding episodes.

Semantic embeddings are a separate optional configuration for environment-driven deployments, including the development setup above. Desktop profiles do not currently expose embedding configuration; setting these variables does not enable embeddings in the managed desktop service. In an environment-driven deployment, the service and worker must use the same model, version identifier, and actual output dimensions:

```dotenv
MEMORY_EMBEDDING_BASE_URL=https://your-provider.example/v1
MEMORY_EMBEDDING_API_KEY=<your API key>
MEMORY_EMBEDDING_MODEL=<your embedding model>
MEMORY_EMBEDDING_MODEL_VERSION=<your stable model revision>
MEMORY_EMBEDDING_DIMENSIONS=1024
```

`1024` is only an example. Replace it with the selected model's actual dimensions, within 1–8192. Background processing creates vectors for newly published memories. Queries call the embedding provider only when their request budget explicitly sets `max_embedding_calls > 0`. The default is zero; environment variables alone do not enable embedding calls on every query. For example:

```python
from agent_memory.client import default_budget

budget = {**default_budget(16000), "max_embedding_calls": 1}
result = memory.recall(
    "Which scents did I used to prefer?",
    conversation_id=conversation_id, branch_id="main", budget=budget,
)
```

Lexical retrieval combines the original feature-hash route with a bounded PostgreSQL word-match route for normalized ASCII query terms. Each route retains at most 512 candidates for recall or 256 for learning, often fewer under the request budget; each query reads one extra row to detect truncation. The combined pool is deduplicated and ranked by actual lexical overlap. The source bridge below remains a separate bounded route, and final source, dependency, and context budgets are unchanged. This reduces hash-collision misses without making recall exhaustive or database work constant-time.

Candidates can come from lexical retrieval, configured semantic vectors, episode structure, and association paths, then undergo common source and applicability checks. After a lexical hit on a retained original source, retrieval also follows supporting evidence to the corresponding semantic belief. For example, even if the source is Chinese and its generalization is English, a query matching a valid supporting fragment can still find that belief. The bridge checks exact source versions, content hashes, scope, branch, and deletion status; counterexamples or background citations cannot masquerade as support. Complete dependencies and current applicability are still checked afterward. The source bridge reads at most 64 source events and retains at most 512 linked beliefs for recall or 256 for learning, with actual budgets potentially lower and one extra candidate row used to detect truncation. Reaching a limit reports restricted coverage. This does not establish arbitrary cross-language understanding or recover every paraphrase from sources with no lexical match. Representations use different access-decay policies: details move into deeper tiers sooner, while generalizations and key beliefs remain accessible longer, and explicit cues may still recover details. Decay parameters are adjustable engineering policies, not yet calibrated against long-term conversation and not biological laws of human forgetting. Access decay does not automatically erase original sources; permanent deletion still uses `forget`.

Internal retrieval hits and model claims that a memory was helpful do not increase external-use counts. The runtime now automatically considers a following original user message containing a complete explicit confirmation or correction, such as “correct” or “wrong.” It can affect access only after an actual delivered answer containing one unique exact selected statement or quotation, with the current memory version and all supporting sources intact. The original interpretation hint is preserved; no additional model request or belief promotion occurs. Paraphrases, added questions or claims, ambiguous identical statements, imported feedback, and tool observations without outcome linkage remain trace-only. A recalled literal tail without a published assertion also remains trace-only.

The prepared turn durably binds the final server-issued packet, exact context hash, selected versions, and source hashes. Later validation prepares or request-cache eviction cannot replace that binding. Custom contexts explicitly opt out, preventing both automatic credit and manual legacy fallback. Older turns without a binding retain their conservative manual checks, including rejection of ambiguous preparation attempts, and receive no automatic credit. State transitions and event sequence establish delivery order without requiring application and database wall clocks to agree.

Each attributable feedback event/version contributes once to a separate provenance ledger. Correction or erasure invalidates affected contributions and rebuilds access from surviving evidence. Processing an older feedback event does not make it recent. Negative outcomes lower utility without increasing activation or refreshing reinforcement time. Migration 014 clears older access aggregates because they cannot establish exact delivered-use provenance; original sources and memories remain. These metrics describe utility, not truth probabilities, and do not promote an assistant's earlier statements into facts about the world.

The [first-release foundations report](../evaluations/v1-foundations-2026-10-25.md) records upgrade, feedback-provenance, semantic-outcome, provider-boundary, and interrupted-call verification. It is not the final first-release acceptance.

The fresh [October 25 lifecycle smoke](../evaluations/deepseek-2026-10-25-lifecycle-smoke.md) completed all five short scenarios with source-consistent delivered answers, known usage for 24 calls, and a correctly resolved preference change. It retains a minor unsupported detail in one stored episode and substantial `always`-mode latency. Sustained-history and first-release acceptance remain separate gates.

## Development monitoring interfaces

These endpoints use the same Bearer authentication as the memory service:

| GET path (prefix `/v1/brains/{brain_id}/runtime`) | Content |
| --- | --- |
| `/status` | Persistence and index progress, learning progress, legacy non-completed job counts, turn phases, active lease counts, and reserved scope counts |
| `/learning-progress` | Brain-wide learning windows, runnable/waiting/running/blocked counts, and the oldest gaps |
| `/cognitive-usage` | Retained cognitive and embedding reservations, per-phase outcomes, provider-reported tokens, and unknown-usage counts |
| `/cognitive-budget` | Effective policy, revision, used allowance, and remaining allowance |
| `/scope?conversation_id=...&branch_id=...` | Reserved turn, or oldest unfinished legacy turn, for the exact scope; `turn: null` when empty |
| `/turns?limit=100` | Recent turns, up to 200 |
| `/turns/{turn_id}` | Current phase, revision, source versions, context usage, recall plan, cumulative call budget, and recovery state |
| `/turns/{turn_id}/history?limit=100` | Ordered phase history, up to 200 entries |
| `/turns/{turn_id}/sources` | Separately authorized expansion of currently valid inputs and outputs |
| `/jobs?limit=100` | Recent background and cognition jobs, up to 200 |
| `/jobs/{job_id}` | Job type, status, attempts, cumulative call and input budgets, error category, and safe learning summary |

Use `RuntimeClient(memory).active_scope(conversation_id, branch_id="main")` in Python or `memory.activeScope(conversationId, branchId)` in TypeScript to find the turn holding up a conversation. This lookup is independent of the recent-turn list, reads metadata only, and does not claim or cancel the turn. A legacy queued turn can have `scope_reserved=false` until claimed after the previous owner finishes.

`recall_plan` contains selected memory versions, observed source versions, stop reasons, search counts, elapsed time, coverage limits, and a structured decision trace. The trace stores actions, query hashes, and candidate counts without copying query text or model explanations. `recall_model_calls` and `recall_input_bytes` provide cumulative budget records across recovery. `orientation_job_id` links to the turn's situation job. Job endpoints do not return job bodies, raw model results, or lease credentials. The service enforces call-count and input-byte limits but does not guarantee a monetary cap.

`RuntimeClient(memory).cognitive_usage()` in Python and `memory.cognitiveUsage()` in TypeScript read the durable cognitive and embedding operation ledger. Proposal and verification calls settle independently: a later verification failure does not turn an earlier successful provider response into an uncertain call. `completed` here means the provider returned a response, not that its proposals passed semantic verification. Valid provider-reported input, output, and total tokens are retained even when the response is unusable. Reservations that never reached the provider, interrupted calls, older records, and custom providers without valid `last_usage` and a fresh per-thread `last_call_id` remain unknown. Token totals sum only known records; `counts_complete=false` prevents treating missing usage as free. This endpoint covers semantic orientation, background learning, and embedding jobs, not host generation, answer review, recall-controller calls, or an entire account's bill. Embedding input/total counters must agree; their output-token count is zero by the embedding protocol. Shared synchronous provider adapters keep their most recent call metadata separately per thread. No prices or monetary totals are inferred.

New cognitive snapshots freeze `no_unsettled_retry-v1`: a lost settlement leaves its reservation charged, and reclaim closes that job as `provider_uncertain` instead of sending the unchanged request again. This also covers a process crash or database outage after the provider returned. Explicit learning recovery can authorize a new bounded child while preserving the old accounting. Legacy snapshots retain their earlier bounded-retry contract, which may permit another attempt after a separately settled uncertain result if enough authorized budget remains. New `encoding-integration-v1` phases do not automatically repeat paid failed or uncertain attempts. Source changes can authorize fresh source-repair work, but cannot reset earlier reservations or replay an attempted phase under a new allocation.

Monitoring records do not copy content bodies or store lease credentials or hidden reasoning. Source corrections remove dependent outputs from valid recall. Erasing a source recursively erases derived outputs and filters exported archives held by the server. Archives retain event dependencies so deletion constraints remain enforceable after restoration. Active leases and unfinished turns do not migrate with a memory archive.

The `learning_summary` in cognition job lists and details returns only processing state, proposal outcome, accepted and published counts, fixed sampling limits, and rejection-reason counts. For example, a `completed` job with `learning_outcome=no_proposals` has finished processing without proposing anything. Nonzero `accepted_counts` also does not establish that long-term memory was written; check `published_counts`. `published_counts.pattern_views` counts organization views available in this run, and `belief_relations` counts published belief relations. Neither is a count of objective facts or cumulative learning events. `learning_window` shows the input interval, attempted and successfully processed positions, and whether an interval remains pending. `pending_sequence_span` is a sequence span, not a message count. Summaries exclude original text, model explanations, and source IDs. Pending intervals remain independently visible even when the original cognitive result has been cleared because its sources became invalid.

`learning_summary.consolidation` separately counts comparison targets, attempts, verified results, explicit no-change decisions, unresolved targets, and deduplications, exposing only fixed reason enums. `no_change` is the model's decision on this attempt, not proof that there is no shared meaning. `satisfied` requires a verified abstraction relation, not merely a completed job or model self-report.

`learning_progress` aggregates all background input windows, independently of the most recent 200 jobs. It reports provider configuration separately from processing state, counts remaining windows without double-counting valid continuation or recovery handoffs, and includes up to eight oldest blocked examples with job IDs and fences. Invalid or missing handoffs are reported as incomplete coverage. These are processing diagnostics, not message counts or a measure of what the model understands. The existing `pending_job_count` also includes historical cancelled and superseded jobs; use `learning_progress` to find actionable work. Indexing can be current while semantic learning still has a gap.

Its separate `semantic_outcomes` aggregate reports retained latest job results, evaluated and unevaluated jobs, accepted and published counts, and fixed rejection reasons. An evaluated window may have rejected every proposal; it has no successful publication merely because processing coverage advanced. These are historical job outcomes, not every attempt, unique source counts, or currently valid memories. Source erasure and invalidated, missing, or malformed results produce unknown counts and `semantic_outcomes.counts_complete=false`; they do not become trustworthy zero-success results. At most eight examples are returned, without source text or model explanations.

After fixing the cause of a blocked window, schedule recovery with `POST /v1/brains/{brain_id}/runtime/jobs/{job_id}/retry-learning` and `{"request_id":"recover-1","expected_fence":1}`. The fence must match the blocked job. Reusing the same request ID and fence returns the same recovery job; a different authorization for the same parent conflicts. The response is the recovery job's current public metadata. The service must have cognition configured. This operation queues work without making a model request itself. Ordinary queued, running, deferred, or automatically retryable jobs cannot be manually duplicated.

Recovery follows the failed job's learning contract. For legacy jobs without phase metadata, it reads current valid sources over the unprocessed part of the original interval. Each recovery batch has at most two model calls and 196,608 input bytes. Successful batches may continue across that remaining interval with the same per-batch limits; two calls is **not** the total authorization for a multi-batch legacy window. Later messages cannot expand the recovery interval.

For `encoding-integration-v1` jobs, recovery preserves the failed phase and its original frozen source and comparison plan. It grants a separate allocation capped at two model calls and 196,608 input bytes, rather than a fresh four-call encoding-and-history allocation. The recovery freezes the current service's cognitive policy once before its first call; restarting cannot refresh it again. If correction or deletion invalidates the frozen plan, recovery cannot silently substitute newer sources. Successful encoding batches may continue over the untouched original tail with separately bounded allocations; they do not repeat a failed attempted prefix. A completed phase cannot be manually retried.

Both contracts preserve the parent's result, call counts, input-byte reservations, and uncertain outcomes, and remain subject to the brain's rolling budget. A parent that already delegated an encoding or history tail cannot be explicitly retried; recovery of only its failed selected portion is not implemented. Provider failures requiring another paid attempt need a new explicit recovery authorization where eligible. Queuing recovery does not establish successful learning or publication. See the [source-change and recovery rules](../architecture/local-observation-consolidation.md#source-changes-recovery-and-compatibility) for the phase-specific boundaries.

For the desktop service, these commands use the saved profile and require the service to be running. They do not start a worker implicitly:

```sh
agent-memory desktop learning-status
agent-memory desktop learning-status --job JOB_ID
agent-memory desktop retry-learning JOB_ID --expected-fence 1 --request-id recover-1
```

Use the job ID and fence returned by monitoring. If the recovery response is lost, reuse the exact request ID and fence. Python exposes `RuntimeClient(memory).learning_progress()`, `.job(job_id)`, and `.retry_learning(job_id, request_id="recover-1", expected_fence=1)`; TypeScript exposes `memory.learningProgress()` and `memory.retryLearning(jobId, {requestId: "recover-1", expectedFence: 1})`. Neither SDK automatically retries this write.

New cognitive jobs use `source-composition-v3`. Assistant speech claims retain exact selected source quotations with embedded speakers unresolved; independently generated short views are omitted for these speech claims. Mixed episodes quote complete member contributions or use a neutral membership view if they exceed the statement limit. Publication recomputes these source-rendered views. User/operator reports keep ordinary semantic encoding and optional verified short expressions. This does not resolve every narrator, prove a drafted commitment happened, rewrite old memories, or change the retrieval/context ceilings. See [source composition](../architecture/source-composition.md) for the representation tradeoffs.

## Current low-level interfaces

The Python client is in `agent_memory.client`, with an underlying workflow example in [examples/connect.py](../examples/connect.py). The TypeScript client is in [sdk/typescript](../sdk/typescript). The following HTTP contract is for adapters to orchestrate; it is not the target product's required step-by-step workflow for users or the main agent:

1. `observe` persists actually received or delivered messages with stable source IDs.
2. `prepare` reads the current situation, working state, and relevant memories.
3. `prepared_context` / `preparedContext` produces the material actually sent to the model.
4. Use `recall` and `inspect` if the model needs further cues.
5. Only after actual delivery, `observe` the output and `commit` the turn's state.
6. Use `checkpoint` before host compaction or interruption.

Do not inject the entire `ContextPacket` or `RecallResult` control envelope into the model. `prepared_context` / `preparedContext` and `recalled_context` / `recalledContext` produce `compact-memory-v1`: material appears directly as JSON objects, sources and relations are each presented once, and `memory_versions` maps short references to exact memory IDs and versions usable with `inspect`. MCP's `memory_recall` also returns this compact `context` rather than the complete HTTP envelope. Use `inspect` when complete records are needed.

The server's `model_context` contains the authoritative text assembled within the budget. The TypeScript SDK prefers this text unchanged, preventing scientific notation, large integers, or other cross-language parsing differences from changing text, values, or context length. Compatibility with older servers still permits reconstruction from fields, but reconstruction that exceeds the server's measurement is explicitly rejected rather than silently overflowing the budget. To change material, request a new server-side assembly instead of modifying envelope fields while retaining stale authoritative text.

`RecallResult` may also contain `source_passages`: bounded complete original user/operator messages reached through visible memory anchors. The compact context exposes their actor, delivery, world, recording time and source references; prepared context uses `verbatim_evidence` blocks. These are original communications, not instructions or independent proof of world facts. Source bytes and hashes remain unchanged. Explicit summary, selected-memory and valid-time reads do not expand to whole messages; source integrity, permission, cutoff, correction and context-budget guards still apply. See [source composition](../architecture/source-composition.md) for limits and fallback behavior. Full memory records remain inspectable in `items`, including literal captures and plain episodic reports whose duplicate wrappers are omitted from model context. Version 2 preserves explicit occurrence-time and other qualifying metadata as separate records. Candidate admission shares quota between eligible versions of the same exact source unit, while retaining a hard maximum of 128 versions and the existing dependency limits.

The default `prepare` budget of 2,000 units is the memory allocation left after the host accounts for system instructions, current input, and reserved output; `recall` defaults to 16,000. Both conservatively measure the UTF-8 bytes of the actual model material described above. They are not exact tokenizer counts or byte limits for the entire HTTP response. HTTP still exposes full records and diagnostics. Related belief groups enter or leave the budget together, with coverage limits returned when a group cannot fit. Literal source memories are labeled `historical_report_not_current_rule`; they cannot bypass temporal, exception, and conflict checks on semantic beliefs. Detailed `inspect` output continues to be metered separately.

`POST /v1/actions` accepts the [JSON Schema contract](../architecture/memory-contract.schema.json). Identity is bound to the API key. Request parameters can select only an authorized brain and cannot set a tenant or authenticated identity. The running service's capability declaration is available at `GET /v1/capabilities`.

### MCP

MCP uses stdio through the official Python SDK 1.x, with its version pinned by `uv.lock`. Desktop installations should use the secret-free configuration generated by `agent-memory desktop mcp-config`. The following is a manual client configuration for development deployments:

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

MCP lets a model explicitly recall memories and submit notes with source labels. It does not intercept host turns, automatically read the host's full conversation, determine delivery, or take over compaction. Configuring a server-side cognition model does not change this boundary. Notes submitted through MCP are always marked as assistant-sourced and cannot impersonate user statements. Fully automatic capture and pre-answer recall require the host to integrate lifecycle interfaces.

## Current implementation boundaries

| Area | Current implementation |
| --- | --- |
| Persistence | PostgreSQL source events, source revisions, idempotent submission, and transactional job enqueueing |
| Working state | Branch state, revision checks, compaction handoff, and optional situation inferences merged after actual delivery |
| Recall | Optional model-driven cue rewriting and candidate selection, multi-route candidate fusion, applicability conditions, source rechecks, persistent plans, and budgets |
| Consolidation | Literal capture; optional semantic extraction, episode boundaries, cross-source beliefs, and procedural candidates with inference labels and version dependencies |
| Shared content and differences | Exact semantic sharing, independent episode bindings, supported bounded generalization, and context budgets applied to complete groups |
| Belief change and continuous learning | Conditional exceptions, supersession, equivalence, and contradiction; persistent learning windows, frozen snapshots, and budgeted continuation |
| Access dynamics | Representation- and time-dependent access tiers; trusted external feedback updates use counts and usefulness assessments |
| Correction and deletion | Version updates, synchronous invalidation, derived-dependency cleanup, and deletion-record checks during archive restoration |
| Isolation | Server-bound identity, application query filtering, and forced PostgreSQL row-level security |
| Integration | Python automatic synchronous text runtime; low-level HTTP, Python, TypeScript, and MCP operations |
| Desktop operation | Stable local profile, brain, and credentials; automatic database/API/worker startup; automatic MCP and SDK connections; data retained on shutdown |
| Development aids | Authenticated status, turn and phase logs, cognition jobs, recall stop reasons and budgets, separate source expansion, and a removable local workbench |

Condition-triggered proactive reminders, calibrated decay and association propagation, multimodal understanding, complete tool loops and streaming host adapters, cloud accounts, and shared permissions still need implementation and validation. Procedural candidates are only sourced steps and conditions; they do not execute themselves or prove task success. Only owner-private scope is currently supported; shared-collection requests are explicitly rejected. Without configured semantic embeddings, existing lexical feature hashes are not treated as semantic vectors. With embeddings configured, vectors are used only when model, version, dimensions, and content hash match.

A source-backed original statement does not establish independent verification of its claims about the world. Default recall returns supported candidates and uses `partial` to indicate that semantic sufficiency has not been assessed. Semantic judgments by the optional controller and cognition processor can still be wrong, and multi-route search cannot guarantee complete retrieval of relevant history. Quoting a substring from an original source does not establish that it can stand alone as a new conclusion.

A [10,000-record synthetic retrieval diagnostic](../evaluations/retrieval-scale-2026-10-25.md) improved the same 20 labeled-cue queries from 18/20 to 20/20 hits after the candidate and scope-projection changes. Measured median recall time changed from 487.009 ms to 92.3195 ms and p95 from 503.284 ms to 123.173 ms. These are local in-process measurements without HTTP or model latency; the repaired replay reused a warm fixture, and concurrent host load was uncontrolled. The report preserves exact data, the excluded invalid-boundary attempt, and the limits of this comparison.

Only direct statements that preserve original meaning and source attribution can enter `grounded` working state. The semantic processor's current-situation output uses `inferred` status and a validity period; the current situation cannot become historical evidence in reverse. The generic submission interface for unresolved items currently accepts only `unknown` status. A cited note cannot simply declare an item completed.

Archive import/export is separate from the implemented desktop logical backup and fresh-install recovery. The latter uses an independent current deletion authority, operational quarantine, and explicit activation, with real Docker and installed-package checks. Physical database backups, migration at scale, operational SLAs, and long-term conversation quality require separate deployment and testing. Stopping and restarting the service does not delete committed records.

An archive is currently limited to 16 MB and 5,000 records of each type. Exceeding either limit produces an explicit error, not a silently truncated backup. Sharded export and large-scale restoration remain unimplemented.

## Project structure

- `src/agent_memory/cognition.py`: episode capture, cue-driven recall, evidence boundaries, and workspace assembly.
- `src/agent_memory/runtime.py`, `turns.py`: automatic text lifecycle, persistent phases, and call budgets.
- `src/agent_memory/recall_control.py`, `recall_provider.py`: semantic recall decisions and the optional model adapter.
- `src/agent_memory/semantic.py`, `learning.py`, `consolidation.py`: semantic proposals and checks, current situation, and background cognitive publication.
- `src/agent_memory/belief_store.py`, `belief_resolution.py`: belief relations, complete evidence closure, and current applicability.
- `src/agent_memory/learning_windows.py`, `neighborhood.py`: persistent processing windows and selection of complete material within count and byte budgets.
- `src/agent_memory/patterns.py`, `pattern_store.py`: exact binding, validation, persistence, and invalidation of shared semantics and episode differences.
- `src/agent_memory/candidates.py`, `retrieval.py`, `dynamics.py`: candidate fusion, version dependencies, access tiers, and external feedback.
- `src/agent_memory/jobs.py`, `embeddings.py`, `vector_jobs.py`: persistent jobs, call budgets, and optional vector processing.
- `src/agent_memory/service.py`: transactions, versions, lifecycle management, and the ten memory operations.
- `src/agent_memory/storage.py`, `migrations/`: PostgreSQL data model and isolation.
- `src/agent_memory/api.py`, `web/`: authentication, HTTP, and the local workbench.
- `src/agent_memory/client.py`, `mcp_server.py`, `sdk/typescript/`: host integration.
- `src/agent_memory/local_profile.py`, `desktop.py`, `desktop_connection.py`: local configuration, service lifecycle, and automatic connections.
- `src/agent_memory/agent_session.py`, `chat_config.py`, `chat_provider.py`, `desktop_chat.py`: direct model integration, durable local sessions, explicit recovery, and terminal chat.
- `architecture/`: complete goals, machine-readable contracts, and 52 system acceptance scenarios.

## Verification

Documentation and developer-facing interfaces use English; this README also has a Chinese version. Multilingual test fixtures, language-recognition rules, and original evaluation inputs and outputs retain their source language. Translations of quoted evidence are labeled separately and do not replace raw records. See [repository conventions](../AGENTS.md) for the language policy.

Start and migrate the dedicated project database, then run:

```sh
uv run pytest -q
uv run ruff check src tests examples
uv build
```

See `sdk/typescript/package.json` for TypeScript tests and type-check commands. Database-dependent tests are skipped when no database is configured; a skip is not a pass. Tests use newly created random tenants and brains, do not depend on private conversations, and do not call paid models or Codex/Claude subscriptions.

GitHub Actions is manual-only, with no automatic execution on push or pull requests. Day-to-day development prioritizes relevant local checks. Before publishing, validate according to the change's impact and avoid redundant cloud CI runs.

The [standalone chat acceptance report](../evaluations/standalone-chat-2026-10-24.md) records the installed-wheel, real-database, local-provider checks for direct chat, restart recovery, deduplication, and explicit cancellation. Its deterministic provider does not measure semantic learning quality.

The [background learning recovery report](../evaluations/learning-recovery-2026-10-24.md) records blocked-window monitoring, explicit recovery, preserved budgets and uncertainty, and the remaining architecture gaps.

The [cross-host conversation and delivery recovery report](../evaluations/runtime-concurrency-2026-10-25.md) records durable scope ownership, delivery receipts during memory invalidation, upgrade recovery, and the remaining external-delivery uncertainty.

Interface-structure validation, deterministic regression tests, actual MCP protocol round trips, and database tests verify only their covered behavior. They do not establish that all 52 system scenarios in the complete architecture pass. The project also makes no claim of surpassing any baseline in long-term memory quality.

### Manual DeepSeek evaluation

`examples/evaluate_deepseek.py` evaluates synthetic conversations through actual source capture, background learning, database publication, recall, and context preparation using DeepSeek-V4-Pro in thinking mode. It creates isolated test identities and brains without reading existing user brains. The dedicated project PostgreSQL database must be running and migrated. Put the key in `DEEPSEEK_API_KEY` in the git-ignored `.env` file.

```sh
# Explicitly enable paid calls; check one stage first and stop dependent stages on failure.
uv run python -m examples.evaluate_deepseek --live --scenario temporal --stage-limit 1
uv run python -m examples.evaluate_deepseek --live --resume --scenario temporal --stage-limit 3
uv run python -m examples.evaluate_deepseek --live --resume --scenario conditional --stage-limit 3
uv run python -m examples.evaluate_deepseek --live --resume --scenario abstraction --stage-limit 2

# Recheck saved results without connecting to the database or model.
uv run python -m examples.evaluate_deepseek --recheck
```

Reports and call records are saved in the git-ignored `.local/deepseek-evaluation/` by default. The entire directory shares a maximum of 16 calls and 262,144 reserved output tokens. Each stage allows at most two calls, each capped at 16,384 output tokens, with a total stage time limit of 120 seconds. Reservations are persisted before requests, without automatic retries. Recovery retains existing budgets, stages, and job identities. Incomplete reserved calls still count toward the limit and cannot be treated as unspent. Separate output directories have separate ledgers and must be combined when reporting repair comparisons. Failure reports must be retained; deleting call records to bypass budgets is not allowed.

For slower thinking models, explicitly pass `--max-wall-time-ms 240000`, up to 300,000 milliseconds. This does not increase call or output allowances. The execution policy is saved with each stage, and server-side cognition leases cover the allowed request duration plus a small cleanup margin. Execution stops on failure by default. Only after diagnosing the cause and confirming usable prerequisite evidence should you continue dependent stages with `--live --resume --continue-after-failure "diagnostic reason"`. This retains the original failure and diagnostic record, does not rerun completed stages, and does not turn the entire scenario into a pass.

Call records store only safe usage fields, latency, model names, and final JSON content, excluding request bodies, keys, error bodies, and hidden reasoning. Compressed responses are decoded with limits. Incomplete or unparseable responses explicitly record unknown usage. The current metering example uses POSIX file locks and is intended for macOS/Linux development environments.

Saved reports include the [initial evaluation](../evaluations/deepseek-2026-10-24.md) and [compact-context and proactive-generalization follow-up](../evaluations/deepseek-2026-10-24-compact-planning.md). The latter includes zero-call replay with an 8,000-unit budget, 14 requests under shared metering, and failed generalization and cross-language scenarios.

The [source-bridge and per-instance generalization repair report](../evaluations/deepseek-2026-10-24-memory-repairs.md) also retains one failed and one successful independent cognition-processor diagnostic. `examples/evaluate_memory_repairs.py --replay` reuses saved local synthetic data for free replay and freezes the inputs. Only `--live` permits up to two calls. Only after a completed, failed diagnostic can `--live --followup "specific reason"` append a single two-call follow-up to the same ledger. The original report is unchanged, the entire ledger is capped at four calls, and `--resume` only reads reports. An independent processor test is not end-to-end real-model acceptance of database publication and host answers.

Acceptance checks fixed sources, relation direction, conditions, validity, and actual recall/context output. Temporal tests inject a domain clock; this does not establish an implemented host historical-situation API. Content keywords are only supplementary guardrails, and generated propositions still need human review. Small-sample diagnostics are not long-term quality benchmarks, and main-chat-model answers are not used as substitutes for memory-quality evaluation.

### Complete synchronous conversation evaluation

`examples/evaluate_conversation.py` connects an actual DeepSeek main model to `BrainRuntime` and runs input capture, situation understanding, context preparation, real generation, and delivery confirmation through the local TCP HTTP API and PostgreSQL. Delivery targets a persistent local test inbox. Each turn reconstructs the service, client, and runtime, switches conversations, and disables recent-conversation supplementation. Fixed synthetic preferences test initial learning, cross-conversation recall, changed preferences, and unsupported questions, alongside an independent empty-brain control. No hidden conversation history or reference answers are injected, and no actual chat channel is connected.

```sh
uv run python -m examples.evaluate_conversation --plan
uv run python -m examples.evaluate_conversation --live
uv run python -m examples.evaluate_conversation --resume
```

Only `--live` reads local keys and makes paid calls. The shared ledger is capped at 20 calls and 249,856 reserved output tokens. Each of five turns allows up to two situation-processing calls and one main-model call. Only the two turns introducing new information each execute a consolidation job, giving a planned maximum of 19 requests. Main-model output is capped at 4,096 tokens per call and cognition output at 16,384 tokens. Model timeouts are not retried automatically. An existing output directory rejects another paid run; `--resume` only reads the report, and interruption does not authorize automatic resubmission.

Reports separately record the persistent semantic versions actually supplied to the main model, prior supporting user sources, final answers, actual deliveries, task completeness, and usage. Passing pipeline checks does not establish that an answer is semantically correct; natural-language answers require separate review against criteria fixed in advance. Probe messages also create background jobs normally, but this run's budget does not execute those pending windows. This test covers a small synchronous-text sample, not long-term stability, tool loops, streaming, OS-process crash recovery, or complete integration of arbitrary agents.

The [complete synchronous conversation diagnostic](../evaluations/deepseek-2026-10-24-conversation.md) records original stages, partial failures, and later targeted follow-ups separately. An answer that agrees with its sources does not necessarily respect the runtime's uncertainty labels.

The missing-validity issue exposed by that baseline has a single targeted diagnostic available through `uv run python -m examples.evaluate_temporal_repair --live`. It requires an exact match to the original local five-stage report, fixed source versions, and ledger with 18 confirmed calls. It reprocesses only the original user text from initial learning, adding at most two calls of 8,192 output tokens each within the original totals of 20 calls and 249,856 reserved output tokens. Results are saved separately as `temporal-followup.json`, without database publication, host-answer generation, or rewriting the original failures. Once the file exists, another execution is rejected. `--resume` only reads the targeted result.

Repairs to current-request handling, episode organization, and the memory interpretation protocol use a separate experiment entry point:

```sh
uv run python -m examples.evaluate_interaction_repair --plan
uv run python -m examples.evaluate_interaction_repair --live
uv run python -m examples.evaluate_interaction_repair --resume
```

This experiment reruns the same five-turn real-model chain and adds an independent host diagnostic. It freezes the complete context with unknown applicability from the earlier published report, inserts only the new general memory interpretation protocol, and checks whether the answer turns uncertainty into certainty. The model does not receive old answers or grading criteria. The diagnostic neither publishes memory nor delivers to a user and does not contribute to learning in the new five-turn run. Both parts share a maximum of 20 calls and 253,952 reserved output tokens. Input and code fingerprints are persisted before the first request. Unknown usage blocks all subsequent paid calls. An existing default directory, `.local/deepseek-interaction-repair/`, rejects reruns, and `--resume` is read-only. Machine checks and semantic answer reviews are saved separately.

The saved [current-request, episode-organization, and memory-consumption evaluation](../evaluations/deepseek-2026-10-24-interaction-repair.md) reports full passes on 3/5 turns; the original independent diagnostic reached its output limit. The v2 comparison passed 2/3 objectives, while the host still overrode unknown applicability. Engineering regression passes do not make this real-model acceptance pass. The implementation and failure reports were committed together at the user's subsequent request, preserving the original verdicts.

`examples/evaluate_consumption_repair.py --plan / --live / --resume` runs three independent host comparisons for current self-report, established applicability, and unknown applicability. It freezes the original learn/recall/independent-diagnostic inputs and replaces only protocol messages, without sending old answers or grading criteria to the model. Sources must have complete, known usage. The only additional permitted failure state is the strict case above: all first five turns completed and the final diagnostic exactly reached the output limit. Other interruptions are not permitted. The new ledger allows at most three calls and 12,288 reserved output tokens, which must be added to the original experiment's usage. There are no retries, an existing directory rejects reruns, and `--resume` is read-only. Execution completion and semantic success are recorded separately.

Answer review and the division of foreground work are evaluated through:

```sh
uv run python -m examples.evaluate_answer_review --plan
uv run python -m examples.evaluate_answer_review --live
uv run python -m examples.evaluate_memory_lifecycle --plan
uv run python -m examples.evaluate_memory_lifecycle --live
uv run python -m examples.evaluate_memory_lifecycle --orientation-policy on_demand --output .local/lifecycle-on-demand --live
uv run python -m examples.evaluate_memory_lifecycle --profile repair --orientation-policy on_demand --plan
uv run python -m examples.evaluate_memory_lifecycle --profile long --history-turns 104 --plan
```

The first freezes three previously incorrect answers and three normal controls, evaluating only the reviewer. It allows at most six calls and 49,152 reserved output tokens; labels are not sent to the model. The lifecycle v2 runner retains the same five-turn smoke fixture. Each turn allows up to two foreground cognition calls, one answer, and one review. Each of the two learning turns authorizes at most four background calls shared between local encoding and historical integration, with at most two calls per phase: 28 calls and 356,352 reserved output tokens overall. Historical continuation does not add an allocation. The runner checks review binding, durable delivery, current-source encoding coverage, publication, and restart replay, and records exact job/root identities, retained operation reservations, actual model requests, responses, and latency. Historical assessment, publication, deferred work, and unassessed selected objectives remain separate evidence; a completed encoding job does not imply completed historical integration. Both entry points have read-only `--resume` behavior and reject paid execution in an existing output directory. Neither retries paid work automatically.

For the lifecycle runner, create a file named `STOP` directly inside the selected output directory to request an orderly stop. It is checked before setup, between foreground history/probe turns, before each learning job, and around summary updates and probe preparation. An active foreground turn or learning job finishes before the next boundary. At most eight untouched ready historical jobs can run per stage, each within its existing shared allocation; deferred work remains recorded. The report preserves `operator_stop`, actual usage, and closed transports. If the directory contains only `STOP` before startup, the runner writes a stopped report without reading credentials or opening the database or providers. Existing evidence is never overwritten, and `--resume` remains read-only. Failed/cancelled learning, `sampling.publication_restricted=true`, or `learning_window.processing_succeeded=false` stops as `learning_processing_incomplete`; unknown usage, runtime failures, and provider or budget failures take precedence. A valid completed partial result remains nonfatal, with any unresolved historical debt visible for review.

The separate `repair` profile runs exactly the first 16 original turns of the seeded 100-turn dataset, including the earlier stage-003 failure case. It uses only the memory arm, with no held-out probes or baseline comparison, and freezes a maximum of 128 calls and 1,769,472 reserved output tokens. Like the original runner, every fixture uses its own conversation scope. This diagnostic therefore does not test repayment of old history debt by later input in the same conversation. A successful diagnostic is not sustained-history acceptance. Use a new output directory for any explicitly authorized live run; old stopped evidence remains unchanged.

The preserved [continuous-learning repair diagnostic](../evaluations/deepseek-2026-10-25-learning-repair-stopped.md) stopped after eight exchanges. All eight encoded new claims and episodes, repairing the earlier new-input blockage for those inputs, but the eighth historical integration failed on an assistant-based abstraction. The run also exposed excessive comparison debt, two inaccurate episode labels, and a conditional speech-report rejection. It used 37 known calls and 367,241 tokens; sixteen-turn and long-history acceptance remain incomplete.

The saved [answer-review and foreground-work evaluation](../evaluations/deepseek-2026-10-24-answer-review.md) reports that all six fixed regressions behaved as expected. The first chain was blocked on its third turn for ambiguous wording about storage completion; that verdict is retained. In the single follow-up after clarifying only the host acknowledgment rules, all five answers were source-consistent and delivered, but two statement turns were still mislabeled as requests, yielding 3/5 complete-flow passes. Across the runs, there were 44 actual calls and 302,914 tokens, with all usage known. That historical version still required roughly 31–78 seconds of foreground processing. The report preserves original failures and is not a test result for on-demand routing.

The [on-demand analysis and latency evaluation](../evaluations/deepseek-2026-10-24-orientation-routing.md) uses the same five-turn fixture. All five answers were source-consistent and delivered, with 4/5 complete-flow passes. Average foreground model waiting decreased from the historical 56.47 seconds to 20.88 seconds; complete replies in this run averaged 21.68 seconds. This is a historical before/after comparison, not a paired A/B test on the same version. A background proposal that exceeded episode-operation scope was correctly rejected. After the scope contract was completed, an independent two-call processor follow-up using the original complete input passed. The five-turn end-to-end chain was not rerun, and the original verdict remains unchanged. On-demand mode does not immediately derive goals or pending tasks and is not equivalent to full mode.

The `long` runner prepares 100–128 turns of actual input, generation, and delivery (104 by default), then restores six isolated probe brains from the same trusted history snapshot. The memory-core, rolling-summary, and no-memory groups use the same host and context limit, with equal total call caps per group and separately metered actual costs. Summary updates also count toward paid usage. Questions, expected content, and source mappings do not enter history encoding or host input. Summaries are labeled only as unverified historical reports without fabricated provenance. The comparison therefore measures complete integration behavior and cannot be attributed to a single storage structure.

The long-history experiment has not completed real-model acceptance. The v2 `long` and `repair` plans explicitly freeze a finite 30-day server allowance on each new, unused memory brain before its first turn. For the default 104-turn history plus six probes, that allowance is 636 cognition calls and 62,521,344 input bytes; each comparison arm separately has a ceiling of 856 total calls and 11,771,904 reserved output tokens. Actual costs remain separately metered, and the per-root and per-phase server limits still apply. Existing used budgets cannot be replaced, and unknown usage stops further paid calls. These are resource ceilings, not expected consumption or currency prices. A compressed hundred-turn run is not continuous operation over months; simulated timespans and actual operation across days must be reported separately.
