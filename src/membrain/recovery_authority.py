from __future__ import annotations

from psycopg.types.json import Jsonb

from .deletion_registry import DeletionRegistry, RegistryError
from .erasure import apply_erasure, resolve_targets
from .helpers import DomainError, digest


class RecoveryAuthority:
    def __init__(self, registry: DeletionRegistry, installation_id: str, *, restoring=False):
        self.registry = registry
        self.installation_id = installation_id
        self.restoring = restoring

    def state(self, conn, principal):
        return conn.execute(
            'SELECT * FROM memory_recovery_installations WHERE tenant_id=%s AND owner_id=%s',
            (principal.tenant_id, principal.principal_id),
        ).fetchone()

    def check(self, conn, principal, *, initializing=False):
        row = self.state(conn, principal)
        head = self.registry.head()
        if (not row or row['registry_id'] != self.registry.registry_id
                or row['installation_id'] != self.installation_id
                or not initializing and row['checkpoint'].get('authority_initialized') is not True
                or not self.restoring and (row['state'] != 'active'
                                           or head['installation_id'] != self.installation_id)):
            raise RegistryError()
        return row

    def initialize(self, database, principal):
        self.registry.verify()
        with database.transaction(principal) as conn:
            row = self.state(conn, principal)
            if row:
                self.check(conn, principal, initializing=True)
                if row['checkpoint'].get('authority_initialized') is True:
                    return
            else:
                conn.execute(
                    "INSERT INTO memory_recovery_installations(tenant_id,owner_id,registry_id,installation_id,state) "
                    "VALUES(%s,%s,%s,%s,'active')",
                    (principal.tenant_id, principal.principal_id, self.registry.registry_id, self.installation_id),
                )
        from .service import MemoryService

        service = MemoryService(database)
        for public in service.list_brains(principal):
            with database.transaction(principal) as conn:
                brain = service._brain(conn, principal, public['id'])
                service._archive_guard(conn, principal)
                for mode in ('suppress_recall', 'erase_content'):
                    rows = conn.execute(
                        'SELECT object_kind,object_id FROM memory_tombstones WHERE tenant_id=%s AND owner_id=%s '
                        'AND brain_id=%s AND mode=%s ORDER BY object_kind,object_id',
                        (*service._params(principal, brain['id']), mode),
                    ).fetchall()
                    if not rows:
                        continue
                    key = digest(['baseline', principal.tenant_id, principal.principal_id, brain['id'], mode])
                    intent = self.registry.append(
                        key_digest=key, fingerprint=key, principal=principal, brain_id=brain['id'], mode=mode,
                        cutoff=brain['durable_sequence'],
                        event_ids=[row['object_id'] for row in rows if row['object_kind'] == 'event'],
                        memory_ids=[row['object_id'] for row in rows if row['object_kind'] == 'memory'],
                        expected_installation_id=self.installation_id,
                    )
                    self.apply(service, conn, principal, brain, intent)
        with database.transaction(principal) as conn:
            conn.execute(
                "UPDATE memory_recovery_installations SET checkpoint=checkpoint || '{\"authority_initialized\":true}'::jsonb "
                'WHERE tenant_id=%s AND owner_id=%s', (principal.tenant_id, principal.principal_id),
            )

    def apply(self, service, conn, principal, brain, intent):
        if (intent['tenant_id'], intent['owner_id'], intent['brain_id']) != (
                principal.tenant_id, principal.principal_id, brain['id']):
            raise RegistryError()
        existing = conn.execute(
            'SELECT intent_hash,result FROM memory_erasure_receipts '
            'WHERE tenant_id=%s AND owner_id=%s AND registry_id=%s AND sequence=%s',
            (principal.tenant_id, principal.principal_id, self.registry.registry_id, intent['sequence']),
        ).fetchone()
        if existing:
            if existing['intent_hash'] != intent['intent_hash']:
                raise RegistryError()
            return existing['result']
        service._archive_guard(conn, principal)
        result = apply_erasure(
            service, conn, principal, brain, event_ids=intent['event_ids'], memory_ids=intent['memory_ids'],
            mode=intent['mode'], deletion_id=intent['deletion_id'],
        )
        conn.execute(
            'INSERT INTO memory_erasure_receipts(tenant_id,owner_id,brain_id,registry_id,sequence,intent_hash,result) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s)',
            (*service._params(principal, brain['id']), self.registry.registry_id,
             intent['sequence'], intent['intent_hash'], Jsonb(result)),
        )
        return result

    def reconcile_brain(self, database, conn, principal, brain):
        self.check(conn, principal)
        last = conn.execute(
            'SELECT sequence,intent_hash FROM memory_erasure_receipts WHERE tenant_id=%s AND owner_id=%s '
            'AND brain_id=%s AND registry_id=%s ORDER BY sequence DESC LIMIT 1',
            (principal.tenant_id, principal.principal_id, brain['id'], self.registry.registry_id),
        ).fetchone()
        after = last['sequence'] if last else 0
        if last and self.registry.intent(after)['intent_hash'] != last['intent_hash']:
            raise RegistryError()
        from .service import MemoryService

        service = MemoryService(database)
        while True:
            intents, through = self.registry.intents(principal, brain_id=brain['id'], after=after)
            for intent in intents:
                self.apply(service, conn, principal, brain, intent)
            if through == after:
                return
            after = through

    def reconcile(self, database, principal):
        self.registry.verify()
        with database.transaction(principal) as conn:
            self.check(conn, principal)
            brains = conn.execute(
                'SELECT id FROM memory_brains WHERE tenant_id=%s AND owner_id=%s ORDER BY id',
                (principal.tenant_id, principal.principal_id),
            ).fetchall()
        for row in brains:
            with database.transaction(principal) as conn:
                brain = conn.execute(
                    'SELECT * FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s FOR UPDATE',
                    (principal.tenant_id, principal.principal_id, row['id']),
                ).fetchone()
                self.reconcile_brain(database, conn, principal, brain)
        after = 0
        while True:
            intents, through = self.registry.intents(principal, after=after)
            with database.transaction(principal) as conn:
                for intent in intents:
                    if conn.execute('SELECT 1 FROM memory_brains WHERE tenant_id=%s AND owner_id=%s AND id=%s',
                                    (principal.tenant_id, principal.principal_id, intent['brain_id'])).fetchone():
                        continue
                    existing = conn.execute(
                        'SELECT intent_hash,result FROM memory_erasure_receipts WHERE tenant_id=%s AND owner_id=%s '
                        'AND registry_id=%s AND sequence=%s',
                        (principal.tenant_id, principal.principal_id, self.registry.registry_id, intent['sequence']),
                    ).fetchone()
                    if existing:
                        if existing['intent_hash'] != intent['intent_hash'] or existing['result'] != {'absent_at_checkpoint': True}:
                            raise RegistryError()
                        continue
                    if not self.restoring:
                        raise RegistryError()
                    conn.execute(
                        'INSERT INTO memory_erasure_receipts(tenant_id,owner_id,brain_id,registry_id,sequence,intent_hash,result) '
                        'VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                        (principal.tenant_id, principal.principal_id, intent['brain_id'], self.registry.registry_id,
                         intent['sequence'], intent['intent_hash'], Jsonb({'absent_at_checkpoint': True})),
                    )
            if through == after:
                break
            after = through

    def assert_reconciled(self, conn, principal):
        self.check(conn, principal)
        after = 0
        while True:
            intents, through = self.registry.intents(principal, after=after)
            for intent in intents:
                receipt = conn.execute(
                    'SELECT intent_hash FROM memory_erasure_receipts WHERE tenant_id=%s AND owner_id=%s '
                    'AND registry_id=%s AND sequence=%s',
                    (principal.tenant_id, principal.principal_id, self.registry.registry_id, intent['sequence']),
                ).fetchone()
                if not receipt or receipt['intent_hash'] != intent['intent_hash']:
                    raise RegistryError('deletion_in_progress')
            if through == after:
                return
            after = through

    def forget(self, service, conn, principal, brain, request):
        self.check(conn, principal)
        payload = request['payload']
        key = digest([principal.tenant_id, principal.principal_id, brain['id'],
                      payload.get('idempotency_key', request['request_id'])])
        fingerprint = digest({'action': 'forget', 'target': request['target'], 'payload': payload})
        intent = self.registry.find(key, fingerprint)
        if intent is None:
            if brain['scope_generation'] != payload['expected_scope_generation']:
                raise DomainError('version_conflict', 'Scope generation changed.',
                                  current_revision=brain['scope_generation'])
            events, memories = resolve_targets(service, conn, principal, brain, payload['selector'])
            intent = self.registry.append(
                key_digest=key, fingerprint=fingerprint, principal=principal, brain_id=brain['id'],
                mode=payload['mode'], cutoff=brain['durable_sequence'], event_ids=events, memory_ids=memories,
                expected_installation_id=self.installation_id,
            )
        return self.apply(service, conn, principal, brain, intent)

    def recorded_intent(self, principal, request):
        try:
            payload = request['payload']
            key = digest([principal.tenant_id, principal.principal_id, request['target']['brain_ref'],
                          payload.get('idempotency_key', request['request_id'])])
            fingerprint = digest({'action': 'forget', 'target': request['target'], 'payload': payload})
            return self.registry.find(key, fingerprint)
        except (KeyError, TypeError, ValueError, DomainError):
            return None


def reconcile_brain(database, conn, principal, brain):
    authority = getattr(database, 'recovery_authority', None)
    if authority is not None:
        authority.reconcile_brain(database, conn, principal, brain)


def archive_barrier(database, conn, principal):
    authority = getattr(database, 'recovery_authority', None)
    if authority is not None:
        authority.assert_reconciled(conn, principal)


def configure_authority(database, settings):
    principals = set(settings.principals().values())
    if settings.recovery_registry_path is not None:
        authority = RecoveryAuthority(
            DeletionRegistry(settings.recovery_registry_path, settings.recovery_registry_id),
            settings.recovery_installation_id,
        )
        if len(principals) != 1:
            raise RegistryError()
        for principal in principals:
            authority.reconcile(database, principal)
        database.recovery_authority = authority
    else:
        for principal in principals:
            with database.transaction(principal) as conn:
                if conn.execute(
                    'SELECT 1 FROM memory_recovery_installations WHERE tenant_id=%s AND owner_id=%s',
                    (principal.tenant_id, principal.principal_id),
                ).fetchone():
                    raise RegistryError()
