import copy
import os
from contextlib import contextmanager
from dataclasses import replace

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agent_memory import desktop, desktop_restore
from agent_memory.api import create_app
from agent_memory.client import MemoryClient
from agent_memory.deletion_registry import RegistryError
from agent_memory.desktop_backup import backup, verify_bundle
from agent_memory.desktop_connection import open_client
from agent_memory.desktop_restore import activate, restore
from agent_memory.helpers import Principal, uid
from agent_memory.local_profile import ensure_profile, load_profile
from agent_memory.recovery_profile import authority_for, check_start_allowed, load_binding
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get('MEMORY_DESKTOP_E2E') != '1',
    reason='Set MEMORY_DESKTOP_E2E=1 to verify private streamed backup and fresh Docker recovery',
)]


@contextmanager
def connection(profile, *, recovery_epoch=None):
    database = Database(profile.database_url, profile.migration_database_url)
    authority = authority_for(profile)
    database.recovery_authority = authority
    service = MemoryService(database)
    with TestClient(create_app(replace(profile.settings(), allowed_hosts=('testserver',)), service=service)) as server:
        def forward(request):
            response = server.request(request.method, request.url.path, headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, content=response.content)
        with MemoryClient('http://testserver', profile.api_key, profile.brain_id, recovery_epoch=recovery_epoch,
                          transport=httpx.MockTransport(forward)) as client:
            yield client, database, server


def test_full_streamed_backup_restores_current_deletions_and_requires_explicit_epoch(tmp_path, monkeypatch):
    source = ensure_profile(tmp_path / 'source profile')
    restored = None
    try:
        desktop._prepare_database(source)
        with connection(source) as (client, database, _):
            principal = Principal(source.tenant_id, source.principal_id)
            erased = client.observe('This source must be deleted after the checkpoint.',
                                    source_event_key='erased', conversation_id='remember', branch_id='main')['result']['accepted_event_ids'][0]
            retained = client.observe('This confirmed source survives restoration.',
                                      source_event_key='retained', conversation_id='remember', branch_id='main')['result']['accepted_event_ids'][0]
            with psycopg.connect(source.migration_database_url, row_factory=dict_row) as conn:
                original = conn.execute('SELECT * FROM memory_events WHERE id=%s', (retained,)).fetchone()
                with conn.cursor().copy(
                    'COPY memory_events(tenant_id,owner_id,brain_id,id,revision,sequence,producer_ref,source_key,'
                    'source_revision,conversation_id,branch_id,content_hash,input_hash,document) FROM STDIN'
                ) as writer:
                    for index in range(5005):
                        identifier = uid()
                        document = copy.deepcopy(original['document'])
                        document['event_id'] = identifier
                        document['observed']['content']['text'] = ('Private backup coverage ' + str(index) + ' ') * 220
                        writer.write_row((source.tenant_id, source.principal_id, source.brain_id, identifier, 1, index + 3,
                                          'backup-test', identifier, 1, 'coverage', 'main', original['content_hash'],
                                          original['input_hash'], Jsonb(document)))
                conn.execute('UPDATE memory_brains SET durable_sequence=5007 WHERE id=%s', (source.brain_id,))
        output = tmp_path / 'private backup'
        completed = backup(source.directory, output=output, request_id='full-backup')
        authority = authority_for(source)
        manifest = verify_bundle(output, authority.registry)
        assert manifest['members']['database.dump']['size'] > 16 * 1024**2
        assert manifest['checkpoint']['counts']['events'] == 5007
        assert backup(source.directory, output=output, request_id='full-backup')['replayed']
        with connection(source) as (client, _, _):
            client.forget([erased], kind='events', mode='erase_content', expected_scope_generation=1)
        target = tmp_path / 'restored profile'
        original_stream = desktop_restore.database_stream

        def interrupted_import(*args, **kwargs):
            original_stream(*args, **kwargs)
            raise OSError('simulated interruption after import commit')

        with monkeypatch.context() as patch:
            patch.setattr(desktop_restore, 'database_stream', interrupted_import)
            with pytest.raises(OSError):
                restore(backup_path=output, directory=target, registry_path=authority.registry.directory,
                        request_id='fresh-restore')
        restored = load_profile(target)
        original_quarantine = desktop_restore.quarantine

        def interrupted_quarantine(*args, **kwargs):
            original_quarantine(*args, **kwargs)
            raise OSError('simulated interruption after quarantine commit')

        with monkeypatch.context() as patch:
            patch.setattr(desktop_restore, 'quarantine', interrupted_quarantine)
            patch.setattr(desktop_restore, 'database_stream', lambda *args, **kwargs: pytest.fail('import repeated'))
            with pytest.raises(OSError):
                restore(backup_path=output, directory=target, registry_path=authority.registry.directory,
                        request_id='fresh-restore')
        result = restore(backup_path=output, directory=target, registry_path=authority.registry.directory,
                         request_id='fresh-restore')
        restored = load_profile(target)
        assert result['state'] == 'quarantined'
        assert restored.api_key != source.api_key and restored.app_password != source.app_password
        assert restored.installation_id != source.installation_id
        assert restored.brain_id == source.brain_id
        with pytest.raises(RegistryError):
            check_start_allowed(restored)
        with pytest.raises(RegistryError):
            open_client(target, start_service=False)
        assert restore(backup_path=output, directory=target, registry_path=authority.registry.directory,
                       request_id='fresh-restore')['state'] == 'quarantined'
        restored_db = Database(restored.database_url)
        with restored_db.transaction(principal) as conn:
            assert conn.execute('SELECT document,deleted FROM memory_events WHERE id=%s', (erased,)).fetchone() == {
                'document': None, 'deleted': True,
            }
            assert conn.execute('SELECT document FROM memory_events WHERE id=%s', (retained,)).fetchone()['document']
            assert conn.execute('SELECT count(*) AS count FROM memory_events').fetchone()['count'] == 5007
        head = authority.registry.head()
        acknowledgement = dict(recovery_epoch=result['recovery_epoch'], backup_id=completed['backup_id'],
                               checkpoint_at=result['checkpoint']['checkpoint_at'], retire_installation_id=source.installation_id,
                               expected_sequence=head['sequence'], expected_head_hash=head['head_hash'],
                               acknowledge_host_reconciliation=True)
        with monkeypatch.context() as patch:
            patch.setattr(desktop_restore, 'activate_database', lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError('simulated interruption after authority activation')))
            with pytest.raises(OSError):
                activate(target, **acknowledgement)
        assert authority.registry.head()['installation_id'] == restored.installation_id
        with pytest.raises(RegistryError):
            check_start_allowed(restored)
        assert activate(target, **acknowledgement)['state'] == 'active'
        check_start_allowed(restored)
        with connection(source) as (client, _, _):
            with pytest.raises(httpx.HTTPStatusError) as rejected:
                client.brains()
            assert rejected.value.response.status_code == 503
        with connection(restored) as (client, _, server):
            with pytest.raises(httpx.HTTPStatusError) as rejected:
                client.brains()
            assert rejected.value.response.status_code == 409
            state = server.get('/v1/recovery', headers={'Authorization': 'Bearer ' + restored.api_key})
            assert state.status_code == 200 and state.json()['recovery_epoch'] == result['recovery_epoch']
            assert server.get('/v1/brains', headers={'Authorization': 'Bearer ' + source.api_key}).status_code == 401
        with connection(restored, recovery_epoch=load_binding(restored)['recovery_epoch']) as (client, _, _):
            assert client.brains()['brains'][0]['id'] == source.brain_id
            client.observe('Fresh input after explicit recovery.', source_event_key='after-restore', conversation_id='fresh', branch_id='main')
    finally:
        for profile in (restored, source):
            if profile is not None:
                desktop._compose(profile, 'down', '--volumes')
