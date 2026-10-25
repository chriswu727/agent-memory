SET LOCAL row_security = off;

ALTER TABLE memory_runtime_turns
    ADD COLUMN scope_reserved boolean NOT NULL DEFAULT false;

WITH pending AS (
    SELECT tenant_id, brain_id, turn_id,
           row_number() OVER (
               PARTITION BY tenant_id, brain_id, conversation_id, branch_id
               ORDER BY created_at, turn_id
           ) AS position
    FROM memory_runtime_turns
    WHERE stage NOT IN ('completed', 'failed')
)
UPDATE memory_runtime_turns AS turns
SET scope_reserved = true
FROM pending
WHERE pending.position = 1 AND turns.tenant_id = pending.tenant_id
    AND turns.brain_id = pending.brain_id AND turns.turn_id = pending.turn_id;

CREATE UNIQUE INDEX memory_runtime_turns_scope_reservation
    ON memory_runtime_turns (tenant_id, brain_id, conversation_id, branch_id)
    WHERE scope_reserved;

CREATE INDEX memory_runtime_turns_pending_scope
    ON memory_runtime_turns (tenant_id, owner_id, brain_id, conversation_id, branch_id, created_at, turn_id)
    WHERE stage NOT IN ('completed', 'failed');
