ALTER TABLE memory_runtime_turns
    ADD COLUMN response_review_policy text CHECK (
        response_review_policy IS NULL OR response_review_policy ~ '^[0-9a-f]{64}$'),
    ADD COLUMN response_review jsonb CHECK (
        response_review IS NULL OR jsonb_typeof(response_review) = 'object'),
    ADD COLUMN response_review_calls bigint NOT NULL DEFAULT 0 CHECK (response_review_calls BETWEEN 0 AND 1);
