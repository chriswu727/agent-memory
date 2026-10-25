CREATE TABLE memory_recovery_installations (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    registry_id text NOT NULL,
    installation_id text NOT NULL,
    recovery_epoch text,
    state text NOT NULL CHECK (state IN ('active','quarantined')),
    checkpoint jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(checkpoint)='object'),
    PRIMARY KEY (tenant_id,owner_id)
);

CREATE TABLE memory_erasure_receipts (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    registry_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence>0),
    intent_hash text NOT NULL CHECK (intent_hash ~ '^[0-9a-f]{64}$'),
    result jsonb NOT NULL CHECK (jsonb_typeof(result)='object'),
    applied_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,owner_id,registry_id,sequence)
);

CREATE INDEX memory_erasure_receipts_brain ON memory_erasure_receipts
    (tenant_id,owner_id,brain_id,registry_id,sequence DESC);

CREATE TABLE memory_recovery_quarantine (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    brain_id text NOT NULL,
    recovery_epoch text NOT NULL,
    object_kind text NOT NULL CHECK (object_kind IN ('turn','job')),
    object_id text NOT NULL,
    prior_state text NOT NULL,
    quarantined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,brain_id,recovery_epoch,object_kind,object_id),
    FOREIGN KEY (tenant_id,owner_id,brain_id) REFERENCES memory_brains(tenant_id,owner_id,id)
);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['memory_recovery_installations','memory_erasure_receipts','memory_recovery_quarantine'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',relation);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',relation);
        EXECUTE format(
            'CREATE POLICY principal_isolation ON %I USING (tenant_id=current_setting(''app.tenant_id'',true) AND owner_id=current_setting(''app.principal_id'',true)) WITH CHECK (tenant_id=current_setting(''app.tenant_id'',true) AND owner_id=current_setting(''app.principal_id'',true))',
            relation
        );
    END LOOP;
END $$;
