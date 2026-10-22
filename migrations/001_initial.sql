CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_brains (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    name text NOT NULL,
    scope_generation bigint NOT NULL DEFAULT 1,
    dependency_generation bigint NOT NULL DEFAULT 0,
    durable_sequence bigint NOT NULL DEFAULT 0,
    indexed_sequence bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_events (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    revision bigint NOT NULL,
    sequence bigint NOT NULL,
    producer_ref text NOT NULL,
    source_key text NOT NULL,
    source_revision bigint NOT NULL,
    conversation_id text,
    branch_id text,
    content_hash text NOT NULL,
    input_hash text NOT NULL,
    document jsonb,
    is_current boolean NOT NULL DEFAULT true,
    retired boolean NOT NULL DEFAULT false,
    suppressed boolean NOT NULL DEFAULT false,
    deleted boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, brain_id, id, revision),
    UNIQUE (tenant_id, brain_id, sequence),
    UNIQUE (tenant_id, brain_id, producer_ref, source_key, source_revision),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_assertions (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    revision bigint NOT NULL,
    document jsonb,
    event_ids text[] NOT NULL DEFAULT '{}',
    memory_ids text[] NOT NULL DEFAULT '{}',
    is_current boolean NOT NULL DEFAULT true,
    invalidated boolean NOT NULL DEFAULT false,
    suppressed boolean NOT NULL DEFAULT false,
    deleted boolean NOT NULL DEFAULT false,
    search_text text NOT NULL DEFAULT '',
    embedding vector(256),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, brain_id, id, revision),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_working_states (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    conversation_id text NOT NULL,
    branch_id text NOT NULL,
    revision bigint NOT NULL,
    document jsonb NOT NULL,
    PRIMARY KEY (tenant_id, brain_id, conversation_id, branch_id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_jobs (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    kind text NOT NULL,
    state text NOT NULL DEFAULT 'queued',
    payload jsonb NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, brain_id, id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_requests (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    action text NOT NULL,
    key text NOT NULL,
    request_hash text NOT NULL,
    response jsonb,
    PRIMARY KEY (tenant_id, brain_id, action, key),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_tombstones (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    object_kind text NOT NULL,
    object_id text NOT NULL,
    mode text NOT NULL,
    deleted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, owner_id, brain_id, object_kind, object_id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_archives (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    manifest_hash text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE IF NOT EXISTS memory_traces (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    id text NOT NULL,
    document jsonb NOT NULL,
    PRIMARY KEY (tenant_id, brain_id, id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE INDEX IF NOT EXISTS memory_events_tail ON memory_events (tenant_id, brain_id, sequence);
CREATE INDEX IF NOT EXISTS memory_assertions_events ON memory_assertions USING gin (event_ids);
CREATE INDEX IF NOT EXISTS memory_assertions_dependencies ON memory_assertions USING gin (memory_ids);
CREATE INDEX IF NOT EXISTS memory_assertions_search ON memory_assertions USING gin (to_tsvector('simple', search_text));
CREATE INDEX IF NOT EXISTS memory_jobs_ready ON memory_jobs (tenant_id, brain_id, state, created_at);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'memory_brains', 'memory_events', 'memory_assertions', 'memory_working_states',
        'memory_jobs', 'memory_requests', 'memory_tombstones', 'memory_archives', 'memory_traces'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format(
            'CREATE POLICY principal_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true))',
            relation
        );
    END LOOP;
END $$;
