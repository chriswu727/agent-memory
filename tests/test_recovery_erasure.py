import copy

import pytest
from test_deletion_registry import acknowledgement
from test_learning import learning as learning

from membrain.client import MemoryActionError
from membrain.deletion_registry import DeletionRegistry, RegistryError
from membrain.helpers import digest, uid
from membrain.jobs import JobStore
from membrain.recovery_authority import RecoveryAuthority
from membrain.turns import TurnStore

pytestmark = pytest.mark.integration


@pytest.fixture
def recovery(learning, tmp_path):
    installation = uid()
    registry = DeletionRegistry.create(tmp_path / 'authority', installation)
    authority = RecoveryAuthority(registry, installation)
    authority.initialize(learning.database, learning.principal)
    learning.database.recovery_authority = authority
    return learning, authority


def raw_event(harness, identifier):
    with harness.database.transaction(harness.principal) as conn:
        return conn.execute('SELECT document,deleted FROM memory_events WHERE tenant_id=%s AND brain_id=%s AND id=%s',
                            (harness.principal.tenant_id, harness.brain, identifier)).fetchone()


def pending(harness, authority, identifier):
    return authority.registry.append(
        key_digest=digest(uid()), fingerprint=digest('erasure'), principal=harness.principal,
        brain_id=harness.brain, mode='erase_content', cutoff=1, event_ids=[identifier], memory_ids=[],
    )


def test_forget_is_authoritative_before_database_commit_and_replays_once(recovery, monkeypatch):
    harness, authority = recovery
    identifier = harness.observe('Erase this source.')['accepted_event_ids'][0]
    original = authority.apply

    def interrupt(*args, **kwargs):
        raise RegistryError()

    with monkeypatch.context() as patch:
        patch.setattr(authority, 'apply', interrupt)
        with pytest.raises(MemoryActionError) as rejected:
            harness.memory.forget([identifier], kind='events', mode='erase_content', expected_scope_generation=1)
    assert rejected.value.response['outcome'] == 'failed'
    assert rejected.value.code == 'deletion_in_progress'
    assert authority.registry.head()['sequence'] == 1
    assert raw_event(harness, identifier)['document'] is not None
    with harness.database.transaction(harness.principal) as conn:
        brain = harness.service._brain(conn, harness.principal, harness.brain)
        assert brain['scope_generation'] == 2
        receipt = original(harness.service, conn, harness.principal, brain, authority.registry.intent(1))
        assert receipt['scope_generation'] == 2
    assert raw_event(harness, identifier) == {'document': None, 'deleted': True}
    authority.reconcile(harness.database, harness.principal)
    with harness.database.transaction(harness.principal) as conn:
        assert harness.service._brain(conn, harness.principal, harness.brain)['scope_generation'] == 2


@pytest.mark.parametrize('surface', ['service', 'turn', 'job'])
def test_pending_erasure_is_applied_before_every_operational_brain_access(recovery, surface):
    harness, authority = recovery
    identifier = harness.observe('A pending deletion.')['accepted_event_ids'][0]
    pending(harness, authority, identifier)
    if surface == 'service':
        with harness.database.transaction(harness.principal) as conn:
            harness.service._brain(conn, harness.principal, harness.brain)
    elif surface == 'turn':
        TurnStore(harness.database).list(harness.principal, harness.brain)
    else:
        JobStore(harness.database).list(harness.principal, harness.brain)
    assert raw_event(harness, identifier) == {'document': None, 'deleted': True}


def test_conversation_erasure_replays_resolved_ids_without_erasing_later_input(recovery):
    harness, authority = recovery
    old = harness.observe('Old conversation input.')['accepted_event_ids'][0]
    harness.memory.forget(['chat'], kind='conversation', mode='erase_content', expected_scope_generation=1)
    intent = authority.registry.intent(1)
    later = harness.observe('New conversation input.')['accepted_event_ids'][0]
    with harness.database.transaction(harness.principal) as conn:
        brain = harness.service._brain(conn, harness.principal, harness.brain)
        before = copy.deepcopy(brain)
        authority.apply(harness.service, conn, harness.principal, brain, intent)
        assert brain == before
    assert raw_event(harness, old)['document'] is None
    assert raw_event(harness, later)['document'] is not None


def test_missing_or_retired_authority_blocks_memory_access(recovery):
    harness, authority = recovery
    harness.observe('An existing source.')
    authority.registry.activate(uid(), uid(), digest('manifest'), **acknowledgement(authority.registry.head()))
    with pytest.raises(RegistryError):
        JobStore(harness.database).list(harness.principal, harness.brain)


def test_owner_archive_access_fails_closed_until_another_brains_erasure_is_applied(recovery):
    harness, authority = recovery
    identifier = harness.observe('A source held by a server archive.')['accepted_event_ids'][0]
    with harness.database.transaction(harness.principal) as conn:
        brain = harness.service._brain(conn, harness.principal, harness.brain)
        archive = harness.service._export(conn, harness.principal, brain, {'payload': {
            'include_assertions': True, 'include_events': True, 'include_working_states': True,
        }})
    pending(harness, authority, identifier)
    with pytest.raises(RegistryError) as blocked:
        harness.service.download_archive(harness.principal, archive['archive_id'])
    assert blocked.value.code == 'deletion_in_progress'
    authority.reconcile(harness.database, harness.principal)
    result = harness.service.download_archive(harness.principal, archive['archive_id'])
    assert identifier not in [event['event_id'] for event in result['document']['events']]


def test_restore_records_absent_post_checkpoint_brain_without_recreating_it(recovery):
    from membrain.helpers import Principal

    harness, authority = recovery
    absent = uid()
    authority.registry.append(key_digest=digest('absent'), fingerprint=digest('absent'),
                              principal=harness.principal, brain_id=absent, mode='erase_content',
                              cutoff=8, event_ids=[uid()], memory_ids=[])
    with pytest.raises(RegistryError):
        authority.reconcile(harness.database, harness.principal)
    restoring = RecoveryAuthority(authority.registry, authority.installation_id, restoring=True)
    restoring.reconcile(harness.database, harness.principal)
    authority.reconcile(harness.database, harness.principal)
    with harness.database.transaction(Principal(harness.principal.tenant_id, harness.principal.principal_id)) as conn:
        assert conn.execute('SELECT id FROM memory_brains WHERE id=%s', (absent,)).fetchone() is None
        receipt = conn.execute('SELECT result FROM memory_erasure_receipts WHERE brain_id=%s', (absent,)).fetchone()
        assert receipt['result'] == {'absent_at_checkpoint': True}
