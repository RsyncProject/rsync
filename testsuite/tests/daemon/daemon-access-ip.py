#!/usr/bin/env python3

import subprocess

from harness.daemon import probe_module
from harness.rsync import (
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

for mod in ('allow-exact', 'allow-cidr'):
    if probe_module(url, mod) != 0:
        test_fail(f"connection to {mod} should be ALLOWED but was refused")
for mod in ('deny-cidr', 'allow-other'):
    if probe_module(url, mod) == 0:
        test_fail(f"connection to {mod} should be DENIED but succeeded")

proc = subprocess.run(
    rsync_argv('-r', '--address=127.0.0.1', f'{url}allow-cidr/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
if proc.returncode != 0:
    test_fail(f"--address=127.0.0.1 client connection failed: {proc.stderr}")

print("daemon-access-ip: hosts allow/deny matching + client --address verified")
