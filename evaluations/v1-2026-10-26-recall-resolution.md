# Source-preserving recall resolution and bounded follow-up

Status: **implementation batch complete; longitudinal acceptance remains unpassed**. Original records are retained. Forgetting here concerns default context detail and cue-dependent accessibility, not physical age-based deletion. This report preserves an initial failed real-model encoding and a later unsupported autobiographical draft; it is not a stable-release certificate.

Final runtime build: `d8cd5deb13986cab0589476a9a3a572180df482bc37aefe8ee147fd2ca934117`. The earlier fully integrated build was `f5b4afc82577d07d7bd8723b8a522186721984f97504e5efc2c4d7a9ca7f775e`. The final change tightens only the new local verifier schema and local instructions following the real-model format failure. Full-suite, installed-package, desktop, performance and affected-check builds are distinguished below.

See the [architecture](../architecture/recall-resolution.md), [structured results](v1-2026-10-26-recall-resolution.json), [artifact manifest](v1-2026-10-26-recall-resolution-artifacts.json), [evidence manifest](v1-2026-10-26-recall-resolution-evidence.json) and [raw retrieval measurements](v1-2026-10-26-recall-resolution-performance.json).

## Delivered behavior

- `recall-resolution-v1` permits optional source-supported short expressions and model-selected retention in the existing proposal/verification pair. A separate summary-fidelity verdict is required at verification and publication. No per-read or recurring age-based summarization call is added.
- Local outlines and anchors remain attributed episodic reports, with unknown executable validity. The model chooses useful content and whether shortening is worthwhile; the runtime does not classify emotional importance or require duplicate records for every layer.
- Below everyday accessibility, eligible memories can expose a shorter statement. Concrete omitted source terms, explicit recollection intents, full-detail requests, pinned memories and open commitments retain or recover detail. Full statements and sources remain inspectable.
- Covered literal captures are deferred from the compact model view rather than included alongside the summary. Exact source containment, scope/world checks and atomic budget grouping protect attribution and truncation. Corrections, source deletion and lifecycle guards remain in force.
- The new bounded local mode omits unused registry/capability data and unreachable schema definitions. Historical comparisons are not silently skipped. Old frozen request byte checks remain unchanged.
- Host memory allocation reserves the known instructions/current-input overhead and JSON escaping before preparation, within the existing full-input ceiling. This fixes the reproduced `input_budget_exceeded` path without raising product defaults.
- `memory-consumption-v6` and optional `answer-grounding-v3` explicitly cover source fidelity in autobiographical drafts. MCP has the same detail override as HTTP/Python; TypeScript fallback projection now agrees with Python for the new view and applicability timestamps.
- Migration `024_recall_resolution.sql` prevents older runtimes from interpreting the new persisted representation. Existing records need no rewrite, reset or re-embedding. Upgrade does not automatically spend money reprocessing the archive.

## Local verification and build boundaries

On integrated build `f5b4af…`, the full suite passed **5,427 tests**, with four opt-in Docker cases skipped and one existing Python fork warning. The same build's isolated wheel passed **224 checks** and all four real macOS/Docker flows: desktop lifecycle/MCP, terminal chat, upgrade/downgrade, and backup/fresh-install recovery. These four flows exercise the four skips separately, not four extra unique tests. Deterministic callbacks or local HTTP stubs were used; provider credentials and dotenv loading were disabled for those processes.

The final two-file local-verifier repair was followed by **617 affected source checks** and **227 isolated installed-wheel checks**, all passing. An unchanged full suite and desktop matrix were not repeated for that restricted schema/instruction change. The final source, sdist, wheel and installation match **120 runtime/schema/migration files**. This is a runtime-byte identity claim; the test-built archives are not published release assets and do not claim to contain subsequent documentation edits.

TypeScript passed **38 tests** and strict typechecking. Ruff and whitespace checks passed. Relevant regressions exercise short/full views, source-only detail cues, explicit expansion, external reinforcement, pinned detail, source hashes, scope, superseded evidence, atomic budgets, same-version inspection after reconstruction, trusted restore, explicit erasure and safe downgrade refusal. A new real subprocess is killed after its provider reservation; reconstruction preserves one charged uncertain operation and refuses automatic replay. This is process-interruption evidence, not an actual machine-sleep or provider-result reconciliation test. No Codex/Claude subscription test or routine CI was triggered.

Failures during development remain recorded. Initial legacy wire checks failed because a schema extension and an instruction-splicing indentation error affected frozen formats; these were corrected without regenerating golden values. A TypeScript fallback check exposed an extra applicability timestamp and the typechecker exposed a missing source-act declaration; both were repaired. Several new storage-test preparations used incorrect SDK arguments before the real restore/erase checks passed; those preparation failures are retained.

An initial full run had **5,416 passes, three failures and four skips**. One deterministic fixture emitted situation updates after the bounded-mode transition, where they are prohibited. Its mode-aware fixture now supplies an authorized local interaction group and explicit history abstentions. The original forty inputs, five encoding windows, ten encoding calls and exact-once coverage remain asserted for both old and new contracts; additional history work is separately accounted. Another failure exposed the genuine prompt-allocation bug repaired above. The third was a frozen campaign fingerprint mismatch caused by editing code while that initial test run was active. The final complete run used a stable runtime. Intermediate failing follow-ups and available original logs remain in the evidence bundle; no failure is relabeled passed.

## Bounded efficiency observations

One deterministic matched source/encoding fixture measured these UTF-8 message bytes:

| View | Prior contract | Final contract |
| --- | ---: | ---: |
| Proposal messages | 30,260 | 16,546 |
| Verification messages | 20,607 | 14,735 |
| Combined messages | 50,867 | 31,281 |

The combined reduction is about **38.5% in this fixture**. A fresh detailed context used 2,907 bytes, its aged short view 1,453, and a specific-cue expansion 2,926. Full source and statement bytes remained stored. These are deterministic wire/context bytes, not measured provider tokens, a general compression ratio or an overall cognitive-cost benchmark. Recent context and essential uncertainty still consume space. The fixture uses a validated access-time seam, not ten years of actual elapsed interaction.

On the reused 10,000-record/twenty-query fixture, the installed `f5b4af…` build found **20/20** sources: median **120.688 ms**, p95 **129.309 ms**, maximum **136.545 ms**, with zero model/embedding calls. All observations count; caches were not flushed and machine load was not controlled. This fixed lexical regression meets its 500-ms p95 budget; it is not cold-cache, semantic-quality or full-reply latency evidence. The final local-verifier-only patch does not alter this retrieval path; these timings are not attributed to new bytes or advertised as a statistically established speedup.

## Real-model plans and outcomes

All real calls used the authorized `deepseek-v4-pro`: enabled thinking/low effort for cognition, enabled thinking/high effort for host drafts, 16,384 output tokens per cognitive call, 4,096 per host call, a shared 600-second cognition deadline and 240-second host deadlines. Responses were bounded at 262,144 bytes. No actor, paid answer reviewer, retry, replacement probe, delivery, memory publication or background worker ran.

The encoding input is the original retained message about an old photo, Grandma lighting a lantern and putting buttons in a tea tin for “us” to sort, and the dark house feeling safe. The original archive and closed longitudinal journals were not modified. The three host requests reuse the same retained memory data and original questions; they regenerate only host instructions. Their diagnostic full-input bound is explicitly 32,000 bytes instead of the earlier fixture's 24,000. Product defaults are unchanged. These frozen request replays are not a new complete `BrainRuntime.respond`/learning campaign and do not test new summary publication in a real brain.

Plans were frozen and source-reviewed before calls. Judgments below are from the implementing Codex reviewer, independent of the tested provider; this is not a blinded external panel or a statistical estimate.

### Initial plan: closed after failed encoding

Plan digest `59cc2377c4e363dca338b7118c5f4f4dff68e3bb91795ad5bb8bcae53b5ab09b` allowed at most five calls and 45,056 reserved output tokens. Only **two calls** ran. The verifier emitted `supporting_evidence_indices` on ordinary local entries, including the episode. That field is authorized only for historical abstraction selection. The processor correctly returned **invalid_response**, accepted nothing and published nothing. The unexecuted host allowance was not reused.

The proposal supplied mostly verbatim fragments and no short expression. Its isolated “It made…” safety quote lacked a self-contained antecedent; the verifier marked citation coverage unknown. That adverse judgment was not discarded. Actual use was **20,857 tokens**: 9,240 input and 11,617 output, with zero unknown usage.

The final repair removes historical-only support-selection, instance and current-request fields from the new local verifier schema and explicitly forbids them in its instructions. New proposals are asked to retain antecedents and cite the source units establishing them. The runtime still rejects a response returning those fields; it never strips them to manufacture acceptance. Old contracts remain unchanged.

### Separate finite follow-up

Plan digest `3047df1142cc8154507ddbf4e357bdfb3e116643452f1984cf68580f50e214ec` authorized one new bounded five-call diagnostic after the local repair and its verification. This is a separate allowance; the failed plan remains closed.

The two-call encoding completed with three attributed reports and model-selected retention priorities. The safety statement now cites both the antecedent event and the expressed meaning. Whole-source review found the three claim statements supported. **No optional short expression was produced**, so these calls do not demonstrate automatic real-model short-view formation or compression.

An episode description says “Grandma lighting a lantern and sorting buttons.” The source assigns sorting to “us” and putting buttons to Grandma. This compression can be read as transferring the sorting action to Grandma; source review records an **actor-ambiguity warning**, not an unambiguous fidelity pass, despite the model's positive organization verdict. No encoding result was published. The remaining independently frozen host requests contain none of these new proposals, so their execution does not propagate the warning into memory or erase it.

| Host case | Recall paragraph | Entire answer |
| --- | --- | --- |
| Unaided card | Correct scene and source-expressed safety meaning without re-teaching. | **Fail:** the first-person draft introduces “those dark nights”; no time of day was supplied. |
| Broad power-cut cue | Recovers unsupplied lantern/buttons/tin and safety meaning. | **Pass:** no unsupported scene additions. |
| Specific tea-tin cue | Recovers unsupplied details and meaning; supplied cue facts get no recall credit. | **Pass:** no invented night, sound, location or house owner. |

The whole-answer result is **two passes and one failure**. A correct recall paragraph cannot excuse invented detail in the later draft. The night wording could be figurative, but an unqualified first-person autobiographical draft fails the frozen criterion; this does not establish that the original event happened during daytime. Host inference took 10.46–16.23 seconds per frozen request, excluding preparation and learning. This small observation is not a full-reply SLA or proof of general improvement.

| Execution | Calls | Input tokens | Output tokens | Known total |
| --- | ---: | ---: | ---: | ---: |
| Closed failed encoding | 2 | 9,240 | 11,617 | 20,857 |
| Follow-up encoding | 2 | 9,461 | 7,597 | 17,058 |
| Follow-up host answers | 3 | 19,384 | 3,442 | 22,826 |
| **Actual combined use** | **7** | **38,085** | **22,656** | **60,741** |

Unknown calls are **zero**. Two separate five-call ceilings were not a spending target: seven calls actually ran, with 77,824 output tokens reserved across those calls, not consumed. The unused capacity is closed. No additional real calls are planned in this batch, and the temporary sleep assertion was released.

## Remaining release work

The source-preserving presentation mechanism and its engineering checks are implemented. Real-model automatic summary selection on richer longitudinal material remains unestablished, episode wording still needs source-aware handling, and the host can still embellish autobiographical drafts despite explicit guidance. Adding a reviewer or repair call would have explicit cost, latency and fallibility tradeoffs; none was silently enabled here. Historical-comparison usefulness and total background cost still require representative measurement. The process-kill test does not establish recovery of an actual sleep-interrupted remote response.

G4 remains unpassed. Another complete varied campaign must wait for the remaining defects and its own frozen finite plan. Earlier campaigns and failures remain closed and unchanged. No stable tag, package-index release, routine CI or promotional publication is implied by committing this batch.
