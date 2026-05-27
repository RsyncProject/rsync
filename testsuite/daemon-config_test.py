#!/usr/bin/env python3
"""Daemon coverage: the &include config directive (params.c include_config /
parse_directives) and a module whose path doesn't exist (clientserver.c
path_failure).

Uses a hand-written rsyncd.conf because &include is a directive line, not a
`name = value` parameter.
"""

import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    make_tree, rmtree, rsync_argv, start_test_daemon, test_fail,
)

DAEMON_PORT = 12893

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

inc = SCRATCHDIR / 'included.conf'
inc.write_text(f"[inc-mod]\n\tpath = {src}\n\tread only = yes\n\tcomment = via-include\n")

conf = SCRATCHDIR / 'daemon-config.conf'
conf.write_text(
    f"pid file = {SCRATCHDIR}/rsyncd.pid\n"
    "use chroot = no\n"
    "hosts allow = localhost 127.0.0.0/8\n"
    f"log file = {SCRATCHDIR}/rsyncd.log\n"
    f"&include {inc}\n"
    f"\n[badpath]\n\tpath = {SCRATCHDIR}/no-such-dir\n\tread only = yes\n"
)
url = start_test_daemon(conf, DAEMON_PORT)

# &include pulled in inc-mod: it must be listable and present in the module list.
proc = subprocess.run(rsync_argv('-r', f'{url}inc-mod/'),
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
if proc.returncode != 0:
    test_fail(f"&include-defined module not reachable: {proc.stderr}")
proc = subprocess.run(rsync_argv(url), capture_output=True, text=True)
if 'inc-mod' not in proc.stdout:
    test_fail(f"&include-defined module absent from the listing:\n{proc.stdout}")

# A module whose path does not exist must fail the connection (path_failure).
proc = subprocess.run(rsync_argv('-r', f'{url}badpath/'),
                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
if proc.returncode == 0:
    test_fail("a module with a non-existent path unexpectedly served a connection")

print("daemon-config: &include directive + bad-path failure verified")
