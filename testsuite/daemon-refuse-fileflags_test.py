#!/usr/bin/env python3
# Python rewrite of testsuite/daemon-refuse-fileflags.test.
#
# Copyright (C) 2026 by Andrew Tridgell
# This program is distributable under the terms of the GNU GPL (see COPYING).
#
# The rsync daemon must refuse --fileflags / --unsafe-fileflags /
# --force-change / --force-uchange / --force-schange by default, even on a
# module with no explicit "refuse options" setting. These options are
# foot-guns when handed to a daemon -- sender-controlled st_flags applied to
# the daemon's filesystem (--fileflags), or receiver-side immutability
# cleared on demand (--force-change family) -- so the policy is "you have to
# explicitly opt in per-module before clients can use them", not "you have
# to explicitly refuse them per-module".
#
# Also verify the opt-in works: a module listing "refuse options =
# !fileflags" accepts --fileflags.

import re
import subprocess

import rsyncfns
from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    build_rsyncd_conf, hands_setup, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped,
)


DAEMON_PORT = 12886

if '"file_flags": true' not in rsyncfns.run_rsync('-VV', capture_output=True).stdout:
    test_skipped("Rsync is configured without fileflags support")

# Daemon mode for fileflags negotiates the varint flag encoding which only
# exists at protocol >= 30; under check29 the handshake fails before we can
# exercise the refuse-options policy.
m = re.search(r'--protocol[ =](\d+)', rsyncfns.RSYNC)
if m and int(m.group(1)) < 30:
    test_skipped("fileflags daemon test requires protocol >= 30")


conf = build_rsyncd_conf()

# Writable module for the force_change tests (push), read-only for the
# fileflags ones (download); plus an explicit opt-in module.
uploaddir = SCRATCHDIR / 'upload'
uploaddir.mkdir(parents=True, exist_ok=True)
with open(conf, 'a') as f:
    f.write(f"""
[default-ro]
\tpath = {FROMDIR}
\tread only = yes

[default-rw]
\tpath = {uploaddir}
\tread only = no

[opt-in]
\tpath = {FROMDIR}
\tread only = yes
\trefuse options = !fileflags
""")

hands_setup()

url = start_test_daemon(conf, DAEMON_PORT)


# Run an rsync client against the test daemon, capturing stderr to a FILE
# rather than an inherited PIPE. In the default secure transport the client
# forks a daemon over RSYNC_CONNECT_PROG that inherits our stderr fd; draining
# that as a subprocess.PIPE hangs whenever the forked daemon lingers holding
# the write end -- it hung the whole test for 300s on OpenBSD even though the
# daemon had already refused correctly. A plain file fd has no EOF-drain step,
# so we only ever wait on the direct client process; the timeout converts any
# genuine client hang into a clean failure instead of the harness's 300s kill.
_errfile = SCRATCHDIR / 'refuse.err'


def run_client(args, timeout=120):
    with open(_errfile, 'w') as ef:
        try:
            proc = subprocess.run(rsync_argv(*args), stdout=subprocess.DEVNULL,
                                  stderr=ef, timeout=timeout)
        except subprocess.TimeoutExpired:
            test_fail(f"rsync client timed out after {timeout}s: {' '.join(args)}")
    return proc.returncode, _errfile.read_text()


def expect_refused(args, opt):
    rc, err = run_client(args)
    if rc == 0:
        print(err)
        test_fail(f"{opt} was accepted by the daemon (expected refuse)")
    if opt not in err:
        print(err)
        test_fail(f"{opt}: refuse error did not name the option")
    print(f"ok: daemon refused {opt} by default")


# --fileflags / --unsafe-fileflags propagate to the daemon in either
# direction; test against the read-only module via download.
for opt in ('--fileflags', '--unsafe-fileflags'):
    rmtree(TODIR)
    TODIR.mkdir()
    expect_refused(['-a', opt, f'{url}default-ro/', f'{TODIR}/'], opt)

# The --force-* family is a sender-side propagation, so it only reaches the
# daemon when the client is uploading (am_sender == 1). Hit the writable
# module with --force-uchange / --force-schange (server_options emits the
# USR/SYS bits under those names rather than --force-change).
push_src = SCRATCHDIR / 'push_src'
push_src.write_text("data\n")
for opt in ('--force-uchange', '--force-schange'):
    expect_refused([opt, str(push_src), f'{url}default-rw/'], opt)

# --fileflags works against the explicit opt-in module.
rmtree(TODIR)
TODIR.mkdir()
rc, err = run_client(['-a', '--fileflags', f'{url}opt-in/', f'{TODIR}/'])
if rc != 0:
    print(err)
    test_fail("--fileflags refused by opt-in module despite "
              "refuse-options = !fileflags")
print("ok: opt-in module accepts --fileflags")
