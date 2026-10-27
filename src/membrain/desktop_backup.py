from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import desktop
from .backup_transport import BackupError, database_stream, limits
from .build_identity import BUILD_ID, MIGRATIONS, SCHEMA_ID, manifest_digest
from .deletion_registry import _uuid
from .helpers import Principal, digest, uid
from .local_profile import _directory, _open_private, _private, load_profile, profile_lock
from .recovery_profile import (
    MAINTENANCE_FILE,
    authority_for,
    load_binding,
    read_document,
    sync_directory,
    write_document,
)
from .storage import Database

_MEMBERS = {'database.dump', 'configuration.json', 'sessions.jsonl'}
_MAX_SESSIONS = 4096
_MAX_SESSION_BYTES = 8192


def file_digest(path, *, max_bytes):
    total, hashed = 0, hashlib.sha256()
    descriptor = _open_private(path, os.O_RDONLY)
    with os.fdopen(descriptor, 'rb') as stream:
        while chunk := stream.read(65536):
            total += len(chunk)
            if total > max_bytes:
                raise BackupError('backup_limit_exceeded')
            hashed.update(chunk)
    return {'size': total, 'sha256': hashed.hexdigest()}


def _configuration(profile):
    from .chat_config import load_chat_configuration
    from .cognition_config import load_cognition_policy

    with profile_lock(profile.directory):
        current = load_profile(profile.directory)
        value = {name: getattr(current, name) for name in (
            'installation_id', 'brain_id', 'tenant_id', 'principal_id', 'name', 'cognition',
        )}
        if current.database_engine != 'docker':
            value['database_engine'] = current.database_engine
        value['chat'] = read_document(profile.directory / 'chat.json')
        value['cognition_policy'] = read_document(profile.directory / 'cognition-policy.json')
        if value['chat'] is not None:
            load_chat_configuration(profile.directory)
        load_cognition_policy(profile.directory)
        return value


def _sessions(profile, path):
    from .agent_session import _identity, _read_pending

    root = profile.directory / 'sessions'
    descriptor = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    count = 0
    with os.fdopen(descriptor, 'wb') as stream:
        if root.exists() or root.is_symlink():
            _private(root, directory=True)
            for directory in root.iterdir():
                count += 1
                if count > _MAX_SESSIONS:
                    raise BackupError('backup_limit_exceeded')
                _private(directory, directory=True)
                pending = read_document(directory / 'pending.json', max_bytes=_MAX_SESSION_BYTES)
                if pending is None:
                    continue
                identity = pending.get('identity', {})
                expected = _identity(profile, identity.get('conversation_id'), identity.get('branch_id'))
                if directory.name != digest(expected):
                    raise BackupError('backup_invalid')
                validated = _read_pending(directory / 'pending.json', expected)
                if validated is None:
                    continue
                stream.write((json.dumps(validated, sort_keys=True) + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())


def _checkpoint(connection, profile):
    connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    stamp = connection.execute('SELECT pg_export_snapshot() AS snapshot,clock_timestamp() AS checkpoint_at').fetchone()
    if connection.execute('SELECT 1 FROM memory_brains WHERE tenant_id<>%s OR owner_id<>%s LIMIT 1',
                          (profile.tenant_id, profile.principal_id)).fetchone():
        raise BackupError('backup_invalid')
    names = [row['name'] for row in connection.execute('SELECT name FROM memory_migrations ORDER BY name')]
    if names != [name for name, _ in MIGRATIONS]:
        raise BackupError('backup_invalid')
    version = connection.execute("SELECT current_setting('server_version_num') AS postgres_version,"
                                 "(SELECT extversion FROM pg_extension WHERE extname='vector') AS vector_version").fetchone()
    counts = {}
    for name in ('brains', 'events', 'assertions', 'episodes', 'tombstones', 'runtime_turns', 'jobs', 'cognitive_operations'):
        counts[name] = connection.execute('SELECT count(*) AS count FROM memory_' + name).fetchone()['count']
    return stamp['snapshot'], {'checkpoint_at': stamp['checkpoint_at'].isoformat(),
                              'migration_names': names, 'schema_id': SCHEMA_ID, 'build_id': BUILD_ID,
                              **version, 'counts': counts}


def verify_bundle(path: Path, registry, *, max_bytes=64 * 1024**3, registered=True):
    limits(max_bytes, 1)
    directory = _directory(path)
    _private(directory, directory=True)
    if {entry.name for entry in directory.iterdir()} != _MEMBERS | {'manifest.json'}:
        raise BackupError('backup_invalid')
    manifest = read_document(directory / 'manifest.json')
    required = {'version', 'backup_id', 'request_hash', 'source_installation_id', 'registry', 'checkpoint', 'members'}
    if (not isinstance(manifest, dict) or set(manifest) != required or type(manifest['version']) is not int
            or manifest['version'] != 1 or not isinstance(manifest['members'], dict)
            or set(manifest['members']) != _MEMBERS or not isinstance(manifest['registry'], dict)
            or not isinstance(manifest['checkpoint'], dict)
            or manifest['registry'].get('registry_id') != registry.registry_id):
        raise BackupError('backup_invalid')
    registry_state = manifest['registry']
    if (set(registry_state) != {'registry_id', 'installation_id', 'sequence', 'head_hash'}
            or type(registry_state['sequence']) is not int or registry_state['sequence'] < 0
            or any(not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None
                   for value in (registry_state['head_hash'], manifest['request_hash']))):
        raise BackupError('backup_invalid')
    for value in (manifest['backup_id'], manifest['source_installation_id'], registry_state['installation_id']):
        _uuid(value)
    if registry_state['installation_id'] != manifest['source_installation_id']:
        raise BackupError('backup_invalid')
    if registered:
        registry.verify_backup(manifest['backup_id'], digest(manifest))
    checkpoint = manifest['checkpoint']
    names = checkpoint.get('migration_names')
    if (not isinstance(names, list) or not names or names != [name for name, _ in MIGRATIONS[:len(names)]]
            or checkpoint.get('schema_id') != manifest_digest(dict(MIGRATIONS[:len(names)]))):
        raise BackupError('backup_invalid')
    total = 0
    for name in sorted(_MEMBERS):
        metadata = manifest['members'][name]
        if (not isinstance(metadata, dict) or set(metadata) != {'size', 'sha256'}
                or type(metadata['size']) is not int or metadata['size'] < 0):
            raise BackupError('backup_invalid')
        total += metadata['size']
        if total > max_bytes or file_digest(directory / name, max_bytes=metadata['size']) != metadata:
            raise BackupError('backup_invalid')
    return manifest


def backup(directory: Path | None, *, output: Path, request_id: str | None = None,
           max_bytes=64 * 1024**3, timeout_seconds=3600):
    limits(max_bytes, timeout_seconds)
    if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 256
                                   or any(ord(value) < 32 for value in request_id)):
        raise BackupError('backup_invalid')
    profile = load_profile(directory)
    destination = _directory(output)
    if destination == profile.directory or profile.directory in destination.parents or not destination.parent.is_dir():
        raise BackupError('backup_invalid')
    request_hash = digest([request_id, str(destination), profile.installation_id, max_bytes, timeout_seconds])
    with profile_lock(profile.directory / 'lifecycle', timeout=360):
        authority = authority_for(profile)
        if destination == authority.registry.directory or destination in authority.registry.directory.parents:
            raise BackupError('backup_invalid')
        marker_path = profile.directory / MAINTENANCE_FILE
        marker = read_document(marker_path)
        if marker is not None and (marker.get('operation') != 'backup' or marker.get('request_hash') != request_hash):
            raise BackupError('backup_conflict')
        if marker is not None:
            _uuid(marker.get('backup_id'))
        if destination.exists():
            manifest = verify_bundle(destination, authority.registry, max_bytes=max_bytes)
            if manifest['request_hash'] != request_hash:
                raise BackupError('backup_conflict')
            if marker is not None:
                if marker.get('backup_id') != manifest['backup_id']:
                    raise BackupError('backup_conflict')
                marker_path.unlink()
                sync_directory(profile.directory)
            return {'state': 'completed', 'backup_id': manifest['backup_id'], 'output': str(destination),
                    'checkpoint': manifest['checkpoint'], 'private_data': True, 'replayed': True}
        if authority.registry.head()['installation_id'] != profile.installation_id:
            raise BackupError('backup_conflict')
        if marker is None:
            marker = {'operation': 'backup', 'request_hash': request_hash, 'backup_id': uid(),
                      'output': str(destination)}
            write_document(marker_path, marker)
        staging = destination.parent / ('.agent-memory-backup-' + marker['backup_id'])
        if staging.exists():
            _private(staging, directory=True)
            if (staging / 'manifest.json').exists():
                manifest = verify_bundle(staging, authority.registry, max_bytes=max_bytes, registered=False)
                if (manifest['request_hash'] != request_hash or manifest['backup_id'] != marker['backup_id']
                        or manifest['source_installation_id'] != profile.installation_id):
                    raise BackupError('backup_conflict')
                authority.registry.register_backup(marker['backup_id'], digest(manifest), manifest['registry'])
                os.rename(staging, destination)
                sync_directory(destination.parent)
                marker_path.unlink()
                sync_directory(profile.directory)
                return {'state': 'completed', 'backup_id': manifest['backup_id'], 'output': str(destination),
                        'checkpoint': manifest['checkpoint'], 'private_data': True, 'replayed': True}
            if {entry.name for entry in staging.iterdir()} - (_MEMBERS | {'manifest.json'}):
                raise BackupError('backup_conflict')
            for member in staging.iterdir():
                _private(member)
                member.unlink()
        else:
            staging.mkdir(mode=0o700)
        desktop.stop(profile.directory)
        desktop._database_up(profile)
        desktop._check_database(profile, require_current=True)
        authority.reconcile(Database(profile.database_url), Principal(profile.tenant_id, profile.principal_id))
        head = authority.registry.verify()
        write_document(staging / 'configuration.json', _configuration(profile))
        _sessions(profile, staging / 'sessions.jsonl')
        members = {name: file_digest(staging / name, max_bytes=max_bytes)
                   for name in ('configuration.json', 'sessions.jsonl')}
        remaining = max_bytes - sum(item['size'] for item in members.values())
        if remaining < 1:
            raise BackupError('backup_limit_exceeded')
        with psycopg.connect(profile.migration_database_url, row_factory=dict_row) as connection:
            snapshot, checkpoint = _checkpoint(connection, profile)
            members['database.dump'] = database_stream(profile, staging / 'database.dump', snapshot=snapshot,
                                                       max_bytes=remaining, timeout_seconds=timeout_seconds)
        manifest = {'version': 1, 'backup_id': marker['backup_id'], 'request_hash': request_hash,
                    'source_installation_id': profile.installation_id, 'registry': head,
                    'checkpoint': checkpoint, 'members': members}
        write_document(staging / 'manifest.json', manifest)
        sync_directory(staging)
        authority.registry.register_backup(marker['backup_id'], digest(manifest), head)
        if destination.exists():
            raise BackupError('backup_conflict')
        os.rename(staging, destination)
        sync_directory(destination.parent)
        marker_path.unlink()
        sync_directory(profile.directory)
        return {'state': 'completed', 'backup_id': marker['backup_id'], 'output': str(destination),
                'checkpoint': checkpoint, 'private_data': True, 'service': 'stopped',
                'registry_path': load_binding(profile)['registry_path'], 'replayed': False}
