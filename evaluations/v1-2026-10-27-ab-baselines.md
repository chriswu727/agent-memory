# A/B baselines on a fresh multi-session scenario

Date: 2026-10-27. Status: **completed; the memory arm did not beat a naive full-history baseline.** Raw evidence: [JSON](v1-2026-10-27-ab-baselines.json), [scenario](v1-2026-10-27-ab-baselines-scenario.py).

## Method

A new scenario, not seen by the implementing agent, covers ten sessions and twenty user inputs about one user: a hard constraint (no consecutive run days for the left knee), a date change (race moved from November 30 to December 7), a temporary exception (a work trip), intention versus completion (a photo not yet requested), two similar names (Tom and Tomás), an irrelevant work task, a switch to Chinese, and a fabrication probe. Twelve cases were frozen before execution.

All arms used DeepSeek `deepseek-v4-pro` with thinking enabled and the same host instructions:

- **M**: Agent Memory through `open_agent`, default runtime policy, one conversation per session, cognition with low reasoning effort, waiting for background learning after every session.
- **N**: the current conversation only.
- **F**: every earlier message from all sessions in the prompt.

A reviewer independent of the implementing agent scored the replies with labels shuffled per case and the mapping sealed until all verdicts were written. Pass scores 1, partial 0.5.

## Results

| Case | M | N | F |
| --- | --- | --- | --- |
| No consecutive run days after travel | fail | fail | pass |
| Work email stays on topic | pass | pass | pass |
| Race-week meals in Chinese (vegetarian, December 7) | fail | fail | pass |
| Night-before meal in Chinese (vegetarian) | fail | fail | pass |
| Sore knee after back-to-back runs | partial | fail | pass |
| Weeks left until the moved race | fail | fail | pass |
| Photo status and exact bike story | pass | fail | partial |
| Supported wedding facts only | partial | fail | pass |
| Race-week plan | fail | fail | pass |
| Run the day before the race | partial | fail | pass |
| Right-ankle fabrication probe | pass | pass | pass |
| Tom versus Tomás | fail (turn failed) | fail | pass |
| **Total of 12** | **4.5** | **2** | **11.5** |

| Measure | M | N | F |
| --- | --- | --- | --- |
| Host tokens | 84,478 | 32,452 | 79,631 |
| Cognition calls and tokens | 184 calls, 2,366,007 tokens (744,645 output) | none | none |
| Manual interventions | 58 | 0 | 0 |

## Diagnosis

Most memory-arm failures were retrieval misses, not unused context. In the failing turns, the host prompt did not contain the vegetarian statement (stored in English, asked in Chinese), the race-date change, the wedding date and groom, or the consecutive-days rule. Background learning published 48 claims and 28 episode versions, but those did not reach the turns that needed them.

The default runtime also failed operationally. Synchronous orientation made replies wait 1.5 to 4.6 minutes. Learning windows were blocked by `context_stale` and needed `retry-learning`. Foreground turns failed with `lease_conflict` while background work held the brain; one turn stayed unrecoverable after three cancel-and-resend attempts. A cancelled turn also left a stale client pending record that blocked resending.

## Limits

- Twenty inputs fit easily in one prompt. A full-history prompt becomes costly or impossible for much longer histories, which this run does not test.
- One scenario and one reviewer; this is a diagnostic, not a statistical comparison.
- Waiting for learning after each session favours the memory arm compared with real use.
- A first memory-arm attempt was discarded because the cognition client ignores proxy and CA environment settings and could not reach the provider from the sandbox; a loopback relay was used afterwards. Plugin development during the run changed the runtime build and forced two service restarts; the memory core was unchanged.
