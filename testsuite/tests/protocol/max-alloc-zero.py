#!/usr/bin/env python3

import os
import shlex

from harness.rsync import (
    SCRATCHDIR, SRCDIR, expect_fail, rsh_cmd, rmtree, rsync_argv,
    rsync_path_arg, run_rsync, test_fail,
)

base = SCRATCHDIR / 'max-alloc-zero'
rmtree(base)
src = base / 'from'
dst = base / 'to'
src.mkdir(parents=True)
dst.mkdir(parents=True)
(src / 'file.txt').write_text('hello\n')

run_rsync('-r', '--max-alloc=0', f'{src}/', f'{dst}/')
if (dst / 'file.txt').read_text() != 'hello\n':
    test_fail('--max-alloc=0 did not copy the file')

argv_log = base / 'server-argv'
wrapper = base / 'log-rsh.sh'
wrapper.write_text(
    '#!/bin/sh\n'
    f'printf \'%s\\n\' "$*" >> {shlex.quote(str(argv_log))}\n'
    f'exec {shlex.quote(str(SRCDIR / "support" / "lsh.sh"))} "$@"\n'
)
wrapper.chmod(0o755)

rmtree(dst)
dst.mkdir()
os.environ['RSYNC_RSH'] = rsh_cmd(str(wrapper))
run_rsync('-r', '--max-alloc=0', f'--rsync-path={rsync_path_arg()}',
          f'localhost:{src}/', f'{dst}/')
del os.environ['RSYNC_RSH']

if (dst / 'file.txt').read_text() != 'hello\n':
    test_fail('--max-alloc=0 did not copy the file over the remote shell')

logged = argv_log.read_text() if argv_log.exists() else ''
if not logged:
    test_fail('the remote-shell wrapper logged no command line')
if '--max-alloc=0' not in f' {logged} '.replace('\n', ' '):
    test_fail('--max-alloc=0 was not forwarded verbatim; the peer was sent:\n'
              f'{logged}'
              '\nA resolved number here would be rejected as "too large" by a '
              'peer with a smaller SIZE_MAX.')

expect_fail(rsync_argv('--max-alloc=8192P', f'{src}/', f'{dst}/'), 'is too large')

expect_fail(rsync_argv('--max-alloc=1', f'{src}/', f'{dst}/'),
            'or 0 for unlimited')

print('max-alloc-zero: 0 is accepted, forwarded verbatim, and the bound holds')
