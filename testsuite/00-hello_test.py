#!/usr/bin/env python3
# Python rewrite of testsuite/00-hello.test.
#
# Foundational smoke test: --version / --info=help / --debug=help all
# work, plus a round-trip transfer of a directory whose name contains
# shell-special characters via the lsh.sh remote-shell stand-in.

import os

from rsyncfns import (
    FROMDIR, RSYNC, RSYNC_PEER, SRCDIR, TODIR,
    checkit, run_rsync, test_fail,
)


# Set RSYNC_RSH so rsync picks up lsh.sh for the "lh:" hosts below.
os.environ['RSYNC_RSH'] = str(SRCDIR / 'support' / 'lsh.sh')

# Basic help dumps must not crash.
if run_rsync('--version', check=False).returncode != 0:
    test_fail('--version output failed')
if run_rsync('--info=help', check=False).returncode != 0:
    test_fail('--info=help output failed')
if run_rsync('--debug=help', check=False).returncode != 0:
    test_fail('--debug=help output failed')

weird_name = "A weird)name"

FROMDIR.mkdir(parents=True, exist_ok=True)
weird_dir = FROMDIR / weird_name
weird_dir.mkdir()


def append_line(line: str) -> None:
    print(line)
    with open(weird_dir / 'file', 'a') as f:
        f.write(line + '\n')


def copy_weird(args: list, src_host: str, dst_host: str) -> None:
    checkit(
        [*args, f'--rsync-path={RSYNC_PEER}',
         f'{src_host}{weird_dir}/',
         f'{dst_host}{TODIR / weird_name}'],
        FROMDIR, TODIR,
    )


append_line('test1')
checkit(['-ai', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

append_line('test2')
copy_weird(['-ai'], 'lh:', '')

append_line('test3')
copy_weird(['-ai'], '', 'lh:')

append_line('test4')
copy_weird(['-ais'], 'lh:', '')

append_line('test5')
copy_weird(['-ais'], '', 'lh:')

# test6: --old-args lets two whitespace-separated names go through as a
# single "one two" remote argument to be re-split by the remote shell.
print('test6')
(FROMDIR / 'one').touch()
(FROMDIR / 'two').touch()

saved = os.getcwd()
os.chdir(FROMDIR)
try:
    run_rsync('-ai', '--old-args', f'--rsync-path={RSYNC_PEER}',
              'lh:one two', f'{TODIR}/')
finally:
    os.chdir(saved)

if not (TODIR / 'one').is_file() or not (TODIR / 'two').is_file():
    test_fail("old-args copy of 'one two' failed")

# test7: the RSYNC_OLD_ARGS=1 env var should be equivalent to --old-args.
print('test7')
(TODIR / 'one').unlink()
(TODIR / 'two').unlink()

env = os.environ.copy()
env['RSYNC_OLD_ARGS'] = '1'
import subprocess
from rsyncfns import rsync_argv

os.chdir(FROMDIR)
try:
    subprocess.run(
        rsync_argv('-ai', f'--rsync-path={RSYNC_PEER}',
                   'lh:one two', f'{TODIR}/'),
        env=env, check=True,
    )
finally:
    os.chdir(saved)

# check=True only proves a zero exit; confirm the env-var path actually copied
# both files (as the explicit --old-args case above does).
if not (TODIR / 'one').is_file() or not (TODIR / 'two').is_file():
    test_fail("RSYNC_OLD_ARGS=1 copy of 'one two' failed")
