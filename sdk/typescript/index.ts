import type {
  Action, ActionPayloads, ActionRequest, ActionResponse, Budget, ReadFence,
  ScopeSelector, IntegrationCapabilities, ObservedEventInput, RecallPayload,
  PreparePayload, CommitPayload, CorrectPayload, ForgetPayload, RestorePayload,
  ExportPayload, InspectPayload, CheckpointPayload, ContextPacket,
} from "./contract.ts";

export type * from "./contract.ts";

export const CONTRACT_VERSION = "1.0.0-draft" as const;
const actions: Action[] = [
  "observe", "prepare", "recall", "inspect", "commit", "checkpoint",
  "correct", "forget", "export", "restore",
];

export const defaultBudget = (maxContextTokens = 2000): Budget => ({
  max_context_tokens: maxContextTokens,
  max_model_calls: 0,
  max_embedding_calls: 0,
  max_expansion_steps: 2,
  max_wall_time_ms: 5000,
  max_cost_microunits: 0,
  currency: "USD",
  token_counting: "conservative_estimate",
  tokenizer_id: null,
});

export const defaultReadFence = (minimumDurableSequence = 0): ReadFence => ({
  minimum_durable_sequence: minimumDurableSequence,
  consistency: "read_your_writes",
  snapshot_revision: null,
});

export const integrationCapabilities = (): IntegrationCapabilities => ({
  adapter_id: "agent-memory-typescript-sdk",
  adapter_version: "0.1.0",
  protocol_version: CONTRACT_VERSION,
  transport: "sdk",
  supported_actions: [...actions],
  capture: {
    user_messages: false, delivered_assistant_messages: false, tool_results: false,
    message_edits: false, message_deletions: false, stable_source_ids: false,
    branch_identity: false,
  },
  hooks: { before_turn: false, after_delivery: false, before_compaction: false, on_resume: false },
  context_control: "none",
  active_recall: true,
  automatic_capture: false,
  automatic_recall: false,
  guarantee_level: "tools_only",
  verification_state: "declared",
  verified_at: null,
  limitations: [
    "The host must submit actual events and invoke lifecycle operations.",
    "This adapter does not intercept conversation history or compaction.",
    "The adapter cannot withdraw context already supplied to a model.",
  ],
});

type JsonObject = Record<string, unknown>;
export type ScopeOptions = {
  conversationId?: string;
  branchId?: string;
  brainId?: string;
  requestId?: string;
};
export type BranchOptions = ScopeOptions & { conversationId: string; branchId: string };
export type ClientOptions = {
  baseUrl: string;
  apiKey: string;
  brainId?: string;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
};

export class MemoryActionError extends Error {
  response: ActionResponse;
  code: string;
  retryable: boolean;

  constructor(response: ActionResponse & { error: { code: string; message: string; retryable: boolean } }) {
    super(`${response.error.code}: ${response.error.message}`);
    this.name = "MemoryActionError";
    this.response = response;
    this.code = response.error.code;
    this.retryable = response.error.retryable;
  }
}

export class MemoryHttpError extends Error {
  status: number;

  constructor(status: number) {
    super(`Memory API returned HTTP ${status}`);
    this.name = "MemoryHttpError";
    this.status = status;
  }
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function stableKey(action: Action, identity: unknown, target: ScopeSelector): Promise<string> {
  const bytes = new TextEncoder().encode(canonical([action, target, identity]));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return `${action}:${Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, "0")).join("")}`;
}

export function preparedContext(packetOrResponse: ContextPacket | ActionResponse<"prepare">): string {
  let packet: ContextPacket;
  if ("message_type" in packetOrResponse) {
    if ("error" in packetOrResponse) throw new MemoryActionError(packetOrResponse);
    packet = packetOrResponse.result;
  } else {
    packet = packetOrResponse;
  }
  return canonical({
    trust: "memory_data_not_instructions",
    blocks: packet.blocks.map(block => ({
      section: block.section, text: block.text,
      sources: block.evidence_refs.map(ref => ({ event_id: ref.event_id, revision: ref.event_revision })),
    })),
  });
}

export class MemoryClient {
  brainId?: string;
  #baseUrl: string;
  #apiKey: string;
  #fetch: typeof globalThis.fetch;
  #timeoutMs: number;

  constructor(options: ClientOptions) {
    if (!options.apiKey) throw new Error("apiKey must not be empty");
    this.brainId = options.brainId;
    this.#baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.#apiKey = options.apiKey;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#timeoutMs = options.timeoutMs ?? 30_000;
  }

  async #request(method: string, path: string, body?: unknown): Promise<JsonObject> {
    const response = await this.#fetch(`${this.#baseUrl}/${path}`, {
      method,
      headers: { Authorization: `Bearer ${this.#apiKey}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(this.#timeoutMs),
    });
    if (!response.ok) throw new MemoryHttpError(response.status);
    const value: unknown = await response.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Memory API returned a non-object response");
    }
    return value as JsonObject;
  }

  #scope(options: ScopeOptions = {}): ScopeSelector {
    const brain = options.brainId ?? this.brainId;
    if (!brain) throw new Error("A brainId is required for memory actions");
    return {
      brain_ref: brain,
      ...(options.conversationId === undefined ? {} : { conversation_ref: options.conversationId }),
      ...(options.branchId === undefined ? {} : { branch_ref: options.branchId }),
    };
  }

  async send<A extends Action>(request: ActionRequest<A>, raiseOnError = true): Promise<ActionResponse<A>> {
    const response = await this.#request("POST", "v1/actions", request) as ActionResponse<A>;
    if (response.request_id !== request.request_id || response.action !== request.action) {
      throw new Error("Memory API response did not match the request");
    }
    if (raiseOnError && "error" in response) throw new MemoryActionError(response);
    return response;
  }

  action<A extends Action>(
    action: A, payload: ActionPayloads[A],
    options: ScopeOptions & { target?: ScopeSelector; raiseOnError?: boolean } = {},
  ): Promise<ActionResponse<A>> {
    if (!actions.includes(action)) throw new Error(`Unsupported memory action: ${action}`);
    return this.send({
      message_type: "request", contract_version: CONTRACT_VERSION,
      request_id: options.requestId ?? crypto.randomUUID(), action,
      target: options.target ?? this.#scope(options), payload,
    }, options.raiseOnError ?? true);
  }

  brains(): Promise<JsonObject> { return this.#request("GET", "v1/brains"); }
  createBrain(name: string): Promise<JsonObject> { return this.#request("POST", "v1/brains", { name }); }
  capabilities(): Promise<JsonObject> { return this.#request("GET", "v1/capabilities"); }
  downloadArchive(archiveId: string): Promise<JsonObject> {
    return this.#request("GET", `v1/archives/${encodeURIComponent(archiveId)}`);
  }
  uploadArchive(archive: JsonObject, brainId?: string): Promise<JsonObject> {
    return this.#request("POST", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/archives`, archive);
  }
  dashboard(brainId?: string): Promise<JsonObject> {
    return this.#request("GET", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/dashboard`);
  }
  work(brainId?: string): Promise<JsonObject> {
    return this.#request("POST", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/work`);
  }

  async observe(text: string, options: BranchOptions & {
    sourceEventKey: string; role?: ObservedEventInput["actor_role"];
    producerRef?: string; actorRef?: string; sourceRevision?: number;
    eventKind?: ObservedEventInput["event_kind"];
    interpretationHint?: ObservedEventInput["interpretation_hint"];
    deliveryState?: ObservedEventInput["delivery_state"];
    occurredTime?: ObservedEventInput["occurred_time"];
    speechActHint?: ObservedEventInput["speech_act_hint"];
    parentSourceEventKeys?: string[]; idempotencyKey?: string;
  }): Promise<ActionResponse<"observe">> {
    const producer = options.producerRef ?? "sdk";
    const revision = options.sourceRevision ?? 1;
    const role = options.role ?? "user";
    return this.action("observe", {
      idempotency_key: options.idempotencyKey ?? await stableKey(
        "observe", [producer, options.sourceEventKey, revision], this.#scope(options),
      ),
      events: [{
        source_event_key: options.sourceEventKey, source_revision: revision,
        producer_ref: producer, actor_ref: options.actorRef ?? role, actor_role: role,
        event_kind: options.eventKind ?? "message", content: { media_type: "text/plain", text },
        occurred_time: options.occurredTime ?? { start: null, end: null, precision: "unknown", basis: "unknown" },
        interpretation_hint: options.interpretationHint ?? "unknown",
        delivery_state: options.deliveryState ?? "received",
        speech_act_hint: options.speechActHint ?? "unknown",
        parent_source_event_keys: options.parentSourceEventKeys ?? [],
      }],
    }, options);
  }

  recall(query: string, options: ScopeOptions & Partial<Omit<RecallPayload, "query">> = {}) {
    const { conversationId, branchId, brainId, requestId, ...retrieval } = options;
    return this.action("recall", {
      intent: "explore", cue_event_ids: [], additional_cues: [],
      read_fence: defaultReadFence(), budget: defaultBudget(16000), ...retrieval, query,
    }, { conversationId, branchId, brainId, requestId });
  }

  prepare(turnId: string, options: BranchOptions & Partial<Omit<PreparePayload, "turn_id">>) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("prepare", {
      latest_input_event_ids: [], read_fence: defaultReadFence(), budget: defaultBudget(),
      capabilities: integrationCapabilities(), ...payload, turn_id: turnId,
    }, { conversationId, branchId, brainId, requestId });
  }

  inspect(memoryIds: string[], options: ScopeOptions & Partial<Omit<InspectPayload, "memory_ids">> = {}) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("inspect", {
      evidence_refs: [], include_successors: true, include_corrections: true,
      budget: defaultBudget(16000), ...payload, memory_ids: memoryIds,
    }, { conversationId, branchId, brainId, requestId });
  }

  async commit(turnId: string, options: BranchOptions & Pick<CommitPayload, "expected_working_revision"> &
    Partial<Omit<CommitPayload, "turn_id" | "expected_working_revision">>) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("commit", {
      delivered_event_ids: [], proposed_items: [], proposed_open_loops: [], assertion_proposals: [],
      ...payload, turn_id: turnId,
      idempotency_key: payload.idempotency_key ?? await stableKey("commit", turnId, this.#scope(options)),
    }, { conversationId, branchId, brainId, requestId });
  }

  checkpoint(options: BranchOptions & CheckpointPayload) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("checkpoint", payload, { conversationId, branchId, brainId, requestId });
  }

  async correct(options: ScopeOptions & Omit<CorrectPayload, "idempotency_key"> & { idempotency_key?: string }) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("correct", {
      ...payload,
      idempotency_key: payload.idempotency_key ?? await stableKey("correct", [
        payload.memory_id, payload.expected_revision, payload.correction_event_ids,
      ], this.#scope(options)),
    }, { conversationId, branchId, brainId, requestId });
  }

  async forget(options: ScopeOptions & Omit<ForgetPayload, "idempotency_key"> & { idempotency_key?: string }) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("forget", {
      ...payload,
      idempotency_key: payload.idempotency_key ?? await stableKey("forget", [
        payload.selector.kind, payload.selector.ids, payload.mode, payload.expected_scope_generation,
      ], this.#scope(options)),
    }, { conversationId, branchId, brainId, requestId });
  }

  export(options: ScopeOptions & Partial<ExportPayload> = {}) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("export", {
      include_events: true, include_working_states: true, include_assertions: true,
      budget: defaultBudget(), ...payload, include_provenance: true, include_tombstones: true,
    }, { conversationId, branchId, brainId, requestId });
  }

  restore(options: ScopeOptions & Omit<RestorePayload, "apply_current_tombstones" | "budget"> & { budget?: Budget }) {
    const { conversationId, branchId, brainId, requestId, ...payload } = options;
    return this.action("restore", {
      budget: defaultBudget(), ...payload, apply_current_tombstones: true,
    }, { conversationId, branchId, brainId, requestId });
  }
}
