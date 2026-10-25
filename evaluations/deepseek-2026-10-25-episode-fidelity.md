# Episode fidelity diagnostic: both contracts retain two false acceptances

The fixed-proposal diagnostic completed all **16 calls with known usage**, but semantic acceptance failed. Both `communication-intent-v1` and `episode-fidelity-v1` accepted two unsupported organization statements: uncertainty became a definite negative, and studying became exam preparation. The additional four fidelity verdicts did not improve either recorded failure in this run.

The [review and manifest](deepseek-2026-10-25-episode-fidelity.json) and [byte-exact original evidence](deepseek-2026-10-25-episode-fidelity.evidence.tar.gz) preserve the frozen inputs, requests, responses, local gate results and persistent call ledger. The raw report remains unchanged, including its `pending_developer_review` field. The separate review records the completed developer assessment rather than rewriting model evidence.

## Frozen method

The run used commit `4f1a41d0c595b7f4693c7089cf5febf7c368019e`, archived separately from ongoing development. The model was `deepseek-v4-pro`, with thinking enabled, 8,192 output tokens per call, a 65,536-byte decoded response limit, and a 240-second timeout. The frozen ceiling was sixteen calls and 131,072 reserved output tokens. There were no retries or paid continuation.

Eight fixed source/proposal pairs were each verified once under each contract. Two pairs contain original source events and proposals from previously preserved failures; six are controlled cases. The recorded pairs use their complete original episode members, excluding unrelated historical context. This is not a replay of the entire earlier batch. Requests were built by the production semantic processor, and responses were replayed through the production gate without another provider call. No proposal generation, database, publication, recall, host generation or delivery occurred.

An independent developer agent reviewed the eight complete source/proposal pairs before reading model responses. Its first file read exposed the first developer expectation; subsequent views excluded expectations. The root agent also reviewed the complete pairs and outcomes. This is developer-agent review, not a fully blinded human assessment or an independent clinical/cognitive evaluation. The expectations were never included in model requests.

## Results

| Case | Source-grounded verdict | Previous contract | Four-facet contract |
| --- | --- | --- | --- |
| Recorded uncertainty about storage | Reject | Accept | Accept |
| Faithful uncertainty | Accept | Accept | Accept |
| Recorded studying narrowed to exam preparation | Reject | Accept | Accept |
| Explicitly reported exam preparation | Accept | Accept | Accept |
| Fictional speaker attributed to the real user | Reject | Reject | Reject |
| Unsupported causality | Reject | Reject | Reject |
| Explicitly attributed negative | Accept | Accept | Accept |
| Discussed reminder described as completed scheduling | Reject | Reject | Reject |

Each contract agreed with six of eight developer judgments, with two false acceptances and no false rejections. These are small, deliberately selected diagnostics, not accuracy estimates over real conversations. One response per case does not characterize sampling variability.

In the first recorded case, the original assistant says “不代表已保存为持久安排”; the candidate says “说明未作为持久安排保存”. Lack of confirmation does not establish non-storage. Rejecting the candidate also does not prove that storage occurred. The distinction matters even if an informal conversational reading could imply non-storage.

In the other recorded case, the source says “复习安排” and mentions reading a textbook; the candidate adds “exam-preparation”. Exam preparation is plausible, but the supplied statements do not establish that purpose. The newer verifier returned `supports` for all four facets on both unsupported candidates. Structurally complete verdicts therefore cannot be treated as proof that qualifications were preserved.

## Consequence for the architecture

The current episode store already distinguishes inferred organization from a `MemoryAssertion`. Its organization text is not directly rendered as an ordinary host memory, and pattern construction uses member memories rather than that text. However, raw episode documents still enter later model contexts, where an incorrect organizing label can influence new interpretations. Grouping can also influence candidate discovery even if its label is hidden.

A source-backed navigation projection is the next repair: retain exact member identities and complete source qualifications, keep free-form organizing statements and verifier outcomes in the audit record, and prevent those labels from entering future factual context as evidence. It must preserve old frozen request bytes. This is a proposed containment boundary, not a correction of the false verifier judgments and not evidence that grouping or downstream claims become infallible. The continuous-learning diagnostic separately tests the earlier publication stall; it cannot close this semantic-quality gate.

## Usage and integrity

Actual usage was **47,554 input + 28,127 output = 75,681 total tokens** across sixteen confirmed calls, with zero unknown outcomes. Input cache counts were 27,264 hits and 20,290 misses. These are token counts, not a currency invoice.

The raw report SHA-256 is `d4197f39fd08a57bbde31617fdf9dd37391ebfb73bcc4400153e8cbf7f35e690`. The evidence archive SHA-256 is `50b7b3489910d1291c0eaddfece1e5713ad2ee941bfe31694c0794ae2535486b`. All five archived files were recovered byte for byte and checked against local secret values without printing them. The archive contains no credentials or hidden reasoning. No CI or Codex/Claude subscription tests were used.
