SET LOCAL row_security = off;

CREATE TABLE memory_feedback_contributions (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    memory_id text NOT NULL,
    memory_revision bigint NOT NULL CHECK (memory_revision > 0),
    feedback_event_id text NOT NULL,
    feedback_event_revision bigint NOT NULL CHECK (feedback_event_revision > 0),
    feedback_content_hash text NOT NULL CHECK (feedback_content_hash ~ '^[0-9a-f]{64}$'),
    delivery_event_id text NOT NULL,
    delivery_event_revision bigint NOT NULL CHECK (delivery_event_revision > 0),
    delivery_content_hash text NOT NULL CHECK (delivery_content_hash ~ '^[0-9a-f]{64}$'),
    selection_trace_id text NOT NULL,
    recall_id text NOT NULL,
    turn_id text NOT NULL,
    conversation_id text NOT NULL,
    branch_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('user_confirmed', 'user_corrected', 'observed_outcome')),
    attribution_version text NOT NULL CHECK (attribution_version = 'sole-exact-statement-v1'),
    source_event_refs jsonb NOT NULL CHECK (jsonb_typeof(source_event_refs) = 'array'),
    source_event_ids text[] NOT NULL,
    source_memory_ids text[] NOT NULL,
    observed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    invalidated_at timestamptz,
    PRIMARY KEY (tenant_id, brain_id, memory_id, memory_revision, feedback_event_id, feedback_event_revision),
    FOREIGN KEY (tenant_id, brain_id, memory_id, memory_revision)
        REFERENCES memory_assertions (tenant_id, brain_id, id, revision) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE INDEX memory_feedback_sources ON memory_feedback_contributions USING gin (source_event_ids);
CREATE INDEX memory_feedback_memories ON memory_feedback_contributions USING gin (source_memory_ids);
CREATE INDEX memory_feedback_active ON memory_feedback_contributions
    (tenant_id, owner_id, brain_id, memory_id, memory_revision) WHERE invalidated_at IS NULL;

ALTER TABLE memory_feedback_contributions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_feedback_contributions FORCE ROW LEVEL SECURITY;
CREATE POLICY principal_isolation ON memory_feedback_contributions
    USING (tenant_id = current_setting('app.tenant_id', true)
        AND owner_id = current_setting('app.principal_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)
        AND owner_id = current_setting('app.principal_id', true));

ALTER TABLE memory_access_state ADD COLUMN last_reinforcement_at timestamptz;

-- Previous aggregates cannot establish exact delivered use or reconstruct individual contributions.
DELETE FROM memory_access_state;
