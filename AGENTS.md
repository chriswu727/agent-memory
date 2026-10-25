# Repository conventions

## Language

This project primarily serves English-speaking developers. Use English for repository-facing documentation, architecture specifications, API descriptions, developer-facing UI text, diagnostics, code comments, commit messages, pull requests, issues authored for the project, and release notes.

`README.md` is the default English entry point. Maintain `README.zh-CN.md` as its Chinese counterpart, with language links in both files. Keep setup instructions, implementation status, limitations, and verification claims aligned between them when making changes.

Preserve non-English text when it is intentional multilingual input, a language-recognition rule, a test fixture, or original evaluation evidence. Do not translate historical model inputs or outputs in place, or change test semantics merely to remove non-English characters. Explain quoted evidence in English and label translations separately from the original. Documentation language does not restrict the languages the memory runtime supports.

## Product boundary

The memory runtime is independent of third-party chat frameworks. Optional host adapters call the public API or SDK; the core must not import a chat framework or require one to run. Memory state and algorithms belong to the core. See `architecture/autonomous-runtime.contract.json` for lifecycle responsibilities and integration requirements.

## Verification and publication

Keep planned capabilities separate from implemented and verified behavior. Preserve failed results and limitations when editing documentation or translating reports.

Run local checks appropriate to the change. Do not add or trigger routine CI workflows for documentation updates. Paid model evaluations and subscription-backed tests must follow the user's authorization; documentation edits do not require them.
