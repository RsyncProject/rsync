#!/usr/bin/env python3
"""A confined fd pin must remain usable inside a Linux user namespace."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rsyncfns import makepath, rmtree, rsync_argv, test_fail, test_skipped


if not sys.platform.startswith('linux'):
    test_skipped('rrsync-userns-procfs is Linux-specific')

if not os.environ.get('RSYNC_USERNS_PROCFS'):
    unshare = shutil.which('unshare')
    if unshare is None:
        test_skipped('unshare is unavailable')
    env = os.environ.copy()
    env['RSYNC_USERNS_PROCFS'] = '1'
    launch_dir = Path(tempfile.mkdtemp(prefix='rsync-userns-launch-'))
    launch_dir.chmod(0o755)
    testdir = Path(__file__).resolve().parent
    child_test = launch_dir / Path(__file__).name
    for source in (Path(__file__), testdir / 'rsyncfns.py',
                   testdir / 'exitcodes.py'):
        shutil.copy2(source, launch_dir / source.name)
    rsync_cmd = shlex.split(env['RSYNC'])
    for i, arg in enumerate(rsync_cmd):
        if Path(arg).name in ('rsync', 'rsync.exe') and Path(arg).is_file():
            staged_rsync = launch_dir / Path(arg).name
            shutil.copy2(arg, staged_rsync)
            staged_rsync.chmod(0o755)
            rsync_cmd[i] = str(staged_rsync)
            break
    else:
        rmtree(launch_dir)
        test_fail(f'cannot locate the rsync executable in {env["RSYNC"]!r}')
    env['RSYNC'] = shlex.join(rsync_cmd)
    launcher = []
    if os.geteuid() == 0:
        setpriv = shutil.which('setpriv')
        if setpriv is None:
            test_skipped('setpriv is unavailable for the root-run testsuite')
        launcher = [setpriv, '--reuid=65534', '--regid=65534', '--clear-groups']
    unshare_argv = [unshare, '--user', '--map-root-user', '--mount', '--pid',
                    '--fork', '--mount-proc']
    probe = subprocess.run(
        launcher + unshare_argv + ['true'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode != 0:
        rmtree(launch_dir)
        print(f'user namespaces unavailable (rc={probe.returncode})')
        raise SystemExit(0)
    try:
        proc = subprocess.run(
            launcher + unshare_argv
            + [sys.executable, str(child_test)],
            env=env,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        test_fail('user-namespace regression test timed out')
    finally:
        rmtree(launch_dir)
    if proc.returncode != 0:
        test_fail(f'user-namespace regression test failed (rc={proc.returncode})')
    print('rrsync fd pin works inside a user namespace')
    raise SystemExit(0)

proc_uid = os.lstat('/proc/self').st_uid
if proc_uid in (0, os.geteuid()):
    test_skipped('/proc/self does not expose an overflow uid in this namespace')

base = Path(tempfile.mkdtemp(prefix='rsync-userns-procfs-'))
root = base / 'root'
src = root / 'src'
dest = root / 'dest'
outside = base / 'outside'
makepath(src, dest, outside)
(src / 'file').write_text('content\n')

fd_roots = ['/proc/self/fd']
if Path('/dev/fd').exists():
    fd_roots.append('/dev/fd')

dest_fd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY)
try:
    for index, fd_root in enumerate(fd_roots):
        log_file = dest / f'rsync-{index}.log'
        proc = subprocess.run(
            rsync_argv('-a', f'--confine-root={dest}',
                       f'--log-file={fd_root}/{dest_fd}/{log_file.name}',
                       str(src) + '/', str(dest) + '/'),
            pass_fds=(dest_fd,),
            capture_output=True,
            text=True,
        )
        ctx = (f'fd_root={fd_root!r}, rc={proc.returncode}, '
               f'stderr={proc.stderr.strip()[:300]!r}')
        if proc.returncode != 0:
            test_fail(f'confined transfer through an fd pin failed ({ctx})')
        if not log_file.is_file():
            test_fail(f'confined log path through an fd pin was rejected ({ctx})')
        if (dest / 'file').read_text() != 'content\n':
            test_fail(f'confined transfer did not deliver the file ({ctx})')
finally:
    os.close(dest_fd)

backup_dir = root / 'backup'
backup_dir.symlink_to(outside)
(dest / 'test').write_text('old_content\n')
(src / 'test').write_text('new_different_content\n')

(outside / 'test').write_text('existing_backup\n')

# Open an FD pointing to the confined root
root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
try:
    for fd_root in fd_roots:
        proc = subprocess.run(
            rsync_argv('-a', '--backup', f'--confine-root={root}',
                       f'--backup-dir={fd_root}/{root_fd}/backup/',
                       str(src) + '/', str(dest) + '/'),
            pass_fds=(root_fd,),
            capture_output=True,
            text=True,
        )

        # The transfer must fail because rsync attempts to unlink the pre-existing target file,
        # forcing the path-walker to evaluate the FD pin + symlink and catching the escape.
        if proc.returncode == 0:
            test_fail(f'backup to outside symlink via {fd_root} pin was not observably refused: '
                      f'rc={proc.returncode}, stderr={proc.stderr!r}')
finally:
    os.close(root_fd)

rmtree(base)

