// memBrain for OpenClaw. The memory work happens in the memBrain runtime (installed with uv); this plugin maps
// OpenClaw's hooks onto `membrain plugin hook ... --host openclaw` and its tools onto `membrain plugin tool ...`.
import { spawn } from "node:child_process";

const PACKAGE = process.env.MEMBRAIN_PACKAGE ||
  "membrain-ai[embedded,semantic] @ https://github.com/chriswu727/membrain/archive/fae5f75eab3777fcc1eb478b4142c1cfd132e6fc.zip";
const BLOCK_MS = 5 * 60 * 1000;

function run(command, args, { input, timeoutMs = 12_000, env } = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, { env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "ignore"] });
    } catch {
      resolve(null);
      return;
    }
    let output = "";
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.on("error", () => { clearTimeout(timer); resolve(null); });
    child.on("close", (code) => { clearTimeout(timer); resolve(code === 0 ? output : null); });
    child.stdin.on("error", () => {});
    child.stdin.end(input === undefined ? "" : JSON.stringify(input));
  });
}

function membrain(args, options) {
  return run("uvx", ["--offline", "--python", "3.12", "--from", "membrain-ai", "membrain", "plugin", ...args], options);
}

function textOf(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.filter((part) => part && part.type === "text" && typeof part.text === "string")
      .map((part) => part.text).join("\n");
  }
  return "";
}

export function lastAssistantText(messages) {
  for (let index = (messages?.length ?? 0) - 1; index >= 0; index--) {
    const message = messages[index];
    if (message && message.role === "assistant") {
      const text = textOf(message.content);
      if (text.trim()) return text;
    }
  }
  return "";
}

const TOOLS = [
  {
    name: "membrain_note",
    description: "Save one short, lasting fact about the user (health, people, dates, places, preferences, decisions, " +
      "constraints, plans and their changes) so it is shown at the start of every later session. One fact per note, " +
      "under 300 characters, with dates when they matter. Set kind when it is clear: person (with about = their name, " +
      "aliases, and the relation in the text), constraint, preference, plan (with status intended until the user " +
      "confirms it is scheduled or done, and due or occurs_at as YYYY-MM-DD), routine, milestone, relationship, or " +
      "agent_commitment for something you promised to do. When a fact changes, save the updated note and pass the " +
      "old note's id in replaces. Do not save routine requests or anything the user asked you not to keep.",
    parameters: { type: "object", properties: {
      text: { type: "string" },
      replaces: { type: "array", items: { type: "string" } },
      source_event_ids: { type: "array", items: { type: "string" }, maxItems: 64 },
      source_messages: { type: "array", maxItems: 64, items: { type: "object", properties: {
        producer_ref: { type: "string" }, source_event_key: { type: "string" }, source_revision: { type: "integer", minimum: 1 },
      }, required: ["producer_ref", "source_event_key", "source_revision"] } },
      kind: { type: "string", enum: ["person", "constraint", "preference", "plan", "routine", "milestone",
        "relationship", "agent_commitment", "other"] },
      about: { type: "string" },
      aliases: { type: "array", items: { type: "string" } },
      status: { type: "string", enum: ["intended", "scheduled", "done", "cancelled", "superseded"] },
      due: { type: "string", description: "YYYY-MM-DD" },
      occurs_at: { type: "string", description: "YYYY-MM-DD" },
      pinned: { type: "boolean" },
      sensitive: { type: "boolean" },
    }, required: ["text"] },
  },
  {
    name: "membrain_search",
    description: "Search the user's earlier conversations and notes by a few concrete keywords (names, places, " +
      "activities, dates) when a request depends on personal context that is not already shown.",
    parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
  },
  {
    name: "membrain_pause",
    description: "Stop recording, for example when the user asks you not to remember something. The current " +
      "exchange is not stored either. Without hours it lasts until membrain_resume.",
    parameters: { type: "object", properties: { hours: { type: "number" } } },
  },
  {
    name: "membrain_resume",
    description: "Resume recording after membrain_pause, when the user asks.",
    parameters: { type: "object", properties: {} },
  },
];

export function createMembrain({ exec = membrain, now = () => Date.now(), directory } = {}) {
  const env = directory ? { MEMBRAIN_DIRECTORY: directory } : undefined;
  const blocks = new Map();
  const hook = async (event, payload) => {
    const output = await exec(["hook", event, "--host", "openclaw"], { input: payload, env });
    if (!output) return null;
    try {
      return JSON.parse(output).hookSpecificOutput?.additionalContext || null;
    } catch {
      return null;
    }
  };
  return {
    async beforePromptBuild(event, ctx) {
      const session = ctx?.sessionId || ctx?.sessionKey;
      if (!session) return undefined;
      let cached = blocks.get(session);
      if (!cached || now() - cached.at > BLOCK_MS) {
        cached = { at: now(), block: await hook("session-start", { session_id: session }) };
        blocks.set(session, cached);
      }
      const prompt = event?.currentUserMessage ?? event?.prompt ?? "";
      const recalled = prompt.trim()
        ? await hook("user-prompt", { session_id: session, prompt_id: event?.currentUserMessageId, prompt })
        : null;
      const result = {};
      if (cached.block) result.appendSystemContext = cached.block;
      if (recalled) result.prependContext = recalled;
      return Object.keys(result).length ? result : undefined;
    },
    async agentEnd(event, ctx) {
      const session = ctx?.sessionId || ctx?.sessionKey;
      const reply = lastAssistantText(event?.messages);
      if (!session || !event?.success || !reply.trim()) return;
      await hook("stop", { session_id: session, last_assistant_message: reply });
    },
    async tool(name, params) {
      const output = await exec(["tool", name.replace("membrain_", "")], { input: params ?? {}, env,
        timeoutMs: 20_000 });
      if (name === "membrain_note" || name === "membrain_pause" || name === "membrain_resume") blocks.clear();
      return output || JSON.stringify({ error: "memBrain is not available yet; it may still be installing." });
    },
  };
}

function installRuntime(directory) {
  // Install or update the runtime and start the local service in the background, like the Claude Code plugin.
  run("uv", ["tool", "install", "--quiet", "--python", "3.12", PACKAGE], { timeoutMs: 15 * 60 * 1000 })
    .then(() => membrain(["start"], { timeoutMs: 10 * 60 * 1000,
      env: directory ? { MEMBRAIN_DIRECTORY: directory } : undefined }));
}

export function definePlugin({ install = installRuntime, exec } = {}) {
  return {
    id: "membrain",
    name: "memBrain",
    description: "Local long-term memory with notes, recall and recording.",
    register(api) {
      const directory = api.pluginConfig?.directory || undefined;
      const memory = createMembrain({ directory, ...(exec ? { exec } : {}) });
      install(directory);
      api.on("before_prompt_build", (event, ctx) => memory.beforePromptBuild(event, ctx));
      api.on("agent_end", (event, ctx) => memory.agentEnd(event, ctx));
      for (const spec of TOOLS) {
        api.registerTool({
          ...spec,
          label: spec.name,
          async execute(_id, params) {
            return { content: [{ type: "text", text: await memory.tool(spec.name, params) }] };
          },
        });
      }
    },
  };
}

export default definePlugin();
