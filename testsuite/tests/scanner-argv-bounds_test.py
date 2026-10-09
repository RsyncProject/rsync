#!/usr/bin/python3
# Regression tests for the run5 option/argv bound fixes: a hostile or
# wrapper-built command line must not overflow a fixed buffer, underflow a
# stack array, or exhaust the stack.  Each case used to crash (global-buffer-
# overflow / stack-buffer-underflow / stack exhaustion under ASAN) and now must
# complete a clean transfer.
import os
import subprocess
from rsyncfns import (
    FROMDIR, RSYNC_PEER, SRCDIR, SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail, rsync_path_arg, rsh_cmd,
)

LSH = rsh_cmd()
os.environ['RSYNC_RSH'] = LSH

src = FROMDIR
rmtree(src)
makepath(src)
(src / 'f').write_text('payload\n')


def run(label, extra, local=False):
    dest = SCRATCHDIR / ('dest-' + label)
    rmtree(dest)
    makepath(dest)
    if local:
        argv = rsync_argv('-a', *extra, f'{src}/', f'{dest}/')
    else:
        argv = rsync_argv('-a', *extra, '-e', LSH,
                          f'--rsync-path={rsync_path_arg()}', f'{src}/', f'localhost:{dest}/')
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode < 0 or r.returncode >= 128:
        test_fail(f'{label}: rsync crashed (rc={r.returncode}): {r.stderr.strip()[:200]}')
    if not (dest / 'f').exists() or (dest / 'f').read_text() != 'payload\n':
        test_fail(f'{label}: transfer did not complete (rc={r.returncode}): {r.stderr.strip()[:200]}')


# 0014: ~60 '-v' overran server_options()'s static argstr[64] (global-buffer-
# overflow WRITE) before the count was bounded.
run('verbose-flood', ['-v'] * 60)

# 0015: --info=NAME<overflowing-digits> -> atoi() returns a negative int on
# LP64 -> make_output_option() indexed counts[-1] (stack underflow).
run('info-overflow', ['--info=BACKUP99999999999999999999'])

# 0023: --skip-compress=<one very long token> recursed add_suffix() once per
# character, exhausting the stack; truncated to 32 chars now.
run('skip-compress-long', ['-z', '--skip-compress=' + 'a' * 5000], local=True)

print("scanner-argv-bounds: -v flood, --info overflow, --skip-compress long token all clean")
