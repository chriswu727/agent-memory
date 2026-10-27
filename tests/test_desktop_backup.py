import os

import pytest

from membrain.backup_transport import BackupError
from membrain.build_identity import BUILD_ID, MIGRATIONS, SCHEMA_ID
from membrain.deletion_registry import DeletionRegistry, RegistryError
from membrain.desktop_backup import backup, file_digest, verify_bundle
from membrain.desktop_restore import restore
from membrain.helpers import digest, uid
from membrain.local_profile import LocalProfileError, _open_private, ensure_profile
from membrain.recovery_profile import (
    BINDING_FILE,
    MAINTENANCE_FILE,
    read_document,
    write_document,
)


@pytest.fixture
def bundle(tmp_path):
    profile = ensure_profile(tmp_path / 'source')
    registry = DeletionRegistry.create(tmp_path / 'authority', profile.installation_id)
    write_document(profile.directory / BINDING_FILE, {
        'version': 1, 'registry_path': str(registry.directory), 'registry_id': registry.registry_id,
        'installation_id': profile.installation_id, 'state': 'active', 'recovery_epoch': None,
    })
    output = tmp_path / 'backup'
    output.mkdir(mode=0o700)
    descriptor = _open_private(output / 'database.dump', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(b'bounded dump fixture')
    descriptor = _open_private(output / 'sessions.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    os.close(descriptor)
    configuration = {key: getattr(profile, key) for key in (
        'installation_id', 'brain_id', 'tenant_id', 'principal_id', 'name', 'cognition',
    )}
    write_document(output / 'configuration.json', {**configuration, 'chat': None, 'cognition_policy': None})
    manifest = {
        'version': 1, 'backup_id': uid(),
        'request_hash': digest(['backup-request', str(output), profile.installation_id, 64 * 1024**3, 3600]),
        'source_installation_id': profile.installation_id, 'registry': registry.head(),
        'checkpoint': {'migration_names': [name for name, _ in MIGRATIONS], 'schema_id': SCHEMA_ID,
                       'build_id': BUILD_ID, 'checkpoint_at': '2026-10-25T00:00:00+00:00'},
        'members': {name: file_digest(output / name, max_bytes=100_000)
                    for name in ('database.dump', 'configuration.json', 'sessions.jsonl')},
    }
    write_document(output / 'manifest.json', manifest)
    return profile, registry, output, manifest


def test_registered_private_bundle_survives_later_registry_progress(bundle):
    profile, registry, output, manifest = bundle
    registry.register_backup(manifest['backup_id'], digest(manifest), manifest['registry'])
    assert verify_bundle(output, registry) == manifest
    assert backup(profile.directory, output=output, request_id='backup-request')['replayed']
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(item.stat().st_mode & 0o777 == 0o600 for item in output.iterdir())


@pytest.mark.parametrize('field,value', [('registry', None), ('checkpoint', []), ('members', None), ('version', True)])
def test_malformed_bundle_is_rejected_before_registry_or_database_execution(bundle, field, value):
    _, registry, output, manifest = bundle
    manifest[field] = value
    write_document(output / 'manifest.json', manifest)
    with pytest.raises(BackupError) as rejected:
        verify_bundle(output, registry)
    assert rejected.value.code == 'backup_invalid'


def test_unregistered_or_modified_database_bundle_is_never_trusted(bundle):
    _, registry, output, manifest = bundle
    with pytest.raises(RegistryError):
        verify_bundle(output, registry)
    registry.register_backup(manifest['backup_id'], digest(manifest), manifest['registry'])
    (output / 'database.dump').write_bytes(b'altered')
    with pytest.raises(BackupError):
        verify_bundle(output, registry)


def test_staged_manifest_recovers_crash_before_or_after_registration_without_database_calls(bundle, monkeypatch):
    profile, registry, output, manifest = bundle
    stage = output.parent / ('.agent-memory-backup-' + manifest['backup_id'])
    output.rename(stage)
    marker = {'operation': 'backup', 'request_hash': manifest['request_hash'], 'backup_id': manifest['backup_id'],
              'output': str(output)}
    write_document(profile.directory / MAINTENANCE_FILE, marker)
    original = DeletionRegistry.register_backup

    def interruption(self, *args):
        original(self, *args)
        raise OSError('simulated publication interruption')

    with monkeypatch.context() as patch:
        patch.setattr(DeletionRegistry, 'register_backup', interruption)
        with pytest.raises(OSError):
            backup(profile.directory, output=output, request_id='backup-request')
    assert read_document(stage / 'manifest.json') == manifest
    result = backup(profile.directory, output=output, request_id='backup-request')
    assert result['replayed'] and result['backup_id'] == manifest['backup_id']
    assert not (profile.directory / MAINTENANCE_FILE).exists()
    assert verify_bundle(output, registry) == manifest


def test_completed_publication_replay_clears_matching_maintenance_only(bundle):
    profile, registry, output, manifest = bundle
    registry.register_backup(manifest['backup_id'], digest(manifest), manifest['registry'])
    marker = {'operation': 'backup', 'request_hash': manifest['request_hash'], 'backup_id': manifest['backup_id']}
    write_document(profile.directory / MAINTENANCE_FILE, marker)
    assert backup(profile.directory, output=output, request_id='backup-request')['replayed']
    assert not (profile.directory / MAINTENANCE_FILE).exists()
    write_document(profile.directory / MAINTENANCE_FILE, {**marker, 'backup_id': uid()})
    with pytest.raises(BackupError):
        backup(profile.directory, output=output, request_id='backup-request')
    assert (profile.directory / MAINTENANCE_FILE).exists()


def test_restore_refuses_existing_target_before_starting_database(bundle, monkeypatch, tmp_path):
    _, registry, output, manifest = bundle
    registry.register_backup(manifest['backup_id'], digest(manifest), manifest['registry'])
    target = tmp_path / 'user-data'
    target.mkdir()
    (target / 'precious.txt').write_text('preserved')
    with pytest.raises(BackupError) as rejected:
        restore(backup_path=output, directory=target, registry_path=registry.directory, request_id='restore-request')
    assert rejected.value.code == 'backup_conflict'
    assert (target / 'precious.txt').read_text() == 'preserved'


def test_backup_rejects_hardlinks_symlinks_and_fifo_without_blocking(bundle, tmp_path):
    _, registry, output, manifest = bundle
    registry.register_backup(manifest['backup_id'], digest(manifest), manifest['registry'])
    original = output / 'database.dump'
    target = tmp_path / 'original'
    original.rename(target)
    for kind in ('symlink', 'hardlink', 'fifo'):
        if kind == 'symlink':
            original.symlink_to(target)
        elif kind == 'hardlink':
            os.link(target, original)
        else:
            os.mkfifo(original)
        try:
            with pytest.raises((LocalProfileError, RegistryError)):
                verify_bundle(output, registry)
        finally:
            original.unlink()
