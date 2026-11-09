#!/usr/bin/env python3
"""Coverage: the generator/delete chmod-around-restrictive-perms paths.

generator.c gen_entry_chmod() and delete.c del_chmod() only fire when
`!am_root`: a non-root receiver creating files inside a directory whose
incoming mode lacks S_IRWXU has to temporarily widen it
(generator.c:~1725, restored at ~2387 in the retouch pass), and a
non-root receiver deleting a non-writable file it owns adds S_IWUSR
first (delete.c:~156).  Every other test runs the receiver as root in
the container, so these never trip.

Uses a daemon module with `uid = <non-root>` so the receiver child is
non-root; the dest tree is chowned to that uid up front.  Skips when
chown is unavailable.
"""

import os
import pwd
import stat
import subprocess

from rsyncfns import (
    SCRATCHDIR,
    makepath, owners_supported, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12893

if not owners_supported():
    test_skipped("needs chown to set up a non-root-owned dest tree")

U = next((p for p in pwd.getpwall() if p.pw_uid != 0), None)
if U is None:
    test_skipped("no non-root passwd entry")

src = SCRATCHDIR / 'nrp-src'
dest = SCRATCHDIR / 'nrp-dest'
rmtree(src); rmtree(dest); makepath(src, dest)
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


# --- A. gen_entry_chmod: receive into a dir whose mode lacks write ----------
# Source dir mode 0500 (r-x------): the non-root generator widens it to 0700
# while writing `inner`, then the retouch pass restores 0500.
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


# --- B. del_chmod: --delete a non-writable file the receiver owns -----------
# Dest already has restricted/inner (mode set by previous push).  Seed a
# second dir `gone/` with a 0444 file in it, owned by U; remove `gone/` from
# src and push --delete.  delete_dir_contents() walks gone/, sees ro_file
# with !(mode & S_IWUSR) && !am_root && FLAG_OWNED_BY_US -> del_chmod first.
gone = dest / 'gone'
gone.mkdir()
ro = gone / 'ro_file'
ro.write_bytes(b'x')
os.chmod(ro, 0o444)
os.chown(ro, U.pw_uid, U.pw_gid)
os.chown(gone, U.pw_uid, U.pw_gid)
# Widen restricted/ on the src side again so the generator re-exercises the
# 1725 widen + 2387 restore on a SECOND pass too (idempotence under -p).
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
