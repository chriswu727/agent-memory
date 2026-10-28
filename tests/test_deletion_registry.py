import json
import sqlite3

import pytest

from membrain.deletion_registry import DeletionRegistry, RegistryError
from membrain.helpers import Principal, digest, uid


def append(registry, principal, **changes):
    values = dict(key_digest=digest('request'), fingerprint=digest('body'), principal=principal,
                  brain_id='brain', mode='erase_content', cutoff=10, event_ids=['event'], memory_ids=['memory'])
    values.update(changes)
    return registry.append(**values)


def acknowledgement(head):
    return {'expected_installation_id': head['installation_id'], 'expected_sequence': head['sequence'],
            'expected_head_hash': head['head_hash']}


def test_registry_reopens_with_exact_durable_intents_and_stable_replay(tmp_path):
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    principal = Principal(uid(), uid())
    first = append(registry, principal)
    fresh = DeletionRegistry(registry.directory, registry.registry_id)
    assert fresh.find(digest('request'), digest('body')) == first
    assert append(fresh, principal, event_ids=['later']) == first
    second = append(fresh, principal, key_digest=digest('second'), event_ids=['later'])
    assert second['sequence'] == 2
    assert fresh.verify()['head_hash'] == second['intent_hash']
    assert fresh.intents(principal, after=1)[0] == [second]
    assert fresh.intents(principal, brain_id='other')[0] == []
    assert 'body' not in json.dumps(first)
    with pytest.raises(RegistryError) as conflict:
        append(fresh, principal, fingerprint=digest('different'))
    assert conflict.value.code == 'idempotency_conflict'


def test_registry_flushes_only_writable_descriptors(tmp_path, monkeypatch):
    # Windows rejects fsync on a read-only descriptor (EBADF), which blocked every new installation there.
    import os

    from membrain import deletion_registry

    modes, opened, flushed = {}, deletion_registry._open_private, []

    def open_private(path, flags):
        descriptor = opened(path, flags)
        modes[descriptor] = flags
        return descriptor

    def fsync(descriptor):
        if descriptor in modes:
            flushed.append(modes[descriptor] & (os.O_WRONLY | os.O_RDWR))

    monkeypatch.setattr(deletion_registry, '_open_private', open_private)
    monkeypatch.setattr(deletion_registry.os, 'fsync', fsync)
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    append(registry, Principal(uid(), uid()))
    assert flushed and all(flushed)


@pytest.mark.parametrize('tamper', ['target', 'chain', 'document', 'identity'])
def test_registry_rejects_modified_authority(tmp_path, tamper):
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    append(registry, Principal(uid(), uid()))
    with sqlite3.connect(registry.path) as connection:
        statement = {
            'target': "UPDATE targets SET id='changed'",
            'chain': "UPDATE intents SET previous_hash='changed'",
            'document': "UPDATE intents SET document='[]'",
            'identity': "UPDATE metadata SET value='changed' WHERE key='registry_id'",
        }[tamper]
        connection.execute(statement)
    with pytest.raises(RegistryError):
        registry.verify()


def test_missing_authority_is_not_recreated_from_backup_metadata(tmp_path):
    registry = DeletionRegistry(tmp_path / 'missing', uid())
    with pytest.raises(RegistryError):
        registry.head()
    assert not registry.directory.exists()


def test_backup_provenance_and_activation_are_bound_to_exact_content(tmp_path):
    old_installation, new_installation = uid(), uid()
    registry = DeletionRegistry.create(tmp_path / 'authority', old_installation)
    backup_id, manifest_hash = uid(), digest('manifest')
    registry.register_backup(backup_id, manifest_hash, registry.head())
    registry.verify_backup(backup_id, manifest_hash)
    with pytest.raises(RegistryError):
        registry.verify_backup(backup_id, digest('different'))
    with pytest.raises(RegistryError):
        registry.verify_backup(uid(), manifest_hash)
    epoch = uid()
    accepted = acknowledgement(registry.head())
    registry.activate(epoch, new_installation, manifest_hash, **accepted)
    registry.activate(epoch, new_installation, manifest_hash, **accepted)
    assert registry.head()['installation_id'] == new_installation
    with pytest.raises(RegistryError):
        registry.activate(epoch, uid(), manifest_hash, **accepted)


def test_activation_replay_cannot_reactivate_a_retired_installation(tmp_path):
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    epoch_a, installation_a = uid(), uid()
    first = acknowledgement(registry.head())
    registry.activate(epoch_a, installation_a, digest('first'), **first)
    epoch_b, installation_b = uid(), uid()
    second = acknowledgement(registry.head())
    registry.activate(epoch_b, installation_b, digest('second'), **second)
    with pytest.raises(RegistryError):
        registry.activate(epoch_a, installation_a, digest('first'), **first)
    assert registry.head()['installation_id'] == installation_b


def test_stale_activation_cannot_replace_a_newer_authority_head(tmp_path):
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    accepted = acknowledgement(registry.head())
    append(registry, Principal(uid(), uid()))
    with pytest.raises(RegistryError):
        registry.activate(uid(), uid(), digest('manifest'), **accepted)


def test_backup_registration_cannot_use_an_old_deletion_head(tmp_path):
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    before = registry.head()
    append(registry, Principal(uid(), uid()))
    with pytest.raises(RegistryError):
        registry.register_backup(uid(), digest('manifest'), before)


def test_registry_rejects_linked_files(tmp_path):
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    elsewhere = tmp_path / 'elsewhere'
    registry.path.rename(elsewhere)
    registry.path.symlink_to(elsewhere)
    with pytest.raises(RegistryError):
        registry.head()


def test_registered_backup_publication_replay_survives_a_later_deletion_head(tmp_path):
    registry = DeletionRegistry.create(tmp_path / 'authority', uid())
    original = registry.head()
    identifier, manifest = uid(), digest('registered')
    registry.register_backup(identifier, manifest, original)
    append(registry, Principal(uid(), uid()))
    registry.register_backup(identifier, manifest, original)
    registry.verify_backup(identifier, manifest)
    assert registry.head()['sequence'] == 1


def test_retired_installation_cannot_append_after_its_earlier_authority_check(tmp_path):
    old = uid()
    registry = DeletionRegistry.create(tmp_path / 'authority', old)
    registry.activate(uid(), uid(), digest('manifest'), **acknowledgement(registry.head()))
    with pytest.raises(RegistryError):
        append(registry, Principal(uid(), uid()), expected_installation_id=old)
    assert registry.head()['sequence'] == 0
