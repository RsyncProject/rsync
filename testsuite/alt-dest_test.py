#!/usr/bin/env python3
# Python rewrite of testsuite/alt-dest.test.
#
# Exercise rsync's --compare-dest, --copy-dest and --link-dest
# alternative-destination options, both locally and across the lsh.sh
# remote-shell stand-in. Also covers the tmpfile path in copy_file() by
# pointing --copy-dest at a directory holding a same-name candidate.

import os
import shutil
import time

from rsyncfns import (
    CHKDIR, FROMDIR, RSYNC, RSYNC_PEER, SCRATCHDIR, SRCDIR, TMPDIR, TODIR,
    checkit, hands_setup, rmtree, run_rsync, test_fail,
)


alt1dir = TMPDIR / 'alt1'
alt2dir = TMPDIR / 'alt2'
alt3dir = TMPDIR / 'alt3'

SSH = str(SRCDIR / 'support' / 'lsh.sh')

hands_setup()

# Seed alt1 and alt2 with disjoint single-file subtrees of fromdir.
run_rsync('-av', '--include=text', '--include=*/', '--exclude=*',
          f'{FROMDIR}/', f'{alt1dir}/')
run_rsync('-av', '--include=etc-ltr-list', '--include=*/', '--exclude=*',
          f'{FROMDIR}/', f'{alt2dir}/')

# Create a side dir with one identically-named candidate so copy_file()'s
# tmpfile path gets exercised.
(FROMDIR / 'likely').write_text("This is a test file\n")
alt3dir.mkdir()
(alt3dir / 'likely').write_text("This is a test file\n")

time.sleep(1)
os.utime(FROMDIR / 'dir' / 'text')
os.utime(FROMDIR / 'likely')

# chkdir: what a vanilla copy would produce, minus /text and etc-ltr-list.
run_rsync('-av', '--exclude=/text', '--exclude=etc-ltr-list',
          f'{FROMDIR}/', f'{CHKDIR}/')

# Stacked --compare-dest: dest grows just the deltas alt1+alt2 don't have.
checkit(['-avv', '--no-whole-file',
         f'--compare-dest={alt1dir}', f'--compare-dest={alt2dir}',
         f'{FROMDIR}/', f'{TODIR}/'], CHKDIR, TODIR)

rmtree(TODIR)
# Stacked --copy-dest: dest gets full copy because content can be hardlinked
# from the alt dirs where available.
checkit(['-avv', '--no-whole-file',
         f'--copy-dest={alt1dir}', f'--copy-dest={alt2dir}',
         f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

# Test that copy_file() works correctly with tmpfiles. Combine each of
# {direct, --inplace} with each of {local, remote-source, remote-dest}.
for maybe_inplace in ([], ['--inplace']):
    rmtree(TODIR)
    checkit(['-av', *maybe_inplace, f'--copy-dest={alt3dir}',
             f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)
    # --copy-dest must COPY the unchanged candidate, not hard-link it: the
    # result is a distinct inode from the alt-dir source (this is the property
    # that distinguishes --copy-dest from --link-dest, which checkit's tree
    # comparison alone does not capture).
    if os.stat(TODIR / 'likely').st_ino == os.stat(alt3dir / 'likely').st_ino:
        test_fail(f"--copy-dest{' --inplace' if maybe_inplace else ''} "
                  "hard-linked 'likely' instead of copying it")

    for srchost in ('', 'localhost:'):
        desthost = 'localhost:' if not srchost else ''
        rmtree(TODIR)
        checkit(['-ave', SSH, f'--rsync-path={RSYNC_PEER}', *maybe_inplace,
                 f'--copy-dest={alt3dir}',
                 f'{srchost}{FROMDIR}/', f'{desthost}{TODIR}/'],
                FROMDIR, TODIR)
