const $ = (id) => document.getElementById(id);
const uid = () => crypto.randomUUID();
const state = { key: "", brain: null, dashboard: null, capabilities: null };
const budget = () => ({ max_context_tokens: 16000, max_model_calls: 0, max_embedding_calls: 0,
  max_expansion_steps: 2, max_wall_time_ms: 3000, max_cost_microunits: 0,
  currency: "USD", token_counting: "conservative_estimate", tokenizer_id: null });
const unknownTime = () => ({ start: null, end: null, precision: "unknown", basis: "unknown" });
let messageTimer;

function notify(text, error = false) {
  clearTimeout(messageTimer);
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
  messageTimer = setTimeout(() => { $("message").textContent = ""; }, 7000);
}

async function api(path, body) {
  const response = await fetch(path, { method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${state.key}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok || ["rejected", "conflict", "failed"].includes(data.outcome)) {
    throw new Error(data.error?.message || `Request failed (${response.status})`);
  }
  return data;
}

async function action(name, payload, conversation = $("conversation").value || "personal") {
  if (!state.brain) throw new Error("Create a brain first");
  return api("/v1/actions", { message_type: "request", contract_version: "1.0.0-draft",
    request_id: uid(), action: name,
    target: { brain_ref: state.brain, conversation_ref: conversation, branch_ref: "main" }, payload });
}

function bind(id, event, handler) {
  $(id).addEventListener(event, async (e) => {
    e.preventDefault();
    const buttons = [...$(id).querySelectorAll("button"), ...($(id).tagName === "BUTTON" ? [$(id)] : [])];
    buttons.forEach((button) => { button.disabled = true; });
    try { await handler(e); } catch (error) { notify(error.message, true); }
    finally { buttons.forEach((button) => { button.disabled = false; }); }
  });
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function displayStatement(statement) {
  const roles = { user: "User", assistant: "Assistant", tool: "Tool", operator: "Operator", external: "External source", system: "System" };
  const acts = { direct_statement: "stated", inquiry: "asked", presupposition: "mentioned a presupposition", correction: "corrected", confirmation: "confirmed", instruction: "requested", unknown: "expressed", not_applicable: "recorded" };
  return statement.replace(/^(user|assistant|tool|operator|external|system) (\S+) \((\w+)\): /,
    (_, role, actor, act) => `${roles[role]}${actor === role ? "" : ` (${actor})`} ${acts[act] || "expressed"}: `);
}

async function loadBrains(preferred) {
  const data = await api("/v1/brains");
  const brains = data.brains || data;
  $("brain").replaceChildren();
  for (const brain of brains) {
    const option = element("option", brain.name);
    option.value = brain.id || brain.brain_id;
    $("brain").append(option);
  }
  state.brain = preferred || state.brain || brains[0]?.id || brains[0]?.brain_id || null;
  if (state.brain) $("brain").value = state.brain;
  await refresh();
}

async function refresh() {
  if (!state.brain) {
    $("memories").replaceChildren(element("p", "Create a brain to save your first experience.", "empty"));
    $("stats").replaceChildren();
    return;
  }
  const data = await api(`/v1/brains/${encodeURIComponent(state.brain)}/dashboard`);
  state.dashboard = data;
  const memories = (data.memories || []).map((item) => item.document || item);
  const events = (data.events || []).map((item) => item.document || item);
  const jobs = data.jobs || [];
  const stats = [["Visible experiences", events.length], ["Visible memories", memories.length],
    ["Pending records", jobs.filter((job) => ["queued", "running", "retrying"].includes(job.state)).length],
    ["Recall method", "Cue-based retrieval"]];
  $("stats").replaceChildren(...stats.map(([label, value]) => {
    const item = element("div", undefined, "stat");
    item.append(element("strong", value), element("span", label));
    return item;
  }));
  $("memories").replaceChildren();
  if (!memories.length) $("memories").append(element("p", "No processed memories yet. Saved experiences will be processed in the background.", "empty"));
  for (const memory of memories) renderMemory(memory);
  $("diagnostics").textContent = JSON.stringify({ capabilities: state.capabilities, status: data.status,
    durable_sequence: data.brain?.durable_sequence, indexed_sequence: data.brain?.indexed_sequence }, null, 2);
}

function renderMemory(memory) {
  const row = element("article", undefined, "memory");
  const kinds = { episodic: "Experience", semantic: "Belief", prospective: "Future task", procedural: "Procedure", interaction_preference: "Interaction preference", relationship: "Relationship", self_model: "Self model" };
  const worlds = { real_world: "Real-world report", quoted: "Quotation", roleplay: "Roleplay", hypothetical: "Hypothetical", dream: "Dream", unknown: "Context unconfirmed" };
  const states = { active: "Active", dormant: "Faded", superseded: "Superseded", retracted: "Retracted" };
  row.append(element("div", `${kinds[memory.memory_kind] || memory.memory_kind} · ${worlds[memory.world] || memory.world} · ${states[memory.lifecycle] || memory.lifecycle}`, "meta"));
  row.append(element("p", displayStatement(memory.statement)));
  row.append(element("div", `${memory.evidence_refs?.length || 0} sources · ${memory.system_time?.recorded_at || ""}`, "meta"));
  const actions = element("div", undefined, "memory-actions");
  for (const [label, callback] of [["View sources", () => inspect(memory, row)],
    ["Correct", () => correct(memory)], ["Delete content", () => forget(memory)]]) {
    const button = element("button", label, "secondary");
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await callback(); } catch (error) { notify(error.message, true); }
      finally { button.disabled = false; }
    });
    actions.append(button);
  }
  row.append(actions);
  $("memories").append(row);
}

async function inspect(memory, row) {
  const response = await action("inspect", { memory_ids: [memory.memory_id], evidence_refs: memory.evidence_refs,
    include_successors: true, include_corrections: true, budget: budget() });
  row.querySelector(".source-details")?.remove();
  const details = element("div", undefined, "result source-details");
  for (const event of response.result.events) {
    details.append(element("small", `${event.observed.actor_role} · ${event.system_time.recorded_at}`),
      element("p", event.observed.content.text || JSON.stringify(event.observed.content)));
  }
  if (!response.result.events.length) details.append(element("p", "Sources are currently unavailable."));
  row.append(details);
}

async function observe(text, speech = "direct_statement") {
  return action("observe", { idempotency_key: uid(), events: [{ source_event_key: uid(), source_revision: 1,
    producer_ref: "console", actor_ref: "user", actor_role: "user", event_kind: "message",
    content: { media_type: "text/plain", text }, occurred_time: unknownTime(),
    interpretation_hint: "real_world_report", delivery_state: "received", speech_act_hint: speech }] });
}

async function correct(memory) {
  const text = prompt("What should this memory say instead? Your correction and its source will be recorded.", memory.statement);
  if (!text?.trim()) return;
  const captured = await observe(text.trim(), "correction");
  const ids = [...captured.result.accepted_event_ids, ...captured.result.duplicate_event_ids];
  await refresh();
  const events = (state.dashboard.events || []).map((item) => item.document || item);
  const source = events.find((event) => ids.includes(event.event_id));
  if (!source) throw new Error("The correction was saved, but its source is not available yet. Refresh and try again.");
  const evidence = { event_id: source.event_id, event_revision: source.revision,
    source_content_sha256: source.content_sha256, relationship: "corrects" };
  await action("correct", { memory_id: memory.memory_id, expected_revision: memory.revision,
    correction_event_ids: ids, operation: "replace", idempotency_key: uid(),
    expected_dependency_generation: state.dashboard.brain.dependency_generation,
    replacement: { statement: text.trim(), memory_kind: memory.memory_kind, world_hint: memory.world,
      evidence_refs: [evidence], proposal_reason: "Explicit user correction in the memory console" } });
  await refresh();
  notify("The correction is applied. The old conclusion is no longer used as current memory.");
}

async function forget(memory) {
  if (!confirm("Delete this memory, its related sources, and derived content? Archives downloaded elsewhere cannot be revoked here.")) return;
  await action("forget", { idempotency_key: uid(), selector: { kind: "memories", ids: [memory.memory_id] },
    mode: "erase_content", expected_scope_generation: state.dashboard.brain.scope_generation });
  await refresh();
  $("recall-results").replaceChildren();
  notify("Deletion submitted. Recall results on this page have been cleared.");
}

bind("connect-form", "submit", async () => {
  state.key = $("key").value.trim();
  state.capabilities = await api("/v1/capabilities");
  await loadBrains();
  $("key").value = "";
  $("connect-panel").hidden = true;
  $("workspace").hidden = false;
  $("connection").textContent = "Local service connected";
  $("mode-note").textContent = "Currently supports sourced experience records and retrieval by keywords or Chinese cues. Semantic consolidation and long-term memory quality still need validation. Connecting does not automatically import chats from other apps.";
});
bind("disconnect", "click", async () => { location.reload(); });
bind("create-form", "submit", async () => {
  const brain = await api("/v1/brains", { name: $("brain-name").value });
  $("brain-name").value = "";
  await loadBrains(brain.id || brain.brain_id);
  notify("New brain created.");
});
bind("brain", "change", async () => { state.brain = $("brain").value; $("recall-results").replaceChildren(); await refresh(); });
bind("refresh", "click", refresh);
bind("observe-form", "submit", async () => {
  await observe($("experience").value);
  $("experience").value = "";
  await api(`/v1/brains/${encodeURIComponent(state.brain)}/work`, {});
  await refresh();
  notify("Experience saved durably.");
});
bind("recall-form", "submit", async () => {
  const payload = { query: $("query").value, intent: $("prior").checked ? "cue_recovery" : "explore",
    cue_event_ids: [], additional_cues: [], budget: budget(),
    read_fence: { minimum_durable_sequence: 0, consistency: "read_your_writes", snapshot_revision: null },
    evidence_mode: $("prior").checked ? "prior_record_check" : "contextual" };
  const { result } = await action("recall", payload);
  $("recall-results").replaceChildren();
  const labels = { partial: "Partial evidence found", grounded: "Supporting evidence found", no_match: "No match in the current scope", indexing: "Indexing",
    conflicted: "Conflicting evidence", ambiguous: "Several possibilities", degraded: "Service degraded", deleted: "Related content deleted" };
  $("recall-results").append(element("p", `${labels[result.status] || result.status} · ${result.usage.context_tokens} tokens (conservative estimate)`, "hint"));
  for (const item of result.items) {
    const block = element("div", undefined, "result");
    block.append(element("p", displayStatement(item.memory.statement)));
    $("recall-results").append(block);
  }
  for (const unknown of result.unknowns) {
    const explanation = unknown === "Lexical candidates retain source roles; semantic answer sufficiency is not assessed."
      ? "Relevant original records were found. Whether they fully answer the question still needs assessment." : unknown;
    $("recall-results").append(element("p", explanation, "hint"));
  }
});
bind("process", "click", async () => {
  const result = await api(`/v1/brains/${encodeURIComponent(state.brain)}/work`, {});
  await refresh();
  notify(`${result.processed} records processed.`);
});
bind("export", "click", async () => {
  const { result } = await action("export", { include_events: true, include_working_states: true,
    include_assertions: true, include_provenance: true, include_tombstones: true, budget: budget() });
  const archive = result.archive || result;
  const document = await api(`/v1/archives/${encodeURIComponent(archive.archive_id)}`);
  const url = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2)], { type: "application/json" }));
  const link = element("a"); link.href = url; link.download = `agent-memory-${state.brain}.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  notify("Archive exported with original text and sources. Store it securely.");
});
