import argparse
from types import SimpleNamespace

import psycopg
import pytest

from agent_memory import desktop_cli, desktop_recovery
from agent_memory.local_profile import ensure_profile
from agent_memory.recovery_profile import MAINTENANCE_FILE, read_document, write_document


def parser():
    result = argparse.ArgumentParser()
    desktop_cli.add_commands(result.add_subparsers(dest='command', required=True))
    return result


def test_activation_requires_explicit_host_reconciliation_flag():
    with pytest.raises(SystemExit):
        parser().parse_args(['desktop', 'activate-recovery', '--directory', '/tmp/fresh',
                             '--recovery-epoch', 'epoch', '--backup-id', 'backup', '--checkpoint-at', 'checkpoint',
                             '--retire-installation-id', 'source', '--expected-sequence', '0', '--expected-head-hash', 'head'])


def test_recovery_database_errors_are_redacted(monkeypatch, capsys):
    def unavailable(args):
        raise psycopg.OperationalError('credential-and-private-content-must-not-be-output')

    monkeypatch.setattr(desktop_recovery, 'run', unavailable)
    with pytest.raises(SystemExit) as stopped:
        desktop_cli.dispatch(SimpleNamespace(desktop_command='recovery-status', directory=None))
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert 'database_unavailable' in captured.err
    assert 'credential' not in captured.err and not captured.out


def test_cancel_backup_only_releases_matching_maintenance_and_preserves_evidence(tmp_path):
    from agent_memory.backup_transport import BackupError
    from agent_memory.helpers import uid

    profile = ensure_profile(tmp_path / 'profile')
    identifier = uid()
    marker = {'operation': 'backup', 'backup_id': identifier, 'request_hash': 'unchanged'}
    write_document(profile.directory / MAINTENANCE_FILE, marker)
    evidence = tmp_path / 'partial-evidence'
    evidence.write_bytes(b'private partial dump')
    with pytest.raises(BackupError):
        desktop_recovery.run(SimpleNamespace(desktop_command='cancel-backup', directory=profile.directory, backup_id=uid()))
    assert read_document(profile.directory / MAINTENANCE_FILE) == marker
    result = desktop_recovery.run(SimpleNamespace(desktop_command='cancel-backup', directory=profile.directory, backup_id=identifier))
    assert result['state'] == 'cancelled' and not (profile.directory / MAINTENANCE_FILE).exists()
    assert evidence.read_bytes() == b'private partial dump'
