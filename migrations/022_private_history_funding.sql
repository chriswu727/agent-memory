ALTER TABLE memory_jobs ADD CONSTRAINT memory_jobs_private_history_funding CHECK (
    (NOT payload ? 'history_funding_permission' OR (
        kind = 'consolidate'
        AND payload->>'learning_phase' = 'encoding'
        AND payload->>'allocation_policy' = 'encoding-integration-budget-v1'
        AND payload->>'authorization_root_job_id' = id
        AND NOT payload ?| ARRAY['recovery_budget','recovery_root_job_id','encoding_replan_version']
        AND jsonb_typeof(payload->'history_funding_permission') = 'object'
        AND (payload->'history_funding_permission') - ARRAY[
            'version','visibility','access_policy_id','policy_revision','branch_id'] = '{}'::jsonb
        AND payload #>> '{history_funding_permission,version}' = 'private-brain-history-funding-v1'
        AND payload #>> '{history_funding_permission,visibility}' = 'private'
        AND payload #>> '{history_funding_permission,access_policy_id}' = 'owner:' || brain_id
        AND payload #> '{history_funding_permission,policy_revision}' = '1'::jsonb
        AND jsonb_typeof(payload #> '{history_funding_permission,branch_id}') = 'string'
        AND payload #> '{history_funding_permission,branch_id}' = payload #> '{target,branch_ref}'
    )) IS TRUE
    AND (NOT payload ? 'history_funding_origin' OR (
        kind = 'consolidate'
        AND payload->>'learning_phase' = 'history_integration'
        AND payload->>'allocation_policy' = 'encoding-integration-budget-v1'
        AND jsonb_typeof(payload->'history_funding_origin') = 'object'
        AND (payload->'history_funding_origin') - ARRAY[
            'version','parent_job_id','obligation_fingerprint','manifest_fingerprint'] = '{}'::jsonb
        AND payload #>> '{history_funding_origin,version}' = 'unattempted-history-funding-v1'
        AND jsonb_typeof(payload #> '{history_funding_origin,parent_job_id}') = 'string'
        AND length(payload #>> '{history_funding_origin,parent_job_id}') BETWEEN 1 AND 256
        AND payload #>> '{history_funding_origin,parent_job_id}' !~ '[[:space:][:cntrl:]]'
        AND jsonb_typeof(payload #> '{history_funding_origin,obligation_fingerprint}') = 'string'
        AND payload #>> '{history_funding_origin,obligation_fingerprint}' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(payload #> '{history_funding_origin,manifest_fingerprint}') = 'string'
        AND payload #>> '{history_funding_origin,manifest_fingerprint}' ~ '^[0-9a-f]{64}$'
    )) IS TRUE
    AND (NOT payload ? 'history_attention_policy'
         OR payload->'history_attention_policy' = '"bounded-comparison-attention-v1"'::jsonb)
);

CREATE FUNCTION preserve_private_history_funding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE field text;
BEGIN
    IF OLD.payload ? 'learning_phase'
       AND (OLD.payload->'target') IS DISTINCT FROM (NEW.payload->'target') THEN
        RAISE EXCEPTION 'Learning allocation scope is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.payload->>'learning_phase' = 'history_integration' THEN
        FOREACH field IN ARRAY ARRAY['from_sequence','through_sequence','encoding_parent_job_id',
                                    'encoding_input_fingerprint','history_cutoff_sequence'] LOOP
            IF (OLD.payload->field) IS DISTINCT FROM (NEW.payload->field) THEN
                RAISE EXCEPTION 'Historical obligation scope is immutable' USING ERRCODE = '23514';
            END IF;
        END LOOP;
    END IF;
    FOREACH field IN ARRAY ARRAY['history_funding_permission','history_funding_origin',
                                'history_obligation_manifest','history_attention_policy'] LOOP
        IF (OLD.payload->field) IS DISTINCT FROM (NEW.payload->field) THEN
            RAISE EXCEPTION 'Historical funding and obligation identity are immutable' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE TRIGGER memory_jobs_preserve_private_history_funding
    BEFORE UPDATE OF payload ON memory_jobs
    FOR EACH ROW EXECUTE FUNCTION preserve_private_history_funding();
