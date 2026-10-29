# LoCoMo harness (roadmap A6)

`run_locomo.py` measures end-to-end answer accuracy on [LoCoMo](https://github.com/snap-research/locomo) (`data/locomo10.json`: 10 conversations, 1,540 questions in categories 1 to 4) for three arms answered by the same host model: no memory (`N`), the full conversation in context (`F`), and memBrain through the plugin path (`M`). Answers are judged with Mem0's published LoCoMo judge prompt, used verbatim.

```sh
curl -L -o locomo10.json https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json
export DEEPSEEK_API_KEY=...            # read from the environment, never from a file in the repository
python evaluations/locomo/run_locomo.py --data locomo10.json --out evaluations/locomo/run-1
python evaluations/locomo/run_locomo.py --data locomo10.json --out evaluations/locomo/run-1 --report
```

Results are appended per question and resumed on restart; every model call is capped. The `M` arm builds one embedded installation per conversation (about 420 to 690 stored messages each) and pauses recording before questions so they never become memories. Use `--conversations 0 --max-questions 20` for a cheap first check.

Results: [2026-10-29 report](../locomo-2026-10-29.md), raw answers in `results-2026-10-29/`.
