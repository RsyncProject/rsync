#!/usr/bin/env python3
# Daemon-side `name converter` empty-line response is treated as success
# with id=0, mapping unknown sender names to uid/gid 0.
#
# Threat (Mitchell Benjamin report, May 2026):
#   The documented helper contract (support/nameconvert:14) is
#       "An unknown ID_NUM or NAME results in an empty return value."
#   But namecvt_call() in clientserver.c:1227 does:
#       if (!read_line_old(namecvt_fd_ans, buf, sizeof buf, 0))
#           return False;
#       if (*name_p)
#           *id_p = (id_t)atol(buf);              <-- atol("") == 0
#       else
#           *name_p = strdup(buf);
#       return True;
#   So an empty response is read successfully and atol("") = 0 is treated
#   as a valid mapping.  user_to_uid()/group_to_gid() then return success
#   with *uid_p = 0 / *gid_p = 0.  Receiver recv_add_id() (uidlist.c:273)
#   uses that as the local id, and with `fake super = yes` the file's
#   xattr ends up with "MODE 0,0 0:0" — root-owned setuid metadata that
#   a fake-super restore/list view honours as a real root-owned file.
#
# Test design:
#   * A stub name-converter that always prints an empty line, no matter
#     what the daemon asks.  This simulates the "every sender name is
#     unknown to the daemon" case (the report did the same thing via an
#     NSS shim; the stub is equivalent and far easier).
#   * Daemon module: read only=no, fake super=yes, name converter=stub,
#     numeric ids=no, so the receiver looks up the sender's user/group
#     names through the stub.
#   * Client uploads a regular file under the current user's identity
#     (a non-root uid/gid).
#   * After upload, read user.rsync.%stat xattr on the stored file.
#     Format is "MODE 0,0 UID:GID".  If UID is 0 (and sender's uid was
#     non-zero), the bug is present.
#
# Fix direction:
#   In namecvt_call(), for the name-to-id branch, treat empty/non-numeric
#   buf as lookup failure (return False).  recv_add_id then falls back
#   to the sender's numeric id (uidlist.c:276), so unknown names stay
#   non-root.

import os
import re
import subprocess

from rsyncfns import (
    RSYNC_PREFIX, SCRATCHDIR, _xattr_full, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
    xattrs_supported,
)

# Skips as root (see the my_uid==0 guard below), so the fleet harness reruns it
# in its non-root pass to get real coverage (testsuite/fleettest.py).
fleet_nonroot = True

DAEMON_PORT = 12904

if not xattrs_supported():
    test_skipped("namecvt-empty-response test requires xattr support to "
                 "read fake-super metadata")

if not hasattr(os, 'getxattr'):
    # CPython exposes os.getxattr only on Linux; the OS (e.g. Cygwin) may have
    # xattrs yet no Python binding to read the fake-super %stat metadata.
    test_skipped("this Python build lacks os.getxattr to read fake-super metadata")

my_uid = os.getuid()
my_gid = os.getgid()
if my_uid == 0:
    # Running as root would defeat the test: any namecvt mapping that
    # ends in id=0 is indistinguishable from "the sender was already
    # root."  We need a non-zero sender id so the bug-vs-fix branches
    # produce different xattr UIDs.
    # NOTE: because every Linux CI job runs the suite as root, this test only
    # gets coverage from the dedicated non-root run -- the "check (non-root,
    # targeted)" step in .github/workflows/ubuntu-build.yml. Any new non-root-
    # only test must be added to that step (and declare `fleet_nonroot = True`
    # for the fleet harness's non-root pass) or it will silently never run in CI.
    test_skipped("namecvt-empty-response test must run as a non-root user "
                 "(sender uid/gid must differ from 0 to expose the bug)")


# -- stub name-converter that always returns empty -------------------------
stub = SCRATCHDIR / 'stub_nameconvert'
stub.write_text(
    "#!/usr/bin/env python3\n"
    "# Always-empty stub: every usr/grp/uid/gid lookup answers ''.\n"
    "# The bundled support/nameconvert helper does this only when the\n"
    "# lookup fails (KeyError); the namecvt_call() bug treats the empty\n"
    "# line as a valid numeric 0.\n"
    "import sys\n"
    "for _ in sys.stdin:\n"
    "    print('', flush=True)\n"
)
stub.chmod(0o755)


mod = SCRATCHDIR / 'recvmod'
src = SCRATCHDIR / 'srcdir'   # NOT 'src' -- runtests.py:155 plants
                              # a <scratch>/src symlink to SRCDIR for
                              # every test, so writing files under it
                              # would land in the repo working tree.
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


# -- positive control: upload a file -------------------------------------
# `-a` includes owner+group preservation, and the receiver (daemon w/
# fake-super) writes the metadata into user.rsync.%stat.
proc = subprocess.run(rsync_argv('-a', f'{src}/', f'{url}recv/'),
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.PIPE, text=True)
if proc.returncode not in (0, 23):
    test_fail(f"upload to recv module failed (rc={proc.returncode}): "
              f"{proc.stderr!r}")

stored = mod / 'f'
if not stored.is_file():
    test_fail(f"upload did not deliver the file ({stored})")


# -- read the fake-super xattr ------------------------------------------
# Fake-super only stores an xattr when the metadata to remember DIFFERS
# from the filesystem reality (rsync.c:526:
#   change_uid = am_root && uid_ndx && sxp->st.st_uid != F_OWNER(file)).
# With the FIX:
#   * namecvt empty -> user_to_uid returns 0 (failure)
#   * recv_add_id() falls back to the sender's numeric id
#   * F_OWNER == filesystem-creation uid (both are `my_uid`)
#   * no xattr is written.
# With the BUG:
#   * namecvt empty -> user_to_uid returns success with id=0
#   * F_OWNER becomes 0, filesystem uid is `my_uid`, they differ
#   * xattr is written carrying "0:0" -- that is the leak we detect.
#
# So we check: if an xattr IS present, parse it and confirm it does not
# encode 0:0.  If no xattr is present we infer the namecvt path did not
# fabricate a phantom id=0 -- the metadata simply matches reality.
xkey = _xattr_full(RSYNC_PREFIX + '.%stat')   # 'user.rsync.%stat' on Linux
try:
    raw = os.getxattr(str(stored), xkey)
    xval = raw.decode('utf-8', 'surrogateescape')
except OSError:
    xval = None

if xval is None:
    # No fake-super xattr stored: metadata matched the filesystem, which
    # is what the fix produces because F_OWNER == my_uid == filesystem
    # uid.  That is the GREEN signal.
    print(f"daemon-namecvt-empty-response: no fake-super xattr written -- "
          f"namecvt empty response was correctly treated as a lookup "
          f"failure, so recv_add_id() kept sender uid={my_uid} and the "
          f"receiver had nothing to remember separately from the "
          f"filesystem.")
else:
    # An xattr is present.  user.rsync.%stat format:
    #   "MODE DEV_MAJOR,DEV_MINOR UID:GID"
    # The leak case is UID:GID == 0:0 .
    m = re.match(r'\S+\s+\S+\s+(\d+):(\d+)\s*$', xval)
    if not m:
        test_fail(f"unrecognised fake-super xattr format: {xval!r}")
    stored_uid, stored_gid = int(m.group(1)), int(m.group(2))

    if stored_uid == 0 and stored_gid == 0:
        test_fail(
            f"namecvt empty response was treated as a valid id=0 "
            f"mapping: file uploaded by uid={my_uid}/gid={my_gid} is "
            f"stored with fake-super metadata {xval!r} (uid:gid = 0:0). "
            f" This means a daemon-unknown sender name silently becomes "
            f"root-owned in preserved metadata.  Fix: namecvt_call() "
            f"must treat an empty buf as lookup failure (return False) "
            f"so recv_add_id() falls back to the sender's numeric id "
            f"(clientserver.c:1227, uidlist.c:273-282).")

    print(f"daemon-namecvt-empty-response: xattr {xval!r} present but "
          f"non-zero uid/gid -- empty namecvt response did not produce "
          f"a phantom root-owned mapping.")
