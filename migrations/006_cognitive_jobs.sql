ALTER TABLE memory_jobs
    ADD COLUMN fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
    ADD COLUMN worker_id text,
    ADD COLUMN lease_token_digest text,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN available_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN model_calls bigint NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
    ADD COLUMN input_bytes bigint NOT NULL DEFAULT 0 CHECK (input_bytes >= 0),
    ADD COLUMN max_model_calls bigint CHECK (max_model_calls >= 0),
    ADD COLUMN max_input_bytes bigint CHECK (max_input_bytes >= 0);

CREATE TABLE memory_cognitive_operations (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    job_id text NOT NULL,
    operation_id text NOT NULL,
    request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    input_bytes bigint NOT NULL CHECK (input_bytes >= 0),
    fence bigint NOT NULL CHECK (fence > 0),
    outcome text NOT NULL DEFAULT 'reserved' CHECK (outcome IN ('reserved', 'completed', 'failed', 'uncertain')),
    usage jsonb CHECK (usage IS NULL OR jsonb_typeof(usage) = 'object'),
    response_fingerprint text CHECK (response_fingerprint ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (tenant_id, brain_id, job_id, operation_id),
    FOREIGN KEY (tenant_id, brain_id, job_id) REFERENCES memory_jobs (tenant_id, brain_id, id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE memory_cognitive_budgets (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    window_seconds integer NOT NULL CHECK (window_seconds BETWEEN 1 AND 2592000),
    max_model_calls bigint NOT NULL CHECK (max_model_calls >= 0),
    max_input_bytes bigint NOT NULL CHECK (max_input_bytes >= 0),
    PRIMARY KEY (tenant_id, brain_id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE INDEX memory_cognitive_operations_window ON memory_cognitive_operations
    (tenant_id, owner_id, brain_id, created_at);
CREATE INDEX memory_jobs_leases ON memory_jobs
    (tenant_id, owner_id, brain_id, state, available_at, lease_expires_at);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['memory_cognitive_operations', 'memory_cognitive_budgets'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format(
            'CREATE POLICY principal_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true))',
            relation
        );
    END LOOP;
END $$;
