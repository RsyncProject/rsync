#!/usr/bin/env python3

import errno
import os
import stat

from harness.rsync import FROMDIR, SCRATCHDIR, TODIR, assert_mode, run_rsync, test_fail, test_skipped

error = 'error occurred during test of symlink access change capability: '
notimplemented = 'symlink Unix access change is not implemented on this system'
notsupported = 'symlink Unix access change is not supported on this system'

os.chdir(SCRATCHDIR)

FROMDIR.mkdir(parents=True, exist_ok=True)
TODIR.mkdir(parents=True, exist_ok=True)

testdirectory = 'testdirectory'
testfile = 'testfile'

(FROMDIR / testdirectory).mkdir(parents=True)
(FROMDIR / testfile).touch()

os.chdir(FROMDIR)

os.symlink(testdirectory, 'symlink', target_is_directory=True)

try:
    os.chmod('symlink', stat.S_IREAD + stat.S_IXOTH, follow_symlinks=False)

except NotImplementedError:
    test_skipped(notimplemented, capability='symlink_mode')

except OSError as e:
    if (e.errno == errno.ENOTSUP):
        test_skipped(notsupported, capability='symlink_mode')
    test_fail('OS' + error + f'{e}')

assert_mode('symlink', stat.S_IREAD + stat.S_IXOTH)

os.remove('symlink')

os.chdir(SCRATCHDIR)

symlinks = [
    [ testdirectory, 0o770 ],
    [ testfile, 0o0444 ],
    [ testfile, 0o0711 ],
    [ testfile, 0o0777 ],
    [ testdirectory, 0o7777 ],
    [ testfile, 0o0400 ],
    [ testdirectory, 0o1407 ],
    [ testdirectory, 0o2470 ],
    [ testdirectory, 0o4777 ],
    [ testfile, 0o7777 ],
]

for n, symlink in enumerate(symlinks):
    target, access = symlink

    os.symlink(target, FROMDIR / f'symlink-{n}', target_is_directory=(target == testdirectory))
    os.chmod(FROMDIR / f'symlink-{n}', access, follow_symlinks=False)

run_rsync('-av', f'{FROMDIR}/', f'{TODIR}/')

for n, symlink in enumerate(symlinks):
    expected = stat.S_IMODE(os.stat(FROMDIR / f'symlink-{n}', follow_symlinks=False).st_mode)
    assert_mode(TODIR / f'symlink-{n}', expected)
