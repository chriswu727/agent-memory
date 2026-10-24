CREATE TABLE memory_belief_relations (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    revision bigint NOT NULL CHECK (revision = 1),
    document jsonb,
    event_ids text[] NOT NULL DEFAULT '{}',
    memory_ids text[] NOT NULL DEFAULT '{}',
    endpoint_versions jsonb NOT NULL,
    endpoint_keys text[] NOT NULL,
    invalidated boolean NOT NULL DEFAULT false,
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, brain_id, id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id),
    CHECK (NOT (invalidated OR deleted) OR document IS NULL)
);

CREATE INDEX memory_belief_sources ON memory_belief_relations USING gin (event_ids);
CREATE INDEX memory_belief_memories ON memory_belief_relations USING gin (memory_ids);
CREATE INDEX memory_belief_endpoints ON memory_belief_relations USING gin (endpoint_keys);

ALTER TABLE memory_belief_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_belief_relations FORCE ROW LEVEL SECURITY;
CREATE POLICY principal_isolation ON memory_belief_relations
    USING (tenant_id = current_setting('app.tenant_id', true)
           AND owner_id = current_setting('app.principal_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)
                AND owner_id = current_setting('app.principal_id', true));
