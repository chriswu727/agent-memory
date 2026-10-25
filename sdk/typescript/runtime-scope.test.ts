import test from "node:test";
import assert from "node:assert/strict";
import { MemoryClient, MemoryHttpError } from "./index.ts";

test("scope lookup preserves exact identities and exposes an old reserved turn", async () => {
  const requests: { url: URL; init?: RequestInit }[] = [];
  const result = { turn: { turn_id: "old-turn", stage: "generated", scope_reserved: true } };
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "default",
    fetch: async (input, init) => {
      requests.push({ url: new URL(String(input)), init });
      return Response.json(result);
    },
  });
  assert.deepEqual(await client.activeScope("conversation /?&", "branch /?&", "brain /?#"), result);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url.pathname, "/v1/brains/brain%20%2F%3F%23/runtime/scope");
  assert.deepEqual(Object.fromEntries(requests[0].url.searchParams), {
    conversation_id: "conversation /?&", branch_id: "branch /?&",
  });
  assert.equal(requests[0].init?.method, "GET");
  assert.equal(requests[0].init?.body, undefined);
  assert.equal(new Headers(requests[0].init?.headers).get("Authorization"), "Bearer test-key");
  assert.equal(client.brainId, "default");
});

test("an empty scope is returned without starting or claiming a turn", async () => {
  let calls = 0;
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain",
    fetch: async input => {
      calls++;
      assert.equal(new URL(String(input)).searchParams.get("branch_id"), "main");
      return Response.json({ turn: null });
    },
  });
  assert.deepEqual(await client.activeScope("conversation"), { turn: null });
  assert.equal(calls, 1);
});

test("scope lookup errors are surfaced without an automatic retry", async () => {
  let calls = 0;
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain",
    fetch: async () => {
      calls++;
      return Response.json({ error: { code: "unauthorized" } }, { status: 401 });
    },
  });
  await assert.rejects(() => client.activeScope("conversation"),
    error => error instanceof MemoryHttpError && error.status === 401);
  assert.equal(calls, 1);
});
