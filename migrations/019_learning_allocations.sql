ALTER TABLE memory_jobs
    ADD COLUMN authorization_root_job_id text GENERATED ALWAYS AS
        (payload->>'authorization_root_job_id') STORED,
    ADD CONSTRAINT memory_jobs_scoped_identifier UNIQUE (tenant_id, owner_id, brain_id, id),
    ADD CONSTRAINT memory_jobs_allocation_root FOREIGN KEY
        (tenant_id, owner_id, brain_id, authorization_root_job_id)
        REFERENCES memory_jobs (tenant_id, owner_id, brain_id, id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT memory_jobs_allocation_metadata CHECK (
        NOT (payload ?| ARRAY['learning_contract_version','learning_phase','authorization_root_job_id',
                             'allocation_policy','allocation_budget','phase_budget','allocation_input_fingerprint'])
        OR (
            kind = 'consolidate'
            AND payload ?& ARRAY['learning_contract_version','learning_phase','authorization_root_job_id',
                                'allocation_policy','allocation_budget','phase_budget']
            AND payload->>'learning_contract_version' = 'encoding-integration-v1'
            AND payload->>'learning_phase' IN ('encoding','history_integration')
            AND jsonb_typeof(payload->'authorization_root_job_id') = 'string'
            AND length(payload->>'authorization_root_job_id') BETWEEN 1 AND 256
            AND payload->>'authorization_root_job_id' !~ '[[:space:][:cntrl:]]'
            AND payload->'phase_budget' = '{"max_model_calls":2,"max_input_bytes":196608}'::jsonb
            AND (
                (payload->>'allocation_policy' = 'encoding-integration-budget-v1'
                 AND payload->'allocation_budget' = '{"max_model_calls":4,"max_input_bytes":393216}'::jsonb)
                OR (payload->>'allocation_policy' = 'explicit-phase-recovery-budget-v1'
                    AND payload->'allocation_budget' = '{"max_model_calls":2,"max_input_bytes":196608}'::jsonb)
            )
            AND (NOT payload ? 'allocation_input_fingerprint'
                 OR (jsonb_typeof(payload->'allocation_input_fingerprint') = 'string'
                     AND payload->>'allocation_input_fingerprint' ~ '^[0-9a-f]{64}$'))
        ) IS TRUE
    );

CREATE INDEX memory_jobs_allocation_members ON memory_jobs
    (tenant_id, owner_id, brain_id, authorization_root_job_id)
    WHERE authorization_root_job_id IS NOT NULL;

CREATE FUNCTION preserve_learning_allocation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE field text;
BEGIN
    FOREACH field IN ARRAY ARRAY['learning_contract_version','learning_phase','authorization_root_job_id',
                                'allocation_policy','allocation_budget','phase_budget'] LOOP
        IF (OLD.payload->field) IS DISTINCT FROM (NEW.payload->field) THEN
            RAISE EXCEPTION 'Learning allocation authority is immutable' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    IF OLD.payload ? 'allocation_input_fingerprint'
       AND (OLD.payload->'allocation_input_fingerprint') IS DISTINCT FROM (NEW.payload->'allocation_input_fingerprint') THEN
        RAISE EXCEPTION 'Learning input identity is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER memory_jobs_preserve_learning_allocation
    BEFORE UPDATE OF payload ON memory_jobs
    FOR EACH ROW EXECUTE FUNCTION preserve_learning_allocation();
