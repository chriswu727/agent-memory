# Finite first-release acceptance

Status: acceptance criteria frozen on 2026-10-25; **not passed**. This document defines when first-release work ends. The [development plan](v1-release-plan.md) and historical 52-scenario architecture remain context; they must not expand these release gates during acceptance without an explicit product decision.

## Supported release

The release is an independent, persistent memory runtime for synchronous text agents using the documented Python lifecycle or HTTP integration. The verified desktop target is macOS with Python/uv and Docker; direct API-key chat uses the saved provider configuration. MCP remains an optional tools surface and does not automatically intercept a closed host's conversation. The storage and lifecycle are model-independent; real-model acceptance uses the authorized DeepSeek provider, not a claim that every provider or closed client has been tested.

Personality, emotion, a management UI, native one-click installation, new chat-framework adapters, streaming, multimodal input, new operating-system certification and competitive benchmark claims are outside this release. They are not added while closing the gates below. This is a usable engineering product inspired by memory processes, not a claim of biological equivalence or infallible recall.

## Required gates

### G1 — Install, connect and preserve identity

Build a source archive and wheel from the final candidate. Verify packaged runtime, schema and migration bytes against that candidate and an isolated installation. From that installed artifact, exercise the documented desktop setup, direct text chat and Python lifecycle, stop/restart, upgrade, backup and fresh-install restore. The same brain and committed sources must survive. Incompatible downgrade must refuse safely; restored operational work stays quarantined until explicit activation. A deterministic change of host/model callback must retain the same memory state without regeneration. Real Windows/Linux certification is not implied.

### G2 — Close the current implementation batch

Complete and integrate only the open repairs already justified by evidence:

- Versioned relation-capability projection with the original evidence guards and old frozen requests preserved.
- Bounded historical comparison attention: at most three newly admitted objectives per fresh root; unselected opportunities are not claimed as assessed and do not create an automatic rediscovery queue.
- Same-private-brain, same-branch funding of exact never-attempted history, without changing source scope or increasing the donor's existing allowance.
- Resolve the recorded speech-event/content-world false rejection without promoting assistant statements into world facts or erasing explicit fictional/quoted boundaries.

Run the relevant deterministic and actual PostgreSQL lifecycle tests, including same-conversation and cross-conversation continuation after reconstruction. Then run the full local suite once on the integrated candidate, with desktop Docker tests and the isolated installed wheel. All required checks must pass. A failing check is fixed and rerun; an unchanged passing check is not rerun simply to increase test counts. No routine CI is triggered.

### G3 — Preserve evidence, isolation and finite spending

Existing required regressions must show zero known data-loss, tenant/principal/brain/branch disclosure, deletion resurrection or unverified-result publication defects. Correction/deletion races must invalidate old results. Failed and uncertain provider reservations remain charged; automatic continuations cannot retry them or mint an allowance. Two workers cannot spend the same pair. Legacy jobs, restored quarantine and explicit recovery cannot acquire the new funding permission retroactively.

For each tested fresh root, total encoding plus history remains at most four calls and 393,216 input bytes, with at most two calls and 196,608 bytes per phase, additionally bounded by the effective brain quota. Every real reservation must have a recorded known outcome before further paid work; unknown usage stops the campaign. Monitoring must distinguish unselected opportunity, admitted pending comparison, assessed no-change, rejected proposal and publication. A zero queue is not an accuracy result.

### G4 — Complete one useful real-memory campaign

After G1–G3 pass, freeze the candidate revision, provider settings, history, probe questions, source oracle and spending ceilings before the first provider call. Use one memory-only campaign with **100 actual received/generated/delivered history exchanges**, followed by **twelve isolated probes: the six established source-grounded cases and one separately frozen paraphrase of each**. Keep probe questions and the oracle out of history encoding. Each probe starts from the same trained archive so probes cannot teach one another their answers.

The cases cover changed routine order, a childhood cue, two similar events with different outcomes, an unresolved conditional preference, a rare detail and a false presupposition. Every probe must retain all required source-supported facts and qualifications, and must not invent any oracle-listed unestablished fact. All **12/12** must pass complete source-and-answer review. Keyword hits and a model's positive verdict alone cannot pass a case. This is a finite development acceptance set, not a population accuracy estimate.

All 100 exchanges must retain their actual source and delivery records and complete the required new-input processing without progressive encoding starvation. Review new semantic publications against their exact supporting sources. No unsupported usable assertion, current-rule promotion, fabricated completion or cross-event conflation may remain as an unresolved release defect. Deliberate abstention, valid rejected optional proposals, and an unused fallible organization label retained only as audit data are not automatically factual memories or failures; their effect and coverage remain disclosed. Inaccurate labels must not enter future factual contexts as evidence. Actual admitted debt and exact paid failures remain visible, within G2–G3's finite authorization rules.

Check the first sixteen stages as an early failure boundary of this same campaign; do not run a separate sixteen-turn pretest and then pay to repeat those inputs. Recreate clients/runtime during the history and verify durable replay. This is not an OS-crash or multi-month endurance claim.

### G5 — Keep local overhead bounded

On the recorded development machine, reuse the documented 10,000-record lexical fixture and twenty fixed cue queries. Require **20/20** expected source labels and measured local recall **p95 at most 500 ms**, including every measured request. Record machine, cache state, revision, raw samples and scope; this is a development regression budget, not an SLA or whole-model reply latency. Provider waiting is reported separately.

The relation-capability projection for thirty-two complete eligible endpoints must remain below **100 ms median over five offline measurements** on that machine. Its full metadata counts toward existing request budgets. No extra model calls may be introduced solely to produce capabilities or attention rankings. Per-request source, assertion, dependency, byte and context limits remain enforced even when a candidate cannot fit.

### G6 — Deliver and stop

Publish an English acceptance report identifying the exact candidate, evidence for G1–G5, actual usage and remaining supported-scope limitations. Keep the English and Chinese READMEs equivalent. Commit and push the complete verified change; confirm remote main matches the local revision and the built source identity. Preserve earlier failures. No failing or unexecuted required check may be labeled passed.

When **G1–G6 all pass and no blocker below remains**, mark the first-release goal complete and stop implementation, paid evaluation and unsolicited refactoring. New research ideas go into subsequent work; they do not reopen this release. No baseline superiority experiment is required because the release makes no superiority claim.

## Blockers and spending stop rules

A release blocker is a reproducible loss/isolation/authorization defect, an unbounded automatic spending or task-growth path, failed supported installation/recovery, progressive new-input learning starvation, an incorrect required probe, or a known unsupported usable memory/answer in acceptance. Cosmetic documentation changes, optional adapters and additional research are not blockers. A suspected issue is first reproduced offline or with deterministic providers; do not expand the feature set to work around it.

No new paid campaign begins until the current batch is integrated and locally green. Existing failure requests are replayed offline first. A real call needs a concrete unresolved question that deterministic evidence cannot answer and a recorded call/output ceiling. No Codex/Claude subscription-backed tests are authorized by this plan.

The completion campaign is memory-only. Baseline arms and automatic answer-review model calls are not required for this release; complete outputs are independently reviewed by the developer against the frozen oracle. Preserve stopping at unknown usage, provider failure and failed required processing. Never retry a failed paid job automatically or overwrite a run. At most one newly frozen full follow-up campaign is allowed after a concrete repair; if that also fails, stop further paid campaigns, preserve the failed gate and reassess offline before proposing additional expenditure. This expenditure stop is **not** a declaration that the product is mature.

Passing a gate does not justify repeating it after unrelated documentation edits. Reopen only the gates materially affected by a later code change. After success, stop; after a budget/usage interruption, report unfinished work accurately and retain the checkpoint.

## Running the fixed campaign

The evaluator now has a dedicated acceptance profile. From the frozen candidate checkout, inspect its complete plan without loading provider credentials:

```sh
uv run python -m examples.evaluate_memory_lifecycle --plan --profile acceptance --orientation-policy on_demand
```

After the local prerequisites pass, run the same profile with `--live` and a new `--output` directory. It authorizes only the memory arm, with 100 history exchanges and twelve probes; no baseline or answer-review provider is constructed. Its conservative aggregate ceiling is 736 calls (624 cognitive and 112 host calls), with 10,682,368 reserved output tokens. This is a maximum, not predicted use; the per-root, per-phase and effective brain limits still apply. An existing output directory cannot be reused for paid execution.

After history stage 015, the process writes `checkpoint-016.json` in the output root and waits without making calls. The developer reviews the complete `memory/report.json` sources, delivered answers, publications, debt and usage, then atomically creates `checkpoint-016-decision.json` with exactly `checkpoint_sha256` (the checkpoint's `sha256`) and `decision` (`continue` or `stop`). Continuing uses the same process and ledger. It does not mark semantic acceptance passed. A root `STOP` file ends the campaign at the next safe boundary; `--resume` only reads the saved report and cannot restart paid work.

Completed-turn replay is checked after history stages 015 and 099 using reconstructed clients/runtime and callbacks that fail if generation or delivery is attempted. Each probe receives its own restored brain from the same final trained archive. Even a normally finished run retains `pending_independent_review` until the complete source-and-answer audit is recorded.

## Progress checkpoint

The [local acceptance checkpoint](../evaluations/v1-2026-10-25-local-acceptance.md) records the bounded-attention, private-funding, capability and speech-attribution repairs, exact artifact identity, full regression and necessary fixture follow-up. Local retrieval and capability measurements meet their recorded budgets.

The first actual finite campaign ran on frozen revision `33cff1f19e9cb7f0e687dc19040a4bc50ace2372`. It [stopped after twelve exchanges](../evaluations/v1-2026-10-25-acceptance-attempt-1.md) when a history proposal contradicted its own relation-to-objective accounting. All 47 calls have known usage; no checkpoint, durable replay or probe was reached. The twelve current-input encodings completed and independent review found no unsupported published fact in that scope. The failed historical job and unassessed work remain visible. G4 did not pass.

The [subsequent versioned repair](history-link-coverage.md) derives coverage from exact relation endpoints while retaining strict abstentions, source checks, semantic verification and publication validation. Its [local verification](../evaluations/v1-2026-10-25-link-coverage-repair.md) records the original failed request replay, full regression with a disclosed fixture mismatch and successful follow-up, and 56 installed-wheel checks. These are local prerequisites; no new real-memory pass is implied.

The [single permitted full follow-up](../evaluations/v1-2026-10-25-acceptance-attempt-2.md) ran on frozen revision `0f2685608c2402922f5cd47e8bac55856e82e166`. It passed the sixteen-exchange independent checkpoint and durable replay, then stopped after twenty-three delivered/encoded exchanges when the historical proposal timed out with unknown usage. There were 90 known calls and one unknown call; no probes or end-history replay ran. The failed operation remains charged and unsettled. The campaign exposed monitoring gaps for pending objectives and expired unsettled jobs; their [offline repair and verification](../evaluations/v1-2026-10-25-timeout-monitoring.md) do not change its outcome. The full follow-up allowance is exhausted, so further paid campaigns stop for offline reassessment. G4 and final release acceptance remain unpassed.
