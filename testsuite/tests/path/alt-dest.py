#!/usr/bin/env python3

import os

from fixtures.trees import hands_setup
from harness.comparison import checkit
from harness.environment import CHKDIR, FROMDIR, TMPDIR, TODIR
from harness.filesystem import rmtree
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_path_arg, run_rsync
from harness import metadata

metadata(features={'alt-dest', 'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='3.0.0', mutates={'filesystem', 'process'}, tags={'compatibility', 'transfer', 'version-mix'})

alt1dir = TMPDIR / 'alt1'
alt2dir = TMPDIR / 'alt2'
alt3dir = TMPDIR / 'alt3'

SSH = rsh_cmd()

hands_setup()

run_rsync('-av', '--include=text', '--include=*/', '--exclude=*',
          f'{FROMDIR}/', f'{alt1dir}/')
run_rsync('-av', '--include=etc-ltr-list', '--include=*/', '--exclude=*',
          f'{FROMDIR}/', f'{alt2dir}/')

(FROMDIR / 'likely').write_text("This is a test file\n")
alt3dir.mkdir()
(alt3dir / 'likely').write_text("This is a test file\n")

for path in (FROMDIR / 'dir' / 'text', FROMDIR / 'likely'):
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))

run_rsync('-av', '--exclude=/text', '--exclude=etc-ltr-list',
          f'{FROMDIR}/', f'{CHKDIR}/')

checkit(['-avv', '--no-whole-file',
         f'--compare-dest={alt1dir}', f'--compare-dest={alt2dir}',
         f'{FROMDIR}/', f'{TODIR}/'], CHKDIR, TODIR)

rmtree(TODIR)
checkit(['-avv', '--no-whole-file',
         f'--copy-dest={alt1dir}', f'--copy-dest={alt2dir}',
         f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

for maybe_inplace in ([], ['--inplace']):
    rmtree(TODIR)
    checkit(['-av', *maybe_inplace, f'--copy-dest={alt3dir}',
             f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)
    if os.stat(TODIR / 'likely').st_ino == os.stat(alt3dir / 'likely').st_ino:
        test_fail(f"--copy-dest{' --inplace' if maybe_inplace else ''} "
                  "hard-linked 'likely' instead of copying it")

    for srchost in ('', 'localhost:'):
        desthost = 'localhost:' if not srchost else ''
        rmtree(TODIR)
        checkit(['-ave', SSH, f'--rsync-path={rsync_path_arg()}', *maybe_inplace,
                 f'--copy-dest={alt3dir}',
                 f'{srchost}{FROMDIR}/', f'{desthost}{TODIR}/'],
                FROMDIR, TODIR)
