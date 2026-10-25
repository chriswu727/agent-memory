import assert from "node:assert/strict";
import test from "node:test";
import type { LearningHistorySummaries } from "./index.ts";

test("optional learning summaries preserve legacy absence and disclose only accounting", () => {
  const legacy: LearningHistorySummaries = {};
  const current: LearningHistorySummaries = {
    history_funding: {
      version: "private-brain-history-funding-v1",
      authorization_root_job_id: "donor",
      origin_job_id: "original-unattempted",
    },
    history_attention: {
      version: "bounded-comparison-attention-v1", limit: 3,
      candidate_count: 5, admitted_count: 3, not_selected_count: 2,
      focus_count: 2, exploration_count: 1, source_origin_count: 4,
      retrieval_truncated: false, search_exhaustive: false, discovery_limitations: [],
    },
  };
  assert.deepEqual(legacy, {});
  assert.equal(current.history_attention?.search_exhaustive, false);
  assert.equal(current.history_funding?.authorization_root_job_id, "donor");
  const invalid: LearningHistorySummaries = {
    // @ts-expect-error Private permission payloads are not public summaries.
    history_funding_permission: { branch_id: "main" },
  };
  assert.ok(invalid);
});
