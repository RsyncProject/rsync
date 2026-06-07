#!/usr/bin/env python3
# Reverse-direction version-mixing smoke test: OLD client <-> CURRENT daemon.
#
# Every other two-sided test drives with the current binary and uses the old
# binary only as the server/daemon. That covers new-client -> old-server but
# NOT the more important backward-compat direction: a current daemon/server
# must keep working for the large installed base of OLD clients. This test
# fills that gap by starting the daemon with the CURRENT build (RSYNC) and
# running the OLD binary (RSYNC_PEER) as the client.
#
# It exercises, in BOTH transfer directions and with and without compression:
#   * push (old client = sender,   current daemon = receiver) -> old->new delta
#   * pull (current daemon = sender, old client  = receiver) -> new->old delta
# In each case the receiving side already holds an older version of the file,
# so the rsync delta algorithm actually runs (block matching + token stream)
# rather than a whole-file copy -- verified by asserting the bytes moved over
# the wire are far smaller than the file (a whole-file transfer of this random,
# incompressible data would be ~filesize even with -z).
#
# When no second binary was selected (RSYNC_PEER == RSYNC) this still runs as a
# current<->current smoke test of delta + compression over a daemon.

import filecmp
import os
import re
import shlex
import subprocess

from rsyncfns import (
    FROMDIR, RSYNC, RSYNC_PEER, TMPDIR,
    build_rsyncd_conf, makepath, make_data_file, start_test_daemon, test_fail,
)

DAEMON_PORT = 12894
FILESIZE = 512 * 1024          # big enough that delta savings are unambiguous
# Old rsync (2.6.x era) prints "wrote N bytes  read M bytes"; 3.0+ prints
# "sent N bytes  received M bytes". Accept both so old clients parse too.
_SUMMARY = re.compile(r'(?:sent|wrote) ([\d,]+) bytes\s+(?:received|read) ([\d,]+) bytes')

TODIR = TMPDIR / 'to'


def make_versions(path_old, path_new):
    """Write an 'old' file and a 'new' file derived from it: same head, a
    changed middle block, and an appended tail. The shared blocks give the
    delta algorithm something to match; the changes give it real literal data
    to send."""
    make_data_file(path_old, FILESIZE)
    data = bytearray(open(path_old, 'rb').read())
    data[100000:100050] = bytes(((b + 1) & 0xFF) for b in data[100000:100050])
    data += b'reverse-delta appended tail\n' * 64
    with open(path_new, 'wb') as f:
        f.write(data)


def peer_client(args, label):
    """Run the OLD client (RSYNC_PEER) and return (sent, received) wire bytes
    parsed from rsync's summary line. Fails the test on non-zero exit."""
    argv = shlex.split(RSYNC_PEER) + args
    proc = subprocess.run(argv, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    print(proc.stdout, end='')
    if proc.returncode != 0:
        test_fail(f"{label}: old client exited {proc.returncode}")
    m = _SUMMARY.search(proc.stdout)
    if not m:
        test_fail(f"{label}: could not parse sent/received from client output")
    return int(m.group(1).replace(',', '')), int(m.group(2).replace(',', ''))


def assert_delta(label, moved):
    """A delta transfer of FILESIZE moves far less than the whole file; a
    whole-file copy (delta failed to engage) would move ~FILESIZE."""
    if moved >= FILESIZE // 2:
        test_fail(f"{label}: {moved} bytes crossed the wire -- delta did not "
                  f"engage (file is {FILESIZE} bytes)")


def run_push(compress):
    """old client (sender) -> current daemon (receiver), receiver holds the old
    version as the basis. Exercises old->new delta encoding."""
    tag = "push+z" if compress else "push"
    basis = TODIR / f'{tag}.dat'    # daemon-side basis (old)
    source = src / f'{tag}.dat'     # client source (new)
    make_versions(basis, source)
    opts = ['-a', '-v'] + (['-z'] if compress else [])
    sent, _ = peer_client(opts + [str(source), f'{url}test-to/'], tag)
    if not filecmp.cmp(source, basis, shallow=False):
        test_fail(f"{tag}: daemon-side file does not match source after push")
    assert_delta(tag, sent)
    print(f"{tag}: OK (sent {sent} bytes for a {FILESIZE}-byte file)")


def run_pull(compress):
    """current daemon (sender) -> old client (receiver), client holds the old
    version as the basis. Exercises new->old delta encoding."""
    tag = "pull+z" if compress else "pull"
    served = FROMDIR / f'{tag}.dat'   # daemon module file (new)
    local = dst / f'{tag}.dat'        # client basis (old)
    make_versions(local, served)
    opts = ['-a', '-v'] + (['-z'] if compress else [])
    _, received = peer_client(
        opts + [f'{url}test-from/{tag}.dat', str(dst) + '/'], tag)
    if not filecmp.cmp(served, local, shallow=False):
        test_fail(f"{tag}: client file does not match daemon source after pull")
    assert_delta(tag, received)
    print(f"{tag}: OK (received {received} bytes for a {FILESIZE}-byte file)")


os.chdir(TMPDIR)
makepath(FROMDIR, TODIR)

# Current build is the daemon; old binary is the client.
conf = build_rsyncd_conf()
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=RSYNC)

src = TMPDIR / 'client-src'
dst = TMPDIR / 'client-dst'
makepath(src, dst)

run_push(compress=False)
run_push(compress=True)
run_pull(compress=False)
run_pull(compress=True)
