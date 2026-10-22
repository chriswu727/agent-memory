CREATE TABLE memory_runtime_turns (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    turn_id text NOT NULL,
    conversation_id text NOT NULL,
    branch_id text NOT NULL,
    input_fingerprint text NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    stage text NOT NULL DEFAULT 'started' CHECK (stage IN (
        'started', 'captured', 'prepared', 'generating', 'generated',
        'delivering', 'delivered', 'committed', 'completed', 'failed'
    )),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
    claim_count bigint NOT NULL DEFAULT 0 CHECK (claim_count >= 0),
    generation_count bigint NOT NULL DEFAULT 0 CHECK (generation_count >= 0),
    worker_id text,
    lease_token_digest text,
    lease_expires_at timestamptz,
    recovery_required text CHECK (recovery_required IN ('generation_uncertain', 'delivery_uncertain')),
    input_event_refs jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(input_event_refs) = 'array'),
    context_event_refs jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(context_event_refs) = 'array'),
    output_event_refs jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(output_event_refs) = 'array'),
    context_fingerprint text CHECK (context_fingerprint ~ '^[0-9a-f]{64}$'),
    context_generation bigint CHECK (context_generation >= 0),
    working_revision bigint CHECK (working_revision >= 0),
    durable_sequence bigint CHECK (durable_sequence >= 0),
    context_tokens bigint CHECK (context_tokens >= 0),
    failure_code text CHECK (failure_code IN (
        'capture_failed', 'prepare_failed', 'generation_failed', 'generation_uncertain',
        'delivery_failed', 'delivery_uncertain', 'commit_failed', 'source_unavailable',
        'context_stale', 'cancelled', 'budget_exceeded', 'dependency_unavailable', 'internal_error'
    )),
    last_transition_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, brain_id, turn_id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE TABLE memory_runtime_turn_transitions (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    turn_id text NOT NULL,
    revision bigint NOT NULL,
    stage text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('begin', 'claim', 'advance', 'release', 'fail')),
    fence bigint NOT NULL,
    failure_code text,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, brain_id, turn_id, revision),
    FOREIGN KEY (tenant_id, brain_id, turn_id) REFERENCES memory_runtime_turns (tenant_id, brain_id, turn_id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id)
);

CREATE INDEX memory_runtime_turns_monitor ON memory_runtime_turns (tenant_id, owner_id, brain_id, updated_at DESC, turn_id);

CREATE TABLE memory_event_dependencies (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    input_event_id text NOT NULL,
    output_event_id text NOT NULL,
    PRIMARY KEY (tenant_id, brain_id, input_event_id, output_event_id),
    FOREIGN KEY (tenant_id, owner_id, brain_id) REFERENCES memory_brains (tenant_id, owner_id, id),
    CHECK (input_event_id <> output_event_id)
);

CREATE INDEX memory_event_dependencies_outputs ON memory_event_dependencies (tenant_id, brain_id, output_event_id);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['memory_runtime_turns', 'memory_runtime_turn_transitions', 'memory_event_dependencies'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', relation);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', relation);
        EXECUTE format(
            'CREATE POLICY principal_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true) AND owner_id = current_setting(''app.principal_id'', true))',
            relation
        );
    END LOOP;
END $$;
