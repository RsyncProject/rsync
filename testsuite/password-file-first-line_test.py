#!/usr/bin/env python3
"""Only the first line of --password-file is used, for files and stdin."""

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12904
USER = 'tuser'
PASSWORD = b'secretpass'

base = SCRATCHDIR / 'password-file-first-line'
rmtree(base)
src = base / 'src'
dest = base / 'dest'
makepath(src)
(src / 'payload').write_text('regression test\n')
secrets = base / 'secrets'
secrets.write_bytes(USER.encode() + b':' + PASSWORD + b'\n')
secrets.chmod(0o600)
conf = write_daemon_conf([
    ('auth', {'path': dest, 'read only': 'no',
              'auth users': USER, 'secrets file': secrets}),
])
url = start_test_daemon(conf, DAEMON_PORT)
userurl = url.replace('rsync://', f'rsync://{USER}@', 1)

cases = (
    ('LF', PASSWORD + b'\nignored\n', True),
    ('CRLF', PASSWORD + b'\r\nignored\r\n', True),
    ('empty-file', b'', False),
    ('empty-first-line', b'\n' + PASSWORD + b'\n', False),
    ('no-final-newline', PASSWORD, True),
)

for source in ('file', 'stdin'):
    for name, contents, succeeds in cases:
        rmtree(dest)
        makepath(dest)
        args = ['-a', f'{src}/', f'{userurl}auth/']
        if source == 'file':
            pwfile = base / f'{name}.password'
            pwfile.write_bytes(contents)
            pwfile.chmod(0o600)
            args.insert(1, f'--password-file={pwfile}')
            stdin = subprocess.DEVNULL
        else:
            args.insert(1, '--password-file=-')
            stdin = subprocess.PIPE
        proc = subprocess.run(rsync_argv(*args), input=contents if source == 'stdin' else None,
                              stdin=stdin if source == 'file' else None,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=False, timeout=20,
                              env={**os.environ, 'RSYNC_PASSWORD': 'wrong-fallback'})
        passed = proc.returncode == 0
        if passed != succeeds:
            test_fail(f'{source}/{name}: expected success={succeeds}, '
                      f'rc={proc.returncode}, stderr={proc.stderr!r}')

print('password-file: first-line behavior matches for files and stdin')
