# On-demand orientation: latency, memory behavior, and failures

This implementation reduced foreground model waiting for ordinary recall, but did not pass all background-learning acceptance checks. In five live turns with DeepSeek-V4-Pro thinking mode, all five answers passed independent answer review and were delivered. Developer review also found them grounded in this fixture's sources. The complete pipeline passed **4/5** stages. One background run additionally proposed rebuilding an old episode across conversations and was correctly rejected; its original partial result and command exit code 1 remain recorded.

See the [structured evidence](deepseek-2026-10-24-orientation-routing.json) for complete inputs, outputs, routing proofs, usage, and independent review. It freezes the code and results at the time of this trial. Later fixes do not turn original failures into passes.

The input-contract gap was subsequently repaired, and one independent two-call processor follow-up used the original batch's sources, as reported below. The complete five-turn chain was not rerun; the original 4/5 verdict stands.

## Bottleneck and architectural tradeoffs

The earlier five-turn experiment accumulated 282.36 seconds of foreground provider time, including 201.03 seconds for situation proposal and verification before answering, approximately **71.2%**. Read-only local `_prepare` diagnostics took about 11–117 milliseconds, excluding HTTP, locking, and log writes. Repeated database reads were worth optimizing, but did not explain response waits of tens of seconds.

The new `RuntimePolicy(orientation_policy="on_demand")` lets the server inspect candidate dependency closure before applicability filtering. Unconditional material and empty history can use the existing context package directly. Conditional memories or relations, working items, open loops, incomplete dependencies, existing orientation jobs, or configured semantic recall controllers/vector recall retain full analysis. The default `always` mode is unchanged, and this decision makes no classifier-model call.

This does not silently replace generated situation summaries with unverified text. Skips are explicitly recorded as skip, create no successful semantic job, and derive no immediate goals or tasks. Raw input still reaches the host separately, is durably captured, and enters a background learning window. Agents requiring immediate task state should still use full mode.

Routing proofs bind input, scope, sources, and dependencies, with revalidation at preparation, generation, idempotent replay, and delivery boundaries. Background insertion of new beliefs does not necessarily advance existing version counters, so the server also rereads the original preparation request. It does not cache the whole context across model waiting. Answer review and source, time, correction, and deletion checks remain. The tradeoff is that the complete fast path typically makes five local preparations or validations; other-conversation or index changes can conservatively trigger `context_stale`.

Semantic-job snapshot reads were also changed from per-document database queries to batches by document type, reusing documents verified within the same recovery operation. Exact versions, original-text hashes, access scope, deletion, and invalidation checks remain. Authorization results are not cached across requests.

## Live test with the same questions

The trial reused the previous five-turn fixtures, host instructions, model, and answer-review rules, with a fresh isolated brain and ledger. Each turn rebuilt the service, client, and runtime, changed conversations, and disabled recent dialogue. There was no semantic recall planner or embedding. It ran through the local TCP API, PostgreSQL, and a durable delivery file. A maximum of 24 calls was frozen before execution, without automatic retries or repair loops.

| Stage | Historical full-mode foreground model wait | This on-demand foreground model wait | This complete reply time | Full pipeline |
| --- | ---: | ---: | ---: | --- |
| Initial learning | 69.54 s | 17.71 s | 18.24 s | Passed |
| Recall in a new conversation | 51.92 s | 31.88 s | 32.78 s | Passed |
| Preference change | 78.09 s | 19.95 s | 20.78 s | Background partial rejection |
| Recall of old and new arrangements | 52.14 s | 25.86 s | 27.00 s | Passed |
| Empty-memory control | 30.67 s | 9.00 s | 9.60 s | Passed |

Comparable foreground model waiting averaged **56.47 seconds previously and 20.88 seconds here**, an observed reduction of about **63%** in this sample. Complete reply time here, including capture, database operations, answer review, and delivery, averaged **21.68 seconds**. The old report did not separately measure complete reply time, so an improvement ratio must not compare old model time with new complete reply time.

This is a historical before/after comparison, not a paired A/B on the same version, and the sample is insufficient to estimate tail latency or general accuracy. Code and model outputs differed between runs. What is directly established is that all five turns legitimately skipped orientation, reducing foreground orientation from ten calls to zero while retaining five host-generation and five answer-review calls. Main-model generation totaled about 25.55 seconds; answer review totaled about 78.85 seconds, now roughly **75.5%** of foreground model waiting. This still does not achieve instant-chat speed.

The two background learning stages took approximately 74.76 and 120.97 seconds. They were excluded from time spent waiting for a reply, but all their calls and tokens were counted. Reducing foreground analysis did not make background learning faster.

## Answer and memory review

All five natural-language answers met the predefined source criteria: accurately acknowledging the user's original words; recovering “Origami Lamp” (original Chinese: “折纸灯”) and the old order from actual history; confirming the new order; distinguishing current arrangements from the superseded past; not confirming drink or beach agreements; and not guessing names or orders with an empty brain. Current input was not reused as old memory, and assistant restatements did not replace user supporting evidence.

Initial learning published the user's preference and an episode. The change stage published the new preference, a new episode in the current conversation, and an explicit supersedes relation. In subsequent host context, the new preference was usable and the old one superseded; the answer correctly distinguished them.

The change stage also proposed `episode-create-learn-20261020`, whose members came from an old conversation while the job belonged to the new one. Deterministic validation reported `Episode member source is unavailable or out of scope`. This proposal did not reach semantic verification or publication. Historical sources being readable for old/new comparison does not authorize cross-conversation episode organization in the current job.

The input did have an expression gap: it supplied old sources for relation comparison without an explicit episode-operation scope, eligible-member set, or current learning-input labels. `episodes=[]` meant no editable episode in the current conversation, but could be misread as meaning historical sources had never been organized. This cannot be asserted as the model's internal reason for the error, but the real interface gap can be repaired. The fix preserves all permission and evidence checks, keeps old sources needed for comparison, and does not treat learning references as current-request authorization.

The first answer also volunteered an explanation of persistent-memory state and offered a reminder, making it somewhat technical. It did not invent completed storage, but naturalness still has room to improve.

## Usage and validation

This run made **14 calls using 118,460 tokens**: 76,948 input and 41,512 output, with all usage known. Reserved output allowance was 126,976, distinct from actual output. Including the preceding answer-review batch gives 58 calls and 421,374 tokens. This excludes earlier project experiments and does not equate token counts with billing amounts.

The trial version passed **2,314 Python tests**, 12 TypeScript tests, type checking, Ruff, and build checks. Packages included the new runtime and migrations 010/011. New tests cover conditional-memory INSERTs without version-counter changes, changes during generation, delivery without a reviewer, changed recovery policies, finalizing already-delivered turns, forged preflight proofs, and learning-job enqueueing.

These five cases do not establish reliability with long histories, weeks of continuous use, high concurrency, arbitrary agent integrations, or request classification in full mode. The performance gain also does not establish capability equivalence between skipping immediate goal inference and full mode.

## Follow-up: explicit episode-operation scope

New general jobs freeze `episode-operation-scope-v1` and provide `episode_operation_scope`, `episode_eligible_member_refs` computed under existing evidence rules, and separate `learning_input_refs`. Other historical sources remain available for comparing old and new beliefs; the original cross-conversation proposal is still rejected. Learning references do not enter `current_input_refs` and do not authorize current requests. Old jobs without a version retain their original prompt and input. Reconstructed messages and schemas exactly matched all four general proposal/verification requests in the original report.

Boundary regressions also fixed two compatibility problems: valid empty text can remain in a processed learning window, but cannot be an episode citation; window and neighborhood budgets now count actual learning labels, reducing batch size when long identifiers exceed the budget while retaining the original 2,048-byte sampling reserve. Call and request limits were not increased. Historical sources are not all counted as current learning inputs.

`examples.evaluate_episode_scope_repair` freezes the original change batch's four sources, eight belief versions, working state, sampling, and comparison plan. It adds only the three metadata fields above and general constraints to proposal input. Original processor outputs and review rubrics are not sent to the model; historical assistant messages retain their original attribution. Original report and ledger hashes are preserved. The new independent ledger permits at most two calls and 32,768 reserved output tokens, rejects reruns into an existing directory, stops on unknown usage, and provides read-only `--resume`.

The live follow-up processor returned **completed, with no rejections**. The new preference, assistant restatement, source-supported constraint, one current-conversation episode, and a supersedes relation targeting the exact old version all passed independent model verification. Six machine checks passed. Only the current conversation's two records became episode members, with no renewed proposal to rebuild the old conversation. Independent developer review found complete source support for all five accepted proposals, and the extra constraint gained no current-request authority. This was not cross-validation with another model family. Complete evidence and separate source review are in the [focused follow-up record](deepseek-2026-10-24-episode-scope.json).

This follow-up ran only the semantic processor, without connecting to a database, publishing new memories, generating host answers, or delivering messages. It supports a focused conclusion about the scope-contract repair. It does not turn the original five-turn result into 5/5 or resolve earlier request-intent classification problems in full `always` mode.

The follow-up made **2 calls using 45,381 tokens**, comprising 30,536 input and 14,845 output, all confirmed. Together with the original five turns, that is **16 calls and 163,841 tokens**. Including the preceding answer-review batch gives **60 calls and 466,755 tokens**. Earlier usage was not reset, and no retries were added.

The final implementation passed **2,393 Python tests** in 134.97 seconds, Ruff, and build checks. Every top-level Python module, contract, and migration 010/011 in the wheel matched source. The 12 TypeScript tests and type checking had passed earlier in this batch, with no subsequent changes to those files. No GitHub CI was added or triggered.
