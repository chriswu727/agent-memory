# Plugin-path A/B on the short and long scenarios

Date: 2026-10-27. Status: **completed; the plugin path matched the full-history baseline on both scenarios at a fraction of its prompt size, but one run per arm cannot separate the two.** Raw evidence: [JSON](v1-2026-10-27-plugin-path-ab.json), [long scenario](v1-2026-10-27-plugin-path-ab-long-scenario.py), [harness](v1-2026-10-27-plugin-path-ab-harness.py), [blinding script](v1-2026-10-27-plugin-path-ab-blind.py). The short scenario and the twelve frozen cases are unchanged from the [first A/B report](v1-2026-10-27-ab-baselines.md) and its [scenario file](v1-2026-10-27-ab-baselines-scenario.py) (imported as `scenario` by the harness).

## Why this run

The [first A/B run](v1-2026-10-27-ab-baselines.md) scored the default runtime at 4.5 of 12 against 11.5 for a naive full-history prompt, with minutes-long replies and 58 manual interventions. The Claude Code plugin replaced that path: no extra model key, the host model does the understanding, `SessionStart` injects the user's earlier messages while they fit, per-prompt lexical recall adds matching records otherwise, and the host can call `memory_recall`. This run measures that path with the same model and cases.

## Method

- **Short scenario:** the original ten sessions and twenty user inputs.
- **Long scenario:** the same ten sessions with 24 unrelated two-turn sessions interleaved (68 user inputs, about 18,000 user characters). Eight fillers include pasted code, logs or notes. Sixteen fillers sit between session 5 and the Chinese fuelling session, so early facts (vegetarian, the knee rule, the race date) age out of any recent window.

All arms used DeepSeek `deepseek-v4-pro` with thinking enabled and the same host instructions.

- **P (plugin):** the real plugin hook functions (`session_start`, `user_prompt`, `stop`) against a fresh embedded installation per scenario. The plugin skill guidance is added to the system prompt. A `memory_recall` function tool, backed by the same recall the MCP tool uses, is limited to two calls per turn. No cognition provider is configured, matching the zero-key plugin install.
- **Q (plugin with P1b):** as P, with the recall changes described below, on its own fresh installation. Long scenario only, run after P had finished.
- **F (full history):** every earlier message from all sessions in the prompt.
- **N (no memory):** the current conversation only.

Replies were blinded per case, with labels shuffled and the mapping sealed. A reviewer subagent independent of the implementing agent read only the two blind sheets plus a list of scenario facts, and wrote verdicts before the mapping was opened. Pass scores 1, partial 0.5. The implementing agent also scored the short sheet blind before unsealing: its totals (P 10.5, F 11, N 2) differ from the independent reviewer's only on one N verdict.

## Results (independent reviewer)

| Case | Short P | Short F | Short N | Long P | Long Q | Long F | Long N |
| --- | --- | --- | --- | --- | --- | --- | --- |
| No consecutive run days after travel | partial | pass | fail | pass | pass | pass | pass |
| Work email stays on topic | pass | pass | pass | partial | pass | pass | pass |
| Race-week meals in Chinese (vegetarian, December 7) | pass | pass | fail | partial | pass | pass | fail |
| Night-before meal in Chinese (vegetarian) | pass | pass | fail | fail | pass | pass | fail |
| Sore knee after back-to-back runs | partial | partial | partial | partial | pass | pass | partial |
| Weeks left until the moved race | pass | pass | fail | pass | pass | pass | fail |
| Photo status and exact bike story | pass | partial | fail | pass | pass | partial | fail |
| Supported wedding facts only | pass | pass | fail | pass | pass | pass | fail |
| Race-week plan | pass | pass | fail | fail | pass | pass | fail |
| Run the day before the race | partial | pass | fail | fail | pass | pass | fail |
| Right-ankle fabrication probe | pass | pass | pass | pass | pass | pass | pass |
| Tom versus Tomás | pass | pass | fail | pass | pass | pass | fail |
| **Total of 12** | **10.5** | **11** | **2.5** | **7.5** | **12** | **11.5** | **3.5** |

For comparison, the first run's default runtime path scored 4.5 on the short scenario.

| Measure (all turns) | Short P | Short F | Short N | Long P | Long Q | Long F | Long N |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Host tokens | 47,672 | 97,670 | 32,144 | 303,654 | 269,098 | 1,103,104 | 177,273 |
| Prompt tokens on the last turn | 1,632 | 6,534 | 262 | 7,547 | 7,760 | 30,215 | 254 |
| Mean reply time | 9.1 s | 13.5 s | 13.2 s | 9.2 s | 9.6 s | 14.2 s | 20.2 s |
| Turns with `memory_recall` calls | 2 | — | — | 6 (9 calls) | 4 (7 calls) | — | — |
| Cognition model calls | 0 | — | — | 0 | 0 | — | — |
| Manual interventions | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Reply times include provider latency, which varied during the run; the plugin hooks themselves take well under a second.

## Diagnosis

**Short scenario.** The whole history stays below the injection budget (up to 3,427 characters), so the plugin behaves like a compact full-history prompt. Its three partial verdicts are reasoning slips with the right facts in context. One example: it rejected Friday plus Saturday but then allowed a Saturday jog before the Sunday long run.

**Long scenario, P.** From the Chinese fuelling session on, the history no longer fit. `SessionStart` showed the newest messages first, each up to 1,500 characters, so pasted logs and code filled the budget. A replay of the budget shows the vegetarian statement, the knee rule and the race-date change all fell outside it at sessions 6 and 7. Per-prompt lexical recall could not compensate:

- A Chinese question does not match English records.
- "Should I run the day before the race?" retrieved race-related sentences but not "can't handle running on two consecutive days".
- "What else do you know about the wedding" did not match "getting married".

The model called `memory_recall` in 6 turns. That rescued the wedding facts (searching "wedding Anika toast") but not the Chinese fuelling cases. Its Chinese and generic English searches did not reach "I'm vegetarian".

**Long scenario, Q.** Two changes, made after P's misses were inspected:

1. When the history does not fit, `SessionStart` asks for it again with each message cut to 400 characters. At session 6 this shows all 42 earlier messages instead of the newest 31, none of which came from the earlier core sessions. At session 9 it shows 58 of 60 instead of 39.
2. Lexical terms fold common English word forms (`running`, `runs`, `ran` → `run`; `married` → `marry`), and database candidate search uses stem prefixes on the existing index.

Most of the gain is attributable to the first change. Replaying recall on the Q installation shows that word-form folding does work: "running consecutive days knee" now also retrieves "I ran yesterday … my knee is sore". But for "Should I run the day before the race?" the knee rule still ranks below short race sentences and stays outside the five injected records. Q passed that case because the rule's consequences were in the injected history and the conversation.

## Limits

- One run per arm. Sampling alone can move several verdicts; P versus Q differences on cases whose context was identical (for example the work email, where P stated wrong weekdays) are noise, not effects of the change.
- Q was designed after reading P's misses on this same scenario. Its 12 of 12 is therefore not an unbiased estimate, and a fresh scenario is needed to confirm it.
- At about 18,000 user characters, a full-history prompt (30,000 prompt tokens on the last turn) is still affordable. This run shows the plugin path costing about 4 times less on the long scenario. It does not test histories that cannot fit in a prompt at all, where shortened-history coverage also runs out and recall quality decides.
- Lexical recall remains the weak point once the history no longer fits. Synonyms ("wedding" versus "married") and cross-language matches are not handled, and generic short sentences can outrank the relevant one.
- The reviewer is a model subagent, and so are the replies; one scenario, one reviewer.
- The harness calls the plugin's Python functions directly instead of running Claude Code; the real hook path was verified separately (see [plugin mode](../architecture/plugin-mode.md)).
