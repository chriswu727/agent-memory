from __future__ import annotations

from psycopg.types.json import Jsonb

from .deletion_registry import RegistryError


def quarantine(database, principal, *, source_installation_id, installation_id, registry_id,
               recovery_epoch, manifest_hash, checkpoint):
    params = (principal.tenant_id, principal.principal_id)
    with database.transaction(principal) as conn:
        row = conn.execute('SELECT * FROM memory_recovery_installations WHERE tenant_id=%s AND owner_id=%s FOR UPDATE',
                           params).fetchone()
        if not row or row['registry_id'] != registry_id:
            raise RegistryError()
        if row['recovery_epoch'] == recovery_epoch:
            if (row['installation_id'] != installation_id
                    or row['checkpoint'].get('manifest_hash') != manifest_hash):
                raise RegistryError('idempotency_conflict')
            return
        if row['installation_id'] != source_installation_id:
            raise RegistryError('idempotency_conflict')
        conn.execute('SELECT id FROM memory_brains WHERE tenant_id=%s AND owner_id=%s ORDER BY id FOR UPDATE', params)
        conn.execute(
            'INSERT INTO memory_recovery_quarantine(tenant_id,owner_id,brain_id,recovery_epoch,object_kind,object_id,prior_state) '
            "SELECT tenant_id,owner_id,brain_id,%s,'turn',turn_id,stage FROM memory_runtime_turns "
            "WHERE tenant_id=%s AND owner_id=%s AND stage NOT IN ('completed','failed')", (recovery_epoch, *params),
        )
        conn.execute(
            'INSERT INTO memory_recovery_quarantine(tenant_id,owner_id,brain_id,recovery_epoch,object_kind,object_id,prior_state) '
            "SELECT tenant_id,owner_id,brain_id,%s,'job',id,state FROM memory_jobs "
            "WHERE tenant_id=%s AND owner_id=%s", (recovery_epoch, *params),
        )
        conn.execute(
            "UPDATE memory_runtime_turns SET stage='failed',failure_code='cancelled',revision=revision+1,fence=fence+1,"
            'worker_id=NULL,lease_token_digest=NULL,lease_expires_at=NULL,scope_reserved=false,'
            'prepared_memory_selection=NULL,recall_plan=NULL,orientation_route=NULL,orientation_preflight=NULL,'
            'response_review=NULL,last_transition_hash=NULL,updated_at=clock_timestamp() '
            "WHERE tenant_id=%s AND owner_id=%s AND stage NOT IN ('completed','failed')", params,
        )
        conn.execute(
            'INSERT INTO memory_runtime_turn_transitions(tenant_id,owner_id,brain_id,turn_id,revision,stage,operation,fence,failure_code) '
            "SELECT t.tenant_id,t.owner_id,t.brain_id,t.turn_id,t.revision,t.stage,'fail',t.fence,t.failure_code "
            'FROM memory_runtime_turns t JOIN memory_recovery_quarantine q ON '
            'q.tenant_id=t.tenant_id AND q.owner_id=t.owner_id AND q.brain_id=t.brain_id AND q.object_id=t.turn_id '
            "WHERE t.tenant_id=%s AND t.owner_id=%s AND q.recovery_epoch=%s AND q.object_kind='turn'",
            (*params, recovery_epoch),
        )
        conn.execute(
            "UPDATE memory_jobs SET state='cancelled',last_error='cancelled',fence=fence+1,worker_id=NULL,"
            'lease_token_digest=NULL,lease_expires_at=NULL,updated_at=clock_timestamp() '
            "WHERE tenant_id=%s AND owner_id=%s AND state<>'completed'", params,
        )
        conn.execute(
            "UPDATE memory_cognitive_operations SET outcome='uncertain',completed_at=clock_timestamp() "
            "WHERE tenant_id=%s AND owner_id=%s AND outcome='reserved'", params,
        )
        conn.execute('DELETE FROM memory_working_states WHERE tenant_id=%s AND owner_id=%s', params)
        conn.execute('DELETE FROM memory_traces WHERE tenant_id=%s AND owner_id=%s', params)
        conn.execute('UPDATE memory_requests SET response=NULL WHERE tenant_id=%s AND owner_id=%s', params)
        conn.execute('UPDATE memory_brains SET dependency_generation=dependency_generation+1,scope_generation=scope_generation+1 WHERE tenant_id=%s AND owner_id=%s', params)
        saved = {**checkpoint, 'authority_initialized': True, 'manifest_hash': manifest_hash}
        conn.execute(
            "UPDATE memory_recovery_installations SET installation_id=%s,recovery_epoch=%s,state='quarantined',checkpoint=%s "
            'WHERE tenant_id=%s AND owner_id=%s', (installation_id, recovery_epoch, Jsonb(saved), *params),
        )


def activate_database(database, principal, authority, *, recovery_epoch, manifest_hash):
    with database.transaction(principal) as conn:
        state = authority.check(conn, principal)
        if (state['recovery_epoch'] != recovery_epoch or state['checkpoint'].get('manifest_hash') != manifest_hash
                or authority.registry.head()['installation_id'] != authority.installation_id):
            raise RegistryError('idempotency_conflict')
        authority.assert_reconciled(conn, principal)
        conn.execute("UPDATE memory_recovery_installations SET state='active' WHERE tenant_id=%s AND owner_id=%s",
                     (principal.tenant_id, principal.principal_id))


def metadata(database, principal):
    authority = getattr(database, 'recovery_authority', None)
    if authority is None:
        return {'recovery_epoch': None, 'state': 'active', 'checkpoint': None}
    with database.transaction(principal) as conn:
        row = authority.check(conn, principal)
        checkpoint = row['checkpoint']
        return {'recovery_epoch': row['recovery_epoch'], 'state': row['state'],
                'checkpoint': {key: checkpoint[key] for key in ('checkpoint_at', 'backup_id') if key in checkpoint}}
