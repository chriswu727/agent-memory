CREATE TABLE memory_pattern_views (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    document jsonb,
    event_ids text[] NOT NULL DEFAULT '{}',
    memory_ids text[] NOT NULL DEFAULT '{}',
    episode_ids text[] NOT NULL DEFAULT '{}',
    invalidated boolean NOT NULL DEFAULT false,
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, brain_id, id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id),
    CHECK (NOT (invalidated OR deleted) OR document IS NULL)
);

CREATE INDEX memory_patterns_sources ON memory_pattern_views USING gin (event_ids);
CREATE INDEX memory_patterns_memories ON memory_pattern_views USING gin (memory_ids);
CREATE INDEX memory_patterns_episodes ON memory_pattern_views USING gin (episode_ids);

ALTER TABLE memory_pattern_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_pattern_views FORCE ROW LEVEL SECURITY;
CREATE POLICY principal_isolation ON memory_pattern_views
    USING (tenant_id = current_setting('app.tenant_id', true)
           AND owner_id = current_setting('app.principal_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)
                AND owner_id = current_setting('app.principal_id', true));
