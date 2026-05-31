#!/usr/bin/env python3
# Python rewrite of testsuite/files-from.test.
#
# Verify that --files-from=LIST drives rsync correctly both for a plain
# local sync and across the lsh.sh "remote shell" with each of the four
# files-host / src-host / dest-host placement combinations.

from rsyncfns import (
    CHKDIR, FROMDIR, RSYNC, RSYNC_PEER, SCRATCHDIR, SRCDIR, TODIR,
    checkit, hands_setup, rmtree, run_rsync,
)


SSH = str(SRCDIR / 'support' / 'lsh.sh')

hands_setup()

# Files-from list: skip the contents of subsubdir but include subsubdir2/
# in full (the trailing slash on subsubdir2/ is what flips it).
filelist = SCRATCHDIR / 'filelist'
filelist.write_text(
    "from/./\n"
    "from/./dir/subdir\n"
    "from/./dir/subdir/subsubdir\n"
    "from/./dir/subdir/subsubdir2/\n"
    "from/./dir/subdir/foobar.baz\n"
)

# chkdir is what we expect the transfer to produce: source minus
# dir/text and minus everything under subsubdir/.
run_rsync('-a', '--exclude=dir/text', '--exclude=subsubdir/**',
          f'{FROMDIR}/', f'{CHKDIR}/')

# Local case.
checkit(['-av', f'--files-from={filelist}', str(SCRATCHDIR), f'{TODIR}/'],
        CHKDIR, TODIR)

# All four combinations of files-host / source-host / dest-host across the
# lsh.sh "remote shell".  In each loop iteration exactly one of source or
# dest is remote (matches the original test's branch logic).
for filehost in ('', 'localhost:'):
    for srchost in ('', 'localhost:'):
        desthost = 'localhost:' if not srchost else ''

        rmtree(TODIR)
        checkit(
            ['-avse', SSH, f'--rsync-path={RSYNC_PEER}',
             f'--files-from={filehost}{filelist}',
             f'{srchost}{SCRATCHDIR}', f'{desthost}{TODIR}/'],
            CHKDIR, TODIR,
        )
