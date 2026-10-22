UPDATE memory_assertions AS assertion
SET invalidated = true
WHERE EXISTS (
    SELECT 1 FROM memory_events AS event
    WHERE event.tenant_id = assertion.tenant_id
      AND event.owner_id = assertion.owner_id
      AND event.brain_id = assertion.brain_id
      AND event.id = ANY(assertion.event_ids)
      AND event.document #>> '{observed,event_kind}' IN ('source_correction', 'delivery_receipt')
);
