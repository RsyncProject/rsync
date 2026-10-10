#!/usr/bin/env python3

import os
import pwd
import subprocess
import time

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, SRCDIR, make_tree, makepath, owners_supported, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_text_file,
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

early = write_text_file(
    SCRATCHDIR / 'early.sh',
    f'#!/bin/sh\nprintf "%s|" "$RSYNC_MODULE_NAME" > {markers}/early\n'
    f'cat >> {markers}/early\n',
    0o755,
)

nameconv = write_text_file(
    SCRATCHDIR / 'nameconv.sh',
    f'#!/bin/sh\ntee -a {markers}/nameconv | python3 {SRCDIR}/support/nameconvert\n',
    0o755,
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
        'numeric ids': 'no',
    }),
], name='early-nameconv.conf')
url = start_test_daemon(conf, DAEMON_PORT)

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

nonroot = next((p for p in pwd.getpwall() if p.pw_uid != 0 and p.pw_gid != 0), None)
if not owners_supported() or nonroot is None:
    print("daemon-early-exec-nameconv: early-exec ok; "
          "name-converter SKIPPED (no non-root passwd entry / cannot chown)")
    raise SystemExit(0)
for p in src.rglob('*'):
    os.chown(p, nonroot.pw_uid, nonroot.pw_gid)

r = subprocess.run(
    rsync_argv('-rog', f'{src}/', f'{url}conv/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"push to [conv] failed (rc={r.returncode}):\n{r.stderr}")

nm = markers / 'nameconv'
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not (nm.is_file() and nm.stat().st_size):
    time.sleep(0.05)
if not nm.is_file() or not nm.stat().st_size:
    test_fail("name-converter script never received any namecvt_call() requests")

reqs = nm.read_text().splitlines()
if not any(l.startswith('usr ') for l in reqs):
    test_fail(f"name-converter saw no 'usr' request: {reqs!r}")
if not any(l.startswith('grp ') for l in reqs):
    test_fail(f"name-converter saw no 'grp' request: {reqs!r}")

some_dest = next(p for p in dest_conv.rglob('*') if p.is_file())
st = os.stat(some_dest)
if st.st_uid != nonroot.pw_uid:
    test_fail(f"name-converter mapping did not apply: dest uid {st.st_uid} "
              f"!= {nonroot.pw_uid} ({nonroot.pw_name})")

print(f"daemon-early-exec-nameconv: early-exec env+stdin ok; "
      f"name-converter handled {len(reqs)} request(s)")
