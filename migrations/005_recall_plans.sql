ALTER TABLE memory_runtime_turns
    ADD COLUMN recall_plan jsonb CHECK (recall_plan IS NULL OR jsonb_typeof(recall_plan) = 'object'),
    ADD COLUMN recall_model_calls bigint NOT NULL DEFAULT 0 CHECK (recall_model_calls >= 0),
    ADD COLUMN recall_input_bytes bigint NOT NULL DEFAULT 0 CHECK (recall_input_bytes >= 0);
