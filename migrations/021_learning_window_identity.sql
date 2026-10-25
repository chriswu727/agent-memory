CREATE FUNCTION preserve_learning_window_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.payload ? 'encoding_window_snapshot'
       AND (OLD.payload->'encoding_window_snapshot') IS DISTINCT FROM (NEW.payload->'encoding_window_snapshot') THEN
        RAISE EXCEPTION 'Learning attempted window is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER memory_jobs_preserve_learning_window_identity
    BEFORE UPDATE OF payload ON memory_jobs
    FOR EACH ROW EXECUTE FUNCTION preserve_learning_window_identity();
