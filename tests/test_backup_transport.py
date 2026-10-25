import hashlib
import os
import sys
import time
from types import SimpleNamespace

import pytest

from agent_memory.backup_transport import BackupError, stream_process
from agent_memory.local_profile import LocalProfileError


def test_streamed_dump_and_restore_preserve_large_binary_without_buffering_the_file(tmp_path):
    destination = tmp_path / 'database.dump'
    command = [sys.executable, '-c', "import sys; sys.stdout.buffer.write(b'01234567'*3000000)"]
    result = stream_process(command, os.environ.copy(), destination, write=True,
                            max_bytes=25_000_000, timeout_seconds=10)
    assert result['size'] == 24_000_000
    assert result['sha256'] == hashlib.sha256(b'01234567' * 3000000).hexdigest()
    command = [sys.executable, '-c', 'import sys; assert len(sys.stdin.buffer.read()) == 24000000']
    assert stream_process(command, os.environ.copy(), destination, write=False,
                          max_bytes=25_000_000, timeout_seconds=10) == result


@pytest.mark.parametrize('script,maximum,timeout,code', [
    ("import sys; sys.stdout.write('x'*10000)", 10, 5, 'backup_limit_exceeded'),
    ('import time; time.sleep(10)', 100, 1, 'backup_timeout'),
    ("import sys; sys.stderr.write('secret'); sys.exit(1)", 100, 5, 'backup_process_failed'),
])
def test_bounded_process_failures_are_redacted(tmp_path, script, maximum, timeout, code):
    with pytest.raises(BackupError) as error:
        stream_process([sys.executable, '-c', script], os.environ.copy(), tmp_path / 'output',
                       write=True, max_bytes=maximum, timeout_seconds=timeout)
    assert error.value.code == code
    assert 'secret' not in str(error.value)


def test_existing_backup_is_never_truncated(tmp_path):
    path = tmp_path / 'output'
    path.write_bytes(b'preserved')
    with pytest.raises(BackupError):
        stream_process([sys.executable, '-c', ''], os.environ.copy(), path,
                       write=True, max_bytes=100, timeout_seconds=5)
    assert path.read_bytes() == b'preserved'


@pytest.mark.parametrize('maximum,timeout,script,code', [
    (65536, 5, 'import sys; sys.stdin.buffer.read()', 'backup_limit_exceeded'),
    (2_000_000, 1, 'import time; time.sleep(10)', 'backup_timeout'),
    (2_000_000, 5, "import sys; sys.stderr.write('private restore details'); sys.exit(2)", 'backup_process_failed'),
])
def test_restore_failure_is_bounded_and_preserves_input(tmp_path, maximum, timeout, script, code, capsys):
    path = tmp_path / 'database.dump'
    original = b'0123456789abcdef' * 100_000
    path.write_bytes(original)
    started = time.monotonic()
    with pytest.raises(BackupError) as error:
        stream_process([sys.executable, '-c', script], os.environ.copy(), path,
                       write=False, max_bytes=maximum, timeout_seconds=timeout)
    assert error.value.code == code
    assert time.monotonic() - started < timeout + 3
    assert path.read_bytes() == original
    assert 'private restore details' not in str(error.value)
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


@pytest.mark.parametrize('write,kind', [(write, kind) for write in (True, False)
                                      for kind in ('symlink', 'hardlink', 'directory', 'missing')
                                      if not (write and kind == 'missing')])
def test_unsafe_file_identity_is_rejected_before_subprocess(tmp_path, monkeypatch, kind, write):
    from agent_memory import backup_transport

    original = tmp_path / 'original'
    original.write_bytes(b'preserved')
    path = tmp_path / 'selected'
    if kind == 'symlink':
        path.symlink_to(original)
    elif kind == 'hardlink':
        os.link(original, path)
    elif kind == 'directory':
        path.mkdir()
    monkeypatch.setattr(backup_transport.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Must not launch'))
    with pytest.raises((BackupError, LocalProfileError)):
        stream_process(['not-launched'], os.environ.copy(), path,
                       write=write, max_bytes=100, timeout_seconds=5)
    assert original.read_bytes() == b'preserved'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permissions are verified separately from Windows ACLs')
def test_dump_and_restore_use_private_files(tmp_path):
    path = tmp_path / 'database.dump'
    stream_process([sys.executable, '-c', "import sys; sys.stdout.buffer.write(b'dump')"], os.environ.copy(), path,
                   write=True, max_bytes=4, timeout_seconds=5)
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    stream_process([sys.executable, '-c', "import sys; assert sys.stdin.buffer.read() == b'dump'"],
                   os.environ.copy(), path, write=False, max_bytes=4, timeout_seconds=5)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('restore', [False, True])
def test_database_tools_use_fixed_transaction_and_snapshot_arguments(tmp_path, monkeypatch, restore):
    from agent_memory import backup_transport, desktop

    invocations = []
    profile = SimpleNamespace(installation_id='test')
    environment = {'PRIVATE_TEST': 'not printed'}

    def compose(current, *arguments):
        assert current is profile
        invocations.append(arguments)
        return ['docker', *arguments], environment

    def transfer(command, env, path, **options):
        assert env is environment
        assert path == tmp_path / 'database.dump'
        assert options == {'write': not restore, 'max_bytes': 1234, 'timeout_seconds': 10}
        return {'size': 321, 'sha256': 'a' * 64}

    monkeypatch.setattr(desktop, '_compose_invocation', compose)
    monkeypatch.setattr(backup_transport, 'stream_process', transfer)
    result = backup_transport.database_stream(profile, tmp_path / 'database.dump',
                                             snapshot=None if restore else '00000003-0000001A-1',
                                             restore=restore, max_bytes=1234, timeout_seconds=10)
    assert result['size'] == 321
    arguments = invocations[0]
    assert arguments[:3] == ('exec', '-T', 'database')
    assert arguments[3] == ('pg_restore' if restore else 'pg_dump')
    assert '--no-owner' in arguments and '--no-acl' in arguments
    if restore:
        assert '--single-transaction' in arguments and '--exit-on-error' in arguments
        assert not any(value.startswith('--snapshot=') for value in arguments)
    else:
        assert '--format=custom' in arguments and '--compress=0' in arguments
        assert '--snapshot=00000003-0000001A-1' in arguments


@pytest.mark.parametrize('snapshot', ['', '--file=private', '1; exit', '1\n2', True])
def test_snapshot_arguments_cannot_add_options_or_shell_code(tmp_path, monkeypatch, snapshot):
    from agent_memory import backup_transport, desktop

    monkeypatch.setattr(desktop, '_compose_invocation', lambda *a, **k: pytest.fail('No command expected'))
    with pytest.raises(BackupError) as error:
        backup_transport.database_stream(None, tmp_path / 'dump', snapshot=snapshot)
    assert error.value.code == 'backup_invalid'


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='Named pipes require a POSIX filesystem')
def test_restore_rejects_fifo_before_open_can_wait_for_a_writer(tmp_path):
    path = tmp_path / 'not-a-regular-dump'
    os.mkfifo(path, 0o600)
    script = (
        'import os,sys; from pathlib import Path; '
        'from agent_memory.backup_transport import stream_process; '
        'stream_process([sys.executable,"-c",""],os.environ.copy(),Path(sys.argv[1]),'
        'write=False,max_bytes=100,timeout_seconds=1)'
    )
    import subprocess

    result = subprocess.run([sys.executable, '-c', script, str(path)],
                            capture_output=True, timeout=3, check=False)
    assert result.returncode != 0
    assert b'private regular' in result.stderr or b'backup_process_failed' in result.stderr
