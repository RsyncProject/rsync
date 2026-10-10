#!/usr/bin/env python3

import os
import shutil

from harness.daemon_config import setup_chroot_inner
from harness.process import capture_command
from harness.rsync import makepath, rsync_argv, test_fail

for option in ('compare', 'copy', 'link'):
    base, inner, outside, source, url = setup_chroot_inner(f'chroot-{option}-dest-inner')
    makepath(inner / 'destination')
    (source / 'file').write_text('source\n')
    shutil.copy2(source / 'file', outside / 'file')
    if option == 'copy':
        (outside / 'file').write_text('escape\n')
        mtime = (source / 'file').stat().st_mtime
        os.utime(outside / 'file', (mtime, mtime))
    _, output = capture_command(rsync_argv('-a', f'--{option}-dest=../linkparent',
                                           f'{source}/', f'{url}mod/destination/'))
    result = inner / 'destination' / 'file'
    if not result.exists():
        test_fail(f'{option}-dest suppressed destination creation:\n{output}')
    if option == 'copy' and result.read_text() != 'source\n':
        test_fail(f'copy-dest read an outside basis:\n{output}')
    if option == 'link' and result.stat().st_ino == (outside / 'file').stat().st_ino:
        test_fail(f'link-dest linked an outside basis:\n{output}')

print('alternate destinations remain inside the chroot module')
