# Roadmap: memory for chat and life agents

Status: **in progress.** Workflow hardening now includes source-linked notes, structured editing and People/Plans views, a readiness panel and `membrain doctor`, bounded connection reuse, local embedding batches, ranked recall budgets and automatic code PR verification. Real-model behavior and external package publishing remain separate acceptance gates. Implemented so far: A3 privacy controls, A2 memory page, the shared `membrain.host` layer, A4 adapters (Hermes, Codex, OpenClaw, WorkBuddy and CodeBuddy, AstrBot and the OpenAI-compatible proxy; real-host status in [plugin mode](plugin-mode.md)) and A5 host-memory import. Everything else is planned. This document turns the competitive review of 2026-10-27 into phased work. Implemented and verified behaviour is described in [plugin mode](plugin-mode.md) and the [evaluation reports](../evaluations/).

## Positioning

Most agent-memory products help an agent remember its *work*. claude-mem records tool-use observations from coding sessions and compresses them with extra model calls. Claude Code's auto memory keeps per-project notes. memBrain targets **chat and life agents**: companions, personal assistants and role-play hosts that need to remember a *person* and a *relationship*. Such an agent has to remember:

- who the user is, and the people in their life;
- the user's health limits, preferences and routines;
- the user's plans, and whether those plans actually happened;
- what is coming up;
- what was said in role-play, as opposed to real life;
- how the user felt;
- what the agent itself promised.

Personality and emotion features (phase C) build on that memory.

In short: other tools help an agent remember its work; memBrain helps an agent remember a person.

## Principles

1. **The core stays independent of hosts.** Adapters call the public API or SDK; memory state and algorithms stay in the core ([AGENTS.md](../AGENTS.md)).
2. **No extra key by default.** The host model already in use does the understanding, such as saving notes and judging relevance. memBrain stores, retrieves, budgets and audits.
3. **Provenance over compression.** Every note and recalled line traces back to an original message and an attributed speaker: user, assistant, imported, or role-play. Summaries never replace sources.
4. **The user is in control.** Users can see, correct, pin, pause and delete what is remembered. Sensitive memories are not volunteered.
5. **Measure before claiming.** Claims about a feature are made only after a blinded, held-out evaluation, and failed results are kept.
6. **Safe companionship.** Memory and emotion features never exist to maximize engagement or create dependency.

## Baseline (verified on 2026-10-27)

**Implemented:**
- One-command Claude Code plugin with an embedded PostgreSQL database.
- Automatic recording of messages and replies.
- Notes curated by the host model (`memory_note`, with replacement).
- Session start shows the saved notes and recent history.
- Message-level recall with a cue from the previous prompt.
- Local multilingual semantic recall with no key.
- Deletion and suppression, backup and restore.
- MCP tools, a Python client and a TypeScript SDK.

**Measured:**
- On a held-out scenario with 71 messages, the plugin scored 11.5 of 12, the same as a full-history prompt, while using about a third of its tokens.
- The previous plugin scored 5.5. One run, with DeepSeek V4 Pro as the host model ([report](../evaluations/v1-2026-10-27-fresh-scenarios.md)).

**Known weaknesses:**
- **Plan versus done.** A planned action was sometimes treated as completed.
- **Duplicate notes.** The host sometimes saved overlapping notes for the same fact.
- **No viewer.** There is no user-facing memory page for plugin users.
- **One host.** Only the Claude Code host is supported.
- **No standard benchmarks.** There are no LoCoMo, LongMemEval or PersonaMem numbers yet.
- **Claude not measured.** Claude as the host model has not been measured.

## Competitive gaps

| Capability | Who has it | Phase |
| --- | --- | --- |
| Memory viewer and editor | claude-mem viewer, Nomi Mind Map, ChatGPT memory page, Kindroid Key Memories | A2 |
| Adapters for chat hosts (OpenClaw, Hermes Agent) | claude-mem, Mem0, Honcho, Supermemory | A4 |
| Privacy tags, pause, temporary mode | claude-mem `<private>`, ChatGPT Temporary Chat | A3 |
| Coexistence with host memory files (`MEMORY.md`, `USER.md`) | — | A5 |
| Public benchmark numbers | Mem0, Honcho, MemPalace (largely retrieval-only, and disputed) | A6 |
| Keyless installation from a package index | claude-mem `npx`, Mem0 plugin | A1 |
| A user model plus memory of the relationship | Honcho peer cards, Letta human and persona blocks, Nomi identity core | B, C |

## Phase A: parity

Goal: a user switching from claude-mem, Mem0 or the built-in memory of a host loses nothing they rely on.

### A1. Release and installation

- Publish `membrain-ai` to PyPI with the existing trusted-publishing workflow. This needs a pending publisher on PyPI, configured by the repository owner.
- Pin the plugin hooks and `.mcp.json` to the released version instead of the Git URL.
- Add `membrain doctor`. It checks Python and uv, the database engine, the service, the semantic model, the hook queue and disk space, and prints fixes.
- Document how to disable and uninstall, including what data stays and how to delete it.

**Acceptance:**
- A fresh install from PyPI on Linux, macOS and Windows reaches `semantic_recall: ready` without Git.
- `doctor` reports every failure mode seen so far.

### A2. Memory page

The existing static workspace (`src/membrain/web`) is redesigned for plugin users.

- `membrain plugin ui` opens the page in a browser with a one-time local token, so the user never copies a key.
- **Views:**
  - **Notes:** grouped by kind once B1 lands; pin, edit, replace, delete; each note links to its sources.
  - **Timeline:** sessions and their messages, with search using the same recall as the hooks.
  - **Coming up:** after B3.
  - **People:** after B2.
  - **Privacy:** pause, excluded sessions, export, erase everything.
- **API:**
  - Note endpoints: list, save, replace, pin, suppress, erase.
  - These wrap the existing event, timeline and forget actions so that deletion keeps its audit trail.

**Acceptance:**
- Every note can be traced back to its source message.
- Deleting a note through the page removes it from session-start context and recall, and the deletion survives restoring an older backup.

### A3. Privacy controls

- Remove `<private>…</private>` spans before a message is recorded, spooled or queued.
- Scrub likely secrets (API keys, tokens, passwords in common formats) before storage, and say so in the docs.
- Pause and resume recording with `membrain plugin pause|resume`, from the page, or by asking the agent; the skill maps "don't remember this" to it.
- Temporary sessions: a session marked temporary is neither recorded nor shown its own history.

**Acceptance:**
- Tests show that no private or scrubbed text reaches the database, the spool or the host queue.

### A4. Chat host adapters

Shared work: move host-independent logic out of `plugin.py` into a `membrain.host` module. That covers session-start context, per-prompt recall, recording a turn, notes, privacy filtering and the spool. Each adapter then stays thin.

| Host | Interface | Plan |
| --- | --- | --- |
| **Hermes Agent** (Nous Research) | Python `MemoryProvider`; pip entry point in group `hermes_agent.memory_providers` | `initialize` creates or starts the installation. `system_prompt_block` plus `prefetch` return notes, history and recall. `sync_turn` records both sides without blocking. `get_tool_schemas` and `handle_tool_call` expose note, recall and forget. `on_memory_write` mirrors writes to Hermes's built-in memory as imported notes. `on_session_end` flushes. Storage paths derive from `hermes_home` or the memBrain directory. |
| **OpenClaw** | TypeScript plugin in the exclusive memory slot (`plugins.slots.memory`); `before_prompt_build` and `agent_end` hooks, which need `allowPromptInjection` and `allowConversationAccess` | Build on the TypeScript SDK. The plugin starts the local service through `uvx membrain plugin start`, injects context before the prompt, records turns at `agent_end`, and registers note, recall and forget tools. It skips automatic capture in a turn where the agent already wrote a note. |
| **Claude Code** | Existing plugin | Keep it; adopt the shared module. |
| **Other MCP hosts** | stdio MCP | Keep the tools; document the limits (no automatic recording). |
| **SillyTavern** | Browser extension plus the local HTTP API | Later, with B4. Needs a CORS allow-list, one brain per character, and turns marked as role-play. |

**Acceptance:**
- For each adapter, an end-to-end test on the real host verifies four things: recording, cross-session recall, saving a note, and deletion.
- A recorded install with a one-line instruction for each host.

### A5. Coexisting with host memory

Status: implemented (`membrain plugin import`, Hermes automatic import, per-host skipping at session start). Only memory about the person is imported by default; project and environment memory needs `--all`.

- Import Claude Code `MEMORY.md`, Hermes `MEMORY.md` and `USER.md`, and OpenClaw memory files as notes attributed as *imported*, removing duplicates of existing notes.
- Document a recommended setup for each host: keep the built-in memory for project conventions, and use memBrain for the person. Alternatively, turn the built-in memory off.
- At session start, if the host's own memory file is also injected, do not repeat its content.

### A6. Standard benchmarks

Status: LoCoMo done ([report](../evaluations/locomo-2026-10-29.md)), with DeepSeek-V4.1-Flash answering and judging: memBrain through the plugin path 76.4% after recall changes tuned on three of the ten conversations (76.2% on the other seven, up from 69.3%), full context 81.4%, no memory 2.4%. LongMemEval on a 30-question sample ([report](../evaluations/longmemeval-2026-10-29.md)): memBrain 22, full history 22, no memory 2; the full 500 questions need faster embedding. PersonaMem is next.

Measure end-to-end answer accuracy with a judge, not retrieval alone, for these arms: no memory, full context (where it fits), and memBrain through the plugin path.

| Benchmark | What it measures | Data |
| --- | --- | --- |
| LoCoMo | Long multi-session conversations: single-hop, multi-hop, temporal | `snap-research/locomo`, 10 conversations, about 2,000 questions |
| LongMemEval (S) | Recall across many sessions, knowledge updates, abstention | `xiaowu0162/longmemeval` |
| PersonaMem | Preferences that change over time, using the latest state | `bowen-upenn/PersonaMem` |

- Use the published judge prompts, a fixed host model (DeepSeek, as already authorized; Claude when available), report cost, and keep failed categories.
- Publish the harness so the numbers can be reproduced.

### A7. Validation on real hosts

- Run Claude Code with Claude, using the owner's subscription.
- Check whether Claude saves and replaces notes as DeepSeek did.
- Check the experience of the one-time MCP permission prompt.
- Check how the plugin interacts with auto memory.
- Repeat on Hermes and OpenClaw after A4.

### Milestone v0.1

Phase A ships as **v0.1**: A1 to A4 for Hermes, A6 for LoCoMo, and A7 for Claude Code.

## Phase B: memory for chat and life

Goal: be measurably better than the alternatives at remembering a person.

### B1. Typed notes

Status: implemented. Instead of `application/json` content, a typed note is plain text with a parseable header (`[plan · intended · due 2026-11-14 · about Mei] …`): JSON content would bypass sentence segmentation and put serialized keys into the recall index, while the header keeps recall, the memory page, replacement and deletion unchanged. `source_event_ids` and deferred exact source-message references are stored as dependency links; the memory page can inspect the original messages.

A note becomes a JSON document, stored as an assistant-authored event with `application/json` content in the notes conversation, so no schema migration is needed. It has these fields:

- `kind`: `person`, `constraint` (health, diet, access), `preference`, `plan`, `routine`, `milestone`, `relationship`, `agent_commitment` or `other`.
- `about`: the person the fact concerns.
- `text`: one sentence.
- Dates: `occurs_at` and `due`.
- `status`, for plans: `intended`, `scheduled`, `done`, `cancelled` or `superseded`.
- `pinned`.
- `sensitivity`: `normal` or `sensitive`.
- `source_event_ids` and `replaces`.

The `memory_note` tool accepts these fields, and plain text stays valid. Session start groups notes by kind and shows each plan's status explicitly ("intended, not confirmed done").

**Acceptance:**
- On new scenarios, plan-versus-done cases improve over the current baseline.
- Text-only notes keep working.

### B2. People and aliases

Status: implemented (person notes with `about` and `aliases`, a People group at session start, alias names as recall cues).

- `person` notes carry a name, aliases ("the sparky"), a relation to the user, and short facts.
- Recall expands aliases into cues, so "the sparky" finds the electrician's name.
- Session start shows a compact People block with each name's relation, so similar names stay apart (Tom the colleague, Tomás the brother-in-law).

### B3. Coming up and open loops

Status: implemented (`<membrain-coming-up>` and skill guidance); its effect on the plan-versus-done case is measured in B7.

- At session start, show plans due in the next two weeks, plans whose date has passed but are still `intended`, and agent commitments not yet fulfilled. Dates are relative to the host's local date.
- The skill tells the agent to follow up naturally ("Did the passport arrive?") and never to assume a planned action happened.
- This directly targets the weakest measured case.

### B4. Fiction and real life

Status: implemented for chat hosts (`ChatMemory`, the proxy headers and the AstrBot setting): one brain per person and character, records marked `roleplay`, and opt-in sharing of non-sensitive real-life notes. A SillyTavern extension that sets the character automatically is not built yet; SillyTavern can use the proxy with the header.

- Hosts mark sessions or characters as role-play: always in SillyTavern, and configurable elsewhere. Their records use the core's existing `world: roleplay` scope.
- Role-play facts never enter the real-life context, and real-life sensitive notes never enter role-play unless the user allows it.
- A role-play character's own memory stays separate for each character.

### B5. The agent's own memory

Status: implemented through typed notes: `agent_commitment` notes (promises, with status and due date) appear in Coming up, and `relationship` notes hold how the user wants the agent to be with them. Extracting commitments automatically from the agent's replies is not built.

- Promises the agent made ("I'll remind you on Friday"), opinions it stated, and nicknames it uses become `agent_commitment` and `relationship` notes.
- These use the core's existing recall of the agent's own speech and commitments, and appear in Coming up.

### B6. Keeping notes tidy

Status: implemented, with two parts deliberately left out.

- Session start chooses notes within its budget by priority (pinned, the user's own, constraints and people, open plans and promises, then the newest within each tier), says how many were left out, and lists likely duplicates (same kind and person, high word overlap) for the agent to merge with `replaces`.
- The memory page marks the same likely duplicates and offers **Merge duplicate**: the person edits the merged sentence, which is saved as their own note and replaces both.
- Not built: embedding similarity. Hooks run as short processes and cannot load the embedding model cheaply on every session start. Same kind, same person and word overlap catch the duplicates seen in the evaluations so far.
- Not built: usage-based decay. Within each priority tier, newer notes already come first, and a note is never deleted automatically. Recording note use would mean a write on every recall, for a ranking change that has not been shown to matter.

- Detect near-duplicate notes by embedding similarity and same `about`. Offer merges on the memory page, and let the host merge at session end when notes exceed a threshold.
- When notes exceed the session-start budget, choose notes by pinned status, then by importance, recency and relevance to the session's first message.
- Unpinned trivial notes lose priority over time (decay that resets on use) but are never deleted automatically.

### B7. Evaluation

Status: held-out scenarios done with DeepSeek as host ([report](../evaluations/v1-2026-10-29-phase-b.md)): the plugin scored 18.5 of 22, against 14.5 before Phase B, 18 with full history and 7.5 without memory. Claude as host and PersonaMem are not run yet.

- Run PersonaMem.
- Run two new held-out scenarios written by a separate agent, targeting:
  - plan versus done;
  - aliases;
  - role-play separation;
  - upcoming events;
  - commitments.
- Use DeepSeek and Claude as hosts. Scoring is blind and independent, as before.

### Milestone v0.2

Phase B ships as **v0.2**, together with the OpenClaw adapter and SillyTavern.

## Phase C: personality and emotion

Goal: a companion that stays itself and remembers how the user felt, without manipulation.

### C1. Persona

- The host application (or the user) defines a persona card: stable traits, voice, values, boundaries. It is versioned and injected separately from user memory.
- The style learned for each user (length, humour, formality, language) is stored as `preference` notes the user can see.
- Persona-drift checks run in evaluation: the same probe questions are asked across sessions.

### C2. Emotional memory

The design follows ZifaMem (arXiv 2607.17564). There, emotion tags attached to memories helped, while a separate affect-state block or a complex affect estimator added nothing measurable.

- When a note or episode is saved, the host may attach affect: valence, intensity and topic.
- Affect follows the appraisal boundaries in [memory quality and efficiency](memory-quality-and-efficiency.md):
  - an emotion the user *reported* ("I was gutted") is kept apart from one the model *inferred*;
  - each appraisal records whose it is, the persona version, its evidence and when it applied;
  - salience may steer attention and tone, but it never raises a memory's authority, overrides scope or revives erased material;
  - a later appraisal never rewrites a reaction recorded earlier;
  - correcting or deleting a source also removes the appraisals attached to it.
- The mood trend is the last few observations, not a simulated emotional state.
- Emotionally significant events can create gentle follow-ups in Coming up ("Mom's surgery was on Tuesday").
- Notes marked `sensitive` are used only when the user raises the topic.

### C3. Relationship memory

- Shared milestones (the first conversation, things achieved together), shared jokes, and how the user likes to be supported.
- An optional relationship timeline on the memory page.

### C4. Safety

- No retention tactics: no guilt about absence, and no invented feelings presented as real.
- Honesty about being an AI, and about what is and is not remembered.
- Hooks a host operator can use to meet its own obligations. For example, California SB 243, in force since 2026-01-01, places duties on companion-chatbot operators: disclosure, crisis protocols, and reporting. memBrain provides signals and controls; it does not replace the operator's own compliance.
- Every emotional memory is visible and deletable on the memory page.

### C5. Evaluation

- **Persona consistency:** probes across sessions.
- **Emotional continuity:** scenario endpoints modelled on ALPSBench: persona grounding, preference following, separating fiction from reality, emotional intelligence.
- Human review of a sample, and a check that follow-ups are welcome rather than intrusive.

### Milestone v0.3

Phase C ships as **v0.3**.

## How we differ from claude-mem

| | claude-mem | memBrain |
| --- | --- | --- |
| Unit of memory | Agent actions in a project | A person, their life and the relationship |
| Capture | Tool-use observations compressed by AI (extra model calls; sign-in and a hosted trial by default) | Original messages plus notes curated by the host; no account or extra calls |
| Trust | Summaries | Every note traces back to its source and speaker; audited deletion that survives restore |
| Time | Session timeline | Plan status, replacement, upcoming dates, open loops (B1, B3) |
| Fiction | — | Separate memory for role-play (B4) |
| Languages | Interface languages | Recall across languages without a key (verified with Chinese, English and Spanish) |
| Evidence | — | Blinded held-out evaluations now; LoCoMo, LongMemEval and PersonaMem next (A6) |
| Ahead of us today | Maturity, viewer, many hosts, community | — |

## Risks

- **The host model may not save good notes.** Measured with one host model. Mitigation: A7, the B6 tidy-up, and the memory page for corrections.
- **Permission prompts and host differences** (MCP approval, hook limits such as Claude Code's 10,000-character context). Mitigation: the adapter tests in A4.
- **Competitors move into chat hosts.** claude-mem already lists OpenClaw and Hermes. Mitigation: ship A4 for Hermes early, and differentiate through B rather than by matching their coding features.
- **Benchmarks can mislead** (retrieval-only scores, tuning on the test set). Mitigation: score answers end to end, keep held-out scenarios, and publish the harnesses.
- **Regulation and harm from companion memory.** Mitigation: the principles, C4, and user control from A2 onward.

## Decisions and actions for the owner

1. Register the PyPI trusted publisher (A1).
2. Provide a Claude Code session with Claude for A7, or run the provided script.
3. Confirm the host priority: proposed order is Hermes Agent, then OpenClaw, then SillyTavern.
4. Approve the budget for paid benchmarks. DeepSeek is already authorized; a run with Claude as host would need a key or subscription.
5. Set the scope for role-play and adult content (B4), which affects the SillyTavern work.
6. Define what the persona and emotion features may and may not do (C1, C4), before implementation.
