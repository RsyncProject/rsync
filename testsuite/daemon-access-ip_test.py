#!/usr/bin/env python3
"""Daemon coverage: hosts allow / hosts deny IP and CIDR matching (access.c).

access.c's make_mask / match_address / match_binary only run for a real TCP
peer matched against a numeric hosts allow/deny pattern -- so this needs
--use-tcp (the loopback peer is 127.0.0.1). Verifies that exact-IP and CIDR
allow patterns permit the connection while a CIDR deny / a non-matching allow
refuse it.

The config sets NO global hosts allow: an inherited global allow-list would
match (e.g. via "localhost") and short-circuit before a module's deny is
consulted, so the per-module patterns must be the sole decider here.
"""

import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    make_tree, require_tcp, rmtree, rsync_argv, start_test_daemon, test_fail,
)

DAEMON_PORT = 12892
require_tcp("hosts allow/deny address matching needs a real TCP peer")

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

conf = SCRATCHDIR / 'access-ip.conf'
conf.write_text(
    f"pid file = {SCRATCHDIR}/rsyncd.pid\n"
    "use chroot = no\n"
    f"log file = {SCRATCHDIR}/rsyncd.log\n"
    f"\n[allow-exact]\n\tpath = {src}\n\tread only = yes\n\thosts allow = 127.0.0.1\n"
    f"\n[allow-cidr]\n\tpath = {src}\n\tread only = yes\n\thosts allow = 127.0.0.0/8\n"
    f"\n[deny-cidr]\n\tpath = {src}\n\tread only = yes\n\thosts deny = 127.0.0.0/8\n"
    f"\n[allow-other]\n\tpath = {src}\n\tread only = yes\n\thosts allow = 10.0.0.0/8\n"
)
url = start_test_daemon(conf, DAEMON_PORT)


def connect(mod):
    """Return rsync's exit code for listing the module over the daemon."""
    return subprocess.run(rsync_argv('-r', f'{url}{mod}/'),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True).returncode


for mod in ('allow-exact', 'allow-cidr'):
    if connect(mod) != 0:
        test_fail(f"connection to {mod} should be ALLOWED but was refused")
for mod in ('deny-cidr', 'allow-other'):
    if connect(mod) == 0:
        test_fail(f"connection to {mod} should be DENIED but succeeded")

# Client --address binds the outgoing socket to a local address (socket.c
# try_bind_local) before connecting to the daemon.
proc = subprocess.run(
    rsync_argv('-r', '--address=127.0.0.1', f'{url}allow-cidr/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
if proc.returncode != 0:
    test_fail(f"--address=127.0.0.1 client connection failed: {proc.stderr}")

print("daemon-access-ip: hosts allow/deny matching + client --address verified")
