// memBrain memory page: notes, conversations, search and privacy for one local installation.
const $ = (id) => document.getElementById(id);
const uid = () => crypto.randomUUID();
const NOTES = "membrain-notes";
const state = { key: "", brain: "" };
let messageTimer;

function notify(text, error = false) {
  clearTimeout(messageTimer);
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
  messageTimer = setTimeout(() => { $("message").textContent = ""; }, 6000);
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function api(path, body, method) {
  const response = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { Authorization: `Bearer ${state.key}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || ["rejected", "conflict", "failed"].includes(data.outcome)) {
    throw new Error(data.error?.message || data.detail || `Request failed (${response.status})`);
  }
  return data;
}

function action(name, payload, conversation = "membrain-memory-page") {
  return api("/v1/actions", { message_type: "request", contract_version: "1.0.0-draft", request_id: uid(),
    action: name, target: { brain_ref: state.brain, conversation_ref: conversation, branch_ref: "main" }, payload });
}

function timeline(params) {
  const query = new URLSearchParams({ max_characters: "200000", max_item_characters: "20000", limit: "1000" });
  for (const [key, value] of Object.entries(params)) {
    for (const item of [].concat(value)) query.append(key, item);
  }
  return api(`/v1/brains/${encodeURIComponent(state.brain)}/timeline?${query}`);
}

async function scopeGeneration() {
  const data = await api(`/v1/brains/${encodeURIComponent(state.brain)}/dashboard`);
  return data.brain.scope_generation;
}

async function forget(kind, ids, mode, conversation) {
  return action("forget", { idempotency_key: uid(), selector: { kind, ids }, mode,
    expected_scope_generation: await scopeGeneration() }, conversation);
}

function observe(text, conversation, role, producer) {
  return action("observe", { idempotency_key: uid(), events: [{
    source_event_key: `${producer}:${uid()}`, source_revision: 1, producer_ref: producer, actor_ref: role,
    actor_role: role, event_kind: "message", content: { media_type: "text/plain", text },
    occurred_time: { start: null, end: null, precision: "unknown", basis: "unknown" },
    interpretation_hint: "unknown", delivery_state: "received", parent_source_event_keys: [],
  }] }, conversation);
}

const day = (value) => (value || "").slice(0, 10) || "undated";

function guard(button, task) {
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    button.disabled = true;
    try { await task(); } catch (error) { notify(error.message, true); } finally { button.disabled = false; }
  });
}

function onSubmit(form, task) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const buttons = [...form.querySelectorAll("button")];
    buttons.forEach((button) => { button.disabled = true; });
    try { await task(); } catch (error) { notify(error.message, true); } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  });
}

// Notes ------------------------------------------------------------------------------------------------------

async function loadNotes() {
  const { items } = await timeline({ role: ["assistant", "user"], conversation_id: NOTES });
  const list = $("notes");
  list.replaceChildren();
  if (!items.length) {
    list.append(element("p", "No notes yet. Your agent adds notes when you mention lasting facts; you can add your own above.", "empty"));
    return;
  }
  for (const note of items) list.append(noteItem(note));
}

function noteItem(note) {
  const item = element("div", undefined, "item");
  const meta = element("div", undefined, "meta");
  meta.append(element("span", day(note.recorded_at)));
  meta.append(element("span", note.actor_role === "user" ? "written by you" : "saved by your agent",
    note.actor_role === "user" ? "tag user" : "tag"));
  const actions = element("span", undefined, "actions");
  const edit = element("button", "Edit", "link");
  const remove = element("button", "Delete", "link danger");
  actions.append(edit, remove);
  meta.append(actions);
  const text = element("p", note.text);
  item.append(meta, text);
  edit.addEventListener("click", () => editNote(item, note));
  guard(remove, async () => {
    if (!confirm("Delete this note? Its content is erased.")) return;
    await forget("events", [note.event_id], "erase_content", NOTES);
    notify("Note deleted.");
    await loadNotes();
  });
  return item;
}

function editNote(item, note) {
  const form = element("form", undefined, "row");
  const input = element("input");
  input.value = note.text;
  input.maxLength = 300;
  const save = element("button", "Save");
  const cancel = element("button", "Cancel", "secondary");
  cancel.type = "button";
  form.append(input, save, cancel);
  item.replaceChildren(form);
  input.focus();
  cancel.addEventListener("click", loadNotes);
  onSubmit(form, async () => {
    const text = input.value.trim();
    if (!text || text === note.text) return loadNotes();
    await observe(text, NOTES, "user", "membrain-memory-page");
    await forget("events", [note.event_id], "suppress_recall", NOTES);  // Kept for audit, no longer shown.
    notify("Note corrected. Your version takes precedence.");
    await loadNotes();
  });
}

onSubmit($("note-form"), async () => {
  const text = $("note-text").value.trim();
  if (!text) return;
  await observe(text, NOTES, "user", "membrain-memory-page");
  $("note-text").value = "";
  notify("Note added.");
  await loadNotes();
});

// Conversations ----------------------------------------------------------------------------------------------

async function loadConversations() {
  const { items } = await timeline({ role: ["user", "assistant"] });
  const groups = new Map();
  for (const message of items) {
    if (message.conversation_id === NOTES) continue;
    if (!groups.has(message.conversation_id)) groups.set(message.conversation_id, []);
    groups.get(message.conversation_id).push(message);
  }
  const list = $("conversations");
  list.replaceChildren();
  if (!groups.size) {
    list.append(element("p", "No conversations recorded yet.", "empty"));
    return;
  }
  for (const [conversation, messages] of groups) list.append(conversationItem(conversation, messages.reverse()));
}

function conversationItem(conversation, messages) {
  const item = element("details", undefined, "item");
  const summary = element("summary");
  const first = messages.find((message) => message.actor_role === "user") || messages[0];
  summary.textContent = `${day(messages[0].recorded_at)} · ${messages.length} messages · ${first.text.slice(0, 90)}`;
  item.append(summary);
  for (const message of messages) {
    const block = element("div", undefined, `message ${message.actor_role}`);
    const meta = element("div", undefined, "meta");
    meta.append(element("span", message.actor_role === "user" ? "You" : "Agent"), element("span", day(message.recorded_at)));
    const remove = element("button", "Delete", "link danger");
    const actions = element("span", undefined, "actions");
    actions.append(remove);
    meta.append(actions);
    block.append(meta, element("p", message.text));
    guard(remove, async () => {
      if (!confirm("Delete this message? Its content is erased everywhere it was used.")) return;
      await forget("events", [message.event_id], "erase_content", conversation);
      block.remove();
      notify("Message deleted.");
    });
    item.append(block);
  }
  const removeAll = element("button", "Delete this conversation", "link danger");
  guard(removeAll, async () => {
    if (!confirm("Delete every message in this conversation?")) return;
    await forget("conversation", [conversation], "erase_content", conversation);
    notify("Conversation deleted.");
    await loadConversations();
  });
  item.append(removeAll);
  return item;
}

// Search -----------------------------------------------------------------------------------------------------

onSubmit($("search-form"), async () => {
  const query = $("search-text").value.trim();
  const results = $("results");
  results.replaceChildren();
  const budget = { max_context_tokens: 16000, max_model_calls: 0, max_embedding_calls: 1, max_expansion_steps: 2,
    max_wall_time_ms: 5000, max_cost_microunits: 0, currency: "USD", token_counting: "conservative_estimate",
    tokenizer_id: null };
  const payload = { query, intent: "explore", cue_event_ids: [], additional_cues: [],
    read_fence: { minimum_durable_sequence: 0, consistency: "read_your_writes", snapshot_revision: null },
    evidence_cutoff: { mode: "through_sequence", through_sequence: 2 ** 53 } };
  let response;
  try {
    response = await action("recall", { ...payload, budget });
  } catch {
    response = await action("recall", { ...payload, budget: { ...budget, max_embedding_calls: 0 } });
  }
  const sources = [];
  for (const item of response.result.items) {
    for (const ref of item.memory.evidence_refs || []) {
      if (ref.relationship === "supports" && !sources.includes(ref.event_id)) sources.push(ref.event_id);
    }
  }
  if (!sources.length) {
    results.append(element("p", "Nothing found.", "empty"));
    return;
  }
  const { items } = await timeline({ role: ["user", "assistant"], event_id: sources.slice(0, 20) });
  const byId = new Map(items.map((message) => [message.event_id, message]));
  for (const id of sources.slice(0, 20)) {
    const message = byId.get(id);
    if (!message) continue;
    const block = element("div", undefined, "item");
    const meta = element("div", undefined, "meta");
    meta.append(element("span", day(message.recorded_at)),
      element("span", message.conversation_id === NOTES ? "note" : message.actor_role === "user" ? "You" : "Agent", "tag"));
    block.append(meta, element("p", message.text));
    results.append(block);
  }
});

// Privacy ----------------------------------------------------------------------------------------------------

async function loadRecording() {
  let paused = false;
  try {
    paused = (await api("/v1/local/recording")).recording === "paused";
  } catch {
    $("recording").hidden = true;
    $("recording-text").textContent = "Recording controls are available when this page is opened from the local service.";
    return;
  }
  $("recording").hidden = false;
  $("recording").textContent = paused ? "Recording paused" : "Recording on";
  $("recording").classList.toggle("paused", paused);
  $("recording-text").textContent = paused
    ? "Recording is paused. New messages are not stored."
    : "New messages and replies are being stored.";
  $("pause").hidden = paused;
  $("pause-hours").hidden = paused;
  $("resume").hidden = !paused;
}

guard($("pause"), async () => {
  const hours = $("pause-hours").value;
  await api("/v1/local/recording", { paused: true, hours: hours ? Number(hours) : null });
  await loadRecording();
});
guard($("resume"), async () => {
  await api("/v1/local/recording", { paused: false });
  await loadRecording();
});
guard($("export"), async () => {
  const { result } = await action("export", { include_events: true, include_working_states: true,
    include_assertions: true, include_provenance: true, include_tombstones: true, budget: {
      max_context_tokens: 16000, max_model_calls: 0, max_embedding_calls: 0, max_expansion_steps: 2,
      max_wall_time_ms: 30000, max_cost_microunits: 0, currency: "USD", token_counting: "conservative_estimate",
      tokenizer_id: null } });
  const archive = result.archive || result;
  const document_ = await api(`/v1/archives/${encodeURIComponent(archive.archive_id)}`);
  const url = URL.createObjectURL(new Blob([JSON.stringify(document_, null, 2)], { type: "application/json" }));
  const link = element("a");
  link.href = url;
  link.download = "membrain-export.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  notify("Exported. The file contains your original messages; store it securely.");
});
guard($("erase"), async () => {
  if ($("erase-confirm").value.trim() !== "ERASE") {
    notify("Type ERASE to confirm.", true);
    return;
  }
  await forget("all_authorized_scope", [], "erase_content");
  $("erase-confirm").value = "";
  notify("Everything was erased.");
});

// Navigation and sign-in -------------------------------------------------------------------------------------

const loaders = { notes: loadNotes, conversations: loadConversations, search: async () => {}, privacy: loadRecording };

async function show(view) {
  for (const button of $("tabs").querySelectorAll("button")) button.classList.toggle("active", button.dataset.view === view);
  for (const name of Object.keys(loaders)) $(`view-${name}`).hidden = name !== view;
  try { await loaders[view](); } catch (error) { notify(error.message, true); }
}

for (const button of $("tabs").querySelectorAll("button")) button.addEventListener("click", () => show(button.dataset.view));

async function open(key, brain) {
  state.key = key;
  state.brain = brain;
  if (!state.brain) state.brain = (await api("/v1/brains")).brains?.[0]?.id || "";
  try { sessionStorage.setItem("membrain-page", JSON.stringify({ key: state.key, brain: state.brain })); } catch { /* private window */ }
  $("signin").hidden = true;
  $("tabs").hidden = false;
  await loadRecording();
  await show("notes");
}

onSubmit($("key-form"), () => open($("key").value.trim(), ""));

(async () => {
  const code = new URLSearchParams(location.search).get("code");
  if (code) {
    history.replaceState(null, "", "/memory");
    try {
      const session = await fetch("/v1/local/ui-session", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }) });
      const data = await session.json();
      if (!session.ok) throw new Error(data.detail || "This link has expired.");
      return await open(data.api_key, data.brain_id);
    } catch (error) {
      notify(error.message, true);
    }
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem("membrain-page") || "null");
    if (saved?.key) return await open(saved.key, saved.brain);
  } catch { /* fall through to sign-in */ }
  $("signin").hidden = false;
})();
