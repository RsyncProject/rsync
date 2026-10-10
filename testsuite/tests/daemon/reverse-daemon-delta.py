#!/usr/bin/env python3

import filecmp
import os
import re
import subprocess

from harness.daemon_config import build_rsyncd_conf
from harness.rsync import (
    FROMDIR, RSYNC, RSYNC_PEER, TMPDIR, make_data_file, makepath, split_rsync_cmd,
    start_test_daemon, test_fail,
)
from harness import metadata

metadata(features={'compression', 'daemon', 'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'process', 'socket'}, tags={'compatibility', 'daemon', 'transfer', 'version-mix'})

DAEMON_PORT = 12894
FILESIZE = 512 * 1024
_SUMMARY = re.compile(r'(?:sent|wrote) ([\d,]+) bytes\s+(?:received|read) ([\d,]+) bytes')

TODIR = TMPDIR / 'to'

def make_versions(path_old, path_new):
    make_data_file(path_old, FILESIZE)
    data = bytearray(open(path_old, 'rb').read())
    data[100000:100050] = bytes(((b + 1) & 0xFF) for b in data[100000:100050])
    data += b'reverse-delta appended tail\n' * 64
    with open(path_new, 'wb') as f:
        f.write(data)

def peer_client(args, label):
    argv = split_rsync_cmd(RSYNC_PEER) + args
    proc = subprocess.run(argv, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          env={**os.environ, 'LC_ALL': 'C'})
    print(proc.stdout, end='')
    if proc.returncode != 0:
        test_fail(f"{label}: old client exited {proc.returncode}")
    m = _SUMMARY.search(proc.stdout)
    if not m:
        test_fail(f"{label}: could not parse sent/received from client output")
    return int(m.group(1).replace(',', '')), int(m.group(2).replace(',', ''))

def assert_delta(label, moved):
    if moved >= FILESIZE // 2:
        test_fail(f"{label}: {moved} bytes crossed the wire -- delta did not "
                  f"engage (file is {FILESIZE} bytes)")

def run_push(compress):
    tag = "push+z" if compress else "push"
    basis = TODIR / f'{tag}.dat'
    source = src / f'{tag}.dat'
    make_versions(basis, source)
    opts = ['-a', '-v'] + (['-z'] if compress else [])
    sent, _ = peer_client(opts + [str(source), f'{url}test-to/'], tag)
    if not filecmp.cmp(source, basis, shallow=False):
        test_fail(f"{tag}: daemon-side file does not match source after push")
    assert_delta(tag, sent)
    print(f"{tag}: OK (sent {sent} bytes for a {FILESIZE}-byte file)")

def run_pull(compress):
    tag = "pull+z" if compress else "pull"
    served = FROMDIR / f'{tag}.dat'
    local = dst / f'{tag}.dat'
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

conf = build_rsyncd_conf()
url = start_test_daemon(conf, DAEMON_PORT, rsync_cmd=RSYNC)

src = TMPDIR / 'client-src'
dst = TMPDIR / 'client-dst'
makepath(src, dst)

run_push(compress=False)
run_push(compress=True)
run_pull(compress=False)
run_pull(compress=True)
