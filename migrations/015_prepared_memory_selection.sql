ALTER TABLE memory_runtime_turns
    ADD COLUMN prepared_memory_selection jsonb CHECK (
        prepared_memory_selection IS NULL OR jsonb_typeof(prepared_memory_selection) = 'object');
