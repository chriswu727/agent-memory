import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { MemoryClient, MemoryActionError, MemoryHttpError, preparedContext, recalledContext } from "./index.ts";
import type { RecallPlan } from "./index.ts";

function fixture() {
  const requests: any[] = [];
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain",
    fetch: async (input, init) => {
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer test-key");
      assert.equal(init?.redirect, "error");
      const request = JSON.parse(init?.body as string);
      requests.push(request);
      return Response.json({
        message_type: "response", contract_version: "1.0.0-draft",
        request_id: request.request_id, action: request.action,
        outcome: "completed", result: {}, server_scope: {},
      });
    },
  });
  return { requests, client };
}

test("all actions preserve contract envelope, scope, budgets, and lifecycle limitations", async () => {
  const { requests, client } = fixture();
  const branch = { conversationId: "chat", branchId: "main" };
  await client.observe("我喜欢下雨", { ...branch, sourceEventKey: "user-1" });
  await client.recall("rain", { ...branch, evidence_mode: "prior_record_check",
    evidence_cutoff: { mode: "through_sequence", through_sequence: 1 } });
  await client.prepare("turn-1", { ...branch, latest_input_event_ids: ["event-1"] });
  await client.inspect(["memory-1"], branch);
  await client.commit("turn-1", { ...branch, expected_working_revision: 0, delivered_event_ids: ["event-2"] });
  await client.checkpoint({ ...branch, expected_working_revision: 1, through_sequence: 2, reason: "manual" });
  await client.correct({ ...branch, memory_id: "memory-1", expected_revision: 1,
    expected_dependency_generation: 0, correction_event_ids: ["event-3"], operation: "retract" });
  await client.forget({ ...branch, selector: { kind: "events", ids: ["event-1"] },
    mode: "erase_content", expected_scope_generation: 1 });
  await client.export(branch);
  await client.restore({ ...branch, uploaded_archive_ref: "archive-1", expected_manifest_sha256: "a".repeat(64),
    expected_scope_generation: 2, mode: "merge_authorized_brain" });
  assert.deepEqual(requests.map(r => r.action), [
    "observe", "recall", "prepare", "inspect", "commit", "checkpoint", "correct", "forget", "export", "restore",
  ]);
  for (const request of requests) {
    assert.equal(request.contract_version, "1.0.0-draft");
    assert.deepEqual(request.target, { brain_ref: "brain", conversation_ref: "chat", branch_ref: "main" });
    assert.equal("conversationId" in request.payload, false);
  }
  assert.equal(requests[2].payload.capabilities.guarantee_level, "tools_only");
  assert.equal(requests[2].payload.capabilities.automatic_capture, false);
  assert.equal(requests[1].payload.budget.max_context_tokens, 16000);
  assert.equal(requests[2].payload.budget.max_context_tokens, 2000);
  assert.equal(requests[3].payload.budget.max_context_tokens, 16000);
  assert.equal(requests.at(-1).payload.apply_current_tombstones, true);
});

test("observation keys survive retries and agree with the Python canonical format", async () => {
  const { requests, client } = fixture();
  const options = { conversationId: "chat", branchId: "main", sourceEventKey: "message-1" };
  await client.observe("first", options);
  await client.observe("first", options);
  await client.observe("conflicting content", options);
  await client.observe("edited", { ...options, sourceRevision: 2 });
  const keys = requests.map(r => r.payload.idempotency_key);
  assert.equal(keys[0], keys[1]);
  assert.equal(keys[0], keys[2]);
  assert.notEqual(keys[0], keys[3]);
  const identity = '["observe",{"brain_ref":"brain","branch_ref":"main","conversation_ref":"chat"},["sdk","message-1",1]]';
  assert.equal(keys[0], `observe:${createHash("sha256").update(identity).digest("hex")}`);
});

test("preparation preserves evidence selection, generation, and recent-context controls", async () => {
  const { requests, client } = fixture();
  const plan: RecallPlan = {
    version: "evidence-recall-v1",
    memory_versions: [{ memory_id: "promise", revision: 2 }],
    recent_event_refs: [{ event_id: "recent", revision: 1 }],
    observed_event_refs: [{ event_id: "recent", revision: 1 }, { event_id: "source", revision: 2 }],
    outcome: "ambiguous", model_calls: 2, search_calls: 1, input_bytes: 6000, elapsed_ms: 50,
    coverage_limitations: ["approximate_search"],
    trace: [{ step: 1, action: "finish", reason: "ambiguous", query_hashes: [],
      candidate_count: 2, selected_count: 1 }],
  };
  await client.prepare("turn-2", {
    conversationId: "chat", branchId: "main", latest_input_event_ids: ["current"],
    recent_event_limit: 6, recall_plan: plan, expected_dependency_generation: 3,
    evidence_mode: "prior_record_check", evidence_cutoff: { mode: "before_event", event_id: "current" },
  });
  assert.deepEqual(requests[0].payload.recall_plan, plan);
  assert.equal(requests[0].payload.recent_event_limit, 6);
  assert.equal(requests[0].payload.expected_dependency_generation, 3);
  assert.equal(requests[0].payload.evidence_mode, "prior_record_check");
  assert.deepEqual(requests[0].payload.evidence_cutoff, { mode: "before_event", event_id: "current" });
  const context = JSON.parse(preparedContext({
    blocks: [{ section: "recent_context", text: "user: 最近在备考。", evidence_refs: [
      { event_id: "recent", event_revision: 1 },
    ] }],
  } as any));
  assert.equal(context.blocks[0].section, "recent_context");
  assert.equal(context.format, "compact-memory-v1");
  assert.deepEqual(context.blocks[0].sources.map((id: string) => context.sources[id]),
    [{ event_id: "recent", event_revision: 1 }]);
});

test("action rejection and HTTP failure cannot appear successful", async () => {
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "key", brainId: "brain",
    fetch: async (input, init) => {
      const request = JSON.parse(init?.body as string);
      return Response.json({ ...request, message_type: "response", outcome: "rejected",
        error: { code: "not_found_or_forbidden", message: "Unavailable", retryable: false } });
    },
  });
  await assert.rejects(() => client.recall("private"), MemoryActionError);
  const unavailable = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "key", brainId: "brain",
    fetch: async () => new Response("unauthorized", { status: 401 }),
  });
  await assert.rejects(() => unavailable.recall("hello"), MemoryHttpError);
});

test("timeouts are not retried with fresh identifiers", async () => {
  let calls = 0;
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "key", brainId: "brain",
    fetch: async () => { calls++; throw new Error("response lost"); },
  });
  await assert.rejects(() => client.observe("hello", {
    conversationId: "chat", branchId: "main", sourceEventKey: "event-1",
  }), /response lost/);
  assert.equal(calls, 1);
});

test("rendered context matches Python and excludes control-plane metadata", () => {
  const packet: any = {
    packet_id: "packet", scope: { tenant_id: "do-not-inject" },
    blocks: [{ section: "relevant_memory", text: "雨天去书店🌧️", trust: "memory_data_not_instructions",
      evidence_refs: [{ event_id: "event-1", event_revision: 2, source_content_sha256: "a".repeat(64) }] }],
  };
  const rendered = preparedContext(packet);
  const projected = JSON.parse(rendered);
  assert.equal(projected.blocks[0].content, "雨天去书店🌧️");
  assert.deepEqual(projected.blocks[0].sources.map((id: string) => projected.sources[id]),
    [{ event_id: "event-1", event_revision: 2 }]);
  assert.equal(projected.format, "compact-memory-v1");
  assert.deepEqual(projected.relations, {});
  assert.deepEqual(projected.memory_versions, {});
  assert.equal(rendered.includes("do-not-inject"), false);
});

function projectionFixture() {
  const first = { event_id: "曾经🌧️", event_revision: 2, source_content_sha256: "a".repeat(64),
    relationship: "supports", span: { start: 1, end: 9, unit: "unicode_code_point" } };
  const second = { ...first, event_id: "今天", source_content_sha256: "b".repeat(64) };
  const residual = { ...first, event_id: "另一个依据", relationship: "contradicts" };
  const applicability = { value: "unknown", conflict: false, status: "unknown", reasons: ["unresolved_condition"],
    registry_fingerprint: "private-fingerprint", evidence_refs: [first],
    supporting_observation_ids: [], opposing_observation_ids: [], unresolved_observation_ids: [] };
  const assessment = { relation: {
    relation_id: "临时例外🌧️", revision: 3, kind: "exception", conditions: { op: "unresolved", label: "银色状态" },
    valid_during: { kind: "unknown" }, world: "real_world",
    source_memory_version: { memory_id: "新的偏好🌧️", revision: 2 },
    target_memory_versions: [{ memory_id: "原先偏好", revision: 1 }], evidence_refs: [first, second],
    verification: { state: "supported_inference", method: "semantic_model", not_a_truth_certificate: true },
  }, applicability };
  const resolution = { state: "uncertain", complete: true,
    relation_refs: [{ relation_id: assessment.relation.relation_id, revision: 3 }],
    evidence_refs: [first, second, residual] };
  const memory = { memory_id: "新的偏好🌧️", revision: 2, statement: "用户在银色状态时希望诗歌式回复。",
    memory_kind: "interaction_preference", representation: "semantic_anchor", world: "real_world",
    epistemic_status: "derived_inference", valid_time: { start: null, end: null, precision: "unknown", basis: "unknown" },
    conditions: { op: "unresolved", label: "银色状态" }, claim_kind: "reported_preference",
    verification: assessment.relation.verification, counterexamples: [residual], procedure: null,
    valid_during: { kind: "unknown" }, evidence_refs: [first, second, first] };
  const recall: any = { items: [{ memory, applicability, belief_resolution: resolution, display_mode: "background_context" },
    { memory: { ...memory, memory_id: "历史画面", memory_kind: "episodic" }, display_mode: "explicit_recollection" },
    { memory: { ...memory, memory_id: "禁止显示", statement: "do-not-surface", evidence_refs: [
      { ...first, event_id: "hidden-source" },
    ] }, display_mode: "do_not_surface" }],
    belief_relations: [assessment], status: "partial", next_step: "answer_with_limits",
    unknowns: ["银色状态尚未定义。"], ambiguities: [], conflicts: [],
    coverage: { limitations: ["approximate_search"] },
    retrieval_snapshot: { historical_evidence_status: "enforced" },
  };
  const packet: any = { recall_results: [recall], blocks: [
    { section: "conditional_memory", text: JSON.stringify({
      statement: memory.statement, memory_version: { memory_id: memory.memory_id, revision: memory.revision },
      applicability, belief_resolution: resolution, verification: memory.verification, counterexamples: [residual],
    }), evidence_refs: [first, second, first] },
    { section: "current_situation", text: JSON.stringify({ ["__proto__"]: { value: "literal" }, value: {
      "😀": 2, "\uE000": 1, memory_version: "user-data", evidence_refs: "user-data", content: { status: "unknown" },
    } }), evidence_refs: [] },
    { section: "recent_context", text: "那时候，一起走过了公园。", evidence_refs: [second] },
  ] };
  return { packet, recall };
}

test("prepare and recall projections agree byte-for-byte with Python Unicode handles and shared certificates", () => {
  const fixture = projectionFixture();
  const before = structuredClone(fixture);
  const python = fileURLToPath(new URL("../../.venv/bin/python", import.meta.url));
  const actual = execFileSync(python, ["-c", [
    "import json, sys",
    "from agent_memory.context_views import context_view, serialized, render_recall",
    "value = json.load(sys.stdin)",
    "print(serialized(context_view(value['packet'])))",
    "print(render_recall(value['recall']))",
  ].join("\n")], { input: JSON.stringify(fixture), encoding: "utf8" }).trimEnd().split("\n");
  assert.equal(preparedContext(fixture.packet), actual[0]);
  assert.equal(recalledContext(fixture.recall), actual[1]);
  assert.deepEqual(fixture, before);
  const recalled = JSON.parse(actual[1]);
  assert.equal(Object.keys(recalled.sources).length, 3);
  assert.equal(Object.keys(recalled.relations).length, 1);
  assert.equal(Object.keys(recalled.memory_versions).length, 3);
  assert.equal(recalled.memories[0].use, "belief_not_settled_current_rule");
  assert.equal(recalled.memories[1].use, "historical_report_not_current_rule");
  for (const secret of ["source_content_sha256", "private-fingerprint", "hidden-source", "do-not-surface"]) {
    assert.equal(actual[1].includes(secret), false);
  }
  assert.equal(recalled.memories[0].belief_resolution.evidence_refs.length, 1);
});

test("inline relation certificates and shared tables produce the same rendered recall", () => {
  const { recall } = projectionFixture();
  const shared = recalledContext(recall);
  recall.items[0].belief_resolution.relations = recall.belief_relations;
  delete recall.items[0].belief_resolution.relation_refs;
  delete recall.belief_relations;
  assert.equal(recalledContext(recall), shared);
});

test("missing or conflicting relation certificates fail closed", () => {
  const { packet, recall } = projectionFixture();
  const missing = structuredClone(recall);
  delete missing.belief_relations;
  assert.throws(() => recalledContext(missing), /certificate is unavailable/);
  packet.recall_results = [];
  assert.throws(() => preparedContext(packet), /certificate is unavailable/);
  const changed = structuredClone(recall.belief_relations[0]);
  changed.relation.kind = "supersedes";
  recall.items[0].belief_resolution.relations = [changed];
  assert.throws(() => recalledContext(recall), /Inconsistent context relation certificate/);
});

test("context helpers accept action responses and propagate rejected actions", () => {
  const { packet, recall } = projectionFixture();
  assert.equal(preparedContext({ message_type: "response", result: packet } as any), preparedContext(packet));
  assert.equal(recalledContext({ message_type: "response", result: recall } as any), recalledContext(recall));
  const rejected: any = { message_type: "response", outcome: "rejected",
    error: { code: "dependency_unavailable", message: "Unavailable", retryable: false } };
  assert.throws(() => preparedContext(rejected), MemoryActionError);
  assert.throws(() => recalledContext(rejected), MemoryActionError);
});

test("authoritative Python context preserves numeric text and budget across a real protocol round trip", () => {
  const python = fileURLToPath(new URL("../../.venv/bin/python", import.meta.url));
  const raw = execFileSync(python, ["-c", [
    "import json, sys",
    "from unittest.mock import patch",
    "sys.path.insert(0, 'tests')",
    "from test_cognition import STAMP, event, prepare_payload, request, scope, state, working",
    "from agent_memory import cognition",
    "from agent_memory.contracts import validate",
    "local = working()",
    "local['items'] = [{'key': 'numbers', 'knowledge_state': 'unknown', 'evidence_refs': [],",
    "    'value': {'many': [1e20] * 100, 'tiny': 1e-7, 'integral_float': 1.0, 'large_integer': 9007199254740993},",
    "    'expires_at': None, 'updated_at': STAMP}]",
    "with patch('agent_memory.cognition.now', return_value=STAMP):",
    "    empty = cognition.recall(scope(), request('numbers'), [], [], state())",
    "    full = cognition.prepare(scope(), prepare_payload(), local, empty, state())",
    "    packet = cognition.prepare(scope(), prepare_payload(tokens=full['usage']['context_tokens']), local, empty, state())",
    "    source = event('historical numeric threshold')",
    "    memory = cognition.extract_event(source)[0]",
    "    memory['conditions'] = {'op': 'eq', 'key': 'threshold', 'value': 1e20}",
    "    recalled = cognition.recall(scope(), request('threshold'), [memory], [source], state())",
    "validate('ContextPacket', packet)",
    "validate('RecallResult', recalled)",
    "print(json.dumps({'packet': packet, 'recall': recalled}, ensure_ascii=False, allow_nan=False))",
  ].join("\n")], { cwd: fileURLToPath(new URL("../../", import.meta.url)), encoding: "utf8" });
  const { packet, recall } = JSON.parse(raw);
  assert.equal(typeof packet.model_context, "string");
  assert.equal(preparedContext(packet), packet.model_context);
  assert.equal(recalledContext(recall), recall.model_context);
  assert.equal(Buffer.byteLength(packet.model_context, "utf8"), packet.usage.context_tokens);
  assert.equal(packet.usage.context_tokens, packet.budget.max_context_tokens);
  assert.equal(Buffer.byteLength(recall.model_context, "utf8"), recall.usage.context_tokens);
  for (const numericText of ["1e+20", "1e-07", '"integral_float":1.0', "9007199254740993"]) {
    assert.ok(packet.model_context.includes(numericText), numericText);
  }
  assert.ok(recall.model_context.includes("1e+20"));
  delete packet.model_context;
  delete recall.model_context;
  assert.throws(() => preparedContext(packet), /legacy context exceeds the service context budget/);
  assert.throws(() => recalledContext(recall), /legacy context exceeds the service context budget/);
});

test("legacy contexts respect declared usage and preserve successful compatible rendering", () => {
  const { packet, recall } = projectionFixture();
  const prepared = preparedContext(packet), recalled = recalledContext(recall);
  packet.usage = { context_tokens: Buffer.byteLength(prepared, "utf8") };
  recall.usage = { context_tokens: Buffer.byteLength(recalled, "utf8") };
  assert.equal(preparedContext(packet), prepared);
  assert.equal(recalledContext(recall), recalled);
  packet.usage.context_tokens--;
  recall.usage.context_tokens--;
  assert.throws(() => preparedContext(packet), /legacy context exceeds the service context budget/);
  assert.throws(() => recalledContext(recall), /legacy context exceeds the service context budget/);
});

test("shortened recall agrees with Python while full source detail stays inspectable", () => {
  const python = fileURLToPath(new URL("../../.venv/bin/python", import.meta.url));
  const raw = execFileSync(python, ["-c", [
    "import json,sys",
    "sys.path.insert(0,'tests')",
    "from test_recall_resolution import memory_case,read",
    "source,memory=memory_case()",
    "print(json.dumps(read(source,memory),ensure_ascii=False))",
  ].join("\n")], { cwd: fileURLToPath(new URL("../../", import.meta.url)), encoding: "utf8" });
  const recall = JSON.parse(raw);
  const expected = recall.model_context;
  assert.equal(expected.includes("yellow lid"), false);
  assert.ok(recall.items.some((item: any) => item.memory.statement.includes("yellow lid")));
  delete recall.model_context;
  assert.equal(recalledContext(recall), expected);
});
