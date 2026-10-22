ALTER TABLE memory_assertions ALTER COLUMN embedding TYPE vector;
ALTER TABLE memory_assertions ADD COLUMN embedding_model text NOT NULL DEFAULT 'lexical-feature-hash';
ALTER TABLE memory_assertions ADD COLUMN embedding_version text NOT NULL DEFAULT 'v1-d256-blake2s';
