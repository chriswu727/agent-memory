// memBrain memory page: notes, conversations, search and privacy for one local installation.
const $ = (id) => document.getElementById(id);
const uid = () => crypto.randomUUID();
const HOST_NAMES = { "claude-code": "Claude Code", codex: "Codex", hermes: "Hermes", openclaw: "OpenClaw", workbuddy: "WorkBuddy" };
const NOTES = "membrain-notes";
const state = { key: "", brain: "", notes: [], connectionShown: false };
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
    if (["role", "event_id"].includes(key)) for (const item of [].concat(value)) query.append(key, item);
    else query.set(key, String(value));
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

const KINDS = ["person", "constraint", "preference", "plan", "routine", "milestone", "relationship", "agent_commitment", "other"];
const STATUSES = ["intended", "scheduled", "done", "cancelled", "superseded"];
const LABELS = { person: "Person", constraint: "Health or constraint", preference: "Preference", plan: "Plan", routine: "Routine",
  milestone: "Milestone", relationship: "Relationship", agent_commitment: "Agent promise", other: "Other" };
const openPlan = (fields) => ["intended", "scheduled"].includes(fields.status || "intended");

function typedNote(text) {
  const match = /^\[([^\[\]]{1,200})\] ([\s\S]+)$/.exec(text || "");
  const tokens = match ? match[1].split(" · ") : [];
  const fields = {};
  if (!match || !KINDS.includes(tokens[0])) return { fields, tags: [], sentence: text };
  fields.kind = tokens[0];
  for (const token of tokens.slice(1)) {
    if (STATUSES.includes(token)) fields.status = token;
    else if (["pinned", "sensitive"].includes(token)) fields[token] = true;
    else if (/^(due|on) \d{4}-\d{2}(-\d{2})?$/.test(token)) fields[token.startsWith("due ") ? "due" : "occurs_at"] = token.split(" ")[1];
    else if (token.startsWith("about ")) fields.about = token.slice(6);
    else if (token.startsWith("aka ")) fields.aliases = token.slice(4).split(",").map((value) => value.trim());
    else return { fields: {}, tags: [], sentence: text };
  }
  return { fields, tags: tokens.map((token) => token.replace("agent_commitment", "agent promise")), sentence: match[2] };
}

function words(text) {
  return new Set((text.toLowerCase().match(/[\p{L}\p{N}_]+/gu) || []).filter((word) => word.length > 2));
}

function duplicatePairs(items) {
  const parsed = items.filter((note) => note.actor_role !== "user").map((note) => ({ note, ...typedNote(note.text), words: words(typedNote(note.text).sentence) }));
  const pairs = new Map();
  parsed.forEach((first, index) => {
    for (const second of parsed.slice(index + 1)) {
      if (first.fields.kind !== second.fields.kind || first.fields.about !== second.fields.about || pairs.has(first.note.event_id)) continue;
      const union = new Set([...first.words, ...second.words]);
      const shared = [...first.words].filter((word) => second.words.has(word)).length;
      if (union.size && shared / union.size >= 0.75) pairs.set(first.note.event_id, second.note);
    }
  });
  return pairs;
}

async function loadNotes(view = "notes") {
  const response = await timeline({ role: ["assistant", "user"], conversation_id: NOTES });
  state.notes = response.items;
  const list = $(view);
  list.replaceChildren();
  let items = state.notes;
  if (view === "people") items = items.filter((note) => typedNote(note.text).fields.kind === "person");
  if (view === "plans") items = items.filter((note) => {
    const fields = typedNote(note.text).fields;
    return ["plan", "agent_commitment", "milestone"].includes(fields.kind) && ($("show-completed").checked || openPlan(fields));
  });
  items = [...items].sort((a, b) => Number(Boolean(typedNote(b.text).fields.pinned)) - Number(Boolean(typedNote(a.text).fields.pinned)));
  if (!response.complete) list.append(element("p", "This is a partial list. Some older notes did not fit; use Search to find them.", "hint"));
  if (!items.length) list.append(element("p", view === "notes" ? "No notes yet. Add a lasting fact above, or ask your connected agent to remember one." : "Nothing here yet. Add a note with the matching kind.", "empty"));
  const pairs = duplicatePairs(items);
  for (const note of items) list.append(noteItem(note, pairs.get(note.event_id)));
}

function field(form, controls, name, label, type = "text", options) {
  const wrapper = element("label", undefined, "field");
  wrapper.append(element("span", label));
  const control = element(type === "textarea" ? "textarea" : options ? "select" : "input");
  if (type !== "textarea" && !options) control.type = type;
  if (options) for (const [value, title] of options) {
    const option = element("option", title); option.value = value; control.append(option);
  }
  control.name = name;
  wrapper.append(control); form.append(wrapper); controls[name] = control;
  return control;
}

function noteEditor(note, merged = [], after = () => loadNotes(), initialSources = []) {
  const form = element("form", undefined, "note-editor");
  const controls = {};
  const parsed = typedNote(note?.text || "");
  const fields = parsed.fields;
  const text = field(form, controls, "text", "Note text", "textarea");
  text.value = parsed.sentence || ""; text.maxLength = 300; text.required = true; text.rows = 3;
  field(form, controls, "kind", "Kind", "text", KINDS.map((kind) => [kind, LABELS[kind]])).value = fields.kind || "other";
  field(form, controls, "status", "Plan status", "text", STATUSES.map((value) => [value, value])).value = fields.status || "intended";
  field(form, controls, "about", "Person or topic").value = fields.about || "";
  field(form, controls, "aliases", "Other names, separated by commas").value = (fields.aliases || []).join(", ");
  for (const [name, title] of [["due", "Due date"], ["occurs_at", "Event date"]]) {
    const value = fields[name] || "";
    field(form, controls, name, title, value.length === 7 ? "month" : "date").value = value;
  }
  for (const [name, title] of [["pinned", "Pin this note"], ["sensitive", "Sensitive: mention only when I bring it up"]]) {
    const input = field(form, controls, name, title, "checkbox"); input.checked = Boolean(fields[name]); input.parentElement.classList.add("check");
  }
  const sources = new Set([...(note?.source_event_ids || []), ...initialSources]);
  const sourceDetails = element("details", undefined, "source-picker");
  sourceDetails.append(element("summary", "Link original messages"), element("p", "Choose messages this note is based on. Unlinked notes stay clearly labelled.", "hint"));
  const select = element("select"); select.multiple = true; select.size = 5; select.setAttribute("aria-label", "Original messages");
  sourceDetails.append(select); form.append(sourceDetails);
  let loaded = false;
  for (const id of sources) { const option = element("option", `Linked message ${id}`); option.value = id; option.selected = true; select.append(option); }
  sourceDetails.addEventListener("toggle", async () => {
    if (!sourceDetails.open || loaded) return;
    try {
      const { items } = await timeline({ role: ["user", "assistant"] });
      const candidates = items.filter((item) => item.conversation_id !== NOTES);
      for (const message of candidates) {
        let option = [...select.options].find((value) => value.value === message.event_id);
        if (!option) { option = element("option"); option.value = message.event_id; select.append(option); }
        option.textContent = `${day(message.recorded_at)} · ${message.actor_role === "user" ? "You" : "Agent"}: ${message.text.slice(0, 130)}`;
        option.selected = sources.has(message.event_id);
      }
      loaded = true;
    } catch (error) { notify(error.message, true); }
  });
  const actions = element("div", undefined, "row editor-actions");
  const save = element("button", note?.event_id ? "Save correction" : "Add note");
  const cancel = element("button", "Cancel", "secondary"); cancel.type = "button";
  actions.append(save, cancel); form.append(actions);
  cancel.addEventListener("click", after);
  const updateStatus = () => { controls.status.parentElement.hidden = !["plan", "agent_commitment"].includes(controls.kind.value); };
  controls.kind.addEventListener("change", updateStatus); updateStatus();
  let requestId = uid(), previousBody = "";
  onSubmit(form, async () => {
    const body = { text: controls.text.value.trim(), kind: controls.kind.value, role: "user", producer: "membrain-memory-page",
      replaces: note?.event_id ? [note.event_id, ...merged.map((item) => item.event_id)] : [],
      source_event_ids: [...select.selectedOptions].map((option) => option.value) };
    for (const name of ["about", "due", "occurs_at"]) if (controls[name].value.trim()) body[name] = controls[name].value.trim();
    if (!controls.status.parentElement.hidden) body.status = controls.status.value;
    if (controls.aliases.value.trim()) body.aliases = controls.aliases.value.split(",").map((value) => value.trim()).filter(Boolean);
    body.pinned = controls.pinned.checked; body.sensitive = controls.sensitive.checked;
    const serialized = JSON.stringify(body);
    if (serialized !== previousBody) { requestId = uid(); previousBody = serialized; }
    await api(`/v1/brains/${encodeURIComponent(state.brain)}/notes`, { request_id: requestId, ...body });
    notify(note?.event_id ? "Note corrected. Your version takes precedence." : "Note added.");
    await after(); await loadServiceStatus();
  });
  return form;
}

function resetNoteForm() {
  $("note-form-host").replaceChildren(noteEditor(null, [], async () => {
    resetNoteForm(); $("add-note").open = false; await loadNotes();
  }));
}

async function showSources(container, ids) {
  container.replaceChildren(element("p", "Loading original messages…", "hint"));
  try {
    const { items } = await timeline({ role: ["user", "assistant", "operator", "tool"], event_id: ids });
    container.replaceChildren();
    if (items.length < ids.length) container.append(element("p", "Some original messages are no longer available.", "hint"));
    for (const message of items.reverse()) {
      const block = element("div", undefined, "message");
      block.append(element("div", `${day(message.recorded_at)} · ${message.actor_role === "user" ? "You" : "Agent"}`, "meta"), element("p", message.text));
      container.append(block);
    }
  } catch (error) { container.replaceChildren(element("p", error.message, "hint")); }
}

function noteItem(note, duplicate) {
  const item = element("article", undefined, "item");
  const parsed = typedNote(note.text), fields = parsed.fields;
  const meta = element("div", undefined, "meta");
  meta.append(element("span", day(note.recorded_at)));
  const imported = (note.producer || "").startsWith("membrain-import:") ? note.producer.slice(16) : null;
  meta.append(element("span", note.actor_role === "user" ? "written by you" : imported ? `imported from ${HOST_NAMES[imported] || imported}` : "saved by your agent", "tag"));
  for (const tag of parsed.tags) meta.append(element("span", tag, "tag"));
  const actions = element("span", undefined, "actions");
  const edit = element("button", "Edit", "link"), remove = element("button", "Delete", "link danger");
  actions.append(edit, remove); meta.append(actions); item.append(meta, element("p", parsed.sentence));
  const date = fields.due || fields.occurs_at;
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  if (date && openPlan(fields) && date < (date.length === 7 ? today.slice(0, 7) : today)) item.append(element("p", "Date has passed; completion is not confirmed.", "hint"));
  const ids = note.source_event_ids || [];
  if (note.pending_source_count) item.append(element("p", "Waiting for the source message to finish recording.", "hint"));
  if (ids.length) {
    const details = element("details"); details.append(element("summary", `${ids.length} original message${ids.length === 1 ? "" : "s"}`));
    const content = element("div"); details.append(content); item.append(details);
    let loaded = false;
    details.addEventListener("toggle", () => { if (details.open && !loaded) { loaded = true; showSources(content, ids); } });
  } else if (!note.pending_source_count) item.append(element("p", note.actor_role === "user" ? "Standalone note written by you." : "Original message not linked. Edit to attach its source.", "hint"));
  edit.addEventListener("click", () => { item.replaceChildren(noteEditor(note, [], () => show(currentView()))); item.querySelector("textarea").focus(); });
  if (duplicate) {
    const merge = element("button", "Merge duplicate", "link");
    merge.addEventListener("click", () => item.replaceChildren(noteEditor(note, [duplicate], () => show(currentView())))); actions.prepend(merge);
  }
  guard(remove, async () => {
    if (!confirm("Delete this note? Its content is erased.")) return;
    await forget("events", [note.event_id], "erase_content", NOTES); notify("Note deleted."); await show(currentView());
  });
  return item;
}

$("show-completed").addEventListener("change", () => loadNotes("plans"));
const currentView = () => $("tabs").querySelector("button.active")?.dataset.view || "notes";

// Conversations ----------------------------------------------------------------------------------------------

async function loadConversations(reset = true) {
  const response = await timeline({ role: ["user", "assistant"], limit: 100,
    ...(reset || !state.conversationCursor ? {} : { before_sequence: state.conversationCursor }) });
  state.conversations = reset ? response.items : [...state.conversations, ...response.items];
  state.conversationCursor = response.next_before_sequence;
  const items = state.conversations;
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
  if (state.conversationCursor) {
    const more = element("button", "Load older messages", "secondary");
    guard(more, () => loadConversations(false)); list.append(more);
  }
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
    const saveNote = element("button", "Save as note", "link");
    actions.prepend(saveNote);
    saveNote.addEventListener("click", () => {
      const editor = noteEditor({ text: message.text.slice(0, 300), source_event_ids: [message.event_id] }, [], loadConversations);
      block.replaceChildren(editor); editor.querySelector("textarea").focus();
    });
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

const INSTALL_COMMANDS = {
  "claude-code": "claude plugin install membrain --marketplace chriswu727/membrain",
  codex: "codex plugin marketplace add chriswu727/memBrain\ncodex plugin add membrain@membrain",
};
function setupCommand() { $("setup-command").textContent = INSTALL_COMMANDS[$("setup-host").value]; }
$("setup-host").addEventListener("change", setupCommand);
setupCommand();
guard($("copy-setup"), async () => {
  try { await navigator.clipboard.writeText($("setup-command").textContent); notify("Install command copied."); }
  catch { notify("Select and copy the install command above."); }
});
guard($("first-preference"), async () => {
  await show("notes"); $("add-note").open = true;
  const form = $("note-form-host").querySelector("form");
  form.elements.kind.value = "preference"; form.elements.kind.dispatchEvent(new Event("change"));
  form.elements.text.focus();
});
guard($("prepare-model"), async () => {
  await api("/v1/local/semantic-model", {});
  notify("Preparing recall by meaning. Keyword search keeps working; this page will update automatically.");
  await loadServiceStatus();
});

function showConnection(brain, local) {
  if (!local) { $("first-use").hidden = true; return; }
  $("first-use").hidden = false;
  const received = brain.recorded_messages > 0;
  $("connection-summary").textContent = received ? "Messages are arriving" : "Finish connecting your agent";
  if (!state.connectionShown) {
    $("first-use").open = !received; $("connect-agent").open = !received;
    if (brain.last_message_producer === "membrain-codex") $("setup-host").value = "codex";
    setupCommand(); state.connectionShown = true;
  }
  $("setup-service").textContent = local.ready ? "Running on this computer." : "Needs attention. Run membrain doctor for a diagnosis.";
  const producer = { "membrain-plugin": "Claude Code", "membrain-codex": "Codex", "membrain-hermes": "Hermes",
    "membrain-openclaw": "OpenClaw", "membrain-workbuddy": "WorkBuddy" }[brain.last_message_producer];
  $("setup-recording").textContent = received
    ? `${brain.recorded_messages} message(s) recorded${producer ? `; latest from ${producer}` : ""}${brain.last_message_at ? ` on ${day(brain.last_message_at)}` : ""}.`
    : "No agent messages received yet. Connect below, send a message and wait for its reply, then refresh.";
  const semantic = local.semantic_recall;
  $("setup-semantic").textContent = semantic === "ready"
    ? `Enabled. ${brain.embedding_pending ? `${brain.embedding_pending} memory item(s) still preparing.` : "Finds related wording across languages."}`
    : semantic === "downloading" ? "Downloading the local model (about 135 MB). Keyword search already works."
    : semantic === "download_failed" ? "The download failed. Check your connection and retry below; keyword search still works."
    : semantic === "restart_required" ? "The model is downloaded. Finish enabling it below."
    : semantic === "unavailable" ? "Local model support is missing. Reinstall using the documented semantic extra."
    : "Optional local model; no API key or usage charge. Keyword search already works.";
  for (const [id, ready] of [["setup-service", local.ready], ["setup-recording", received], ["setup-semantic", semantic === "ready"]]) {
    $(id).parentElement.dataset.ready = String(Boolean(ready));
  }
  $("prepare-model").hidden = ["ready", "downloading", "unavailable"].includes(semantic);
  $("prepare-model").textContent = semantic === "download_failed" ? "Retry model download"
    : semantic === "restart_required" ? "Finish enabling recall" : "Enable recall by meaning";
}

async function loadServiceStatus() {
  $("service-status").hidden = false;
  try {
    const [brain, local] = await Promise.all([
      api(`/v1/brains/${encodeURIComponent(state.brain)}/status`), api("/v1/local/memory-status").catch(() => null),
    ]);
    const lag = brain.index_lag;
    const semantic = local?.semantic_recall;
    $("service-heading").textContent = (local && !local.ready) || brain.failed_jobs ? "Memory service needs attention"
      : local?.pending_records ? "Messages waiting for delivery" : lag ? "Indexing your messages"
      : semantic === "ready" ? "Memory is ready" : "Keyword memory is ready";
    const recall = semantic === "ready" ? "Keyword and semantic recall enabled"
      : semantic === "downloading" ? "Keyword recall ready; semantic model downloading"
      : semantic === "download_failed" ? "Keyword recall ready; model download needs a retry"
      : semantic === "restart_required" ? "Keyword recall ready; semantic model needs activation"
      : local ? "Keyword recall ready; semantic model not enabled" : "Keyword recall available; local model status unknown";
    $("service-detail").textContent = `${recall}. ${lag ? `${lag} message(s) waiting for indexing.` : "Message index is up to date."} ${local?.pending_records ? `${local.pending_records} queued record(s).` : ""} ${brain.failed_jobs ? `${brain.failed_jobs} background job(s) need attention. Run membrain doctor.` : ""}`;
    showConnection(brain, local);
  } catch (error) {
    $("service-heading").textContent = "Unable to check memory";
    $("service-detail").textContent = `${error.message} Run membrain doctor for help.`;
  }
}
guard($("refresh-status"), loadServiceStatus);

const loaders = { notes: () => loadNotes(), people: () => loadNotes("people"), plans: () => loadNotes("plans"),
  conversations: loadConversations, search: async () => {}, privacy: loadRecording };

async function show(view) {
  for (const button of $("tabs").querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  for (const name of Object.keys(loaders)) $(`view-${name}`).hidden = name !== view;
  try { await loaders[view](); } catch (error) { notify(error.message, true); }
}
for (const button of $("tabs").querySelectorAll("button")) button.addEventListener("click", () => show(button.dataset.view));

async function open(key, brain) {
  state.key = key; state.brain = brain;
  if (!state.brain) state.brain = (await api("/v1/brains")).brains?.[0]?.id || "";
  if (!state.brain) throw new Error("No memory installation found. Run membrain plugin start first.");
  await api(`/v1/brains/${encodeURIComponent(state.brain)}/status`);
  try { sessionStorage.setItem("membrain-page", JSON.stringify({ key: state.key, brain: state.brain })); } catch { /* private window */ }
  $("signin").hidden = true; $("tabs").hidden = false;
  resetNoteForm();
  await Promise.all([loadRecording(), loadServiceStatus(), show("notes")]);
}
onSubmit($("key-form"), () => open($("key").value.trim(), ""));
setInterval(() => { if (state.key && !$("tabs").hidden) { loadServiceStatus(); loadRecording(); } }, 30000);

(async () => {
  const code = new URLSearchParams(location.search).get("code");
  if (code) {
    history.replaceState(null, "", "/memory");
    try {
      const session = await fetch("/v1/local/ui-session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
      const data = await session.json();
      if (!session.ok) throw new Error(data.detail || "This link has expired.");
      return await open(data.api_key, data.brain_id);
    } catch (error) { notify(error.message, true); }
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem("membrain-page") || "null");
    if (saved?.key) return await open(saved.key, saved.brain);
  } catch { try { sessionStorage.removeItem("membrain-page"); } catch { /* private window */ } }
  $("signin").hidden = false;
})();
