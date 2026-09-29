#!/usr/bin/env python3
"""--backup, --backup-dir and --suffix, including at depth.

test_options.py already covers plain --backup on a top-level file.  What the
upstream backup and backup-deep tests add is the directory form, where the
old copy is moved into a parallel tree -- so the receiver has to build the
backup path alongside the destination path, which is the part worth checking
on a platform whose separators differ from the wire's.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

from wintest import SCRATCH, check, ok, rewrite, rsync, write

src = SCRATCH / 'from'
dst = SCRATCH / 'to'
write(src / 'top.txt', 'top v1\n')
write(src / 'a' / 'one.txt', 'one v1\n')
write(src / 'a' / 'b' / 'deep.txt', 'deep v1\n')

rsync('-rt', f'{src}/', f'{dst}/')

# --- --backup-dir keeps the old copies in a parallel tree -----------------
backups = SCRATCH / 'backups'
rewrite(src / 'top.txt', 'top v2\n')
rewrite(src / 'a' / 'one.txt', 'one v2\n')
rewrite(src / 'a' / 'b' / 'deep.txt', 'deep v2\n')

rsync('-rt', '--backup', f'--backup-dir={backups}', f'{src}/', f'{dst}/')

check((dst / 'a' / 'b' / 'deep.txt').read_text() == 'deep v2\n',
      '--backup-dir did not install the new version at depth')
check((backups / 'top.txt').read_text() == 'top v1\n',
      '--backup-dir did not preserve the top-level file')
check((backups / 'a' / 'one.txt').read_text() == 'one v1\n',
      '--backup-dir did not reproduce the a/ path for the backup')
check((backups / 'a' / 'b' / 'deep.txt').read_text() == 'deep v1\n',
      '--backup-dir did not reproduce the a/b/ path for the backup')
# With a backup dir there is no ~ suffix.
check(not (dst / 'top.txt~').exists(),
      '--backup-dir should not also leave a ~ file at the destination')

# --- --suffix --------------------------------------------------------------
sdst = SCRATCH / 'to-suffix'
rsync('-rt', f'{src}/', f'{sdst}/')
rewrite(src / 'top.txt', 'top v3\n')
rsync('-rt', '--backup', '--suffix=.bak', f'{src}/', f'{sdst}/')
check((sdst / 'top.txt').read_text() == 'top v3\n',
      '--suffix run did not install the new version')
check((sdst / 'top.txt.bak').read_text() == 'top v2\n',
      '--suffix did not name the backup top.txt.bak')
check(not (sdst / 'top.txt~').exists(),
      '--suffix left a ~ backup as well')

# --- --backup-dir with a suffix ------------------------------------------
b2 = SCRATCH / 'backups2'
rewrite(src / 'a' / 'b' / 'deep.txt', 'deep v3\n')
rsync('-rt', '--backup', f'--backup-dir={b2}', '--suffix=.old',
      f'{src}/', f'{dst}/')
check((b2 / 'a' / 'b' / 'deep.txt.old').read_text() == 'deep v2\n',
      '--backup-dir with --suffix did not name the backup deep.txt.old')

# --- --backup rescues a file that --delete would remove ------------------
# This is the combination that makes --backup-dir worth having: the deleted
# file must end up in the backup tree rather than simply going away.
ddst = SCRATCH / 'to-del'
rsync('-rt', f'{src}/', f'{ddst}/')
write(ddst / 'obsolete.txt', 'no longer in the source\n')
b3 = SCRATCH / 'backups3'
rsync('-rt', '--delete', '--backup', f'--backup-dir={b3}', f'{src}/', f'{ddst}/')
check(not (ddst / 'obsolete.txt').exists(),
      '--delete did not remove the extraneous file')
check((b3 / 'obsolete.txt').read_text() == 'no longer in the source\n',
      '--backup-dir did not rescue the deleted file')

ok('--backup-dir at depth, --suffix and backup-on-delete all correct')
