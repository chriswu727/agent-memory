# Desktop backup and recovery

Desktop recovery creates a private, streamed PostgreSQL backup and restores it into a fresh installation. It preserves confirmed memory and operational history, applies deletions recorded after the checkpoint, and blocks old unfinished work. Restore and activation do not start the API, worker, chat model, or delivery callbacks.

A backup directory and the **independently current deletion registry** are both required. Keep the registry outside the database volume and protect it independently. The default registry is under `.agent-memory-recovery/<installation-id>` beside the profile directory. `desktop recovery-status` reports its location. This default separates the registry from the database; it does not protect against losing the entire disk. A registry copy taken with an old backup cannot establish that later deletions are covered. If the current authority is lost, supported recovery refuses to proceed.

## Create a backup

Pause external chat adapters before taking a checkpoint. The command confirms shutdown of the local API and worker, but cannot stop model or messaging operations already running in another application.

```sh
agent-memory desktop backup \
  --directory /private/path/current-profile \
  --output /private/path/backups/checkpoint-001 \
  --request-id checkpoint-001
```

The output is a new directory containing `manifest.json`, `database.dump`, `configuration.json`, and `sessions.jsonl`. The dump uses PostgreSQL custom format without compression. The bundle has owner-only access and includes conversation data and potentially provider keys. It is not encrypted. Original local API and database credentials are excluded; restored installations receive new credentials.

The default limit is 64 GiB for the bundle's data members, with a 3,600-second dump timeout. `--max-bytes` supports 1 byte through 1 TiB; `--timeout-seconds` supports 1 through 43,200 seconds. Metadata validation, service shutdown, and local database startup have their own bounds; the dump timeout is not a deadline for the entire command. A separate memory archive's 16 MB / 5,000-record limits do not apply to this backup.

The reported `checkpoint.checkpoint_at` is the database snapshot time. The command registers the manifest digest in the independent authority and atomically publishes the completed directory. It leaves the API and worker stopped. Resume normal use with `desktop start --directory ...` when appropriate.

If interrupted, repeat exactly the same command, destination, request ID, and limits. Complete staged backups resume publication without generating another checkpoint under the same backup ID. Existing completed backups are validated and returned without replacement. A different request cannot overwrite an existing artifact.

To abandon an incomplete backup, inspect `desktop recovery-status`, then use:

```sh
agent-memory desktop cancel-backup \
  --directory /private/path/current-profile \
  --backup-id BACKUP_UUID_FROM_STATUS
```

Cancellation releases maintenance mode and preserves partial files for inspection. It does not restart services or delete the original database. Remove abandoned private artifacts only after deciding they are no longer needed.

## Restore into a fresh installation

The destination must be empty. Do not use a current profile directory or a live database volume. Supply the registry that is independently known to contain all accepted deletions, including deletions after the selected checkpoint.

```sh
agent-memory desktop restore-backup \
  --directory /private/path/recovered-profile \
  --backup /private/path/backups/checkpoint-001 \
  --registry /private/path/current-deletion-authority \
  --request-id recovery-001

agent-memory desktop recovery-status \
  --directory /private/path/recovered-profile
```

Optional `--api-port` and `--database-port` select unused local ports. The same byte and timeout flags are available as on backup. Restore accepts registered product-generated bundles; a matching hash supplied only with an arbitrary SQL dump is insufficient.

The destination has a new installation ID, Docker project, local credentials, and recovery epoch. Brain, owner, source, and runtime identities are retained. An import uses a single PostgreSQL transaction. Repeating the same restore request detects a committed import and resumes migration, quarantine, or deletion replay without importing over an existing populated database. Conflicting requests and existing unrelated data are rejected.

The private journal reports `created`, `quarantined`, `activating`, or `active`. While blocked, regular startup, MCP connection, and local chat sessions are refused. The database container may be running for controlled recovery work; this is not an active chat service.

Quarantine preserves completed turns, confirmed delivery receipts, and historical usage. It closes unfinished turns with `cancelled`, releases scope reservations, invalidates leases and prepared permissions, and retains reserved model operations as `uncertain`. Every restored job receives a persistent replay guard, including completed jobs that later deletion replay might otherwise make eligible for automatic source repair. An explicit `retry-learning` request can authorize a fresh bounded child after activation.

Pending session files are retained as `recovery-sessions.jsonl` evidence. They are not copied into active sessions and cannot resend a message. A confirmed receipt remains confirmed even when its old unfinished memory commit is cancelled; cancellation is not evidence that the message was never delivered.

## Acknowledge the recovery point and activate

First stop the previous installation and any external adapters. Reconcile the host's input cursor, pending messages, and delivery records against the checkpoint and retained receipts. The restored database cannot know messages or acknowledgements first recorded after its checkpoint. It cannot safely infer that replaying those messages is a duplicate.

Copy the exact values from `recovery-status`:

```sh
agent-memory desktop activate-recovery \
  --directory /private/path/recovered-profile \
  --recovery-epoch RECOVERY_EPOCH_UUID \
  --backup-id BACKUP_UUID \
  --checkpoint-at EXACT_CHECKPOINT_TIMESTAMP \
  --retire-installation-id CURRENT_REGISTRY_INSTALLATION_UUID \
  --expected-sequence CURRENT_REGISTRY_SEQUENCE \
  --expected-head-hash CURRENT_REGISTRY_HEAD_HASH \
  --acknowledge-host-reconciliation
```

Activation atomically compares the current registry head and installation before transferring authority. New requests from the retired installation are blocked. If a new deletion changes the head, inspect the new status, reconcile it, and explicitly acknowledge the updated values. Interrupted activation can be repeated with its original values; replay never restores an older installation over a newer activation.

Activation leaves services stopped. Start the recovered installation explicitly:

```sh
agent-memory desktop start --directory /private/path/recovered-profile
```

The local SDK and MCP bridge use the epoch acknowledged by activation. External API adapters must explicitly supply `X-Memory-Recovery-Epoch` on operational requests. An authenticated `GET /v1/recovery` returns the epoch and recovery point without accepting them on the adapter's behalf.

```python
from agent_memory.client import MemoryClient

# Call recovery() to inspect metadata, then reconcile the external host first.
with MemoryClient(base_url, new_api_key) as inspection:
    recovery = inspection.recovery()

with MemoryClient(
    base_url, new_api_key, brain_id,
    recovery_epoch=explicitly_accepted_epoch,
) as memory:
    pass  # Submit newly accepted host input through the normal adapter lifecycle.
```

The TypeScript client accepts the corresponding `recoveryEpoch` constructor option and exposes `recovery()`. Neither client automatically adopts an epoch returned by the server. Missing or stale acknowledgement receives HTTP 409; original installation credentials are not valid destination credentials.

## Boundaries and verification

This is local logical recovery, not in-place rollback, distributed replication, or exactly-once messaging across rollback. A copied authority and copied head can both be stale; their hash chain cannot prove otherwise. Independent storage of the current registry and truthful host reconciliation remain necessary. Registry fencing also cannot withdraw a callback already running in an external host. Do not run disconnected clones after activation.

Automated verification includes real subprocess transport above 16 MiB, byte/deadline failures and unsafe-file rejection; database quarantine across generation, sending, delivery, and reservation stages; and a real Docker restore of 5,007 events with a dump above 16 MiB. The Docker test applies a post-checkpoint deletion, verifies credential and epoch rejection, and injects interruptions after import, quarantine, and authority activation. The same Docker path is exercised with the built wheel outside the repository. These checks do not claim every operating system, disk-loss scenario, PostgreSQL version combination, or external chat framework has been certified.

See [the design and remaining acceptance matrix](recovery-design.md) for the invariants and broader verification goals.
