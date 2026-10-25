from __future__ import annotations

import os
import secrets
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import desktop
from .backup_transport import BackupError, database_stream, limits
from .cli import bootstrap
from .deletion_registry import DeletionRegistry, _uuid
from .desktop_backup import file_digest, verify_bundle
from .helpers import Principal, digest, uid
from .local_profile import (
    DesktopProfile,
    _available_port,
    _directory,
    _save,
    load_profile,
    profile_lock,
)
from .recovery import activate_database, quarantine
from .recovery_authority import RecoveryAuthority
from .recovery_profile import (
    BINDING_FILE,
    MAINTENANCE_FILE,
    authority_for,
    load_binding,
    read_document,
    sync_directory,
    write_document,
)
from .storage import Database

JOURNAL_FILE = 'restore.json'


def _request_id(value):
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(character) < 32 for character in value):
        raise BackupError('backup_invalid')
    return value


def _new_profile(directory, journal, configuration, *, api_port, database_port):
    api = _available_port(api_port, 8788, set())
    database = _available_port(database_port, 55433, {api})
    profile = DesktopProfile(
        directory=directory, installation_id=journal['installation_id'], brain_id=configuration['brain_id'],
        tenant_id=configuration['tenant_id'], principal_id=configuration['principal_id'], name=configuration['name'],
        cognition=configuration['cognition'], api_port=api, database_port=database,
        api_key='am_' + secrets.token_urlsafe(36), admin_password=secrets.token_hex(24), app_password=secrets.token_hex(24),
    )
    _save(profile)
    return profile


def _restore_configuration(profile, configuration):
    from .chat_config import load_chat_configuration
    from .cognition_config import load_cognition_policy

    for key, name in (('chat', 'chat.json'), ('cognition_policy', 'cognition-policy.json')):
        existing = read_document(profile.directory / name)
        if existing is not None and existing != configuration[key]:
            raise BackupError('backup_conflict')
        if existing is None and configuration[key] is not None:
            write_document(profile.directory / name, configuration[key])
    if configuration['chat'] is not None:
        load_chat_configuration(profile.directory)
    load_cognition_policy(profile.directory)


def _database_source(profile, manifest):
    with psycopg.connect(profile.migration_database_url, row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        exists = conn.execute("SELECT to_regclass('memory_recovery_installations') AS relation").fetchone()['relation']
        if not exists:
            if conn.execute("SELECT 1 FROM pg_class WHERE relnamespace=current_schema()::regnamespace "
                            "AND relname LIKE 'memory\\_%' ESCAPE '\\' AND relkind IN ('r','p') LIMIT 1").fetchone():
                raise BackupError('restore_blocked')
            return False
        rows = conn.execute('SELECT * FROM memory_recovery_installations').fetchall()
        if (len(rows) != 1 or rows[0]['tenant_id'] != profile.tenant_id or rows[0]['owner_id'] != profile.principal_id
                or rows[0]['registry_id'] != manifest['registry']['registry_id']
                or rows[0]['installation_id'] not in (manifest['source_installation_id'], profile.installation_id)):
            raise BackupError('restore_blocked')
        return True


def restore(*, backup_path: Path, directory: Path, registry_path: Path, request_id: str,
            api_port=None, database_port=None, max_bytes=64 * 1024**3, timeout_seconds=3600):
    limits(max_bytes, timeout_seconds)
    _request_id(request_id)
    if any(port is not None and (type(port) is not int or not 1024 <= port <= 65535)
           for port in (api_port, database_port)) or api_port is not None and api_port == database_port:
        raise BackupError('backup_invalid')
    source, target, authority_path = _directory(backup_path), _directory(directory), _directory(registry_path)
    if (target == source or target in source.parents or source in target.parents
            or target == authority_path or target in authority_path.parents or authority_path in target.parents):
        raise BackupError('backup_invalid')
    raw = read_document(source / 'manifest.json')
    if not isinstance(raw, dict) or not isinstance(raw.get('registry'), dict):
        raise BackupError('backup_invalid')
    registry = DeletionRegistry(authority_path, _uuid(raw['registry'].get('registry_id')))
    manifest = verify_bundle(source, registry, max_bytes=max_bytes)
    configuration = read_document(source / 'configuration.json')
    expected = {'installation_id', 'brain_id', 'tenant_id', 'principal_id', 'name', 'cognition', 'chat', 'cognition_policy'}
    if (not isinstance(configuration, dict) or set(configuration) != expected
            or configuration['installation_id'] != manifest['source_installation_id']):
        raise BackupError('backup_invalid')
    request_hash = digest([request_id, str(target), str(source), str(authority_path), digest(manifest),
                           api_port, database_port, max_bytes, timeout_seconds])
    with profile_lock(target, timeout=360):
        journal_path = target / JOURNAL_FILE
        journal = read_document(journal_path)
        if journal is None:
            if any(item.name != '.profile.lock' for item in target.iterdir()):
                raise BackupError('backup_conflict')
            journal = {'version': 1, 'request_hash': request_hash, 'installation_id': uid(), 'recovery_epoch': uid(),
                       'manifest_hash': digest(manifest), 'backup_id': manifest['backup_id'],
                       'source_installation_id': manifest['source_installation_id'], 'checkpoint': manifest['checkpoint'],
                       'stage': 'created', 'activation': None}
            write_document(journal_path, journal)
        if journal.get('request_hash') != request_hash or journal.get('manifest_hash') != digest(manifest):
            raise BackupError('backup_conflict')
        if (target / 'profile.json').exists():
            profile = load_profile(target)
            if (profile.installation_id != journal['installation_id']
                    or any(getattr(profile, key) != configuration[key] for key in (
                        'brain_id', 'tenant_id', 'principal_id', 'name', 'cognition'))):
                raise BackupError('backup_conflict')
        else:
            profile = _new_profile(target, journal, configuration, api_port=api_port, database_port=database_port)
        if journal['stage'] == 'active':
            if load_binding(profile)['state'] != 'active':
                raise BackupError('restore_blocked')
            authority_for(profile).registry.verify()
            return recovery_status(target)
        binding = {'version': 1, 'registry_path': str(authority_path), 'registry_id': registry.registry_id,
                   'installation_id': profile.installation_id, 'state': 'quarantined', 'recovery_epoch': journal['recovery_epoch']}
        write_document(target / BINDING_FILE, binding)
        write_document(target / MAINTENANCE_FILE, {'operation': 'restore', 'request_hash': request_hash})
        _restore_configuration(profile, configuration)
        desktop._compose(profile, 'up', '--detach', '--wait', '--wait-timeout', '90')
        if not _database_source(profile, manifest):
            database_stream(profile, source / 'database.dump', restore=True, max_bytes=max_bytes,
                            timeout_seconds=timeout_seconds)
        database = Database(profile.database_url, profile.migration_database_url)
        database.check_compatibility()
        with redirect_stdout(StringIO()):
            bootstrap(profile.settings(), app_password=profile.app_password)
        principal = Principal(profile.tenant_id, profile.principal_id)
        quarantine(database, principal, source_installation_id=manifest['source_installation_id'],
                   installation_id=profile.installation_id, registry_id=registry.registry_id,
                   recovery_epoch=journal['recovery_epoch'], manifest_hash=digest(manifest),
                   checkpoint={**manifest['checkpoint'], 'backup_id': manifest['backup_id']})
        authority = RecoveryAuthority(registry, profile.installation_id, restoring=True)
        authority.reconcile(database, principal)
        evidence = target / 'recovery-sessions.jsonl'
        if not evidence.exists():
            from .local_profile import _open_private

            temporary = target / '.recovery-sessions.tmp'
            if temporary.exists() or temporary.is_symlink():
                descriptor = _open_private(temporary, os.O_RDONLY)
                os.close(descriptor)
                temporary.unlink()
            incoming = _open_private(source / 'sessions.jsonl', os.O_RDONLY)
            outgoing = _open_private(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(incoming, 'rb') as reader, os.fdopen(outgoing, 'wb') as writer:
                while chunk := reader.read(65536):
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if file_digest(temporary, max_bytes=max_bytes) != manifest['members']['sessions.jsonl']:
                raise BackupError('backup_invalid')
            os.replace(temporary, evidence)
            sync_directory(target)
        elif file_digest(evidence, max_bytes=max_bytes) != manifest['members']['sessions.jsonl']:
            raise BackupError('backup_invalid')
        journal['stage'] = 'quarantined'
        write_document(journal_path, journal)
        return recovery_status(target)


def recovery_status(directory):
    profile = load_profile(directory)
    binding = load_binding(profile)
    journal = read_document(profile.directory / JOURNAL_FILE)
    if journal is None:
        marker = read_document(profile.directory / MAINTENANCE_FILE)
        state = 'maintenance' if marker else binding['state'] if binding else 'not_initialized'
        if binding and state == 'active' and authority_for(profile).registry.head()['installation_id'] != profile.installation_id:
            state = 'retired'
        return {'state': state, 'recovery_epoch': binding['recovery_epoch'] if binding else None,
                'registry_path': binding['registry_path'] if binding else None,
                'operation': marker.get('operation') if marker else None,
                'backup_id': marker.get('backup_id') if marker else None}
    head = authority_for(profile, restoring=True).registry.verify()
    state = 'retired' if journal['stage'] == 'active' and head['installation_id'] != profile.installation_id else journal['stage']
    return {'state': state, 'backup_id': journal['backup_id'], 'recovery_epoch': journal['recovery_epoch'],
            'installation_id': profile.installation_id, 'checkpoint': journal['checkpoint'], 'registry': head,
            'automatic_start': False, 'external_host_epoch_required': True,
            'pending_sessions': 'preserved_as_evidence_only'}


def activate(directory, *, recovery_epoch, backup_id, checkpoint_at, retire_installation_id,
             expected_sequence, expected_head_hash, acknowledge_host_reconciliation=False):
    if acknowledge_host_reconciliation is not True:
        raise BackupError('recovery_acknowledgement_required')
    _uuid(recovery_epoch)
    _uuid(backup_id)
    _uuid(retire_installation_id)
    if type(expected_sequence) is not int or expected_sequence < 0:
        raise BackupError('backup_invalid')
    profile = load_profile(directory)
    with profile_lock(profile.directory, timeout=360):
        journal = read_document(profile.directory / JOURNAL_FILE)
        if (not journal or journal['recovery_epoch'] != recovery_epoch or journal['backup_id'] != backup_id
                or journal['checkpoint'].get('checkpoint_at') != checkpoint_at
                or journal['stage'] not in ('quarantined', 'activating', 'active')):
            raise BackupError('backup_conflict')
        acknowledgement = {'recovery_epoch': recovery_epoch, 'backup_id': backup_id, 'checkpoint_at': checkpoint_at,
                           'retire_installation_id': retire_installation_id, 'expected_sequence': expected_sequence,
                           'expected_head_hash': expected_head_hash}
        authority = authority_for(profile, restoring=True)
        authority.registry.verify_backup(journal['backup_id'], journal['manifest_hash'])
        if (journal['activation'] is not None and journal['activation'] != acknowledgement
                and (journal['stage'] == 'active' or authority.registry.head()['installation_id'] == profile.installation_id)):
            raise BackupError('backup_conflict')
        if journal['stage'] == 'active':
            authority.registry.activate(recovery_epoch, profile.installation_id, journal['manifest_hash'],
                                        expected_installation_id=retire_installation_id,
                                        expected_sequence=expected_sequence, expected_head_hash=expected_head_hash)
            marker = read_document(profile.directory / MAINTENANCE_FILE)
            if marker is not None:
                if marker != {'operation': 'restore', 'request_hash': journal['request_hash']}:
                    raise BackupError('backup_conflict')
                (profile.directory / MAINTENANCE_FILE).unlink()
                sync_directory(profile.directory)
            return recovery_status(profile.directory)
        desktop._compose(profile, 'up', '--detach', '--wait', '--wait-timeout', '90')
        database = Database(profile.database_url, profile.migration_database_url)
        database.check_compatibility(require_current=True)
        principal = Principal(profile.tenant_id, profile.principal_id)
        authority.reconcile(database, principal)
        with database.transaction(principal) as conn:
            state = authority.check(conn, principal)
            if (state['recovery_epoch'] != recovery_epoch
                    or state['checkpoint'].get('checkpoint_at') != checkpoint_at
                    or state['checkpoint'].get('backup_id') != backup_id
                    or state['checkpoint'].get('manifest_hash') != journal['manifest_hash']):
                raise BackupError('backup_conflict')
        journal.update(stage='activating', activation=acknowledgement)
        write_document(profile.directory / JOURNAL_FILE, journal)
        authority.registry.activate(recovery_epoch, profile.installation_id, journal['manifest_hash'],
                                    expected_installation_id=retire_installation_id,
                                    expected_sequence=expected_sequence, expected_head_hash=expected_head_hash)
        activate_database(database, principal, authority, recovery_epoch=recovery_epoch, manifest_hash=journal['manifest_hash'])
        binding = load_binding(profile)
        write_document(profile.directory / BINDING_FILE, {**binding, 'state': 'active'})
        journal['stage'] = 'active'
        write_document(profile.directory / JOURNAL_FILE, journal)
        (profile.directory / MAINTENANCE_FILE).unlink(missing_ok=True)
        sync_directory(profile.directory)
        return recovery_status(profile.directory)
