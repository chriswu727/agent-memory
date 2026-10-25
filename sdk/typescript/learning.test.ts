import test from "node:test";
import assert from "node:assert/strict";
import { MemoryClient, MemoryHttpError } from "./index.ts";
import type { RetryLearningOptions } from "./index.ts";

test("learning progress uses the runtime endpoint and preserves an incomplete summary", async () => {
  const requests: { url: string; init?: RequestInit }[] = [];
  const summary = { brain_id: "brain", state_counts: { cancelled: 1 }, pending_sequence_span: 12 };
  const client = new MemoryClient({
    baseUrl: "http://memory.test/", apiKey: "test-key", brainId: "brain",
    fetch: async (input, init) => {
      requests.push({ url: String(input), init });
      return Response.json(summary);
    },
  });
  assert.deepEqual(await client.learningProgress(), summary);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://memory.test/v1/brains/brain/runtime/learning-progress");
  assert.equal(requests[0].init?.method, "GET");
  assert.equal(requests[0].init?.body, undefined);
  assert.equal(new Headers(requests[0].init?.headers).get("Authorization"), "Bearer test-key");
  assert.equal(requests[0].init?.redirect, "error");
  assert.ok(requests[0].init?.signal instanceof AbortSignal);
});

test("learning endpoints encode identities and allow an explicit brain override", async () => {
  const urls: string[] = [];
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "default",
    fetch: async input => { urls.push(String(input)); return Response.json({}); },
  });
  await client.learningProgress("brain /?#");
  await client.retryLearning("job /?#", { brainId: "brain /?#", requestId: "retry-1", expectedFence: 3 });
  assert.deepEqual(urls, [
    "http://memory.test/v1/brains/brain%20%2F%3F%23/runtime/learning-progress",
    "http://memory.test/v1/brains/brain%20%2F%3F%23/runtime/jobs/job%20%2F%3F%23/retry-learning",
  ]);
  assert.equal(client.brainId, "default");
});

test("explicit learning retries preserve request identity and return the queued child directly", async () => {
  const requests: { url: string; method?: string; body: unknown }[] = [];
  const child = { id: "child-job", kind: "consolidate", state: "queued", model_calls: 0, fence: 0 };
  const options: RetryLearningOptions = Object.freeze({ requestId: "retry-after-repair", expectedFence: 2 });
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain",
    fetch: async (input, init) => {
      requests.push({ url: String(input), method: init?.method, body: JSON.parse(init?.body as string) });
      assert.equal(init?.redirect, "error");
      assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer test-key");
      return Response.json(child);
    },
  });
  assert.deepEqual(await client.retryLearning("parent-job", options), child);
  assert.deepEqual(await client.retryLearning("parent-job", options), child);
  assert.deepEqual(requests, Array.from({ length: 2 }, () => ({
    url: "http://memory.test/v1/brains/brain/runtime/jobs/parent-job/retry-learning",
    method: "POST", body: { request_id: "retry-after-repair", expected_fence: 2 },
  })));
  assert.deepEqual(options, { requestId: "retry-after-repair", expectedFence: 2 });
});

for (const status of [401, 404, 409, 503]) {
  test(`learning retry HTTP ${status} is surfaced without an automatic retry`, async () => {
    let calls = 0;
    const client = new MemoryClient({
      baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain",
      fetch: async () => { calls++; return Response.json({ error: { code: "unavailable" } }, { status }); },
    });
    await assert.rejects(() => client.retryLearning("parent", { requestId: "retry-1", expectedFence: 1 }),
      error => error instanceof MemoryHttpError && error.status === status);
    assert.equal(calls, 1);
  });
}

test("a lost retry response keeps the caller's stable request ID and is never retried automatically", async () => {
  const requests: unknown[] = [];
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain",
    fetch: async (_, init) => {
      requests.push(JSON.parse(init?.body as string));
      throw new Error("response lost");
    },
  });
  const retry = { requestId: "retry-keep-this-id", expectedFence: 4 };
  await assert.rejects(() => client.retryLearning("parent", retry), /response lost/);
  assert.deepEqual(requests, [{ request_id: "retry-keep-this-id", expected_fence: 4 }]);
  await assert.rejects(() => client.retryLearning("parent", retry), /response lost/);
  assert.deepEqual(requests[1], requests[0]);
});

test("learning endpoints require a brain and reject non-object success responses", async () => {
  let calls = 0;
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key",
    fetch: async () => { calls++; return Response.json([]); },
  });
  assert.throws(() => client.learningProgress(), /brainId is required/);
  assert.throws(() => client.retryLearning("parent", { requestId: "retry-1", expectedFence: 1 }), /brainId is required/);
  assert.equal(calls, 0);
  await assert.rejects(() => client.learningProgress("brain"), /non-object response/);
  await assert.rejects(() => client.retryLearning("parent", {
    brainId: "brain", requestId: "retry-1", expectedFence: 1,
  }), /non-object response/);
  assert.equal(calls, 2);
});
