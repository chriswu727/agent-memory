CREATE INDEX memory_events_conversation_tail
ON memory_events (
    tenant_id, owner_id, brain_id, conversation_id,
    (COALESCE('value:' || branch_id, 'null')), sequence
)
WHERE is_current;
