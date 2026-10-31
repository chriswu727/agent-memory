CREATE INDEX memory_events_note_sources ON memory_events
USING gin ((document->'observed'->'source_references') jsonb_path_ops)
WHERE is_current AND NOT deleted AND NOT suppressed AND NOT retired
AND conversation_id='membrain-notes'
AND document->'observed'->'source_references' IS NOT NULL;
