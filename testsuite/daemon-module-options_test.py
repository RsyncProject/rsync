#!/usr/bin/env python3
"""Daemon coverage: assorted rsyncd.conf module/global options whose code
paths the suite never exercised.  One test, many small option-gates --
each is a handful of lines but together they were a measurable gap.

Covers (all in clientserver.c rsync_module() / send_listing() unless noted):
  motd file                global  -> read+echo via open_no_attacker_symlinks
  socket options           global  -> set_socket_options() (socket.c) on the
                                      daemon's per-connection fd
  incoming chmod           module  -> parse_chmod into daemon_chmod_modes
  outgoing chmod           module  -> same, am_sender branch
  dont compress            module  -> token.c init_set_compression's
                                      lp_dont_compress() / `dont compress`
                                      daemon-side suffix list under -z
  list = no                module  -> the "(list=no)" skip in send_listing()
  comment                  module  -> printed in send_listing()
  --sockopts               client  -> set_socket_options() at connect() time

`motd file` and `socket options` need a real TCP daemon (the
RSYNC_CONNECT_PROG pipe transport bypasses send_listing/motd and there's
no socket fd to setsockopt on), so this test is require_tcp()-gated.
"""

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, FROMDIR,
    claim_ports, make_tree, makepath, require_tcp, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

require_tcp("motd/socket-options need a real socket")

PORT = 19884
claim_ports(PORT)

src = FROMDIR
dst_in = SCRATCHDIR / 'dest-modopt-in'
dst_out = SCRATCHDIR / 'dest-modopt-out'
dst_hidden = SCRATCHDIR / 'dest-modopt-hidden'
for d in (src, dst_in, dst_out, dst_hidden):
    rmtree(d)
make_tree(src, depth=1)
makepath(dst_in, dst_out, dst_hidden)
# Seed the outgoing module so a PULL has content for `outgoing chmod`.
(dst_out / 'o0').write_text('out\n')
(dst_out / 'o0').chmod(0o644)

motd = SCRATCHDIR / 'motd.txt'
motd.write_text('** TEST MOTD BANNER **\n')

mods = [
    ('inmod', {
        'path': str(dst_in), 'read only': 'no',
        'incoming chmod': 'Fu+x,g-w,o=r',
        # daemon-side suffix list (token.c lp_dont_compress(), distinct from
        # the client --skip-compress path the existing tests already cover)
        'dont compress': '*.gz *.bz2',
        'comment': 'incoming-chmod test module',
    }),
    ('outmod', {
        'path': str(dst_out), 'read only': 'yes',
        'outgoing chmod': 'F-x,o-rwx',
        'comment': 'outgoing-chmod test module',
    }),
    ('hidden', {
        'path': str(dst_hidden), 'read only': 'no',
        'list': 'no',
    }),
]
conf = write_daemon_conf(
    mods,
    globals={
        'motd file': str(motd),
        # OPT_BOOL + OPT_INT= + OPT_ON, plus an unknown to hit the
        # "Unknown socket option" branch.
        'socket options': 'SO_KEEPALIVE SO_RCVBUF=8192 SO_BROADCAST NOSUCHOPT',
    },
    name='module-options.conf',
)
url = start_test_daemon(conf, PORT)


# --- module listing: motd echoed, [hidden] absent, comments shown ----------
# A bare daemon URL (ends in '/') with no destination = module listing.
r = subprocess.run(rsync_argv(f'{url}'), capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"module listing failed (rc={r.returncode}):\n{r.stderr}")
if 'TEST MOTD BANNER' not in r.stdout:
    test_fail(f"motd file content not echoed to client:\n{r.stdout!r}")
if 'hidden' in r.stdout:
    test_fail(f"list=no module 'hidden' leaked into listing:\n{r.stdout}")
if 'inmod' not in r.stdout or 'outmod' not in r.stdout:
    test_fail(f"expected modules missing from listing:\n{r.stdout}")
# The comment column: send_listing() prints "name\tcomment\n" per module.
if 'incoming-chmod test module' not in r.stdout:
    test_fail(f"module comment missing from listing:\n{r.stdout!r}")

# --- incoming chmod: push, then check Fu+x,g-w,o=r applied -----------------
r = subprocess.run(
    rsync_argv('-rzp', '--sockopts=SO_KEEPALIVE,SO_SNDBUF=8192',
               f'{src}/', f'{url}inmod/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"push to [inmod] failed (rc={r.returncode}):\n{r.stderr}")
# Find any regular file in dst_in and check the mode bits.
for f in dst_in.rglob('*'):
    if f.is_file():
        m = f.stat().st_mode & 0o777
        # Fu+x -> u has x; g-w -> g has no w; o=r -> other == r exactly
        if not (m & 0o100):
            test_fail(f"incoming chmod Fu+x not applied to {f.name}: {oct(m)}")
        if m & 0o020:
            test_fail(f"incoming chmod g-w not applied to {f.name}: {oct(m)}")
        if (m & 0o007) != 0o004:
            test_fail(f"incoming chmod o=r not applied to {f.name}: {oct(m)}")
        break
else:
    test_fail("push to [inmod] produced no regular files")

# --- outgoing chmod: pull, then check F-x,o-rwx applied to received perms --
pull_dst = SCRATCHDIR / 'pull-outmod'
rmtree(pull_dst); makepath(pull_dst)
r = subprocess.run(
    rsync_argv('-rp', f'{url}outmod/', f'{pull_dst}/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"pull from [outmod] failed (rc={r.returncode}):\n{r.stderr}")
m = (pull_dst / 'o0').stat().st_mode & 0o777
if m & 0o111:
    test_fail(f"outgoing chmod F-x not applied: {oct(m)}")
if m & 0o007:
    test_fail(f"outgoing chmod o-rwx not applied: {oct(m)}")

# --- list=no module is still REACHABLE by name -----------------------------
r = subprocess.run(rsync_argv('-r', f'{src}/', f'{url}hidden/'),
                   capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"push to list=no [hidden] should still work by name "
              f"(rc={r.returncode}):\n{r.stderr}")

print("daemon-module-options: motd, socket options, --sockopts, "
      "incoming/outgoing chmod, dont compress, list=no, comment ok")
