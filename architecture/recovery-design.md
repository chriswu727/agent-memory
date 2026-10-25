# Desktop backup and recovery design

Status: proposed; the backup, deletion registry, and restore quarantine described here are not implemented. Build identity and schema compatibility checks are a separate implemented prerequisite. This document defines a bounded recovery path for the [first-release plan](v1-release-plan.md), not a claim of disaster-recovery acceptance.

## Decision

Implement a private, streamed **full-database backup** and an offline restore into a fresh installation. Use PostgreSQL custom-format dump and restore rather than copying a live Docker volume or extending the current memory archive. This is a logical database backup, not a physical backup. It preserves the complete database, including operational history, and avoids making PostgreSQL data-directory portability across machines a first-release requirement. PostgreSQL and pgvector compatibility still require explicit checks.

The minimal complete recovery feature includes an authoritative deletion registry outside the database snapshot and a restore quarantine. Shipping only a dump command would leave two consequential failures: old backups could revive erased information, and restored drafts or job reservations could repeat external operations. Neither a checksum nor a successful database import resolves those failures.

The first supported recovery destination is an empty, local desktop installation. In-place rollback, merging two live brains, cloud replication, arbitrary third-party dumps, and automatic resumption of pre-backup operations are excluded. The original installation must be retired before the recovered installation is activated. A disconnected clone cannot be proven retired by probing its old port; there is no claim of distributed fencing between independently copied databases.

## Existing behavior that must be preserved

- [Desktop configuration](../src/agent_memory/local_profile.py) holds installation, tenant, owner, and brain identities plus local credentials. [The Compose resource](../src/agent_memory/resources/desktop-compose.yaml) stores PostgreSQL in a separate named Docker volume. Copying the profile directory does not copy memory.
- [Desktop lifecycle operations](../src/agent_memory/desktop.py) use private files, authenticated instance shutdown, and lifecycle locks. Shutdown timeout preserves the database. Backup must not turn an unconfirmed shutdown into permission to kill arbitrary processes or copy a live database directory.
- [Session sidecars](../src/agent_memory/agent_session.py) hold pending identities and configuration fingerprints without message bodies. PostgreSQL remains authoritative. Sidecars alone cannot establish that a generation or delivery completed.
- [Memory export and restore](../src/agent_memory/service.py) cap archives at 16 MB and 5,000 records of each type. Archive restoration remaps data and omits runtime turns, reservations, and delivery receipts. The archive is a scoped portability operation, not an installation backup.
- Erasure writes `memory_tombstones`, invalidates derived records, removes content, and sanitizes archives held by the server. Current restoration combines tombstones from the current database with those in the archive. There is no deletion authority outside that database; restoring an older database loses later tombstones.
- [Runtime turns](../src/agent_memory/turns.py) and [background jobs](../src/agent_memory/jobs.py) retain fences, budgets, source bindings, and uncertain operations. A receipt can establish delivery while the turn failed to commit memory state. Recovery must preserve that distinction and all historical reservations.

New cognitive snapshots persist `operation_settlement_policy=no_unsettled_retry-v1`. On an expired lease, a remaining reserved operation blocks automatic reclaim: the job closes with `provider_uncertain`, and the original reservation remains charged with an uncertain outcome. Frozen snapshots without that policy retain their previous bounded reclaim behavior. An unsupported policy fails closed. This protects against repeating an unchanged request after losing its settlement; it does not prohibit automatic source repair after a correction or deletion. A changed source version can authorize a fresh background child, while the superseded parent's operations and cumulative budget history remain intact. This restart rule is separate from the stronger restore quarantine proposed below.
- [Migration compatibility](../src/agent_memory/storage.py) rejects unknown or non-prefix migration histories. The schema identity describes packaged migration bytes; the historical migration registry does not prove that previously executed SQL bytes matched them.

## Backup artifact and checkpoint

The bundle contains a versioned manifest, a full database dump, identity/configuration documents, and validated session-sidecar metadata. Each member has an exact size and SHA-256 digest. The manifest records the build and schema identities, database and extension versions, snapshot time, original brain identity, deletion-registry identity and applied sequence, and operational counts at the checkpoint. It records the recovery point; it must not use a later file-copy completion time as if it were the database snapshot time.

Local API/database credentials and provider configuration are sensitive. The output is a private artifact, created with the same owner-only file protections as the current profile. It is explicitly reported as containing private data and potentially credentials. Owner-only permissions are not encryption. No automatic upload or public artifact attachment is part of this feature. Only trusted bundles from the user's own installation may be restored; matching hashes establish integrity, not that an arbitrary SQL dump is safe to execute.

Before publishing a supported bundle, register its backup ID and manifest digest with the independent authority. Restore verifies that provenance against the already trusted authority, not against a key or digest supplied only by the incoming bundle. This permits restoring a known product-generated dump without claiming to safely execute arbitrary uploaded SQL. A freshly supplied authority is not automatically trusted merely because it agrees with a supplied backup.

Read and write dump bytes through bounded streams. The existing 16 MB archive ceiling does not apply to this separate format. Disk space, total artifact bytes, member count, expanded bytes, subprocess runtime, and parser metadata sizes have explicit limits with clear failure codes. Hitting a limit fails the backup instead of dropping records. Never buffer the database into one Python object.

Write to a new private staging location, flush and synchronize completed files, and publish the manifest last with an atomic final rename. Reject symlinks, hard links, path traversal, duplicate member names, unexpected members, and overwriting an existing destination. Cancellation or failure removes only files created by this attempt. Logs report identifiers, sizes, hashes, phases, and fixed failure codes; they do not report credentials, message bodies, raw SQL, or subprocess stderr.

## Authoritative deletion registry

A copy of tombstones inside each backup is insufficient: it contains only deletions known at that recovery point. The authority must survive replacement of the memory database and must not be rolled back together with the selected backup.

For a local first release, use a separate, private registry with an installation lineage identifier, strictly increasing sequence, stable request digests, and metadata-only erasure records. Its configured location is outside the managed database volume and outside restore staging. A registry stored on the same physical disk protects against database loss, not disk loss. Machine-loss recovery requires the current authority to survive independently; if it does not, the restore stays quarantined. A hash chain can expose missing or changed records relative to a trusted head, but cannot prove freshness if the entire authority and its head were rolled back together.

An erasure record identifies authorized tenant/owner/brain scope, resolved stable object IDs, mode, and the original durable cutoff. It contains no memory text. Do not replay an old conversation selector against newly created messages; persist its resolved targets. Recompute dependent closure against the restored graph as well, because an older snapshot can contain derived objects that were no longer present when the erasure was requested.

The required write protocol is:

1. Validate ownership, request identity, scope generation, and targets before writing an intent. Serialize registry updates and the relevant brain transaction in one documented lock order.
2. Durably append an authoritative erasure intent to the independent registry before acknowledging deletion. Acknowledged erasure must never depend solely on an outbox row inside the database being backed up.
3. Apply the intent through the existing invalidation, recursive dependency cleanup, archive sanitization, feedback cleanup, and tombstone paths. Persist the applied registry sequence with the database transaction.
4. Return completed only after the database application commits. If the registry intent is durable but database application fails, the operation remains pending and is reconciled idempotently. It is not silently rolled back or represented as an ordinary unaccepted request. The accepted deletion intent remains authoritative.
5. On startup and restore, reconcile pending intents before serving memory or starting workers. A database watermark ahead of the supplied registry, a missing authority, conflicting identity, malformed history, or incomplete replay blocks activation.

This requires a small shared erasure-application helper; copying `_forget` SQL into a restore script would diverge as new derived tables are added. Registry replay must retain the original authorization and avoid creating model calls or synthetic user events. Repeated replay must preserve scope/version semantics and not repeatedly inflate deletion or revision counts.

Legacy installations need an explicit initialization barrier: export the current tombstone set into the registry, reconcile it, and commit the initial watermark before issuing supported backups. Earlier deletions unknown to the live database cannot be reconstructed or claimed covered. The registry's identity and freshness requirements apply equally when restoring onto another machine. An embedded registry snapshot is useful evidence, but is not accepted as the current authority merely because it was packaged with the dump.

## Quiescence and restore quarantine

Backup takes the lifecycle lock, stops the owned API and worker with confirmed shutdown, and acquires a maintenance barrier that prevents automatic MCP/SDK startup and new local sessions until the checkpoint is complete. PostgreSQL can remain running only for the controlled dump. A successful local shutdown does not prove that an external host's already authorized model or delivery callback stopped. The checkpoint therefore records unfinished turns and operations without declaring them cancelled or uncharged.

Restore is a separate state machine: `validated`, `database_restored`, `deletions_applied`, `operations_quarantined`, `ready_for_activation`, and `activated`. Its journal is private, durable, and keyed by a stable restore request digest. Repeating the same attempt resumes safe local work; a different artifact, identity, registry head, or destination conflicts. Restoring is not a model call, and none of these phases starts the learning worker or invokes a host callback.

The fresh destination gets a new installation ID, Docker project, ports, and local API/database credentials. Original tenant, owner, brain, source, and turn identities remain intact so evidence dependencies and idempotency history remain meaningful. Do not silently replace an existing profile or adopt its database volume. Source credentials are not reused as live destination credentials. Provider settings can be retained as private configuration, but paid processing remains disabled until activation.

Before activation, the quarantine transaction:

- Preserves completed turns, confirmed delivery receipts, input/output provenance, correction history, and prior budget accounting.
- Fences every unfinished runtime turn. Invalidates old worker leases and sending authorizations, releases scope occupancy, and records a fixed restore disposition. It does not fabricate a delivery receipt or promote a draft to delivered memory. An existing confirmed receipt remains historical fact even when unfinished memory commit cannot be continued.
- Retains reserved cognitive operations as uncertain, closes or quarantines unfinished jobs without resetting counters, and prevents automatic continuation from a restored frozen job. Explicit later learning recovery authorizes fresh bounded work using the existing recovery lineage rules.
- Invalidates prepared plans, transient working-state authorizations, and pending answer-review/recall permissions. Derived content removed by registry replay remains unavailable.
- Reconciles sidecars against authoritative restored identities. Sidecars are retained as recovery evidence or rebuilt as blocked metadata; they never authorize replay by themselves. Installation-identity changes require deliberate session rebinding, not editing a fingerprint until it matches.

The new recovery epoch is exposed through monitoring and the host handshake. Pre-restore credentials and leases cannot operate on the destination. Before an adapter submits traffic, it must explicitly accept the recovery point and reconcile its own input cursor and delivery records. Fresh desktop input can begin after explicit activation; old pending messages do not auto-resend.

An old backup cannot deduplicate a message whose first receipt was recorded only after that backup. It also cannot know post-checkpoint corrections or recover later memories. These are recovery-point losses, not successful idempotency. Without an independent host receipt/input ledger, historical transport replay remains blocked; the product must not claim exactly-once behavior across rollback. This boundary is separate from the runtime's existing restart recovery against its current database.

## Smallest complete implementation

Implement the following connected pieces before exposing restore as supported:

| Piece | Expected files and responsibilities |
| --- | --- |
| Erasure authority | New `deletion_registry.py`, a migration for applied watermarks and reconciliation metadata, and a shared erasure helper extracted from `service.py`; preserve current authorization, derivation, archive, and feedback cleanup |
| Offline backup | New `desktop_backup.py`, narrowly shared lifecycle/maintenance helpers in `desktop.py`, and CLI commands in `desktop_cli.py`; bounded dump subprocesses, private staging, manifest validation, and checkpoint publication |
| Restore quarantine | New `recovery.py` and a migration for restore identity/epoch and quarantine disposition; controlled changes to `turns.py`, `jobs.py`, and startup gating; preserve operational history while disabling old permissions |
| Session and API gate | `agent_session.py`, `desktop_connection.py`, server startup, runtime clients, and local profile version handling; surface recovery state and require deliberate activation/reconciliation |
| Acceptance | New backup/restore unit and real Docker tests, installed-package exercise, English operational report, and bilingual README instructions |

A reasonable CLI surface is `desktop backup --output PATH`, `desktop restore --input PATH --directory EMPTY_DIRECTORY --registry CURRENT_AUTHORITY --request-id ID`, and `desktop recovery-status`. Activation is a separate explicit operation with the exact restore ID and recovery-point acknowledgement. The command names and persisted schema are proposals; they are not current APIs.

The restore implementation should create and validate a new database privately, apply deletions and quarantine, run compatibility and ownership checks, and publish the destination profile only after it reaches a consistent blocked state. Failed imports must never replace an existing installation. Running the old installation after activation is unsupported; if stronger cross-machine fencing becomes required, the authority must grow into an independently available epoch/ownership service rather than relying on duplicated local files.

## Acceptance required before claiming recovery support

Use deterministic local providers and real isolated PostgreSQL/Docker installations; no paid model is needed for these checks.

1. Restore a database larger than both memory-archive limits into a fresh installation. Compare retained source IDs, revisions, dependencies, beliefs, episodes, tombstones, runtime receipts, budgets, and stable brain ownership. Confirm bounded streaming instead of a single in-memory archive.
2. Back up, erase a source afterward, then restore the older backup with the current independent registry. Verify that original text, derived outputs, beliefs, episodes, relations, feedback contributions, and server-held archives do not revive. Cover conversation and whole-scope erasure cutoffs.
3. Crash before intent durability, after registry durability, after database commit, during dump, during restore, during deletion replay, and before activation. Repeating the same request must not truncate the previous artifact, duplicate erasure, lose accounting, call a model, or deliver a message.
4. Restore checkpoints at generation, draft, sending, confirmed-delivery/uncommitted, and background-operation reservation stages. Confirm zero automatic model calls or sends, preserved known receipts, explicit unknown outcomes, and rejection of old leases/credentials.
5. Reject corrupt or truncated bundles, oversized/decompression-bomb members, unsafe paths, unexpected SQL/format provenance, unknown schema, incompatible database/extension versions, nonempty targets, missing or stale authority, and conflicting restore IDs before activation or replacement of user data.
6. Verify that source and restored installations cannot accidentally share the same managed Docker project or local control identity. Exercise host recovery-epoch acknowledgement and replay rejection for transport history beyond the checkpoint.
7. Repeat the complete path from installed artifacts outside the repository. Check that diagnostics contain no conversation text, credentials, provider keys, or raw subprocess errors. Publish the exact recovery-point and authority limitations alongside the successful checks.
