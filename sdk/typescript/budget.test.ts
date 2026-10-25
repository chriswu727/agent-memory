import test from "node:test";
import assert from "node:assert/strict";
import { MemoryClient, MemoryHttpError } from "./index.ts";
import type { CognitiveBudgetOptions } from "./index.ts";

test("cognitive budget reads preserve used limits and encode scope", async () => {
  const requests: { url: string; init?: RequestInit }[] = [];
  const status = { revision: 2, is_explicit: true, used: { model_calls: 101, input_bytes: 2_020_000 } };
  const client = new MemoryClient({
    baseUrl: "http://memory.test/", apiKey: "test-key", brainId: "default",
    fetch: async (input, init) => { requests.push({ url: String(input), init }); return Response.json(status); },
  });
  assert.deepEqual(await client.cognitiveBudget("brain /?#"), status);
  assert.equal(client.brainId, "default");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://memory.test/v1/brains/brain%20%2F%3F%23/runtime/cognitive-budget");
  assert.equal(requests[0].init?.method, "GET");
  assert.equal(requests[0].init?.body, undefined);
  assert.equal(new Headers(requests[0].init?.headers).get("Authorization"), "Bearer test-key");
});

test("budget changes preserve immutable policy and explicit retry identity", async () => {
  const requests: { url: string; method?: string; body: unknown }[] = [];
  const policy = Object.freeze({ window_seconds: 3600, max_model_calls: 200, max_input_bytes: 4_000_000 });
  const options: CognitiveBudgetOptions = Object.freeze({
    requestId: "budget-1", expectedRevision: 2, policy, brainId: "brain /?#",
  });
  const receipt = { request_id: "budget-1", expected_revision: 2, configuration: { revision: 3, policy } };
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key",
    fetch: async (input, init) => {
      requests.push({ url: String(input), method: init?.method, body: JSON.parse(init?.body as string) });
      assert.equal(init?.redirect, "error");
      return Response.json(receipt);
    },
  });
  assert.deepEqual(await client.configureCognitiveBudget(options), receipt);
  assert.deepEqual(await client.configureCognitiveBudget(options), receipt);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], {
    url: "http://memory.test/v1/brains/brain%20%2F%3F%23/runtime/cognitive-budget", method: "PUT",
    body: { request_id: "budget-1", expected_revision: 2, policy },
  });
  assert.deepEqual(requests[0], requests[1]);
});

for (const failure of [409, 503, "timeout"]) {
  test(`budget ${failure} propagates without automatic resubmission`, async () => {
    let calls = 0;
    const client = new MemoryClient({
      baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain",
      fetch: async () => {
        calls++;
        if (failure === "timeout") throw new Error("response lost");
        return Response.json({ error: { code: "unavailable" } }, { status: failure as number });
      },
    });
    await assert.rejects(() => client.configureCognitiveBudget({
      requestId: "budget-1", expectedRevision: 2,
      policy: { window_seconds: 3600, max_model_calls: 200, max_input_bytes: 4_000_000 },
    }), error => failure === "timeout" ? (error as Error).message === "response lost"
      : error instanceof MemoryHttpError && error.status === failure);
    assert.equal(calls, 1);
  });
}
