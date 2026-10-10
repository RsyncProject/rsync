#!/usr/bin/env python3

import os
import pwd
import stat
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, makepath, owners_supported, rmtree, rsync_argv, start_test_daemon, test_fail,
    test_skipped,
)

DAEMON_PORT = 12893

if not owners_supported():
    test_skipped("needs chown to set up a non-root-owned dest tree", capability='ownership')

U = next((p for p in pwd.getpwall() if p.pw_uid != 0), None)
if U is None:
    test_skipped("no non-root passwd entry", capability='ownership')

src = SCRATCHDIR / 'nrp-src'
dest = SCRATCHDIR / 'nrp-dest'
rmtree(src)
rmtree(dest)
makepath(src, dest)
os.chown(dest, U.pw_uid, U.pw_gid)

conf = write_daemon_conf([
    ('nrp', {
        'path': str(dest), 'read only': 'no', 'use chroot': 'no',
        'uid': str(U.pw_uid), 'gid': str(U.pw_gid),
    }),
], name='nonroot-perms.conf')
url = start_test_daemon(conf, DAEMON_PORT)

def push(*extra):
    r = subprocess.run(
        rsync_argv('-rp', *extra, f'{src}/', f'{url}nrp/'),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        test_fail(f"push -rp {' '.join(extra)} -> rc={r.returncode}\n{r.stderr}")
    return r

(src / 'restricted').mkdir()
(src / 'restricted' / 'inner').write_bytes(b'x')
os.chmod(src / 'restricted', 0o500)

push()

st = os.stat(dest / 'restricted')
if stat.S_IMODE(st.st_mode) != 0o500:
    test_fail(f"restricted/ perms not restored after retouch: "
              f"{oct(stat.S_IMODE(st.st_mode))} (expected 0o500)")
if not (dest / 'restricted' / 'inner').is_file():
    test_fail("restricted/inner not received (gen_entry_chmod widen failed?)")
if st.st_uid != U.pw_uid:
    test_fail(f"restricted/ not owned by the module uid ({st.st_uid} != {U.pw_uid})")

gone = dest / 'gone'
gone.mkdir()
ro = gone / 'ro_file'
ro.write_bytes(b'x')
os.chmod(ro, 0o444)
os.chown(ro, U.pw_uid, U.pw_gid)
os.chown(gone, U.pw_uid, U.pw_gid)
os.chmod(src / 'restricted', 0o500)

push('--delete')

if (dest / 'gone').exists():
    test_fail("--delete did not remove gone/ (del_chmod path)")
if not (dest / 'restricted' / 'inner').is_file():
    test_fail("restricted/inner lost on the second pass")
if stat.S_IMODE(os.stat(dest / 'restricted').st_mode) != 0o500:
    test_fail("restricted/ perms not re-restored on the second pass")

print(f"nonroot-restrictive-perms: gen_entry_chmod widen+restore (0500 dir), "
      f"del_chmod on 0444 file, as uid={U.pw_uid}({U.pw_name})")
