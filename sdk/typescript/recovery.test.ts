import test from "node:test";
import assert from "node:assert/strict";
import { MemoryClient } from "./index.ts";

for (const epoch of [undefined, "8d8e9a28-4d16-407d-9cf1-e15ae67cfc80"]) {
  test(`recovery metadata uses only an explicitly acknowledged epoch: ${epoch}`, async () => {
    const headers: Headers[] = [];
    const client = new MemoryClient({
      baseUrl: "http://memory.test", apiKey: "test-key", recoveryEpoch: epoch,
      fetch: async (input, init) => {
        assert.equal(String(input), "http://memory.test/v1/recovery");
        headers.push(new Headers(init?.headers));
        return Response.json({ recovery_epoch: "8d8e9a28-4d16-407d-9cf1-e15ae67cfc80" });
      },
    });
    await client.recovery();
    await client.recovery();
    assert.equal(headers.length, 2);
    assert.equal(headers[0].get("X-Memory-Recovery-Epoch"), epoch ?? null);
    assert.equal(headers[1].get("X-Memory-Recovery-Epoch"), epoch ?? null);
  });
}

for (const recoveryEpoch of ["", "not-an-epoch", "8D8E9A28-4D16-407D-9CF1-E15AE67CFC80", "x\r\nInjected: yes"]) {
  test(`invalid recovery epoch is rejected before transport: ${JSON.stringify(recoveryEpoch)}`, () => {
    assert.throws(() => new MemoryClient({ baseUrl: "http://memory.test", apiKey: "test", recoveryEpoch }), /canonical UUID/);
  });
}
