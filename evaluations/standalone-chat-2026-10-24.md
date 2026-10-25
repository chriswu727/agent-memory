# Standalone chat and local session acceptance

Date: 2026-10-24. Platform: macOS, Python 3.12, local Docker and PostgreSQL.

This change adds a direct synchronous text entry point over the existing memory runtime. It does not change the memory algorithms or establish human-like memory quality. No paid model or subscription-backed requests were made for this acceptance.

## Architecture

`desktop chat` and Python `open_agent()` share `AgentSession`, which delegates to `BrainRuntime` and the public memory API. `ChatCompletionsGenerator` supplies the model callback; the host supplies actual delivery. Database state remains authoritative. Local session files contain identities and fingerprints, not another copy of message bodies.

Reply settings are stored separately in private `chat.json`. Configuring or changing replies does not modify the stable brain, cognition settings, or the service restart fingerprint. Reusing cognition settings requires an explicit choice. The existing profile format and database schema are unchanged.

## Verified behavior

| Area | Covered behavior |
| --- | --- |
| Provider | Exact runtime messages; optional thinking setting; bounded response and decompression; no retries, redirects, or environment proxy use; rejection of truncated, tool, refusal, invalid, and nontext replies; redacted failures and validated usage |
| Configuration | Strict versioned data; bounded reads; duplicate-field rejection; private files; symlink and hard-link rejection; atomic writes; no implicit environment or cognition fallback |
| Concurrency | Same-scope local processes and threads cannot run simultaneous turns; independent scopes remain available; an active server lease cannot be stolen during cancellation |
| Recovery | Pending state persists before execution; drafts resume without regeneration; unknown generation and delivery never automatically retry; source and input identity remain checked |
| Message identity | A completed message ID reused with different input is rejected without creating a misleading pending record; recovery cannot substitute an older answer for another input |
| Configuration changes | Model, instructions, and policy stay frozen while generation or delivery remains possible; already confirmed delivery can complete after configuration changes without another external call |
| Cancellation | Exact pending turn required; uncertainty needs explicit acknowledgement; sources and uncertainty classifications remain; cancellation cannot turn a draft into delivered memory or undo external effects |
| Terminal | Complete write and flush precede delivery confirmation; short writes and output failures remain uncertain; malformed commands do not accidentally become model messages |

The installed-wheel test runs separate CLI processes from outside the repository against an isolated desktop database and a real loopback HTTP provider. It completes a first exchange, stops the service, recalls the earlier source in a new conversation after restart, deduplicates a completed message, rejects altered input under the same ID, blocks a truncated response and automatic regeneration, explicitly cancels the uncertain turn, and continues with a new message. Four fake-provider requests are made. Cancellation preserves the failed turn's input and does not create a delivered output event. The test also verifies that service logs and session sidecars do not contain the conversation bodies or provider key.

## Validation results

- Final full suite with desktop tests enabled: **2,908 passed, zero skipped**, 173.12 seconds.
- Session-specific tests: **90 passed**, including 17 real PostgreSQL cases.
- Ruff over `src`, `tests`, and `examples`: passed.
- Source distribution and wheel build: passed; the wheel was installed in a separate Python environment for the standalone test.
- README local links, runtime-contract JSON, and whitespace checks: passed.
- No CI workflows were added or triggered.

To reproduce full acceptance after building and installing the wheel into a separate environment:

```sh
MEMORY_DESKTOP_E2E=1 \
MEMORY_CHAT_E2E_PYTHON=/absolute/path/to/wheel-environment/bin/python \
uv run pytest -q
```

The regular database tests require the existing dedicated development database. Desktop tests create isolated installations and remove only their own test volumes. The provider is deterministic local HTTP, so cross-conversation recall here establishes source retrieval and lifecycle wiring, not semantic learning or model answer quality.

## Remaining boundaries

Support remains synchronous Chat Completions text. Streaming, tool loops, multimodal protocols, a model-compatible gateway, and third-party chat adapters are not implemented by this entry point. Session locks cover one local installation, not arbitrary API users or other machines. MCP remains tools-only.

The desktop path still requires uv and local Docker. This acceptance does not establish Windows or Linux desktop behavior. Reply credentials are protected plaintext rather than a keychain. Per-request limits and most-recent-call usage diagnostics are not cumulative billing limits. Cancellation preserves unknown external outcomes; it cannot prove that a remote generation was not charged or a partially written answer was not seen.

Long-term cognitive quality, aging and cue-driven memory behavior, and performance under sustained concurrent use still require separate evaluation. The new entry point reuses the existing memory core and does not claim to complete those goals.
