from __future__ import annotations

import hashlib
import os
import queue
import subprocess
import threading
import time

from .local_profile import _open_private


class BackupError(RuntimeError):
    def __init__(self, code):
        allowed = {'backup_limit_exceeded', 'backup_process_failed', 'backup_timeout', 'backup_invalid',
                   'backup_conflict', 'restore_blocked', 'recovery_acknowledgement_required'}
        self.code = code if code in allowed else 'backup_process_failed'
        super().__init__(self.code + ': Recovery did not complete. Existing installations and artifacts were preserved.')


def limits(max_bytes, timeout_seconds):
    if (type(max_bytes) is not int or not 1 <= max_bytes <= 1024**4
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 43200):
        raise BackupError('backup_limit_exceeded')


def stream_process(command, environment, path, *, write, max_bytes, timeout_seconds):
    limits(max_bytes, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    cancelled = threading.Event()
    messages = queue.Queue(maxsize=2)
    process = None
    worker = None
    descriptor = None

    def report(value):
        while not cancelled.is_set():
            try:
                messages.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def transfer():
        total = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, 'wb' if write else 'rb') as local:
                source, destination = (process.stdout, local) if write else (local, process.stdin)
                while not cancelled.is_set():
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise BackupError('backup_limit_exceeded')
                    digest.update(chunk)
                    destination.write(chunk)
                if write:
                    local.flush()
                    os.fsync(local.fileno())
                else:
                    process.stdin.close()
            report({'size': total, 'sha256': digest.hexdigest()})
        except BackupError as error:
            report(error)
        except (OSError, ValueError):
            report(BackupError('backup_process_failed'))

    try:
        descriptor = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL if write else os.O_RDONLY)
        process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL if write else subprocess.PIPE,
                                   stdout=subprocess.PIPE if write else subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker = threading.Thread(target=transfer, daemon=True)
        worker.start()
        result = None
        while result is None or process.poll() is None:
            if time.monotonic() >= deadline:
                raise BackupError('backup_timeout')
            if result is None:
                try:
                    result = messages.get(timeout=0.05)
                    if isinstance(result, BackupError):
                        raise result
                except queue.Empty:
                    pass
            else:
                time.sleep(0.02)
        if process.returncode:
            raise BackupError('backup_process_failed')
        return result
    except (OSError, subprocess.SubprocessError):
        raise BackupError('backup_process_failed') from None
    finally:
        cancelled.set()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        if worker is not None:
            worker.join(timeout=5)
        elif descriptor is not None:
            os.close(descriptor)
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def database_stream(profile, path, *, snapshot=None, restore=False, max_bytes=64 * 1024**3, timeout_seconds=3600):
    from .desktop import _compose_invocation

    arguments = ['exec', '-T', 'database', 'pg_restore' if restore else 'pg_dump',
                 '--username=postgres', '--dbname=agent_memory', '--no-owner', '--no-acl']
    arguments += ['--exit-on-error', '--single-transaction'] if restore else ['--format=custom', '--compress=0']
    if snapshot is not None:
        if not isinstance(snapshot, str) or not snapshot or any(char not in '0123456789ABCDEFabcdef-' for char in snapshot):
            raise BackupError('backup_invalid')
        arguments.append('--snapshot=' + snapshot)
    if getattr(profile, 'database_engine', 'docker') == 'embedded':
        from .embedded_database import client_command

        command, environment = client_command(profile, *arguments[3:])
    else:
        command, environment = _compose_invocation(profile, *arguments)
    return stream_process(command, environment, path, write=not restore,
                          max_bytes=max_bytes, timeout_seconds=timeout_seconds)
