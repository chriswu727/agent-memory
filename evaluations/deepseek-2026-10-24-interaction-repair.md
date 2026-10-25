# Current requests, episode organization, and memory consumption: 2026-10-24

This work added source binding for current requests, episode-organization claims with whole-source verification, archive compatibility, and a memory-consumption protocol. Live testing confirmed that all three question turns reached the host correctly, both long-term preferences and episodes were actually published, and cross-conversation recall used historical semantic versions. However, the host still described the latest record with unknown applicability as the current arrangement. **Complete acceptance has not passed.** Publication was initially paused after review; the user subsequently explicitly requested delivery of the current implementation together with the failure report. Evaluation verdicts remain unchanged.

The [structured report](deepseek-2026-10-24-interaction-repair.json) preserves the original five-turn experiment, independent diagnostic, three subsequent controls, call ledger, and independent developer review. Original statuses, failures, and pending-review fields were not rewritten; conclusions are stored separately under `developer_review`. A developer agent reviewed complete sources, model inputs and outputs, and state. This was not an independent human benchmark or keyword scoring.

## Implementation

- `current_request` describes only a question or request actually made by the current user. The server binds it to the real turn/job and exact input versions, separately verifies the whole claim, request intent, and each source, and rechecks publication, snapshot recovery, and result reuse. Question presuppositions do not become historical facts, and background consolidation does not gain this authority.
- New episode proposals must provide an `organization_statement` explaining how interaction records form a group or change its boundaries. Verification includes all old and new members, exact old versions, source roles, and the resulting grouping, with at most 32 sources. Old records remain compatible.
- Archives strictly support the new organization fields and restore source-event version mappings correctly. `organization_source_version` is only an audit record of identity before conversion. It neither promises that an archive contains an old organization snapshot nor serves as independent evidence. Uploaded archives remain quarantined.
- Content with counterevidence or unsettled belief state is no longer labeled a directly usable current rule. The fixed interpretation protocol enters the host prompt, input budget, and generation fingerprint. Its v2 explicitly requires reading the current original message, forbids using a summary's omission to negate an explicit self-report or inventing dates to override runtime applicability, and asks for natural-language expression.

## Five live turns: 3/5 passed all pipeline checks; answers assessed separately

The experiment again used DeepSeek-V4-Pro thinking mode, local TCP HTTP, PostgreSQL, and local persistent delivery. Each turn rebuilt the service, client, and runtime and changed conversations, with recent-conversation supplementation disabled. There were no embeddings, semantic recall controller, hidden chat history, or reference answers. Only learn/change executed one background consolidation window each.

| Stage | All pipeline checks | Answer objective | Main result |
| --- | --- | --- | --- |
| learn | Passed | Failed | Long-term memory was published correctly, but the answer ignored the user's explicit current statement that this was a long-term preference |
| recall | Passed | Failed | Recovered the name and order but invented the current date and denied an arrangement already evaluated as applicable |
| change | Failed | Passed | A current statement was misclassified as a request and rejected; background learning correctly published the new preference, superseding relation, and episode |
| recall_change | Failed | Passed | Correct request interpretation and answer; an extra episode claim lacked complete support and was rejected |
| empty_control | Passed | Passed | Acknowledged missing history without guessing a name, order, or drink |

The learn answer treated the derived situation summary as all available material and said it did not directly state a long-term preference, ignoring the explicit wording in the final original user message. The database preference itself fully preserved the name, order, start, long-term intent, and lack of a scheduled end.

The recall semantic memory had `applicability=true`, and the turn date was 2026-10-24. The answer instead invented 2026-05-07 as the current date and said the arrangement had not started. This was a host information-use error; it does not negate that the historical semantic chain reached the model.

In change, the model represented a preference statement as `current_request`. Independent verification returned unknown for the request, whole claim, and sources, and the system rejected it. Self-reports must not automatically become requests to obtain an all-green result.

The extra recall_change episode claim mentioned “Origami Lamp” (original Chinese: “折纸灯”), but its sole cited member, the current question, did not contain that name. The name existed elsewhere in history but was not in this proposal's member evidence. This is a specific support gap, not knowledge of the verifier's private reason for returning unknown. The rejection retains its original verdict.

All three question turns were independently checked for complete question meaning, source versions and hashes, turn/job binding, request/claim/source verification, and the block actually sent to the host. The beach agreement remained an unconfirmed presupposition; no historical claim was created from the question.

Read-only database auditing confirmed that both open episodes from learn/change actually existed, with organization statements, members, and sources matching accepted proposals. The complete original verification-request payload was not captured. Its organization context was reconstructed from frozen code and saved sources, which must not be described as a byte-for-byte audit of that request.

## The original unknown-state diagnostic was incomplete; one v2 control still failed

After the five turns, a separate host diagnostic froze old context and inserted only the v1 generic protocol. Call 20 exhausted its 4,096-output-token limit and returned `incomplete_response`, without a complete answer. The original experiment's top-level state was therefore **interrupted**. Its semantic result cannot be assessed; it is neither a pass nor a semantic failure with a complete answer.

Following the two observed answer defects, the protocol was revised to v2 and three independent controls were fixed in advance: the original learn input, recall input, and unknown-state diagnostic input. Only the protocol system message changed. Memory, questions, original host instructions, model, thinking mode, and the 4,096 output cap stayed the same. Old answers and review rubrics were not sent to the model. The five turns were not rerun, and diagnostic answers were not written to the database, delivered, or learned from.

All three calls completed; **2/3 semantic objectives passed**:

- learn correctly acknowledged the current user's long-term preference. Its wording “我记下了” (original Chinese; “I've noted it”) is not evidence that this independent diagnostic persisted anything, and remains ambiguous.
- recall correctly recovered the name, order, and long-term preference, saying only that it could not find a drink agreement. It no longer invented the current date.
- The unknown-state diagnostic still failed. It first said it could not confirm whether the effective time had arrived, then stated, in the original Chinese: “但按记录中的最新约定，‘现在’的顺序是：先看教材，再散步。” English translation: “But according to the latest recorded arrangement, the order ‘now’ is: read the textbook first, then take a walk.” This still promotes the latest statement to current applicability, contradicting both memories' unknown applicability and the old belief's uncertain state. An initial hedge does not cancel a later definite conclusion.

These results show improvement on two specific regression samples, not reliable host compliance with the uncertainty protocol. The final failure was retained, without further calls or a larger output cap to seek all-green results.

## Usage and latency

The five turns and original independent diagnostic made **20 calls**, using 151,083 input and 81,733 output tokens: 232,816 total. The three later host controls used another 7,849 input and 2,317 output tokens: 10,166 total.

This batch totaled **23 calls and 242,982 tokens**, with all 23 usage records confirmed. Reserved output caps totaled 266,240 tokens, distinct from actual output of 84,050. The follow-up ledger did not reset earlier usage, and token counts are not billing amounts.

For the original five turns, the two foreground cognitive calls plus one answer call accumulated approximately **62, 66, 159, 64, and 41 seconds** of provider time. These are metered request durations, excluding complete end-to-end overhead. This strong thinking-model configuration is still unsuitable for instant-chat speed; the roles and budgets of foreground interpretation and background consolidation need further optimization.

## Reproduction, validation, and delivery status

`examples/evaluate_interaction_repair.py` provides `--plan / --live / --resume` for the original 20-call experiment. `examples/evaluate_consumption_repair.py` runs the three additional controls. The latter accepts only a source with complete known usage, or the exact failure state here: all five turns complete, with only the final frozen diagnostic hitting its output cap. It does not generally allow other interrupted runs. Both reject repeat paid execution when the output directory already exists; `--resume` is read-only.

The original report's SHA-256 is `553bb310ff2f992ddae91eb8985e78dc290ff75abb3cadf5a774a71b7531f1ca`; the follow-up's is `afde0cbf0c5ded7a3083da0fd65d308b1037d04404a5f0da5d8acc534abade3e`. The full five turns used v1. In the final production code, only the runtime protocol changed to v2; all other production-code fingerprints from the original experiment remained identical. The full five turns were not rerun with v2.

The final full Python run had **2,010 passing tests** in 115.90 seconds. Ruff, package build, and diff checks passed. The initial archive regression was fixed. Database disconnections during earlier parallel testing had no confirmed root cause and did not recur in the serial full run. TypeScript was unchanged and its tests were not repeated. No cloud CI was added or triggered, and Codex/Claude subscriptions were not used as models under test.

Remaining issues include hosts potentially overriding unknown states, unstable situation classification and episode support, and high real foreground latency. Under the user's repository rule to report validation failures first, commit and push were initially paused. The user subsequently explicitly requested publication of the local code, so the implementation and all evidence were delivered together. The structured report preserves delivery status at the original review and records the later publication decision separately. Passing engineering regressions or publishing code does not constitute complete product acceptance.
