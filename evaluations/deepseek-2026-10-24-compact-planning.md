# Compact context and proactive abstraction evaluation: 2026-10-24

These changes address reproduced context duplication and let background learning explicitly schedule comparisons across experiences. Live model tests still did not all pass: only one of five standard stages passed completely, and both supplementary abstraction tests using old instances failed. Candidate comparison was operating and unknown conditions used the new representation, but autonomous abstraction could not yet be called reliable.

The [structured report](deepseek-2026-10-24-compact-planning.json) preserves failures, diagnostic continuations, published beliefs, actual context, final proposals and verification from supplementary tests, and the shared call ledger. The earlier [eight-stage report](deepseek-2026-10-24.md) remains unchanged. All sources were synthetic. No existing user brain was read, and host chat answers were not substituted for memory checks.

## Verified context improvements

The diagnostic reused three temporal preferences and two change relations previously written by the live model, reading only the database without model calls. Within an 8,000-unit budget, all three time points retained the complete semantic relation group and correctly represented the same-day exception, next-day restoration, and long-term replacement after the specified date. The original 48,000-unit read had dropped relation endpoints because of duplicated structures.

| Evaluation date | Recall usage | Prepare usage | Temporal relation checks |
| --- | ---: | ---: | --- |
| October 24 | 7,886 | 7,900 | Passed |
| October 25 | 7,898 | 7,912 | Passed |
| October 27 | 7,867 | 7,881 | Passed |

These figures are UTF-8 byte counts of the actual model context, used as conservative budget units, not model-tokenizer token counts. The compact format shares references to sources, relations, and memory versions; complete verification records remain expandable on the server. Additional literal source reports were omitted to stay within budget, and coverage limits were explicitly returned. This is therefore not a lossless compression of all supplied material.

Python, TypeScript, MCP, and the active recall controller use the corresponding representation. SDKs prefer authoritative text supplied by the server, avoiding JavaScript numeric reserialization that could exceed the server-calculated budget. Raw historical reports are explicitly marked historical and cannot bypass current conditions or temporal applicability.

## Actual paid-test results

| Test | Result | Observation |
| --- | --- | --- |
| Create two independent experiences | Failed | Four semantic facts were published, but two episode-creation proposals invented IDs and were rejected; the old query also missed clothing facts |
| Comparison after a reminder | Failed | Initial planning selected shorter clothing facts; an abstraction was proposed, but verification timed out and nothing was published |
| Base preference for direct advice | Passed | Publication, recall, and context preparation passed |
| Listen first during exam preparation | Failed | Conditional-exception and old-rule preservation checks passed; an additional open loop failed source validation, leaving the job partial |
| Conflicting self-reports and “silver state” | Failed | The unexplained condition remained unknown as `unresolved`, and the conflict remained uncertain; other proposals failed verification and Chinese recall missed the English semantic claim |
| Reuse old instances: initial-planning supplement | Failed | Both complete experiences were selected as material, but long statements had too little proportional lexical overlap; no comparison objective or abstraction resulted |
| Reuse old instances: v2 weak-cue exploration | Failed | Correctly selected both experiences and proposed a shared belief; whole-claim and relation verdicts were both `unknown`, so nothing was written |

Later diagnostics retained earlier failures and continued only when valid prerequisite facts existed. They did not replace failed results with successful reruns or relabel partial success as a fully passing scenario.

The v2 planner selects at most two strong lexical objectives and one weak-cue objective per batch, or three strong objectives when no weak objective exists. Weak objectives must still share content words, with deduplication before selection. All source, condition, world, and independence constraints remain unchanged. Free preflight checks confirmed that it selected the two experiences from actual saved data, rather than the reminder assertion that the user recalled the past. Old v1 frozen plans remain resumable.

The final model proposal captured that both reported instances involved walking before studying and retained independent parent-instance versions, but also claimed that the durations differed. The riverside instance did not report a duration; missing information cannot establish a difference. Developer review identified this concrete error. The verifier returned `unknown` for the whole claim and relation, and publication was rejected. It gave no reason, so the duration error cannot be asserted as the reason for rejection.

This rejection demonstrates that support for individual citations is insufficient to establish a whole abstraction. Lowering the whole-claim verification threshold to obtain a pass would be inappropriate. The next step is to distinguish shared claims, observed differences, and unknown details more clearly and assess bounded revision. A fully evaluated but unsupported objective is currently deduplicated rather than retried indefinitely at a cost after every reminder. New evidence or changed instance versions can form a new objective.

## Repairs and remaining gaps

New proposals cannot use legacy free-key `eq/ne`; unrecognized concepts are preserved explicitly as `unresolved`, while old data remains readable. The episode-creation schema now matches server rules, with IDs allocated by the runtime. Slow-model job leases cover the configured request timeout, and evaluation waiting time can be explicitly increased without increasing call count. Network failures or incomplete verification do not permanently suppress the same comparison objective.

The main unresolved problems are discovering English semantic rewrites from Chinese cues, comparison-objective quality after facts are split at different granularities, and reliable abstraction without invented detail. Supplementary tests did not establish an end-to-end pass for the initially rejected episode-creation scenario; a schema fix is not a substitute for that live validation. There are also no conclusions about long-running use, real-user conversations, growth in scale, or final-answer quality.

## Usage and reproduction

This report uses one shared ledger for 14 DeepSeek-V4-Pro thinking-mode requests, with at most two calls per stage and no automatic retries. The 13 requests with confirmed usage totaled 105,028 input and 71,873 output tokens: 176,901 tokens. One timeout returned no usage and cannot be counted as zero. These figures exclude the earlier report's 24 requests and are not billing amounts.

The first two stages had a total timeout of 120 seconds; later stages allowed 240 seconds. Every call retained a 16,384-token output cap. Temporal-context replay and weak-candidate preflight made no model calls. The run stopped at its declared 14-call allowance, without further attempts to accumulate passing results.

See the [README](../README.md#manual-deepseek-evaluation) for the standard entry point and diagnostic-continuation parameters after failure. Supplementary tests reused actual instances from an earlier diagnostic brain; running only the standard entry point does not reproduce those supplementary results. The public report preserves the relevant inputs, outputs, and fingerprints. Local raw diagnostics remain under ignored `.local/`; credentials and hidden reasoning are not published.

Final local checks: **1,447 Python tests passed**; **12 TypeScript tests** and type checking passed. Ruff, Python package build, and diff checks passed. No cloud CI was added or triggered, and no Codex/Claude subscription tests were run. These deterministic checks cover code behavior and do not alter the live-model failures above.
