ALTER TABLE memory_runtime_turns
    ADD COLUMN orientation_preflight jsonb CHECK (
        orientation_preflight IS NULL OR jsonb_typeof(orientation_preflight) = 'object'),
    ADD COLUMN orientation_route jsonb CHECK (
        orientation_route IS NULL OR jsonb_typeof(orientation_route) = 'object');
