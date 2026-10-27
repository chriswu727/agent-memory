from psycopg.types.json import Jsonb

from .helpers import digest, now, uid


def resolve_targets(service, conn, principal, brain, selector):
    params = service._params(principal, brain['id'])
    kind, ids = selector['kind'], selector['ids']
    event_ids, memory_ids = set(), set()
    if kind == 'memories':
        for row in conn.execute(
            'SELECT id,event_ids FROM memory_assertions WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s '
            'AND id=ANY(%s::text[])', (*params, ids),
        ):
            memory_ids.add(row['id'])
            event_ids.update(row['event_ids'])
    else:
        condition = {'events': 'id=ANY(%s::text[])', 'conversation': 'conversation_id=ANY(%s::text[])',
                     'all_authorized_scope': 'true'}[kind]
        for row in conn.execute(
            'SELECT DISTINCT id FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND ' + condition,
            (*params, ids) if kind != 'all_authorized_scope' else params,
        ):
            event_ids.add(row['id'])
    return service._dependent_events(conn, principal, brain, event_ids), memory_ids


def apply_erasure(service, conn, principal, brain, *, event_ids, memory_ids, mode, deletion_id=None):
    params = service._params(principal, brain['id'])
    event_ids = service._dependent_events(conn, principal, brain, event_ids)
    memory_ids = set(memory_ids)
    memory_ids.update(service._invalidate(conn, principal, brain, event_ids, memory_ids))
    service._advance(conn, principal, brain, scope=True)
    for object_kind, object_ids in (('event', event_ids), ('memory', memory_ids)):
        for object_id in sorted(object_ids):
            conn.execute(
                'INSERT INTO memory_tombstones(tenant_id,owner_id,brain_id,object_kind,object_id,mode) '
                'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,owner_id,brain_id,object_kind,object_id) '
                "DO UPDATE SET mode=CASE WHEN memory_tombstones.mode='erase_content' THEN 'erase_content' ELSE EXCLUDED.mode END",
                (*params, object_kind, object_id, mode),
            )
    erase = mode == 'erase_content'
    if erase:
        from .belief_store import invalidate_beliefs
        from .pattern_store import invalidate_patterns

        invalidate_patterns(conn, service, principal, brain, event_ids=event_ids, memory_ids=memory_ids, deleted=True)
        invalidate_beliefs(conn, service, principal, brain, event_ids=event_ids, memory_ids=memory_ids, deleted=True)
    conn.execute(
        "UPDATE memory_jobs SET result=NULL WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND kind='query_embedding'",
        params,
    )
    conn.execute(
        'UPDATE memory_events SET suppressed=true,deleted=deleted OR %s,document=CASE WHEN %s THEN NULL ELSE document END '
        'WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s::text[])',
        (erase, erase, *params, sorted(event_ids)),
    )
    conn.execute(
        'UPDATE memory_assertions SET suppressed=true,deleted=deleted OR %s,'
        "document=CASE WHEN %s THEN NULL ELSE document END,search_text='',embedding=NULL "
        'WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=ANY(%s::text[])',
        (erase, erase, *params, sorted(memory_ids)),
    )
    conn.execute('DELETE FROM memory_working_states WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s', params)
    conn.execute('DELETE FROM memory_traces WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s', params)
    if erase:
        for archive in conn.execute(
            'SELECT id,brain_id,document FROM memory_archives WHERE tenant_id=%s AND owner_id=%s',
            (principal.tenant_id, principal.principal_id),
        ).fetchall():
            data = service._sanitize_archive(conn, principal, archive['document'])
            if data != archive['document']:
                conn.execute(
                    'UPDATE memory_archives SET document=%s,manifest_hash=%s '
                    'WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s AND id=%s',
                    (Jsonb(data), digest(data), principal.tenant_id, principal.principal_id, archive['brain_id'], archive['id']),
                )
    return {'deletion_id': deletion_id or uid(), 'mode': mode, 'scope_generation': brain['scope_generation'],
            'recall_blocked_at': now(), 'purge_state': 'completed' if erase else 'not_requested',
            'backup_expiry_deadline': None, 'tombstone_recorded': True,
            'affected_object_count': len(event_ids) + len(memory_ids)}
