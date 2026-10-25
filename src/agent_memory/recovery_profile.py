from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .deletion_registry import DeletionRegistry, RegistryError, _uuid
from .helpers import Principal, uid
from .local_profile import _directory, _open_private, _pairs, profile_lock
from .recovery_authority import RecoveryAuthority
from .storage import Database

BINDING_FILE = 'recovery.json'
MAINTENANCE_FILE = 'recovery-maintenance.json'
_MAX_JSON_BYTES = 131072


def read_document(path: Path, *, missing=None, max_bytes=_MAX_JSON_BYTES):
    try:
        descriptor = _open_private(path, os.O_RDONLY)
        with os.fdopen(descriptor, 'rb') as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise RegistryError('budget_exceeded')
        value = json.loads(content, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise RegistryError()
        return value
    except FileNotFoundError:
        return missing
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise RegistryError() from None


def sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_document(path: Path, value):
    content = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()
    if len(content) > _MAX_JSON_BYTES:
        raise RegistryError('budget_exceeded')
    temporary = path.parent / ('.recovery-' + secrets.token_hex(16) + '.tmp')
    try:
        if path.exists() or path.is_symlink():
            descriptor = _open_private(path, os.O_RDONLY)
            os.close(descriptor)
        descriptor = _open_private(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def load_binding(profile):
    value = read_document(profile.directory / BINDING_FILE)
    if value is None:
        return None
    if set(value) != {'version', 'registry_path', 'registry_id', 'installation_id', 'state', 'recovery_epoch'}:
        raise RegistryError()
    if (type(value['version']) is not int or value['version'] != 1
            or value['installation_id'] != profile.installation_id
            or value['state'] not in ('initializing', 'active', 'quarantined')
            or not isinstance(value['registry_path'], str) or not Path(value['registry_path']).is_absolute()):
        raise RegistryError()
    _uuid(value['registry_id'])
    if value['recovery_epoch'] is not None:
        _uuid(value['recovery_epoch'])
    path = _directory(Path(value['registry_path']))
    if path == profile.directory or profile.directory in path.parents:
        raise RegistryError()
    return value


def profile_settings(profile):
    binding = load_binding(profile)
    if binding is None:
        return {}
    return {'recovery_registry_path': Path(binding['registry_path']), 'recovery_registry_id': binding['registry_id'],
            'recovery_installation_id': profile.installation_id}


def authority_for(profile, *, restoring=False):
    binding = load_binding(profile)
    if binding is None:
        raise RegistryError()
    return RecoveryAuthority(DeletionRegistry(Path(binding['registry_path']), binding['registry_id']),
                             profile.installation_id, restoring=restoring)


def ensure_authority(profile):
    principal = Principal(profile.tenant_id, profile.principal_id)
    database = Database(profile.database_url)
    with profile_lock(profile.directory):
        binding = load_binding(profile)
        with database.transaction(principal) as conn:
            current = conn.execute('SELECT * FROM memory_recovery_installations WHERE tenant_id=%s AND owner_id=%s',
                                   (principal.tenant_id, principal.principal_id)).fetchone()
        if binding is None:
            if current is not None:
                raise RegistryError()
            directory = profile.directory.parent / '.agent-memory-recovery' / profile.installation_id
            binding = {'version': 1, 'registry_path': str(directory), 'registry_id': uid(),
                       'installation_id': profile.installation_id, 'state': 'initializing', 'recovery_epoch': None}
            write_document(profile.directory / BINDING_FILE, binding)
        registry = DeletionRegistry(Path(binding['registry_path']), binding['registry_id'])
        if not registry.path.exists():
            if current is not None or binding['state'] != 'initializing':
                raise RegistryError()
            registry = DeletionRegistry.create(registry.directory, profile.installation_id,
                                               registry_id=registry.registry_id)
        authority = RecoveryAuthority(registry, profile.installation_id)
        authority.initialize(database, principal)
        authority.reconcile(database, principal)
        if binding['state'] != 'active':
            write_document(profile.directory / BINDING_FILE, {**binding, 'state': 'active'})
        return authority


def check_start_allowed(profile, *, allow_initializing=False):
    if read_document(profile.directory / MAINTENANCE_FILE) is not None:
        raise RegistryError('deletion_in_progress')
    binding = load_binding(profile)
    allowed = ('active', 'initializing') if allow_initializing else ('active',)
    if binding is not None and binding['state'] not in allowed:
        raise RegistryError('deletion_in_progress')
    if binding is not None and binding['state'] == 'active':
        if authority_for(profile).registry.head()['installation_id'] != profile.installation_id:
            raise RegistryError()
