# First-release closeout

Status on 2026-10-26: **pre-release; acceptance incomplete**. This is the execution checklist for closing version 0.1.0, not a new architecture roadmap. The [finite acceptance contract](../architecture/v1-acceptance.md) remains authoritative. Once its gates pass, stop development and evaluation for this release.

## Frozen scope

Ship the existing persistent memory engine for synchronous text agents: Python lifecycle integration, HTTP API, TypeScript client, optional MCP memory tools, and local API-key chat. The documented desktop target is macOS with uv and Docker. Preserve the model's responsibility for semantic decisions, bounded evidence-backed processing, and the existing default verification format. Experimental shared-source verification is not promoted.

Personality, emotion, a management UI, native installers, streaming, multimodal input, additional framework adapters, new operating-system certification and competitive benchmark claims remain outside this release. No routine CI is added. A new idea does not reopen the release scope.

## Closeout checklist

- [ ] **Behavioral acceptance (G4).** Independently review a frozen plan before paid execution. Use the existing varied life scenario with one durable brain; review whole answers and published memory against only the available source prefix. Exercise updates, temporary exceptions, distinct people, intention versus completion, unfinished tasks, delayed returns, cue recovery, uncertainty and irrelevant-memory suppression. Separately record assistance and corrections. Bind isolated cue and age diagnostics to their declared checkpoint. Failed or unexecuted cases cannot pass.
- [ ] **Final candidate evidence (G1–G3, G5).** Reuse checks whose runtime and behavior are unchanged. Reopen only checks affected by an actual repair. Build the final source archive and wheel after packaging changes; compare their runtime, schema and migration bytes with source and an isolated installation. Retain the existing installed desktop, restart, upgrade and recovery evidence when those bytes are unchanged. Record hashes, revision and applicable performance measurements.
- [ ] **Delivery (G6).** Write an English final acceptance report with exact evidence, usage, limitations and unresolved failures. Keep README status equivalent in English and Chinese. Include the owner-selected [MIT License](../LICENSE) and matching package metadata in both distribution formats. Commit and push, verify remote main, then prepare the version tag and release assets from that exact revision only after all required gates pass.

The owner selected MIT on 2026-10-26. Licensing is resolved; behavioral acceptance and final release publication remain pending.

The current technical baseline is commit `18a969921d8da30b8ec84f94dec7d699292a8ba4`, runtime build `4f9655bd59349144ec973c9747661edcab55018ac248da5576ccf952462bf3dc`. The [preparation report](../evaluations/v1-2026-10-25-learning-preparation.md) records the full source run's two fixture failures, their successful affected-file follow-up, installed checks and four desktop flows. It must not be summarized as an all-green full run. Earlier real-model failures remain failures; local engineering evidence does not pass G4.

## Completed varied diagnostic and remaining blockers

The [2026-10-26 varied diagnostic](../evaluations/v1-2026-10-26-longitudinal-round.md) reached the final authored session on one persistent brain: all 33 inputs were attempted, 32 replies completed learning, and one sleep-overlapping timeout remains failed. Fifteen sessions completed and one is partial. The last fourteen inputs ran in a separately frozen diagnostic tail after the original journal closed; this is not a clean primary-campaign pass. Fictional span was 56 days; actual primary-to-tail calendar duration was about three hours twenty minutes, including sleep and review gaps.

The time-allowance candidate is based on `f1daa5b3e92a88a666b78f9410be1f3dbbdc7f80`, runtime build `eceb600da38d0607ec065540e4cc0ffd6f49422616fe2322c96d201cd0d7ee73`. The report binds its exact patch, scoped source/installed checks, 117 packaged runtime-file hashes, source reviews, full journals and usage. No fresh full regression suite is claimed.

Whole-sequence review records 27 passes (three assisted), two partial results and four failures. Five isolated cue probes and eighteen zero-model access-age retrievals are complete as exploratory diagnostics. G4 remains failed: repair declarative-source rejection, missed meaningful recall, assistant-origin contamination and language continuity; assess unnecessary clarification and cognitive processing cost using the retained evidence. Access-age classifications alone did not demonstrate the intended forgetting behavior. Final packaging and stable publication remain pending.

This round used 153 real calls with **1,710,383 known tokens and two unknown-usage calls**, including the closed initial time-budget failure. The original primary and tail share their original 248-call ceiling; auxiliary probes used five of fifteen allowed calls. No paid answer reviewer was used. The earlier stopped plan-review attempt below remains separate and closed. No further paid execution is active; locally verified repairs and a newly frozen bounded plan are required before another acceptance run.

The [source and recall repair follow-up](../evaluations/v1-2026-10-26-longitudinal-repairs.md) records the subsequent candidate, local checks and separately bounded failure-path diagnostics. It does not resume or retry the closed campaign. G4, useful gist-first forgetting, cognitive-cost improvements and stable publication remain open.

## Historical proposed longitudinal follow-up

The [2026-10-26 preparation record](../evaluations/v1-2026-10-26-closeout.md) stopped at the independent plan-review call: it timed out with unknown usage. No primary session or auxiliary diagnostic started. The proposed ceilings below are preserved for audit; unused capacity is not permission to resume or retry that stopped attempt.

The proposed closeout run retains `longitudinal-life-v1`: sixteen irregular sessions and thirty-three authored turns spanning fifty-six fictional days. Review boundaries are after 3, 6, 11 and 16 completed sessions. The eleven-session boundary is also the eligible point for isolated cue comparisons and frozen-snapshot access-age diagnostics. These boundaries are spending and review controls, not success thresholds.

The primary runner's ceiling is 248 calls and 3,387,904 reserved output tokens. Separate auxiliary ceilings are fifteen calls and 184,320 reserved output tokens for cue diagnostics, and six independent review calls with at most 16,384 output tokens each. The combined ceiling is **269 calls and 3,670,528 reserved output tokens**; it is a maximum reservation, not actual token use or a spending target. Exact inputs, policies, runtime build and code fingerprints are frozen privately before execution. Input and per-brain limits remain those of the frozen runner. Review input is additionally limited to 524,288 bytes per call. Credentials and personal configuration stay out of evidence and release assets.

A failed required behavior, unsupported publication, invalid fixture, failed required processing, provider error, unknown usage or exhausted allowance stops the run. There is no automatic retry, replacement campaign or allowance reset. Preserve the failure and diagnose it before any further paid work. Historical stopped campaigns remain closed. A review objection can stop execution before any new simulated history is generated.

Fictional day labels do not age memory. Access-age diagnostics use the existing validated seam and stable snapshot; they do not rewrite source timestamps or claim years of interaction. Report actual elapsed time and active uptime separately. A real-time field trial is useful later work, not an unbounded additional gate for this release.

## Release assets and claims

Prepare a source archive, wheel, SHA-256 checksums, release notes and the final acceptance report. Inspect archive contents for unintended local files and credentials. Verify the documented install command against the chosen distribution before advertising it. A package name in `pyproject.toml` does not establish availability or ownership on a package index.

Use a GitHub versioned release as the first delivery record. Package-index publication and promotional posts are separate distribution steps; this checklist does not claim they have happened. Do not label a tag or release stable while acceptance is incomplete. The current [changelog](../CHANGELOG.md) is an unreleased draft, not a published release.
