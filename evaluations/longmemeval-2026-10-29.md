# LongMemEval, 2026-10-29: a 30-question sample

Roadmap item A6, second benchmark. LongMemEval-S gives each question its own chat history of about 115,000 tokens (40 to 50 sessions); the question asks about something said in one or more of those sessions. Status: **sample only**. Storing and embedding one history took 12 to 18 minutes on the machine used (four cores, shared with other runs), so the memBrain arm was stopped after 30 questions.

## Result

DeepSeek-V4.1-Flash answered with thinking disabled. It was judged with LongMemEval's own judge prompts (`get_anscheck_prompt`, including the abstention variant). All three arms answered the same 30 questions.

| Question type | Questions | memBrain (`M`) | Full history (`F`) | No memory (`N`) |
| --- | --- | --- | --- | --- |
| Single session, user said | 6 | 6 | 6 | 0 |
| Single session, assistant said | 5 | **1** | 5 | 1 |
| Single session, preference | 3 | 2 | 1 | 0 |
| Multi-session | 6 | 4 | 2 | 0 |
| Knowledge update | 6 | 6 | 6 | 0 |
| Temporal reasoning | 3 | 2 | 2 | 0 |
| Abstention | 1 | 1 | 0 | 1 |
| **Overall** | 30 | **22 (73.3%)** | **22 (73.3%)** | 2 (6.7%) |

- memBrain and full history tied overall. Each was right on five questions the other got wrong. Full history scored 75.8% on all 62 questions of the stratified sample, against 73.3% on these 30, so these 30 are not unusually easy for it.
- memBrain gave the model about 17,800 characters per question (session context plus recall). Full history sent about 115,000 tokens. Prompt tokens per question were about one twentieth.
- **Weak spot: what the assistant said.** memBrain answered one of five questions about the assistant's own earlier replies ("which bottle did you recommend fifth?"). This follows from a design choice: recall by meaning leaves out the assistant's own speech, so a model's suggestions are not mistaken for facts about the user; only keyword matches reach it. The other four misses were two multi-session counts, a temporal question and a preference.
- Knowledge updates (the newest value of something that changed) were all right in both arms.

## Method

- **Data**: `longmemeval_s_cleaned.json` (500 questions). A sample of 62, stratified by question type and abstention with seed 7, was drawn with `--sample 60`. `N` and `F` answered all 62. `M` ran as three shards (`--shard 1/3` to `3/3`, each its own installation) and was stopped after 30 answers. Shards take every third question in id order, so the 30 are the first 30 of the sample by question id. Ids are random hex, so this does not favour a type.
- **memBrain arm**: per question, its own user memory in an embedded installation.
  - Every turn of the history was stored as the hooks store messages, with the session date prefixed and the real roles (user and assistant).
  - The local multilingual embedding model was installed, and the question was asked only after every stored message was embedded. Recording was then paused.
  - The question was a new session: session-start context and per-message recall were injected, and the model could call `memory_recall` up to twice. It did so on 2 of 30 questions.
  - The reader prompt was LongMemEval's own non-chain-of-thought prompt, with memBrain's context in place of the history.
- **Full history**: the whole history in LongMemEval's reader format. **No memory**: the question only.
- **Harness**: `evaluations/longmemeval/run_longmemeval.py`. `pair_arms.py` produced the tables. Raw answers and usage are in `evaluations/longmemeval/results-2026-10-29/`.
- Nothing was changed in response to these questions.

## Cost

- memBrain: 62 calls, 174,727 prompt tokens and 3,322 completion tokens (answers and judging, 30 questions).
- Full history and no memory together: 249 calls, 7.16 million prompt tokens and 10,344 completion tokens (62 questions each).
- The DeepSeek balance fell from 412.75 to 403.67 CNY (9.08 CNY) while this ran, alongside the end of the second LoCoMo run.

## Limits

- 30 questions, one run, a same-model judge. A difference of one question is 3.3 points; this sample cannot tell apart systems within about 15 points of each other.
- Embedding speed is the bottleneck: the local model embeds one memory per background job. Running all 500 questions would take days on this machine, and batching embeddings is the fix ([changelog](../CHANGELOG.md), Known limits).
- As in LoCoMo, no notes were saved, because there is no host model in the conversation at ingestion time.
