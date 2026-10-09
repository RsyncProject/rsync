#!/usr/bin/env python3
"""Daemon: a `hosts deny` hostname token that cannot be resolved must fail CLOSED.

KI-43 / sibling of CVE-2026-43617 (access.c).  match_hostname() does a forward
DNS lookup of a config-specified hostname token (gethostbyname) and, on failure,
returned "no match" -- indistinguishable from a real non-match -- so allow_access
fell through to ALLOW.  A daemon with `hosts deny = <hostname>` therefore silently
admitted the very host it meant to block whenever the token could not resolve (a
resolver-less chroot -- a recommended hardening -- or a transient DNS failure),
with no attacker DNS control required.

The fix makes an unresolvable token on a DENY list fail closed (treated as a
match, i.e. deny) while leaving allow-list behaviour unchanged.

This needs --use-tcp: match_hostname only runs for a real TCP peer (127.0.0.1).
"""

import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR, RSYNC,
    make_tree, require_tcp, rmtree, rsync_argv, start_test_daemon, test_fail,
)

DAEMON_PORT = 12894
require_tcp("hosts deny DNS-failure matching needs a real TCP peer")

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

# `.invalid` is reserved (RFC 6761) to never resolve, so gethostbyname() on the
# deny token fails deterministically -- exactly the fail-open trigger.
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
# The access check is daemon-side, so the daemon must be the binary under test
# (matters under --rsync-bin2, where RSYNC_PEER could be a different/old binary).
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=RSYNC)


def connect(mod):
    """Return rsync's exit code for listing the module over the daemon."""
    return subprocess.run(rsync_argv('-r', f'{url}{mod}/'),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True).returncode


# Controls (non-vacuity): the daemon answers, the deny mechanism works, and a
# non-matching deny still admits.
if connect('plain') != 0:
    test_fail("control: connection to [plain] (no deny) should be ALLOWED but was refused")
if connect('deny-match') == 0:
    test_fail("control: [deny-match] (hosts deny = 127.0.0.0/8) should be DENIED but succeeded")
if connect('deny-nomatch') != 0:
    test_fail("control: [deny-nomatch] (hosts deny = 10.0.0.0/8, non-matching) should be ALLOWED but was refused")

# The fix: an unresolvable deny-list hostname token must fail CLOSED.
if connect('deny-unresolvable') == 0:
    test_fail("[deny-unresolvable] (hosts deny = nope.nonexistent.invalid) was ALLOWED: "
              "an unresolvable deny token failed OPEN (KI-43)")

print("daemon-deny-dns-failopen: unresolvable hosts-deny token fails closed")
