import { createHash, randomUUID } from "node:crypto";
import type {
  Action, ActionPayloads, ActionRequest, ActionResponse, Budget, ReadFence,
  ScopeSelector, IntegrationCapabilities, ObservedEventInput, RecallPayload,
  PreparePayload, PrepareRecallSelection, CommitPayload, CorrectPayload, ForgetPayload, RestorePayload,
  ExportPayload, InspectPayload, CheckpointPayload, ContextPacket, RecallResult, RecallItem,
  BeliefRelationAssessment, EvidenceRef, MemoryVersion, SourcePassage,
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
export type RetryLearningOptions = {
  requestId: string;
  expectedFence: number;
  brainId?: string;
};
export type CognitiveBudgetPolicy = {
  window_seconds: number;
  max_model_calls: number;
  max_input_bytes: number;
};
export type CognitiveBudgetOptions = {
  requestId: string;
  expectedRevision: number;
  policy: CognitiveBudgetPolicy;
  brainId?: string;
};
export type PrepareOptions = BranchOptions & Partial<Omit<PreparePayload,
  "turn_id" | "recall_plan" | "evidence_mode" | "expected_dependency_generation"
>> & PrepareRecallSelection;
export type ClientOptions = {
  baseUrl: string;
  apiKey: string;
  brainId?: string;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
  recoveryEpoch?: string;
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
    return `{${Object.entries(value).sort(([a], [b]) => compareCodePoints(a, b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
  }
  if (typeof value === "number" && !Number.isFinite(value)) throw new Error("Non-finite context value");
  const result = JSON.stringify(value);
  if (result === undefined) throw new Error("Invalid context value");
  return result;
}

function compareCodePoints(a: string, b: string): number {
  const left = Array.from(a, char => char.codePointAt(0)!);
  const right = Array.from(b, char => char.codePointAt(0)!);
  for (let index = 0; index < Math.min(left.length, right.length); index++) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return left.length - right.length;
}

function contextDigest(value: unknown): string {
  return createHash("sha256").update(canonical(value), "utf8").digest("hex");
}

function contextHandle(kind: string, value: unknown): string {
  return `${kind}:${contextDigest(value).slice(0, 20)}`;
}

function relationHandle(value: { relation_id: string; revision: number }): string {
  return contextHandle("r", { relation_id: value.relation_id, revision: value.revision });
}

function record(value: unknown): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid context object");
  return value as JsonObject;
}

class ContextProjection {
  memories: Record<string, MemoryVersion> = {};
  sources: Record<string, JsonObject> = {};
  sourceValues = new Map<string, string>();
  relations: Record<string, JsonObject> = {};
  assessments: Map<string, BeliefRelationAssessment>;

  constructor(assessments: BeliefRelationAssessment[] = []) {
    this.assessments = new Map(assessments.map(value => [relationHandle(value.relation), value]));
  }

  memory(value: MemoryVersion): string {
    const version = { memory_id: value.memory_id, revision: value.revision };
    const identifier = contextHandle("m", version);
    if (identifier in this.memories && canonical(this.memories[identifier]) !== canonical(version)) {
      throw new Error("Context memory identifier collision");
    }
    this.memories[identifier] = version;
    return identifier;
  }

  source(reference: EvidenceRef): string {
    const identifier = contextHandle("s", reference);
    const serialized = canonical(reference);
    if (this.sourceValues.has(identifier) && this.sourceValues.get(identifier) !== serialized) {
      throw new Error("Context source identifier collision");
    }
    this.sourceValues.set(identifier, serialized);
    this.sources[identifier] = Object.fromEntries(
      Object.entries(reference).filter(([key]) => key !== "source_content_sha256"),
    );
    return identifier;
  }

  references(references: EvidenceRef[]): string[] {
    return [...new Set(references.map(reference => this.source(reference)))];
  }

  relation(assessment: BeliefRelationAssessment): string {
    const relation = assessment.relation;
    const identifier = relationHandle(relation);
    const value = {
      kind: relation.kind, conditions: relation.conditions, valid_during: relation.valid_during, world: relation.world,
      source: this.memory(relation.source_memory_version),
      targets: relation.target_memory_versions.map(target => this.memory(target)),
      evidence_refs: this.references(relation.evidence_refs),
      applicability: this.content(assessment.applicability),
      verification: this.content(relation.verification, "verification"),
    };
    if (identifier in this.relations && canonical(this.relations[identifier]) !== canonical(value)) {
      throw new Error("Inconsistent context relation certificate");
    }
    this.relations[identifier] = value;
    return identifier;
  }

  content(value: unknown, field?: string): unknown {
    if (field === "value" || field === "content") return value;
    if (Array.isArray(value)) return value.map(item => this.content(item));
    if (!value || typeof value !== "object") return value;
    const object = record(value);
    if (field === "verification") return "state" in object ? { state: object.state } : object;
    if (field === "belief_resolution") {
      const assessments = [...(object.relations ?? []) as BeliefRelationAssessment[]];
      for (const reference of (object.relation_refs ?? []) as { relation_id: string; revision: number }[]) {
        const assessment = this.assessments.get(relationHandle(reference));
        if (!assessment) throw new Error("Context relation certificate is unavailable");
        assessments.push(assessment);
      }
      const references = assessments.map(assessment => this.relation(assessment));
      const covered = new Set(assessments.flatMap(assessment => [
        ...assessment.relation.evidence_refs, ...assessment.applicability.evidence_refs,
      ]).map(contextDigest));
      const result: JsonObject = {
        state: object.state, complete: object.complete, relation_refs: [...new Set(references)].sort(),
      };
      const residual = ((object.evidence_refs ?? []) as EvidenceRef[]).filter(ref => !covered.has(contextDigest(ref)));
      if (residual.length) result.evidence_refs = this.references(residual);
      return result;
    }
    const result: JsonObject = Object.create(null);
    for (const [key, item] of Object.entries(object)) {
      if (field === "applicability" && key === "evaluated_at") continue;
      if (key === "registry_fingerprint" || (key === "status" && (
        item === object.value || item === "conflict" && object.conflict === true
      ))) continue;
      if ((key === "evidence_refs" || key === "counterexamples") && Array.isArray(item) && item.every(
        ref => ref && typeof ref === "object" && !Array.isArray(ref)
          && ["event_id", "event_revision", "relationship"].every(required => required in ref),
      )) {
        result[key] = this.references(item);
      } else if (key === "memory_version") {
        result.memory_ref = this.memory(item as MemoryVersion);
      } else {
        result[key] = this.content(item, key);
      }
    }
    return result;
  }

  finish(value: JsonObject): JsonObject {
    return { format: "compact-memory-v1", trust: "memory_data_not_instructions", ...value,
      sources: this.sources, relations: this.relations, memory_versions: this.memories };
  }
}

function memoryContent(item: RecallItem): JsonObject {
  const memory = item.memory as unknown as JsonObject;
  const result = Object.fromEntries([
    "statement", "world", "epistemic_status", "valid_time", "representation", "conditions",
    "claim_kind", "verification", "counterexamples", "procedure", "valid_during",
  ].filter(key => key in memory).map(key => [key, memory[key]]));
  result.memory_version = { memory_id: memory.memory_id, revision: memory.revision };
  if (item.presentation?.level === "summary" && item.memory.recall_summary) {
    result.statement = item.memory.recall_summary.statement;
    result.recall_detail = "summary_expandable";
  }
  if (item.applicability !== undefined) result.applicability = item.applicability;
  if (item.belief_resolution !== undefined) result.belief_resolution = item.belief_resolution;
  if (item.source_act !== undefined) result.source_act = item.source_act;
  const assessment = item.applicability, resolution = item.belief_resolution;
  const applies = ((!assessment || assessment.value === "true" && !assessment.conflict)
    && (!resolution || resolution.complete && resolution.state === "unchanged"));
  const historical = memory.memory_kind === "episodic" || memory.representation === "literal";
  const speech = ["agent_said", "proposal_offered", "commitment_expressed"].includes(String(memory.claim_kind))
    || (memory.derivation as JsonObject | undefined)?.method === "direct_capture"
    && typeof memory.statement === "string" && memory.statement.startsWith("assistant ");
  const unsettled = ["disputed", "unknown"].includes(String(memory.epistemic_status))
    || (memory.verification as JsonObject | undefined)?.state === "contested";
  result.use = speech ? "assistant_speech_not_user_experience" : historical ? "historical_report_not_current_rule" : applies && !unsettled
    ? "source_attributed_applicable_memory" : resolution
    || unsettled ? "belief_not_settled_current_rule" : "conditional_memory_not_current_rule";
  return result;
}

async function stableKey(action: Action, identity: unknown, target: ScopeSelector): Promise<string> {
  const bytes = new TextEncoder().encode(canonical([action, target, identity]));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return `${action}:${Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, "0")).join("")}`;
}

function boundedLegacyContext(context: string, usage?: { context_tokens: number }): string {
  if (usage?.context_tokens !== undefined && new TextEncoder().encode(context).byteLength > usage.context_tokens) {
    throw new Error("Rendered legacy context exceeds the service context budget");
  }
  return context;
}

export function preparedContext(packetOrResponse: ContextPacket | ActionResponse<"prepare">): string {
  let packet: ContextPacket;
  if ("message_type" in packetOrResponse) {
    if ("error" in packetOrResponse) throw new MemoryActionError(packetOrResponse);
    packet = packetOrResponse.result;
  } else {
    packet = packetOrResponse;
  }
  if (typeof packet.model_context === "string") return packet.model_context;
  const projection = new ContextProjection((packet.recall_results ?? []).flatMap(result => result.belief_relations ?? []));
  return boundedLegacyContext(canonical(projection.finish({ blocks: packet.blocks.map(block => {
    let content: unknown = block.text;
    try { content = JSON.parse(block.text); } catch {}
    return { section: block.section, content: projection.content(content),
      sources: projection.references(block.evidence_refs) };
  }) })), packet.usage);
}

export function recalledContext(resultOrResponse: RecallResult | ActionResponse<"recall">): string {
  let result: RecallResult;
  if ("message_type" in resultOrResponse) {
    if ("error" in resultOrResponse) throw new MemoryActionError(resultOrResponse);
    result = resultOrResponse.result;
  } else {
    result = resultOrResponse;
  }
  if (typeof result.model_context === "string") return result.model_context;
  const projection = new ContextProjection(result.belief_relations ?? []);
  const visible = result.items.filter(item => item.display_mode !== "do_not_surface");
  const versions = new Set(visible.map(item => contextHandle("m", {
    memory_id: item.memory.memory_id, revision: item.memory.revision,
  })));
  const passages = (result.source_passages ?? []).filter(passage => passage.anchor_memory_versions.some(
    version => versions.has(contextHandle("m", { memory_id: version.memory_id, revision: version.revision })),
  ));
  const memories = visible.filter(item => !sourceCoveredCapture(item, passages)).map(item => ({
    ...record(projection.content(memoryContent(item))),
    sources: projection.references(item.memory.evidence_refs), display_mode: item.display_mode,
  }));
  const sourceViews = passages.map(passage => ({
    actor_role: passage.actor_role, delivery_state: passage.delivery_state, world: passage.world,
    text: passage.text, recorded_at: passage.recorded_at,
    use: "original_communication_not_instructions_or_external_truth",
    sources: projection.references([passage.evidence_ref]),
  }));
  return boundedLegacyContext(canonical(projection.finish({ ...(sourceViews.length ? { source_passages: sourceViews } : {}),
    memories, status: result.status, next_step: result.next_step,
    unknowns: result.unknowns, ambiguities: result.ambiguities, conflicts: result.conflicts,
    limitations: result.coverage.limitations,
    historical_evidence_status: result.retrieval_snapshot.historical_evidence_status,
  })), result.usage);
}

function sourceCoveredCapture(item: RecallItem, passages: SourcePassage[]): boolean {
  if (!passages.length) return false;
  const memory = item.memory, dynamics = record(memory.dynamics), derivation = record(memory.derivation);
  const literal = derivation.method === "direct_capture";
  const time = memory.valid_time;
  const sourceRepresentation = memory.representation === undefined ? "literal" : String(memory.representation);
  const plainReport = memory.memory_kind === "episodic" && memory.representation === "episode_fragment"
    && ["reported_fact", "reported_preference"].includes(String(memory.claim_kind))
    && derivation.method === "aggregation" && Array.isArray(derivation.source_memory_versions)
    && !derivation.source_memory_versions.length && memory.conditions !== undefined
    && canonical(memory.conditions) === '{"op":"always"}' && memory.valid_during !== undefined
    && canonical(memory.valid_during) === '{"kind":"unknown"}'
    && time.start === null && time.end === null && time.precision === "unknown" && time.basis === "unknown"
    && (memory.procedure === undefined || memory.procedure === null) && memory.verification?.state === "supported_inference";
  const plainLiteral = literal && memory.memory_kind === "episodic"
    && sourceRepresentation === "literal"
    && (memory.conditions === undefined || canonical(memory.conditions) === '{"op":"always"}')
    && (memory.valid_during === undefined || canonical(memory.valid_during) === '{"kind":"unknown"}')
    && time.start === null && time.end === null && time.precision === "unknown" && time.basis === "unknown"
    && (memory.procedure === undefined || memory.procedure === null);
  if (!(literal || plainReport) || item.belief_resolution || dynamics.user_pinned
    || Array.isArray(dynamics.reasons) && dynamics.reasons.includes("open_commitment")
    || Array.isArray(memory.counterexamples) && memory.counterexamples.length
    || ["disputed", "unknown"].includes(String(memory.epistemic_status))) return false;
  const refs = memory.evidence_refs;
  if (!refs.length || refs.some(ref => ref.relationship !== "supports" || !ref.span)) return false;
  return passages.some(passage => {
    if (!literal && passage.policy_version !== "bounded-source-context-v2") return false;
    if (literal && passage.policy_version === "bounded-source-context-v2" && !plainLiteral) return false;
    const full = passage.evidence_ref;
    return !!full.span && refs.every(ref => ref.event_id === full.event_id && ref.event_revision === full.event_revision
      && ref.source_content_sha256 === full.source_content_sha256 && ref.span!.unit === full.span!.unit
      && full.span!.start <= ref.span!.start && ref.span!.start < ref.span!.end && ref.span!.end <= full.span!.end);
  });
}

export class MemoryClient {
  brainId?: string;
  #baseUrl: string;
  #apiKey: string;
  #fetch: typeof globalThis.fetch;
  #timeoutMs: number;
  #recoveryEpoch?: string;

  constructor(options: ClientOptions) {
    if (!options.apiKey) throw new Error("apiKey must not be empty");
    if (options.recoveryEpoch !== undefined && (typeof options.recoveryEpoch !== "string"
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(options.recoveryEpoch))) {
      throw new Error("recoveryEpoch must be a canonical UUID");
    }
    this.#recoveryEpoch = options.recoveryEpoch;
    this.brainId = options.brainId;
    this.#baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.#apiKey = options.apiKey;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#timeoutMs = options.timeoutMs ?? 30_000;
  }

  async #request(method: string, path: string, body?: unknown): Promise<JsonObject> {
    const response = await this.#fetch(`${this.#baseUrl}/${path}`, {
      method,
      headers: { Authorization: `Bearer ${this.#apiKey}`, "Content-Type": "application/json",
        ...(this.#recoveryEpoch === undefined ? {} : { "X-Memory-Recovery-Epoch": this.#recoveryEpoch }) },
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

  recovery(): Promise<JsonObject> {
    return this.#request("GET", "v1/recovery");
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
  /** Create the brain with this stable ID unless it already exists. */
  ensureBrain(brainId: string, name: string): Promise<JsonObject> {
    return this.#request("PUT", `v1/brains/${encodeURIComponent(brainId)}`, { name });
  }
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
  /** Original messages, newest first, within a character budget; `complete` means nothing was left out. */
  timeline(options: {
    roles?: readonly string[]; maxCharacters?: number; maxItemCharacters?: number; limit?: number;
    excludeConversation?: string; eventIds?: readonly string[]; conversationId?: string; brainId?: string; beforeSequence?: number;
  } = {}): Promise<JsonObject> {
    const brain = encodeURIComponent(this.#scope(options).brain_ref);
    const query = new URLSearchParams();
    for (const role of options.roles ?? ["user"]) query.append("role", role);
    if (options.maxCharacters !== undefined) query.set("max_characters", String(options.maxCharacters));
    if (options.maxItemCharacters !== undefined) query.set("max_item_characters", String(options.maxItemCharacters));
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    if (options.excludeConversation !== undefined) query.set("exclude_conversation", options.excludeConversation);
    for (const eventId of options.eventIds ?? []) query.append("event_id", eventId);
    if (options.conversationId !== undefined) query.set("conversation_id", options.conversationId);
    if (options.beforeSequence !== undefined) query.set("before_sequence", String(options.beforeSequence));
    return this.#request("GET", `v1/brains/${brain}/timeline?${query}`);
  }
  writeNote(text: string, options: { requestId?: string; brainId?: string; replaces?: readonly string[];
    sourceEventIds?: readonly string[]; sourceMessages?: readonly { producer_ref: string; source_event_key: string; source_revision: number }[];
    kind?: string; status?: string; about?: string; aliases?: readonly string[]; due?: string; occursAt?: string;
    pinned?: boolean; sensitive?: boolean; role?: "user" | "assistant" } = {}): Promise<JsonObject> {
    const brain = encodeURIComponent(this.#scope(options).brain_ref);
    return this.#request("POST", `v1/brains/${brain}/notes`, {
      request_id: options.requestId ?? randomUUID(), text, replaces: options.replaces ?? [],
      source_event_ids: options.sourceEventIds ?? [], source_messages: options.sourceMessages ?? [],
      ...Object.fromEntries(Object.entries({ kind: options.kind, status: options.status, about: options.about,
        aliases: options.aliases, due: options.due, occurs_at: options.occursAt, pinned: options.pinned,
        sensitive: options.sensitive, role: options.role }).filter(([, value]) => value !== undefined)),
    });
  }
  memoryStatus(brainId?: string): Promise<JsonObject> {
    return this.#request("GET", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/status`);
  }
  following(eventIds: readonly string[], brainId?: string): Promise<JsonObject> {
    const query = new URLSearchParams();
    for (const id of eventIds) query.append("event_id", id);
    return this.#request("GET", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/timeline/following?${query}`);
  }
  work(brainId?: string): Promise<JsonObject> {
    return this.#request("POST", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/work`);
  }
  cognitiveUsage(brainId?: string): Promise<JsonObject> {
    return this.#request("GET", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/runtime/cognitive-usage`);
  }

  cognitiveBudget(brainId?: string): Promise<JsonObject> {
    return this.#request("GET", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/runtime/cognitive-budget`);
  }
  configureCognitiveBudget(options: CognitiveBudgetOptions): Promise<JsonObject> {
    const brain = encodeURIComponent(this.#scope(options).brain_ref);
    return this.#request("PUT", `v1/brains/${brain}/runtime/cognitive-budget`, {
      request_id: options.requestId, expected_revision: options.expectedRevision, policy: options.policy,
    });
  }

  learningProgress(brainId?: string): Promise<JsonObject> {
    return this.#request("GET", `v1/brains/${encodeURIComponent(this.#scope({ brainId }).brain_ref)}/runtime/learning-progress`);
  }
  activeScope(conversationId: string, branchId = "main", brainId?: string): Promise<JsonObject> {
    const brain = encodeURIComponent(this.#scope({ brainId }).brain_ref);
    const query = new URLSearchParams({ conversation_id: conversationId, branch_id: branchId });
    return this.#request("GET", `v1/brains/${brain}/runtime/scope?${query}`);
  }
  retryLearning(jobId: string, options: RetryLearningOptions): Promise<JsonObject> {
    const brain = encodeURIComponent(this.#scope(options).brain_ref);
    return this.#request("POST", `v1/brains/${brain}/runtime/jobs/${encodeURIComponent(jobId)}/retry-learning`, {
      request_id: options.requestId, expected_fence: options.expectedFence,
    });
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

  prepare(turnId: string, options: PrepareOptions) {
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
