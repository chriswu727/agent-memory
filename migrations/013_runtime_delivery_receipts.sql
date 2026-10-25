ALTER TABLE memory_runtime_turns
    ADD COLUMN delivery_receipt jsonb CHECK (
        delivery_receipt IS NULL OR jsonb_typeof(delivery_receipt) = 'object');
