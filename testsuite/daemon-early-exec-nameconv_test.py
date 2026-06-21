#!/usr/bin/env python3
"""Daemon coverage: `early exec` and `name converter` module hooks.

daemon-exec_test.py covers pre-xfer/post-xfer exec; this fills in the other
two start_pre_exec() callers in clientserver.c:

  early exec     -- runs before the @RSYNCD: OK, before pre-xfer exec, with
                    request="(NONE)" and the client's --early-input bytes on
                    its stdin (write_pre_exec_args type 1).
  name converter -- a long-running script that the daemon queries via
                    namecvt_call() (uidlist.c) for every uid/gid <-> name
                    mapping, used when chroot would hide /etc/passwd.

Both go through the start_pre_exec() forked-child path that 845fd1103113
made gcov-visible.
"""

import os
import pwd
import subprocess
import time

from rsyncfns import (
    FROMDIR, SCRATCHDIR, SRCDIR,
    make_tree, makepath, owners_supported, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12891

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

markers = SCRATCHDIR / 'markers'
rmtree(markers)
makepath(markers)
dest_early = SCRATCHDIR / 'dest-early'
dest_conv = SCRATCHDIR / 'dest-conv'
makepath(dest_early, dest_conv)


def script(name, body):
    p = SCRATCHDIR / name
    p.write_text('#!/bin/sh\n' + body)
    p.chmod(0o755)
    return p


# early-exec: capture RSYNC_MODULE_NAME (set before start_pre_exec) and the
# --early-input bytes (piped to the child's stdin after the request/argv).
early = script(
    'early.sh',
    f'printf "%s|" "$RSYNC_MODULE_NAME" > {markers}/early\n'
    f'cat >> {markers}/early\n',
)

# name converter: log every request to a marker so the test can verify
# namecvt_call() actually drove it, then defer to the shipped support script
# for the answer.
nameconv = script(
    'nameconv.sh',
    f'tee -a {markers}/nameconv | python3 {SRCDIR}/support/nameconvert\n',
)

early_input = SCRATCHDIR / 'early-input.bin'
early_input.write_bytes(b'EARLY-PAYLOAD')

conf = write_daemon_conf([
    ('early', {
        'path': str(dest_early), 'read only': 'no', 'use chroot': 'no',
        'early exec': str(early),
    }),
    ('conv', {
        'path': str(dest_conv), 'read only': 'no', 'use chroot': 'no',
        'name converter': str(nameconv),
        # Force name<->id mapping so uidlist.c hits namecvt_call().
        'numeric ids': 'no',
    }),
], name='early-nameconv.conf')
url = start_test_daemon(conf, DAEMON_PORT)


# --- early exec -------------------------------------------------------------

r = subprocess.run(
    rsync_argv('-r', f'--early-input={early_input}', f'{src}/', f'{url}early/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"push to [early] failed (rc={r.returncode}):\n{r.stderr}")
em = (markers / 'early')
if not em.is_file():
    test_fail("early-exec script never ran (no marker file)")
got = em.read_text()
if got != 'early|EARLY-PAYLOAD':
    test_fail(f"early-exec env/--early-input wrong: {got!r}")


# --- name converter ---------------------------------------------------------

# recv_add_id() only calls user_to_uid()/group_to_gid() (-> namecvt_call) when
# the incoming id is NON-zero, so the source tree needs a non-root owner.
# Pick any non-root passwd entry that exists in this environment; skip the
# name-converter half if there isn't one (or we can't chown).
nonroot = next((p for p in pwd.getpwall() if p.pw_uid != 0 and p.pw_gid != 0), None)
if not owners_supported() or nonroot is None:
    print("daemon-early-exec-nameconv: early-exec ok; "
          "name-converter SKIPPED (no non-root passwd entry / cannot chown)")
    raise SystemExit(0)
for p in src.rglob('*'):
    os.chown(p, nonroot.pw_uid, nonroot.pw_gid)

# -og (preserve owner+group) without --numeric-ids: the daemon receiver maps
# the sender's uid/gid names through namecvt_call() -> our script.
r = subprocess.run(
    rsync_argv('-rog', f'{src}/', f'{url}conv/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"push to [conv] failed (rc={r.returncode}):\n{r.stderr}")

# The converter is a co-process; give it a moment to flush the tee.
nm = markers / 'nameconv'
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not (nm.is_file() and nm.stat().st_size):
    time.sleep(0.05)
if not nm.is_file() or not nm.stat().st_size:
    test_fail("name-converter script never received any namecvt_call() requests")

reqs = nm.read_text().splitlines()
# At minimum: a 'usr <name>' and a 'grp <name>' for the sending user's uid/gid.
if not any(l.startswith('usr ') for l in reqs):
    test_fail(f"name-converter saw no 'usr' request: {reqs!r}")
if not any(l.startswith('grp ') for l in reqs):
    test_fail(f"name-converter saw no 'grp' request: {reqs!r}")

# And the dest tree's owner should have been mapped through to the same uid.
some_dest = next(p for p in dest_conv.rglob('*') if p.is_file())
st = os.stat(some_dest)
if st.st_uid != nonroot.pw_uid:
    test_fail(f"name-converter mapping did not apply: dest uid {st.st_uid} "
              f"!= {nonroot.pw_uid} ({nonroot.pw_name})")

print(f"daemon-early-exec-nameconv: early-exec env+stdin ok; "
      f"name-converter handled {len(reqs)} request(s)")
