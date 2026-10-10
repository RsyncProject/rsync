#!/usr/bin/env python3

from harness.rsync import (
    CHKDIR,
    FROMDIR,
    SCRATCHDIR,
    TODIR,
    checkit,
    hands_setup,
    rmtree,
    run_rsync,
    rsync_path_arg,
    rsh_cmd,
)
from harness import metadata

metadata(features={'files-from', 'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process'}, tags={'compatibility', 'transfer', 'version-mix'})

SSH = rsh_cmd()

hands_setup()

filelist = SCRATCHDIR / 'filelist'
filelist.write_text(
    "from/./\n"
    "from/./dir/subdir\n"
    "from/./dir/subdir/subsubdir\n"
    "from/./dir/subdir/subsubdir2/\n"
    "from/./dir/subdir/foobar.baz\n"
)

run_rsync('-a', '--exclude=dir/text', '--exclude=subsubdir/**',
          f'{FROMDIR}/', f'{CHKDIR}/')

checkit(['-av', f'--files-from={filelist}', str(SCRATCHDIR), f'{TODIR}/'],
        CHKDIR, TODIR)

for filehost in ('', 'localhost:'):
    for srchost in ('', 'localhost:'):
        desthost = 'localhost:' if not srchost else ''

        rmtree(TODIR)
        checkit(
            ['-avse', SSH, f'--rsync-path={rsync_path_arg()}',
             f'--files-from={filehost}{filelist}',
             f'{srchost}{SCRATCHDIR}', f'{desthost}{TODIR}/'],
            CHKDIR, TODIR,
        )
