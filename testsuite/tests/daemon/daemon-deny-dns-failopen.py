#!/usr/bin/env python3

from harness.daemon import probe_module
from harness.rsync import (
    FROMDIR, SCRATCHDIR, RSYNC,
    make_tree, require_tcp, rmtree, start_test_daemon, test_fail,
)

DAEMON_PORT = 12894
require_tcp("hosts deny DNS-failure matching needs a real TCP peer")

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

conf = SCRATCHDIR / 'deny-dns.conf'
conf.write_text(
    f"pid file = {SCRATCHDIR}/rsyncd.pid\n"
    "use chroot = no\n"
    f"log file = {SCRATCHDIR}/rsyncd.log\n"
    f"\n[plain]\n\tpath = {src}\n\tread only = yes\n"
    f"\n[deny-match]\n\tpath = {src}\n\tread only = yes\n\thosts deny = 127.0.0.0/8\n"
    f"\n[deny-nomatch]\n\tpath = {src}\n\tread only = yes\n\thosts deny = 10.0.0.0/8\n"
    f"\n[deny-unresolvable]\n\tpath = {src}\n\tread only = yes\n\thosts deny = nope.nonexistent.invalid\n"
)
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=RSYNC)

if probe_module(url, 'plain') != 0:
    test_fail("control: connection to [plain] (no deny) should be ALLOWED but was refused")
if probe_module(url, 'deny-match') == 0:
    test_fail("control: [deny-match] (hosts deny = 127.0.0.0/8) should be DENIED but succeeded")
if probe_module(url, 'deny-nomatch') != 0:
    test_fail("control: [deny-nomatch] (hosts deny = 10.0.0.0/8, non-matching) should be ALLOWED but was refused")

if probe_module(url, 'deny-unresolvable') == 0:
    test_fail("[deny-unresolvable] (hosts deny = nope.nonexistent.invalid) was ALLOWED: "
              "an unresolvable deny token failed OPEN (KI-43)")

print("daemon-deny-dns-failopen: unresolvable hosts-deny token fails closed")
