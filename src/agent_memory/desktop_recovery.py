from pathlib import Path


def add_commands(actions):
    backup = actions.add_parser('backup', help='Create a private streamed database backup; stop the local service')
    backup.add_argument('--directory', type=Path)
    backup.add_argument('--output', type=Path, required=True)
    backup.add_argument('--request-id', required=True)
    restore = actions.add_parser('restore-backup', help='Restore a trusted backup into a fresh quarantined installation')
    restore.add_argument('--directory', type=Path, required=True)
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--registry', type=Path, required=True)
    restore.add_argument('--request-id', required=True)
    restore.add_argument('--api-port', type=int)
    restore.add_argument('--database-port', type=int)
    for command in (backup, restore):
        command.add_argument('--max-bytes', type=int, default=64 * 1024**3)
        command.add_argument('--timeout-seconds', type=int, default=3600)
    status = actions.add_parser('recovery-status', help='Show the recovery checkpoint and activation requirements')
    status.add_argument('--directory', type=Path)
    activate = actions.add_parser('activate-recovery', help='Acknowledge the checkpoint and retire the previous installation')
    activate.add_argument('--directory', type=Path, required=True)
    activate.add_argument('--recovery-epoch', required=True)
    activate.add_argument('--backup-id', required=True)
    activate.add_argument('--checkpoint-at', required=True)
    activate.add_argument('--retire-installation-id', required=True)
    activate.add_argument('--expected-sequence', required=True, type=int)
    activate.add_argument('--expected-head-hash', required=True)
    activate.add_argument('--acknowledge-host-reconciliation', action='store_true', required=True,
                          help='Confirm the prior installation is stopped and host cursors/delivery records are reconciled')
    cancel = actions.add_parser('cancel-backup', help='Leave partial backup evidence and release maintenance mode')
    cancel.add_argument('--directory', type=Path)
    cancel.add_argument('--backup-id', required=True)


def run(args):
    if args.desktop_command == 'backup':
        from .desktop_backup import backup

        return backup(args.directory, output=args.output, request_id=args.request_id,
                      max_bytes=args.max_bytes, timeout_seconds=args.timeout_seconds)
    if args.desktop_command == 'restore-backup':
        from .desktop_restore import restore

        return restore(backup_path=args.backup, directory=args.directory, registry_path=args.registry,
                       request_id=args.request_id, api_port=args.api_port, database_port=args.database_port,
                       max_bytes=args.max_bytes, timeout_seconds=args.timeout_seconds)
    if args.desktop_command == 'activate-recovery':
        from .desktop_restore import activate

        return activate(args.directory, recovery_epoch=args.recovery_epoch, backup_id=args.backup_id,
                        checkpoint_at=args.checkpoint_at, retire_installation_id=args.retire_installation_id,
                        expected_sequence=args.expected_sequence, expected_head_hash=args.expected_head_hash,
                        acknowledge_host_reconciliation=args.acknowledge_host_reconciliation)
    if args.desktop_command == 'cancel-backup':
        from .backup_transport import BackupError
        from .deletion_registry import _uuid
        from .local_profile import load_profile, profile_lock
        from .recovery_profile import MAINTENANCE_FILE, read_document, sync_directory

        _uuid(args.backup_id)
        profile = load_profile(args.directory)
        with profile_lock(profile.directory / 'lifecycle', timeout=360):
            marker_path = profile.directory / MAINTENANCE_FILE
            marker = read_document(marker_path)
            if not marker or marker.get('operation') != 'backup' or marker.get('backup_id') != args.backup_id:
                raise BackupError('backup_conflict')
            marker_path.unlink()
            sync_directory(profile.directory)
        return {'state': 'cancelled', 'backup_id': args.backup_id, 'partial_artifacts': 'preserved', 'service': 'stopped'}
    from .desktop_restore import recovery_status

    return recovery_status(args.directory)
