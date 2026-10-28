import assert from "node:assert/strict";
import test from "node:test";

import { MemoryClient } from "./index.ts";

test("timeline sends roles, budgets and the excluded conversation as query parameters", async () => {
  const requests: { url: URL; init?: RequestInit }[] = [];
  const result = { items: [], complete: true, characters: 0 };
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "brain /?#",
    fetch: async (input, init) => {
      requests.push({ url: new URL(String(input)), init });
      return Response.json(result);
    },
  });
  assert.deepEqual(await client.timeline({ roles: ["user", "assistant"], maxCharacters: 900, excludeConversation: "now /?&" }), result);
  assert.equal(requests[0].url.pathname, "/v1/brains/brain%20%2F%3F%23/timeline");
  assert.deepEqual(requests[0].url.searchParams.getAll("role"), ["user", "assistant"]);
  assert.equal(requests[0].url.searchParams.get("max_characters"), "900");
  assert.equal(requests[0].url.searchParams.get("exclude_conversation"), "now /?&");
  assert.equal(requests[0].init?.method, "GET");
  await client.timeline();
  assert.deepEqual(requests[1].url.searchParams.getAll("role"), ["user"]);
  await client.timeline({ eventIds: ["event-1", "event 2"] });
  assert.deepEqual(requests[2].url.searchParams.getAll("event_id"), ["event-1", "event 2"]);
  await client.timeline({ conversationId: "membrain-notes" });
  assert.equal(requests[3].url.searchParams.get("conversation_id"), "membrain-notes");
});

test("ensureBrain creates or reuses a named brain with PUT", async () => {
  const requests: { url: URL; init?: RequestInit }[] = [];
  const client = new MemoryClient({
    baseUrl: "http://memory.test", apiKey: "test-key", brainId: "home",
    fetch: async (input, init) => {
      requests.push({ url: new URL(String(input)), init });
      return Response.json({ id: "user-abc" });
    },
  });
  assert.deepEqual(await client.ensureBrain("user-abc", "user qq:1001"), { id: "user-abc" });
  assert.equal(requests[0].url.pathname, "/v1/brains/user-abc");
  assert.equal(requests[0].init?.method, "PUT");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), { name: "user qq:1001" });
});
