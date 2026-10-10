#!/usr/bin/env python3

import os
import re
import subprocess

from harness.rsync import (
    RSYNC_PREFIX, SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf, xattr_get, xattrs_supported,
)

DAEMON_PORT = 12904

if not xattrs_supported():
    test_skipped("namecvt-empty-response test requires xattr support to "
                 "read fake-super metadata", capability='xattr_runtime')

my_uid = os.getuid()
my_gid = os.getgid()
if my_uid == 0:
    test_skipped("namecvt-empty-response test must run as a non-root user "
                 "(sender uid/gid must differ from 0 to expose the bug)", capability='nonroot')

stub = SCRATCHDIR / 'stub_nameconvert'
stub.write_text(
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "for _ in sys.stdin:\n"
    "    print('', flush=True)\n"
)
stub.chmod(0o755)

mod = SCRATCHDIR / 'recvmod'
src = SCRATCHDIR / 'srcdir'
rmtree(mod)
rmtree(src)
makepath(mod)
makepath(src)
(src / 'f').write_text("DATA\n")

conf = write_daemon_conf([
    ('recv', {'path':           str(mod),
              'read only':      'no',
              'fake super':     'yes',
              'numeric ids':    'no',
              'name converter': str(stub)}),
])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(rsync_argv('-a', f'{src}/', f'{url}recv/'),
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.PIPE, text=True)
if proc.returncode not in (0, 23):
    test_fail(f"upload to recv module failed (rc={proc.returncode}): "
              f"{proc.stderr!r}")

stored = mod / 'f'
if not stored.is_file():
    test_fail(f"upload did not deliver the file ({stored})")

try:
    raw = xattr_get(RSYNC_PREFIX + '.%stat', stored)
    xval = raw.decode('utf-8', 'surrogateescape')
except OSError:
    xval = None

if xval is None:
    print(f'name converter kept sender uid={my_uid} after an empty response')
else:
    m = re.match(r'\S+\s+\S+\s+(\d+):(\d+)\s*$', xval)
    if not m:
        test_fail(f"unrecognised fake-super xattr format: {xval!r}")
    stored_uid, stored_gid = int(m.group(1)), int(m.group(2))

    if stored_uid == 0 and stored_gid == 0:
        test_fail(f'empty name-converter response mapped uid={my_uid}/gid={my_gid} '
                  f'to root metadata {xval!r}')

    print(f'empty name-converter response retained non-root metadata {xval!r}')
