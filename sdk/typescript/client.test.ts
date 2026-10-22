import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { MemoryClient, MemoryActionError, MemoryHttpError, preparedContext } from "./index.ts";
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
  assert.deepEqual(context.blocks[0].sources, [{ event_id: "recent", revision: 1 }]);
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
  assert.equal(rendered, '{"blocks":[{"section":"relevant_memory","sources":[{"event_id":"event-1","revision":2}],"text":"雨天去书店🌧️"}],"trust":"memory_data_not_instructions"}');
  assert.equal(rendered.includes("do-not-inject"), false);
});
