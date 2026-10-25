ALTER TABLE memory_assertions
    ADD COLUMN scope_visibility text GENERATED ALWAYS AS (document #>> '{scope,visibility}') STORED,
    ADD COLUMN scope_conversation_id text GENERATED ALWAYS AS (document #>> '{scope,conversation_id}') STORED,
    ADD COLUMN scope_branch_id text GENERATED ALWAYS AS (document #>> '{scope,branch_id}') STORED;
