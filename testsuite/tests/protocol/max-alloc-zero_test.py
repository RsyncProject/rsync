#!/usr/bin/env python3
"""``--max-alloc=0`` means "the largest limit this build supports".

Three things are asserted, and the second is the reason 0 is worth keeping as a
spelling at all:

  1. 0 is accepted, and a transfer using it works.

  2. 0 reaches the peer as the literal "0", not as a resolved number.  Each side
     then resolves it against its own SIZE_MAX.  That is what makes 0 the only
     value correct for both ends of a mixed-word-size pairing: the ceiling is
     SIZE_MAX/2, so any number large enough to be worth setting on a 64-bit
     client (over 2047M) is refused as "too large" by a 32-bit daemon.

  3. The parser's upper bound is still enforced.  Accepting 0 again must not
     bring back the unbounded ``size *= atof(size_arg)`` that was fixed in
     3.5.0, so an out-of-range value is still rejected rather than wrapping.

The forwarding check in (2) deliberately inspects the argv the remote shell is
handed rather than a transfer outcome: a resolved number also copies files
happily on a same-word-size pair, so an outcome-based assertion would pass on
exactly the configuration this behaviour does not matter for.
"""

import os
import shlex

from rsyncfns import (
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

# --- 1. 0 is accepted -------------------------------------------------------

run_rsync('-r', '--max-alloc=0', f'{src}/', f'{dst}/')
if (dst / 'file.txt').read_text() != 'hello\n':
    test_fail('--max-alloc=0 did not copy the file')

# --- 2. 0 goes on the wire un-normalized ------------------------------------

argv_log = base / 'server-argv'
wrapper = base / 'log-rsh.sh'
wrapper.write_text(
    '#!/bin/sh\n'
    '# Log the command line built for the peer, then behave like lsh.sh.\n'
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

# --- 3. the upper bound still holds -----------------------------------------

# 8192P is one step past SIZE_ARG_MAX (SIZE_MAX/2) on a 64-bit build; on a
# 32-bit one the P multiplier alone already exceeds it.  Either way: too large.
expect_fail(rsync_argv('--max-alloc=8192P', f'{src}/', f'{dst}/'), 'is too large')

# And the min-value message must keep advertising a spelling that works.
expect_fail(rsync_argv('--max-alloc=1', f'{src}/', f'{dst}/'),
            'or 0 for unlimited')

print('max-alloc-zero: 0 is accepted, forwarded verbatim, and the bound holds')
