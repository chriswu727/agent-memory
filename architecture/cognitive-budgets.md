# Configuring cognitive budgets

The durable per-brain budget covers semantic orientation, background learning, and embedding jobs. By default it permits 100 reserved external calls and 2,000,000 input bytes in the previous 3,600 seconds. It does not cover the host's answer generation, response review, independent recall-controller calls, or an entire provider account. These are resource allowances, not currency limits.

`GET /v1/brains/{brain_id}/runtime/cognitive-budget` returns the effective `policy`, its `revision`, whether it was explicitly configured, and `used` and `remaining` reservation counters. The usual authenticated owner scope applies. Reading does not create a policy row, start a worker, or make a model request. A new brain starts at revision zero.

`PUT` to the same endpoint accepts:

```json
{
  "request_id": "allowance-2026-10-25",
  "expected_revision": 0,
  "policy": {
    "window_seconds": 3600,
    "max_model_calls": 200,
    "max_input_bytes": 4000000
  }
}
```

All three policy values are required integers. The window is 1–2,592,000 seconds, calls are 0–1,000,000, and bytes are 0–1,000,000,000,000. Zero calls prevents new reservations. Configuration and reservations serialize under the brain lock. The update requires the revision just read; concurrent conflicting updates return HTTP 409. Worker defaults cannot silently lower an explicit policy. Existing per-job call and input limits still apply and cannot be increased by retrying that job.

Keep the request ID, expected revision, and policy unchanged if a response is lost. Exact replay returns the original immutable receipt, even after later configuration changes, without reverting the current policy. Reusing an ID for different content conflicts. GET again to inspect the latest policy and usage. Neither the SDK nor CLI automatically resubmits an uncertain change.

Changing a policy never resets, refunds, or deletes recorded operations. Completed, failed, uncertain, and still-reserved operations all consume the window. Lowering a cap below existing use reports zero remaining allowance. A shorter window excludes older reservations from its current view; extending it can include them again. In-flight requests already authorized may complete after a reduction. Actual token use is reported separately by `/cognitive-usage`; absence of validated provider usage is unknown, not zero cost.

## Desktop

The local service must already be running:

```sh
membrain desktop budget
membrain desktop configure-budget \
  --request-id allowance-2026-10-25 --expected-revision 0 \
  --window-seconds 3600 --max-model-calls 200 --max-input-bytes 4000000
```

Use the actual revision from `desktop budget`. The numbers above are an explicit example, not new defaults. Both commands accept `--directory`. Configuration takes effect for subsequent reservations without a restart; the configuration call does not itself invoke a provider or replenish an existing allocation. The worker can reconsider never-attempted deferred historical comparisons after capacity changes. Failed or uncertain paid attempts require the separate bounded recovery operation; inspect learning progress before choosing recovery.

## SDKs

```python
runtime = RuntimeClient(memory)
current = runtime.cognitive_budget()
receipt = runtime.configure_cognitive_budget(
    request_id="allowance-2026-10-25",
    expected_revision=current["revision"],
    window_seconds=3600, max_model_calls=200, max_input_bytes=4_000_000,
)
```

```typescript
const current = await memory.cognitiveBudget();
const receipt = await memory.configureCognitiveBudget({
  requestId: "allowance-2026-10-25",
  expectedRevision: current.revision as number,
  policy: { window_seconds: 3600, max_model_calls: 200, max_input_bytes: 4_000_000 },
});
```

The TypeScript methods accept an optional explicit brain identity. The Python runtime uses its bound `MemoryClient`. Authentication and normal brain ownership apply to both reads and changes.
