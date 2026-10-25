ALTER TABLE memory_cognitive_budgets
    ADD COLUMN revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    ADD COLUMN is_explicit boolean NOT NULL DEFAULT false;

CREATE TABLE memory_cognitive_budget_changes (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    request_id text NOT NULL,
    request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    revision bigint NOT NULL CHECK (revision > expected_revision),
    window_seconds integer NOT NULL CHECK (window_seconds BETWEEN 1 AND 2592000),
    max_model_calls bigint NOT NULL CHECK (max_model_calls BETWEEN 0 AND 1000000),
    max_input_bytes bigint NOT NULL CHECK (max_input_bytes BETWEEN 0 AND 1000000000000),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, brain_id, request_id),
    UNIQUE (tenant_id, brain_id, revision),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

ALTER TABLE memory_cognitive_budget_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_cognitive_budget_changes FORCE ROW LEVEL SECURITY;
CREATE POLICY principal_isolation ON memory_cognitive_budget_changes
    USING (tenant_id = current_setting('app.tenant_id', true) AND owner_id = current_setting('app.principal_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true) AND owner_id = current_setting('app.principal_id', true));
