from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from .helpers import DomainError, digest, now, uid
from .local_profile import _directory, _open_private, _private, profile_lock

_ZERO_HASH = '0' * 64
_MAX_TARGETS = 1_000_000
_SCHEMA = '''
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE intents(sequence INTEGER PRIMARY KEY,key_digest TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,document TEXT NOT NULL,previous_hash TEXT NOT NULL,intent_hash TEXT NOT NULL);
CREATE INDEX intent_brain ON intents(json_extract(document,'$.brain_id'),sequence);
CREATE TABLE targets(sequence INTEGER NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,
    PRIMARY KEY(sequence,kind,id),FOREIGN KEY(sequence) REFERENCES intents(sequence));
CREATE TABLE backups(id TEXT PRIMARY KEY,manifest_hash TEXT NOT NULL,sequence INTEGER NOT NULL,head_hash TEXT NOT NULL);
CREATE TABLE activations(recovery_epoch TEXT PRIMARY KEY,installation_id TEXT NOT NULL,manifest_hash TEXT NOT NULL,
    previous_installation_id TEXT NOT NULL,sequence INTEGER NOT NULL,head_hash TEXT NOT NULL);
'''


class RegistryError(DomainError):
    def __init__(self, code='dependency_unavailable'):
        super().__init__(code, 'The independent recovery authority is unavailable or inconsistent; memory access is blocked.')


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise RegistryError() from None
    return value


class DeletionRegistry:
    def __init__(self, directory: Path, registry_id: str):
        self.directory = _directory(directory)
        self.registry_id = _uuid(registry_id)
        self.path = self.directory / 'authority.sqlite3'

    @classmethod
    def create(cls, directory: Path, installation_id: str, *, registry_id: str | None = None):
        _uuid(installation_id)
        path = _directory(directory)
        if path.exists() and any(path.iterdir()):
            raise RegistryError('idempotency_conflict')
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        _private(path, directory=True)
        registry = cls(path, registry_id or uid())
        descriptor = _open_private(registry.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        try:
            with sqlite3.connect(registry.path) as connection:
                connection.execute('PRAGMA synchronous=FULL')
                connection.executescript(_SCHEMA)
                connection.executemany('INSERT INTO metadata VALUES(?,?)', (
                    ('version', '1'), ('registry_id', registry.registry_id),
                    ('installation_id', installation_id),
                ))
            registry._sync()
        except (OSError, sqlite3.Error):
            raise RegistryError() from None
        return registry

    def _sync(self):
        descriptor = _open_private(self.path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.name != 'nt':
            descriptor = os.open(self.directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    @contextmanager
    def _connection(self, *, write=False):
        try:
            if not self.directory.is_dir():
                raise RegistryError()
            with profile_lock(self.directory):
                descriptor = _open_private(self.path, os.O_RDONLY)
                os.close(descriptor)
                connection = sqlite3.connect('file:' + quote(str(self.path)) + '?mode=rw', uri=True, timeout=5)
                connection.row_factory = sqlite3.Row
                try:
                    connection.execute('PRAGMA synchronous=FULL')
                    connection.execute('PRAGMA foreign_keys=ON')
                    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                    metadata = dict(connection.execute('SELECT key,value FROM metadata'))
                    if metadata.get('version') != '1' or metadata.get('registry_id') != self.registry_id:
                        raise RegistryError()
                    yield connection, metadata
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
                if write:
                    self._sync()
        except (OSError, sqlite3.Error, ValueError):
            raise RegistryError() from None

    @staticmethod
    def _head(connection):
        row = connection.execute('SELECT sequence,intent_hash FROM intents ORDER BY sequence DESC LIMIT 1').fetchone()
        return {'sequence': row['sequence'], 'head_hash': row['intent_hash']} if row else {
            'sequence': 0, 'head_hash': _ZERO_HASH,
        }

    def head(self):
        with self._connection() as (connection, metadata):
            return {'registry_id': self.registry_id, 'installation_id': metadata['installation_id'],
                    **self._head(connection)}

    @staticmethod
    def _document(connection, row):
        document = json.loads(row['document'])
        if not isinstance(document, dict):
            raise RegistryError()
        targets = connection.execute('SELECT kind,id FROM targets WHERE sequence=? ORDER BY kind,id LIMIT ?',
                                     (row['sequence'], _MAX_TARGETS + 1)).fetchall()
        if len(targets) > _MAX_TARGETS or any(item['kind'] not in ('event', 'memory') for item in targets):
            raise RegistryError()
        document.update(event_ids=[item['id'] for item in targets if item['kind'] == 'event'],
                        memory_ids=[item['id'] for item in targets if item['kind'] == 'memory'])
        expected = digest([row['previous_hash'], row['key_digest'], row['fingerprint'], document])
        if expected != row['intent_hash'] or document.get('sequence') != row['sequence']:
            raise RegistryError()
        return {**document, 'intent_hash': row['intent_hash']}

    def verify(self):
        with self._connection() as (connection, metadata):
            previous, sequence = _ZERO_HASH, 0
            for row in connection.execute('SELECT * FROM intents ORDER BY sequence'):
                sequence += 1
                if row['sequence'] != sequence or row['previous_hash'] != previous:
                    raise RegistryError()
                self._document(connection, row)
                previous = row['intent_hash']
            _uuid(metadata['installation_id'])
            return {'registry_id': self.registry_id, 'installation_id': metadata['installation_id'],
                    'sequence': sequence, 'head_hash': previous}

    def find(self, key_digest, fingerprint):
        with self._connection() as (connection, _):
            row = connection.execute('SELECT * FROM intents WHERE key_digest=?', (key_digest,)).fetchone()
            if row is None:
                return None
            if row['fingerprint'] != fingerprint:
                raise RegistryError('idempotency_conflict')
            return self._document(connection, row)

    def append(self, *, key_digest, fingerprint, principal, brain_id, mode, cutoff, event_ids, memory_ids,
               expected_installation_id=None):
        if mode not in ('suppress_recall', 'erase_content') or type(cutoff) is not int or cutoff < 0:
            raise RegistryError()
        events, memories = sorted(set(event_ids)), sorted(set(memory_ids))
        if len(events) + len(memories) > _MAX_TARGETS:
            raise RegistryError('budget_exceeded')
        with self._connection(write=True) as (connection, metadata):
            if expected_installation_id is not None and metadata['installation_id'] != expected_installation_id:
                raise RegistryError('version_conflict')
            existing = connection.execute('SELECT * FROM intents WHERE key_digest=?', (key_digest,)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise RegistryError('idempotency_conflict')
                return self._document(connection, existing)
            head = self._head(connection)
            document = {'sequence': head['sequence'] + 1, 'deletion_id': uid(), 'tenant_id': principal.tenant_id,
                        'owner_id': principal.principal_id, 'brain_id': brain_id, 'mode': mode,
                        'cutoff': cutoff, 'created_at': now(), 'event_ids': events, 'memory_ids': memories}
            fingerprint_hash = digest([head['head_hash'], key_digest, fingerprint, document])
            stored = {key: value for key, value in document.items() if key not in ('event_ids', 'memory_ids')}
            connection.execute('INSERT INTO intents VALUES(?,?,?,?,?,?)', (
                document['sequence'], key_digest, fingerprint, json.dumps(stored, sort_keys=True),
                head['head_hash'], fingerprint_hash,
            ))
            connection.executemany('INSERT INTO targets VALUES(?,?,?)', (
                (document['sequence'], kind, identifier)
                for kind, identifiers in (('event', events), ('memory', memories)) for identifier in identifiers
            ))
            return {**document, 'intent_hash': fingerprint_hash}

    def intents(self, principal, *, brain_id=None, after=0, limit=128):
        with self._connection() as (connection, _):
            rows = connection.execute(
                "SELECT * FROM intents WHERE sequence>? AND (? IS NULL OR json_extract(document,'$.brain_id')=?) "
                'ORDER BY sequence LIMIT ?', (after, brain_id, brain_id, limit),
            ).fetchall()
            result = []
            for row in rows:
                document = self._document(connection, row)
                if (document['tenant_id'], document['owner_id']) != (principal.tenant_id, principal.principal_id):
                    raise RegistryError()
                if brain_id is None or document['brain_id'] == brain_id:
                    result.append(document)
            return result, rows[-1]['sequence'] if rows else after

    def intent(self, sequence):
        with self._connection() as (connection, _):
            row = connection.execute('SELECT * FROM intents WHERE sequence=?', (sequence,)).fetchone()
            if row is None:
                raise RegistryError()
            return self._document(connection, row)

    def register_backup(self, backup_id, manifest_hash, head):
        _uuid(backup_id)
        with self._connection(write=True) as (connection, _):
            existing = connection.execute('SELECT * FROM backups WHERE id=?', (backup_id,)).fetchone()
            values = (backup_id, manifest_hash, head['sequence'], head['head_hash'])
            if existing:
                if tuple(existing) != values:
                    raise RegistryError('idempotency_conflict')
                return
            if self._head(connection) != {key: head[key] for key in ('sequence', 'head_hash')}:
                raise RegistryError('version_conflict')
            connection.execute('INSERT INTO backups VALUES(?,?,?,?)', values)

    def verify_backup(self, backup_id, manifest_hash):
        self.verify()
        with self._connection() as (connection, _):
            row = connection.execute('SELECT manifest_hash FROM backups WHERE id=?', (backup_id,)).fetchone()
            if row is None or row['manifest_hash'] != manifest_hash:
                raise RegistryError()

    def activate(self, recovery_epoch, installation_id, manifest_hash, *, expected_installation_id,
                 expected_sequence, expected_head_hash):
        _uuid(recovery_epoch)
        _uuid(installation_id)
        _uuid(expected_installation_id)
        with self._connection(write=True) as (connection, metadata):
            row = connection.execute('SELECT * FROM activations WHERE recovery_epoch=?', (recovery_epoch,)).fetchone()
            values = (recovery_epoch, installation_id, manifest_hash, expected_installation_id,
                      expected_sequence, expected_head_hash)
            if row:
                if tuple(row) != values or metadata['installation_id'] != installation_id:
                    raise RegistryError('idempotency_conflict')
                return
            if (metadata['installation_id'] != expected_installation_id
                    or self._head(connection) != {'sequence': expected_sequence, 'head_hash': expected_head_hash}):
                raise RegistryError('version_conflict')
            connection.execute('INSERT INTO activations VALUES(?,?,?,?,?,?)', values)
            connection.execute("UPDATE metadata SET value=? WHERE key='installation_id'", (installation_id,))
