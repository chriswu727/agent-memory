# Fresh scenarios: semantic recall falls short, host-curated notes hold up

Date: 2026-10-27. Status: **completed.** On a fresh scenario, the plugin as merged in PR #5 scored well behind a full-history prompt once the history grew. That failure led to host-curated notes and message-level recall. On a second, held-out scenario these scored 11.5 of 12 on the long variant, the same as full history, against 5.5 for the merged plugin. Every result is a single run.

Raw evidence:
- [JSON](v1-2026-10-27-fresh-scenarios.json)
- Scenarios: [B](v1-2026-10-27-fresh-scenario-b.py), [C](v1-2026-10-27-fresh-scenario-c.py)
- Harnesses: [scenario B run](v1-2026-10-27-fresh-scenarios-harness-b.py), [scenario C run](v1-2026-10-27-fresh-scenarios-harness.py)
- [Blinding script](v1-2026-10-27-fresh-scenarios-blind.py)

## Why this run

The [plugin-path report](v1-2026-10-27-plugin-path-ab.md) reached 12 of 12 only after changes designed on its own scenario. This run tests on scenarios the implementing agent had not seen.

## Method

A separate agent wrote two scenarios, B and C, and was told not to reveal their content. The implementing agent did not read them before running them. Each has:

- 12 core sessions with 23 user turns and 12 frozen cases.
- 24 unrelated filler sessions; 8 of them contain pasted code, logs or CSV.
- A long variant with the fillers interleaved: 71 user messages, about 21,000 to 23,000 user characters.

Each scenario covers one user with a durable health constraint, a plan that moves, a temporary exception, an intention that is never confirmed done, and similar names. It also has a session in Chinese (B) or Spanish (C) whose facts matter in English later, plus an irrelevance case and a fabrication probe. Later questions often paraphrase the original facts instead of repeating their words.

The host was DeepSeek `deepseek-v4-pro` with thinking in every arm. Plugin arms call the real plugin hook functions against their own embedded installation, as in the earlier reports. Arms:

- **L**: the plugin as merged in PR #5, without the semantic model (keyword recall).
- **S** / **S0**: the same code with the local semantic model, the plugin as merged in PR #5. It is called S on scenario B and S0 on scenario C.
- **M**: host-curated notes and message-level recall, described below. The host also gets a `memory_note` tool.
- **F**: every earlier message in the prompt.
- **N**: the current conversation only.

Scoring followed the same rules for each scenario. A fresh reviewer subagent read only the ground-truth facts and the blinded sheets, with labels shuffled per case, and wrote verdicts before the mapping was opened. Pass scores 1, partial 0.5.

Order of work:

1. Scenario B was run first with L, S, F and N, as a validation of the merged plugin.
2. After B failed, it became the development set for diagnosis. M was smoke-tested on B, and its run on B's long variant is in the evidence but was not scored.
3. Scenario C stayed sealed until the M design was complete. It was then run once with S0, M, F and N.

## Scenario B (validation of the merged plugin)

| Variant | L | S | F | N |
| --- | --- | --- | --- | --- |
| Short (23 inputs) | 11 | 11.5 | 12 | 2.5 |
| Long (71 inputs) | 8 | 6 | 11 | 2.5 |

On the long variant, semantic recall scored lower than keyword recall. Inspecting the injected context showed three structural problems:

- **Isolated sentences.** Memories are single sentences, so recall injected fragments such as "Today is Thursday, April 2." or "Two kitchen updates." without the numbers they introduced. The budget question never saw the $24,000 ceiling or the quotes.
- **Too few records.** Each memory document is large, so the 3,000-token recall budget fitted about five sentences, roughly 700 characters.
- **Noise.** Vector similarity has no relevance floor, so pasted CSV rows, Terraform plans and unrelated questions were injected. In one case the host then answered from the wrong context.

A larger question remained. Several cases depend on implication, not similarity: a seafood banquet menu and a shellfish allergy stated in Chinese; checking in at urgent care and a parent who needs an interpreter and uses a walker. Ranking every user sentence against those questions with `multilingual-e5-small` put the allergy sentence 435th of 473. `multilingual-e5-base` put it 231st. Better embeddings do not fix this.

## What changed (arm M)

- **Notes curated by the host.** A `memory_note` tool lets the host model save a one-line durable fact and replace an outdated one; the old note is suppressed, not erased. Every session starts with all notes, then the user's recent messages, with runs of pasted code and log lines compacted.
- **Message-level recall.** The hook maps recalled statements to their source messages and shows up to eight complete messages, oldest first, with a window around the match for long ones. It drops data-like fragments and keeps only the three closest meaning-only matches. The session's previous prompt is passed as a cue, so short follow-ups ("What should I tell the front desk?") find their context.
- **Vector candidates.** These now exclude assistant speech, as keyword candidates already did. They also interleave the closest memories written in another script, which helps somewhat with the language bias of the small model.

## Scenario C (held out)

| Case | Short S0 | Short M | Short F | Short N | Long S0 | Long M | Long F | Long N |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Recovery restriction has ended | pass | partial | pass | fail | pass | pass | pass | fail |
| Celiac-safe brunch | pass | pass | pass | fail | pass | pass | pass | fail |
| Spanish session: celiac and mornings | pass | pass | pass | fail | fail | pass | pass | fail |
| AP is Ann, not cousin Ana | pass | pass | pass | partial | pass | pass | pass | partial |
| Prescription treats only | pass | pass | pass | fail | fail | pass | pass | fail |
| Visiting hours stated only in Spanish | pass | pass | pass | fail | partial | pass | pass | fail |
| Sunrise versus stated preference | pass | pass | pass | fail | fail | pass | pass | fail |
| Irrelevance (Excel) | pass | pass | pass | pass | pass | pass | pass | pass |
| Fabrication probe (bakery) | pass | partial | pass | pass | pass | pass | pass | pass |
| Passport renewal never confirmed | partial | partial | pass | fail | fail | partial | partial | fail |
| Sitter cost | pass | pass | pass | fail | fail | pass | pass | fail |
| Superseded travel dates | pass | pass | pass | fail | fail | pass | pass | fail |
| **Total of 12** | **11.5** | **10.5** | **12** | **2.5** | **5.5** | **11.5** | **11.5** | **2.5** |

| Measure | Short S0 | Short M | Short F | Short N | Long S0 | Long M | Long F | Long N |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Host tokens | 71,291 | 133,332 | 219,829 | 40,210 | 338,721 | 472,989 | 1,439,819 | 194,167 |
| Prompt tokens on the last turn | 2,876 | 3,876 | 12,867 | 596 | 4,181 | 6,555 | 34,140 | 622 |
| Mean reply time | 13.1 s | 13.2 s | 26.6 s | 15.2 s | 13.9 s | 13.2 s | 21.8 s | 21.0 s |
| Notes saved / recall searches | 0 / 3 | 14 / 2 | — | — | 0 / 9 | 18 / 5 | — | — |

**Long variant.** S0 had six failures and one partial, mostly on superseded or missing facts:

- It used the original July 2–19 travel dates and holiday rate after they changed.
- It treated a soft-food rule that had ended as current.
- It endorsed gluten-containing dishes in the Spanish session.
- It ignored the stated dislike of early weekend mornings.

One failure was a reply that contained only raw tool-call markup from the host model; the same kind of failure occurred once in scenario B.

M saved 18 notes and replaced them as facts changed. For example, the trip, the flights and the sitter rate were each replaced when the trip moved. It also saved the care home's visiting hours, which the user had stated only in Spanish. M matched full history with about a third of its tokens.

**Short variant.** The whole history fits in the session-start budget, so every arm with memory sees everything. M's three partial verdicts are reasoning slips, not missing facts:

- A care checklist missed an item.
- It named two bakeries while correctly saying it had no record.
- It said a passport renewal that was only planned had been mailed. S0 made the same error on the short variant and M on the long one, so intention-versus-completion remains a weak spot for every memory arm. Full history passed it on the short variant and partly failed it on the long one.

M also costs more host tokens than S0, because saving notes takes extra tool rounds.

## Limits

- One run per arm, one host model and one model reviewer; differences of one or two cases are within noise.
- Notes depend on the host model saving them. DeepSeek V4 Pro saved and replaced notes without being asked, beyond the system guidance; Claude and other hosts have not been measured. Some notes were near duplicates. In Claude Code, the first use of a plugin MCP tool asks for permission.
- The harness calls the plugin's functions directly rather than running Claude Code.
- M's session-start context reached 10,151 characters, above Claude Code's 10,000-character limit for hook output. History budgeting was fixed after the run to stay under 9,500 characters, which shows slightly less history than M saw here.
- The small local model ranks text in the same language as the query above more relevant text in another language. Interleaving other-script candidates mitigates this; it does not remove it.
- Scenario B became a development set after its first run; only scenario C measures M without bias.
- Creating several separate installations at the same instant could choose the same database port; three runs had to be restarted. Normal use is unaffected because sessions on one installation start under a lock.
