# Long-history evaluation stopped after a learning failure: 2026-10-25

The planned 100-history, three-arm experiment was **stopped after 11 completed exchanges** in the memory arm. All 11 delivered answers faithfully acknowledged their current synthetic input, but background learning published new semantic claims and episodes only in the first three exchanges. The next **eight consecutive exchanges failed to publish either**. This is a failed sustained-learning result, not a completed long-history comparison.

The [independent audit and file manifest](deepseek-2026-10-25-long-history-stopped.json) and [compressed original evidence](deepseek-2026-10-25-long-history-stopped.evidence.tar.gz) preserve the failure. The archive contains the byte-exact raw memory report, outer report, frozen plan, source manifest, operator stop record, complete provider ledger, and all 11 local delivery receipts. Original Chinese inputs and outputs remain unchanged.

## Why the raw report still says `started`

The operator stopped the process at a completed-stage boundary after confirming every reserved provider call had a persisted, known outcome. The independent `operator-stop.json` records `stopped_by_operator`, the reason, `SIGSTOP` followed by `SIGTERM`, and hashes of the report and ledger. The operator also confirmed process termination with exit 241. That exit status is operator evidence, not an added field in the original report.

The memory report and outer report retain their original `started` state because termination bypassed normal experiment finalization. All 11 individual base stages are `finished`. `history-010` includes the completed turn, delivery, review, learning result, source snapshots, and timing measurements; later wrapper enrichment such as `performance`, `review_audit`, and `server_budget_after` was not written. Missing fields have not been reconstructed inside the raw record or counted as successful checks.

The original memory report SHA-256 is `a3054fb6778b398caeda15dceed5749043cd8fb6cb718acc89bcbbf25bc5e4db`. The ledger SHA-256 is `1ff7930ad5dfe03ab618c10f65a7edf47fa8a42b5677f3ca24ccd68d521279e0`. Both match the stop record.

## Frozen configuration and actual scope

The experiment used commit `019ec6ad4b50d22ef309d88f199d1fab99dddf6b` and migrations **001–017**, with a separate source snapshot and isolated PostgreSQL database. The source manifest SHA-256 is `2f3f8caa149e5f9a0932f08b6d655bd47a29625e85941ee3361becd1a3a54459`. Later retrieval work, including migration 018 and the additional exact-word candidate channel, is excluded from this result.

The model was `deepseek-v4-pro` with thinking enabled. The runner used `configured_service`; instrumentation replaced only the provider HTTP transport. The frozen semantic policy allowed 32 sources, 32 assertions, 16 episodes, 24 proposals, two calls per job, 98,304 request bytes, 196,608 cumulative input bytes, 65,536 response bytes, 16,384 output tokens per call, and 240 seconds per job. Recorded learning snapshots match that policy. The explicitly configured server allowance was 412 cognitive calls and 40,501,248 input bytes over 2,592,000 seconds; it was not exhausted.

The runtime used `on_demand` orientation, zero recent-dialogue events, a 16,000-unit memory budget, and a 24,000-unit total input budget. Those units use the runner's conservative UTF-8 byte estimator, not provider tokenization. All 11 statement-only inputs received a persisted orientation skip, with no foreground orientation model call. A skip is a routing decision, not a successful semantic orientation. Host generation allowed 4,096 output tokens; the additional answer reviewer allowed 8,192.

The actual path used loopback HTTP, PostgreSQL persistence, a local delivery sink, and awaited background learning. It used no embeddings and did not exercise QQ, another external chat service, or an operating-system restart. The plan included memory, rolling-summary, and no-memory arms, each with 100 history exchanges and six held-out probes. **Only memory histories 000–010 ran.** Histories 011–099, every held-out probe, the trained-archive checkpoint and probe restores, both baseline arms, and the three-arm comparison remain unexecuted.

## What failed in background learning

At `history-003`, the user reported a childhood school's name and a ginkgo tree beside its entrance, explicitly leaving the city unspecified. Both current source records were present in the background proposal request. However, bounded neighborhood selection had omitted six historical packages under `request_bytes_budget` and set `publication_restricted=true`. The request contained eight sources and 11 assertions, below their count limits.

In the frozen implementation, `neighborhood.py` marks this restriction when relevant historical packages cannot be included. `SemanticProcessor.process` then rejects every non-situation proposal under that flag as `incomplete_history_package`, before individual proposal verification. This also blocks new claims and a new episode supported by the complete current learning-input pair. The recorded rejection mechanism is established; a safe finer-grained completeness policy still requires a separate repair and validation. The result does not justify ignoring missing dependencies or weakening evidence checks.

| History | Background calls | New semantic claims | New episodes | Background outcome |
| --- | ---: | ---: | ---: | --- |
| 000 | 2 | 2 | 1 | Completed |
| 001 | 2 | 2 | 1 | Completed |
| 002 | 2 | 2 | 1 | Completed |
| 003 | 1 | 0 | 0 | Partial; job cancelled |
| 004 | 2 | 0 | 0 | Partial; job cancelled |
| 005 | 1 | 0 | 0 | Partial; job cancelled |
| 006 | 1 | 0 | 0 | Partial; job cancelled |
| 007 | 1 | 0 | 0 | Partial; job cancelled |
| 008 | 1 | 0 | 0 | Partial; job cancelled |
| 009 | 2 | 0 | 0 | Partial; job cancelled |
| 010 | 1 | 0 | 0 | Partial; job cancelled |

All eight later stages report the same incomplete-package rejection for new claims and episode proposals; stages 006–010 also report `candidate_limit`. Stage 009 additionally rejects a situation proposal as `semantic_support_unconfirmed`. Extra verification calls in some partial jobs do not mean new semantic memories were published.

This is a loss of learning progress, not deletion of the original conversations. The final recorded snapshot retains 22 source versions, 45 direct-capture memories, and six aggregated semantic memories. The first three jobs created three episodes; no relations were published. Literal capture must not be reported as successful semantic learning. Only three of the 11 stages pass every machine check present in their raw record; the final stage also lacks the later wrapper checks described above.

## Independent answer and memory review

The complete delivered answers preserve the names, label numbers, page numbers, and order in their current inputs. The first answer preserves the reported routine's name, walking-before-reading order, October 20 start instant, and lack of a scheduled end. The school answer leaves its city unknown. The final `history-010` answer correctly acknowledges note 11, the city-sketch label `云井6866`, and page 2, without replacing them with similar earlier notes. Its three memory proposals are nevertheless rejected; answer correctness and background learning success are separate outcomes.

The six accepted semantic claims preserve speaker attribution: three user reports and three `agent_said` records. Assistant repetition is not independent confirmation of a user fact. The routine's semantic claim retains its explicit start and unbounded end. Historical claims with unknown applicability appear with historical-use labels in host context. No delivered answer in these 11 acknowledgments turns those labels into a claim of current applicability or verified external action.

Two accepted episode organization statements do lose an uncertainty qualifier:

- **History 000, original Chinese:** the answer says “不代表已保存为持久安排。” The episode says “说明未作为持久安排保存”. English gloss: the answer does not establish completed durable storage, whereas the episode states that the arrangement was not durably saved.
- **History 002, original Chinese:** the answer says “不能确认已持久保存。” The episode says “声明未持久保存”. English gloss: inability to confirm persistence becomes a statement of non-persistence.

These are bounded but real errors in stored organization text. The delivered answers and corresponding `agent_said` claims retain the original uncertainty. The repeated persistence disclaimers are also formulaic and unnecessarily defensive for routine acknowledgments, even where factually cautious. No paid retry replaced these outputs.

Offline evidence checks confirm that each host received exactly the frozen instructions, consumption protocol, recorded compact memory, and original current input. All compact source versions precede that turn; shown memory statements match their persisted versions. Source hashes and cited spans validate. The actual local delivery equals the host answer and reviewed draft hash in all 11 stages. These checks establish provenance and delivery consistency; independent reading supplies the semantic verdict. Neither a model reviewer nor a valid source hash proves truth.

## Usage, timing, and remaining evidence

There were **38 calls with confirmed usage: 374,526 input + 85,508 output = 460,034 total tokens**. They comprise 11 host calls, 11 answer-review calls, and 16 background semantic calls. All 38 reservations have one recorded completion; unknown usage is zero. The ledger records 89,344 input cache-hit tokens and 285,182 cache-miss tokens. Reserved output was 397,312 tokens. These are token counts, not a currency invoice.

The frozen ceiling was 624 calls and 8,052,736 reserved output tokens per arm, with separate explicit cognitive limits. The operator stopped for repeated learning failure, not quota exhaustion, and did not authorize a paid continuation or repair loop.

Across all 11 recorded base-stage timings, median foreground time was **17.39 seconds** (13.50–44.41), median awaited background learning was **75.81 seconds** (39.39–121.71), and median total stage time was **93.59 seconds** (56.03–139.37). Foreground includes host generation, the additional model reviewer, and local runtime work. These are not isolated retrieval benchmarks or normal desktop-chat latency measurements.

The current-input acknowledgments do not establish 100-history retention, rare-cue recall, changed-preference handling, conditional recall, held-out false-premise resistance, or an advantage over either baseline. No claim of biological forgetting calibration or years of operation follows from this stopped synthetic run. Continued paid comparison should follow a deterministic reproduction and repair of the publication blockage, using a newly frozen plan while retaining this failed evidence.

The archive was checked for byte-exact round-trip recovery, matching source hashes, complete known usage, and actual local secret values without printing them. It contains no API keys, lease tokens, or hidden reasoning text. Provider reasoning-token counts remain usage metadata. Delivery receipt `authorization` objects contain event/version and context/fence bindings, not bearer credentials. No model calls, database mutations, or test-suite runs were performed to assemble this report.
