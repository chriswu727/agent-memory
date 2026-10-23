ALTER TABLE memory_jobs
    ADD COLUMN result jsonb,
    ADD COLUMN source_event_ids text[] NOT NULL DEFAULT '{}',
    ADD COLUMN source_memory_ids text[] NOT NULL DEFAULT '{}';

CREATE INDEX memory_jobs_sources ON memory_jobs USING gin (source_event_ids);
CREATE INDEX memory_jobs_memories ON memory_jobs USING gin (source_memory_ids);

CREATE TABLE memory_episodes (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    conversation_id text NOT NULL,
    branch_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('open', 'closed', 'invalidated')),
    document jsonb,
    event_ids text[] NOT NULL DEFAULT '{}',
    is_current boolean NOT NULL DEFAULT true,
    invalidated boolean NOT NULL DEFAULT false,
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, brain_id, id, revision),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE memory_episode_members (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    episode_id text NOT NULL,
    episode_revision bigint NOT NULL,
    memory_id text NOT NULL,
    memory_revision bigint NOT NULL,
    role text NOT NULL CHECK (role IN ('fragment', 'outline', 'anchor', 'residual', 'schema')),
    position integer NOT NULL CHECK (position >= 0),
    PRIMARY KEY (tenant_id, brain_id, episode_id, episode_revision, memory_id, memory_revision),
    FOREIGN KEY (tenant_id, brain_id, episode_id, episode_revision)
        REFERENCES memory_episodes (tenant_id, brain_id, id, revision) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, brain_id, memory_id, memory_revision)
        REFERENCES memory_assertions (tenant_id, brain_id, id, revision) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE memory_associations (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    source_id text NOT NULL,
    source_revision bigint NOT NULL,
    target_id text NOT NULL,
    target_revision bigint NOT NULL,
    relation text NOT NULL CHECK (relation IN ('same_episode', 'context', 'schema', 'exception', 'procedure')),
    event_ids text[] NOT NULL DEFAULT '{}',
    invalidated boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, brain_id, id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE memory_access_state (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    memory_id text NOT NULL,
    memory_revision bigint NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    external_uses bigint NOT NULL DEFAULT 0,
    successes bigint NOT NULL DEFAULT 0,
    failures bigint NOT NULL DEFAULT 0,
    last_external_use_at timestamptz,
    last_feedback_event_id text,
    policy_version text NOT NULL DEFAULT 'contextual-access-v1',
    PRIMARY KEY (tenant_id, brain_id, memory_id, memory_revision),
    FOREIGN KEY (tenant_id, brain_id, memory_id, memory_revision)
        REFERENCES memory_assertions (tenant_id, brain_id, id, revision) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE memory_embeddings (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    memory_id text NOT NULL,
    memory_revision bigint NOT NULL,
    model text NOT NULL,
    model_version text NOT NULL,
    content_hash text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0 AND dimensions <= 8192),
    embedding vector NOT NULL,
    PRIMARY KEY (tenant_id, brain_id, memory_id, memory_revision, model, model_version),
    CHECK (vector_dims(embedding) = dimensions),
    FOREIGN KEY (tenant_id, brain_id, memory_id, memory_revision)
        REFERENCES memory_assertions (tenant_id, brain_id, id, revision) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

ALTER TABLE memory_runtime_turns ADD COLUMN orientation_job_id text;
ALTER TABLE memory_traces ADD COLUMN applied_at timestamptz;

CREATE INDEX memory_episode_scope ON memory_episodes
    (tenant_id, brain_id, conversation_id, branch_id, is_current);
CREATE INDEX memory_episode_sources ON memory_episodes USING gin (event_ids);
CREATE INDEX memory_members_by_memory ON memory_episode_members (tenant_id, brain_id, memory_id);
CREATE INDEX memory_association_source ON memory_associations (tenant_id, brain_id, source_id);
CREATE INDEX memory_association_target ON memory_associations (tenant_id, brain_id, target_id);
CREATE INDEX memory_association_events ON memory_associations USING gin (event_ids);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'memory_episodes', 'memory_episode_members', 'memory_associations',
        'memory_access_state', 'memory_embeddings'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format(
            'CREATE POLICY principal_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true))',
            relation
        );
    END LOOP;
END $$;
