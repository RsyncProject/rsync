#!/usr/bin/env python3

import ctypes
import grp
import os
import pwd
import stat

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.results import test_fail, test_skipped
from harness.rsync import run_rsync, under_valgrind


@requires(features={'chroot', 'root'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'daemon', 'regression'})
def test(context: TestContext):
    if os.geteuid() != 0:
        test_skipped('requires root to chroot and select the module uid', capability='root')
    if under_valgrind():
        test_skipped('daemon chroot hides Valgrind log files', capability='chroot')
    pid = os.fork()
    if pid == 0:
        os._exit(0 if ctypes.CDLL(None, use_errno=True).chroot(b'/') == 0 else 1)
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status):
        test_skipped('chroot is unavailable', capability='chroot')

    account = pwd.getpwnam('nobody')
    if account.pw_uid < 0 or account.pw_gid < 0:
        account = pwd.getpwnam('daemon')
    group = grp.getgrgid(account.pw_gid).gr_name
    for scope in ('module', 'daemon'):
        base = context.scratch / scope
        source = base / 'source'
        root = base / 'root'
        module = base / 'module' if scope == 'module' else root / 'module'
        module_path = str(module) if scope == 'module' else '/module'
        backup_path = '/backups'
        original = module / 'dest' / 'outer' / 'old'
        saved = module / 'backups' / 'outer'
        source.mkdir(parents=True)
        original.parent.mkdir(parents=True)
        saved.mkdir(parents=True)
        original.write_bytes(b'previous contents')
        original_mode = stat.S_IMODE(original.parent.stat().st_mode)
        for path in (module, module / 'dest', original.parent, original,
                     module / 'backups', saved):
            os.chown(path, account.pw_uid, account.pw_gid)
        saved.chmod(0o555)
        if (module / 'tmp').exists() or (scope == 'daemon' and (root / 'tmp').exists()):
            test_fail('the chroot unexpectedly contains a temporary directory')

        config = write_daemon_conf([('files', {
            'path': module_path, 'use chroot': 'yes' if scope == 'module' else 'no',
            'read only': 'no',
            'uid': account.pw_name if scope == 'module' else str(account.pw_uid),
            'gid': group if scope == 'module' else str(account.pw_gid),
        })], global_options={'daemon chroot': str(root)} if scope == 'daemon' else None,
            name=f'{scope}-chroot.conf')
        url = start_test_daemon(config, 12977)
        result = run_rsync('-a', '--delete-during', '--backup',
                           f'--backup-dir={backup_path}', f'{source}/',
                           f'{url}files/dest/', capture_output=True, check=False)
        if result.returncode:
            test_fail(f'{scope} chroot backup exited {result.returncode}: {result.stderr}')
        if original.exists() or (saved / 'old').read_bytes() != b'previous contents':
            test_fail(f'{scope} chroot did not save the deleted file')
        if stat.S_IMODE(saved.stat().st_mode) != original_mode:
            test_fail(f'{scope} chroot did not preserve the saved directory mode')

        old_data = b'a' * 700 + b'b' * 700 + b'c' * 700
        new_data = b'b' * 700 + b'd' * 700 + b'a' * 700 + b'!'
        source_file = source / 'outer' / 'current'
        dest_file = original.parent / 'current'
        source_file.parent.mkdir()
        dest_file.parent.mkdir()
        source_file.write_bytes(new_data)
        dest_file.write_bytes(old_data)
        for path in (dest_file.parent, dest_file):
            os.chown(path, account.pw_uid, account.pw_gid)
        relative_saved = module / 'dest' / 'backups' / 'outer'
        relative_saved.mkdir(parents=True)
        for path in (relative_saved.parent, relative_saved):
            os.chown(path, account.pw_uid, account.pw_gid)
        relative_saved.chmod(0o400)
        result = run_rsync('-a', '--inplace', '--no-whole-file',
                           '--backup-dir=backups', f'{source}/',
                           f'{url}files/dest/', capture_output=True, check=False)
        if result.returncode:
            test_fail(f'{scope} chroot delta exited {result.returncode}: {result.stderr}')
        if dest_file.read_bytes() != new_data or (relative_saved / 'current').read_bytes() != old_data:
            test_fail(f'{scope} chroot delta lost the current file or its backup')
        if stat.S_IMODE(relative_saved.stat().st_mode) != 0o400:
            test_fail(f'{scope} chroot delta changed the saved directory mode')


if __name__ == '__main__':
    run(test)
