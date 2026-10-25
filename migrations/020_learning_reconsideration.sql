ALTER TABLE memory_brains
    ADD COLUMN learning_reconsideration jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(learning_reconsideration) = 'object');

CREATE INDEX memory_jobs_deferred_learning ON memory_jobs
    (tenant_id, owner_id, brain_id, created_at, id)
    WHERE kind = 'consolidate' AND state = 'deferred';
