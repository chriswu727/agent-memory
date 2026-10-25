# Continuous-learning repair diagnostic stopped at historical integration

All **eight completed exchanges encoded new source-grounded claims and an episode**, including the school report that previously stalled. The run nevertheless **failed its sustained-learning acceptance**: the eighth exchange's separate history integration proposed an unsupported assistant-based abstraction, was rejected before verification, and triggered the planned stop. The original reports retain this failure.

The [structured review](deepseek-2026-10-25-learning-repair-stopped.json) and [byte-exact evidence archive](deepseek-2026-10-25-learning-repair-stopped.evidence.tar.gz) preserve the plan, original reports, all provider reservations and completions, eight delivery receipts, and a separate offline failure replay. They do not replace or revise the [earlier stopped experiment](deepseek-2026-10-25-long-history-stopped.md).

## Frozen scope and outcome

The diagnostic ran commit `4f1a41d0c595b7f4693c7089cf5febf7c368019e` with migrations 001–021 in a separate PostgreSQL database. It used `deepseek-v4-pro` with thinking enabled and the first sixteen original history fixtures, seed 20261025. Only the memory arm was planned. The fixed ceiling was 128 calls and 1,769,472 reserved output tokens; the explicit server cognitive allowance was 96 calls and 9,437,184 input bytes over thirty days. Each new encoding allocation retained its four-call total and two-call phase limits.

Histories 000–007 completed their foreground turns and delivery. The runner then exited with status 1 and recorded `learning_processing_incomplete`. Histories 008–015 did not run. No paid retry or continuation occurred. There were no held-out probes, baseline comparison, embeddings, QQ delivery or operating-system restart. Each fixture uses a separate conversation, so this run does not verify later-input funding within one conversation or across conversations.

| History | New claims | New episodes | Historical comparison |
| --- | ---: | ---: | --- |
| 000 | 2 | 1 | No separate history job; one additional claim rejected |
| 001 | 2 | 1 | No separate history job |
| 002 | 2 | 1 | No separate history job |
| 003 | 4 | 1 | Three assessed `no_change`; eleven explicit objectives remain |
| 004 | 2 | 1 | Three assessed `no_change`; seventeen remain |
| 005 | 2 | 1 | Three assessed `no_change`; nineteen remain |
| 006 | 2 | 1 | Three assessed `no_change`; twenty-seven remain |
| 007 | 2 | 1 | Invalid proposal; three selected objectives unassessed, twenty-nine other objectives remain |

These remaining counts belong to distinct roots; unresolved broader discovery is recorded separately. No belief relations were published. The original source records remain present. Successful new-input encoding does not establish successful historical integration, accurate organization text, or completed processing of every candidate.

## Why historical integration failed

At history 007, the model proposed an `inferred_pattern` saying that the assistant delivered confirmations of numbered notes with labels and pages. Its four citations all refer to delivered assistant messages. The existing evidence rule permits those messages as attributed speech; it does not permit them to prove this claim kind. Both the claim and its `abstracts` link were rejected as `invalid_proposal`. No verification call followed.

The offline replay reproduced the original proposal messages, response schema and request identity exactly, then replayed the recorded response through the processor with a fake provider. Both first exceptions were `assistant_cannot_prove_claim`. JSON structure, source versions, hashes and citation spans were valid. Independently, assistant endpoints fail the abstraction actor gate, and two literal endpoints lack the required independent-instance metadata. This is a capability mismatch, not damaged evidence or an exhausted quota.

The ordinary consolidation plan already contained zero eligible abstraction objectives, but the separate historical manifest offered generic comparison targets. The proposal instructions did not make the unavailable relation capabilities sufficiently explicit. The repair must communicate those capabilities before generation while retaining the evidence guards and a valid `no_change` outcome. It must not turn the failed selected objectives into assessed work, relax assistant evidence rules, or automatically spend another pair retrying them.

## Attention and semantic findings

Independent code-and-evidence review found that stages 003–005 admitted fourteen, twenty and twenty-two memory versions as historical comparison objectives, although they represented only six, eight and nine distinct supporting-event sets. Sharing a source does not prove two claims equivalent, but multiple representations of the same reply consumed scarce comparison slots. All nine assessed objectives in those three jobs returned `insufficient_shared_meaning`.

Lexical recall is intentionally broad. Character-only overlap, source-span bridging and repeated note templates can be useful retrieval cues; automatically converting every positive result into paid comparison debt is a separate mistake. A bounded attention policy needs source-contribution diversity, explicit unselected opportunities, and exploration for rare cues. It must preserve ordinary recall and complete provenance closure. Funding alone cannot resolve an ever-growing comparison queue.

Developer agents reviewed complete inputs, delivered answers and published versions; the root reviewer also inspected histories 006–007. This is neither a blinded human assessment nor a statistical accuracy estimate. All eight answers faithfully acknowledge their current reports. The school answer keeps the city unknown; note names, label numbers and pages remain distinct. These acknowledgments are not historical-recall tests. The eighteen published claims retain attribution, and assistant repetition is not independent confirmation of the user's report.

Two accepted episode labels retain semantic problems:

- History 000 changes “我目前没有可用的已保存记忆来确认这条安排已经存储” into “助手确认并说明暂无已存记忆”. The label drops the availability and confirmation-purpose qualifications, broadening the absence claim. The corresponding speech assertion preserves them.
- History 007 describes the assistant as “确认按该说明记录”, whereas the reply only acknowledges and repeats the note. This adds ambiguous recording/confirmation specificity; it does not establish that storage did or did not occur. The corresponding speech assertion quotes the actual reply.

History 000 also rejected an otherwise faithful attributed conditional invitation about planning or reminders. Offline inspection found `world_mismatch`: lexical detection classifies “如果…” as hypothetical, while the candidate reports the actual speech act as `real_world`. This exposes a distinction between a communication event and the world described inside it. It requires its own repair that preserves explicit fictional and quoted-source boundaries; it is not a reason to weaken the assistant-evidence guard implicated in history 007.

The subsequently published [source-backed navigation change](../architecture/episode-source-navigation.md) prevents old free-form episode labels from entering future factual model contexts. It was **not included in this frozen run**, does not repair these raw labels, and does not certify later semantic accuracy.

## Usage, latency and integrity

Actual use was **257,106 input + 110,135 output = 367,241 tokens**, across 37 known completed calls: eight host calls, eight additional answer-review calls, and twenty-one background semantic calls. Unknown usage was zero. Input cache totals were 70,272 hits and 186,834 misses. Reserved output was 442,368 tokens. These are token counts, not a currency invoice.

Median foreground runtime was **16.11 seconds** (9.55–29.50), median awaited background learning **115.60 seconds** (83.58–196.64), and median total stage time **129.26 seconds** (106.86–212.69). Foreground includes the host model and extra answer reviewer; the harness waits separately for learning. These measurements are not isolated retrieval latency or normal desktop response time.

Offline checks verified all ninety planned code fingerprints against the frozen Git commit, all twenty-one migration files, the complete known-usage ledger, exact host inputs and deliveries, source hashes and citation spans, and byte-exact archive recovery. Local secret values were checked without printing them. The archive contains no API keys or hidden reasoning; provider reasoning-token counts and delivery event/version bindings remain audit metadata. The raw memory report SHA-256 is `a2e979847c10f6e136aaed3f699dc379b2230cf182dc74a5cb6c2c9de65f85fe`.

The earlier publication blockage is repaired for these eight inputs. Sustained learning, useful abstraction, bounded backlog, rare-cue retention, false-premise resistance and baseline advantage remain unaccepted. A separately frozen follow-up must test the concrete repairs and preserve this failed run. No routine CI or Codex/Claude subscription tests were used.
