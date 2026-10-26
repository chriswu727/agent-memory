# Longitudinal follow-up: provider boundaries and actual diagnostics

The previous pilot stopped before any session completed. This follow-up resumed engineering and live verification under the user's continued authorization. Earlier failed campaigns remain closed; none of their journals, reservations or memories were overwritten. Full longitudinal and first-release acceptance remain unpassed.

## Failures that drove the repair

The [v2 invocation](v1-2026-10-25-longitudinal-envelope-failure.json) delivered one answer, then stopped while reading a cognitive proposal beyond the original 65,536-byte HTTP envelope limit. One of its two calls has unknown usage. The incomplete body cannot establish its final content, finish reason or actual token use. It was not retried or retroactively settled.

An explicit provider-envelope limit now permits up to 1 MiB; the campaign chose 262,144 bytes. Semantic answer content remains separately capped at 65,536 bytes. Defaults remain unchanged. Raw and decompressed bodies are bounded, and increasing this limit does not make an old unknown call known.

The [v3 invocation](v1-2026-10-25-longitudinal-reasoning-failure.json) received the full body and established another failure: the proposal used all 16,384 completion tokens for reasoning and returned `finish_reason=length`. Both calls have confirmed usage: 9,845 input tokens, 17,629 output tokens, 27,474 total. One answer was delivered, but no encoding turn or session completed. The first answer respected the class nights and invented no booking or completed draft; proposing a 60–90-minute second writing slot did not fit the user's request for short slots cleanly. Later clarification cannot erase this practical mismatch.

The provider now accepts an optional explicit `reasoning_effort`. It is omitted by default and rejected when paired with explicitly disabled thinking. DeepSeek documents `high` as its default and supports a lower effort while keeping the same Pro model and thinking enabled. See the [official control documentation](https://api-docs.deepseek.com/guides/thinking_mode/). This is a provider configuration control, not an assertion that less reasoning improves memory accuracy.

## Bounded configuration diagnostics

The repaired user selector completed one independent live call with thinking disabled, JSON mode and a 512-token cap: 663 input tokens, 11 output tokens, 674 total, in 1.36 seconds. It returned an exact allowed choice. The [evidence record](v1-2026-10-25-longitudinal-actor-preflight.json) binds the input and retained private files. No memory changed.

A separate one-call diagnostic reused the known-usage v3 proposal input with explicit low effort, the same 16,384-token output cap and no database access. It returned a complete schema-valid proposal in 108.80 seconds: seven claims, one episode action and no situation updates. Usage was 8,905 input tokens, 9,731 output tokens, 18,636 total, including 7,038 reasoning tokens. Independent review found four supportable claims and one supportable episode; three claims require rejection. One accurately reports a past question but is ineligible under the current conservative inquiry-source rule; it is not a hallucinated world fact. Two describe facts present elsewhere in the source but cite the wrong complete utterance segments. These are grounding failures that structural span validation alone cannot catch. No publication occurred. The [proposal record](v1-2026-10-25-longitudinal-low-effort-preflight.json) preserves the review; the separate verifier diagnostic below tests whether the errors are actually rejected. The first local invocation of this helper rejected a list-valued message sequence before reserving or sending a call; that zero-call failure is retained separately, and the corrected helper reconstructed the required tuple.

## Additional actual verification

The v3 distribution's runtime, schema and all 22 migrations matched source, sdist, wheel and isolated installation byte for byte: [artifact identity](v1-2026-10-25-longitudinal-v3-artifacts.json). Python `-I` from `/tmp` against that installed wheel passed **713 tests in 119.51 seconds**. Two warnings remained: upstream Starlette's `httpx` deprecation and the deliberate inherited-lock fork test. This was a focused combined regression, not another full repository or desktop Docker run.

The subsequent optional-effort provider/host patch passed **392 tests in 2.50 seconds**. The runner's effort-selection unit checks passed **35 tests**, with nine integration cases deliberately deselected at that step. These overlapping groups are not unique coverage totals. Ruff and diff checks passed for the verified scopes. No routine CI or Codex/Claude subscription test ran.

The isolated cue helper has a real HTTP/PostgreSQL integration test using local deterministic providers. It restores five distinct brains from one trusted server archive, compares their normalized durable recall graphs, opens fresh conversations and checks that the source brain and cognitive usage remain unchanged. Its real-model execution is separate and requires the primary campaign's clean eleven-session checkpoint.

A deterministic continuity test reconstructs the service, changes the model callback and starts a new conversation. The new callback receives persisted source-backed history; replay calls neither model again. This is integration evidence, not a real-model cross-provider result.

The [actual frozen-snapshot age diagnostic](v1-2026-10-25-access-age-diagnostic.md) exercised the service retrieval path at three age offsets without persistent changes or provider calls. Ordinary scores decayed and ordering changed; cue recovery preserved scores and order. Every memory remained returned in this small, generously budgeted corpus, so the result does not establish loss followed by recovery, years of operation or semantic maturation.

## Verification and product configuration follow-through

The [separate real verifier diagnostic](v1-2026-10-25-longitudinal-verifier-preflight.json) returned complete assessments in 53.26 seconds using 15,267 input and 4,207 output tokens (19,474 total), including 3,630 reasoning tokens. It marked both wrong-citation proposals `unknown` at the affected excerpt and whole-proposal levels, while supporting the four eligible grounded claims and the episode. The deterministic processor excluded the accurately attributed user-question report before verification under its existing conservative eligibility rule. Replaying both saved responses through the actual SemanticProcessor matched both original requests exactly except dynamic timeout: c1–c4 and ep1 were accepted, c5 was invalid, and c6/c7 were rejected as semantic_support_unconfirmed. Proposal and verifier runs were isolated diagnostics, not database publication or a resumed failed job.

Formal service settings and desktop cognition policy now expose optional reasoning effort and bounded HTTP envelope bytes. Defaults remain unchanged, final semantic content limits remain separate, old policy v1 files load without rewriting, and new saves use v2. Changing only these transport options triggers the required service restart. Presets preserve explicit transport choices; both directions of the disabled-thinking conflict are rejected before saving invalid configuration.

The final candidate [artifact identity](v1-2026-10-25-longitudinal-v4-artifacts.json) matches 113 runtime/schema/migration files and the same 22 migrations. Installed Python `-I` from `/tmp` passed **692 tests in 22.62 seconds**, with the previously verified full deterministic scenario deliberately deselected and one upstream Starlette warning. The relevant configuration/desktop checks had independently passed 320 source-tree tests; these counts overlap. No production source changed after this artifact build.

The installed frozen-policy recovery checks additionally passed **2 tests in 1.35 seconds**, with the same upstream warning. The v4 campaign remains a distinct frozen execution with pending behavioral review.
