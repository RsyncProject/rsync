#!/usr/bin/env python3
# Python rewrite of testsuite/daemon.test.
#
# Basic daemon-mode operations against an in-process rsyncd: list
# modules, list a hidden module, list a single-glob match, and the
# atimes-format variant. We avoid actually starting a listening server
# by using RSYNC_CONNECT_PROG to spawn the daemon as a child of rsync.

import os
import subprocess

from rsyncfns import (
    CHKFILE, FROMDIR, OUTFILE, RSYNC, RSYNC_PEER, SCRATCHDIR, SRCDIR, TODIR,
    build_rsyncd_conf, get_rootuid, get_testuid, makepath,
    rsync_argv, run_rsync, start_test_daemon, test_fail,
)


DAEMON_PORT = 12877


SSH = f"{SRCDIR / 'support' / 'lsh.sh'} --no-cd"


def listed_paths(text: str) -> set:
    """The set of path names in an `rsync -r` listing. Each listing line is
    "<mode> <size> <date> <time> <path>"; pull out the trailing path. Comparing
    the whole set (not just checking individual paths are present) catches a
    listing that leaks EXTRA paths, not only one that omits expected ones."""
    paths = set()
    for line in text.splitlines():
        parts = line.split()
        # A listing line starts with a 10-char mode string and ends with the
        # path; -U adds extra date columns, so take the last token (the test's
        # paths contain no spaces).
        if len(parts) >= 5 and len(parts[0]) == 10 and parts[0][0] in '-dlbcps':
            paths.add(parts[-1])
    return paths


conf = build_rsyncd_conf()

makepath(FROMDIR / 'foo', FROMDIR / 'bar' / 'baz', TODIR)
(FROMDIR / 'foo' / 'one').write_text("one\n")
(FROMDIR / 'bar' / 'two').write_text("two\n")
(FROMDIR / 'bar' / 'baz' / 'three').write_text("three\n")

os.chdir(SCRATCHDIR)
if not (SCRATCHDIR / 'rsyncd.conf').exists():
    os.symlink('test-rsyncd.conf', SCRATCHDIR / 'rsyncd.conf')

confopt = []
if get_testuid() == get_rootuid():
    # Root needs an explicit --config; otherwise rsync uses /etc/rsyncd.conf.
    print(f"Forcing --config={conf}")
    confopt = [f'--config={conf}']

expected_modules = (
    "test-from      \tr/o\n"
    "test-to        \tr/w\n"
    "test-scratch   \t\n"
)


def run_and_check(args, label, capture_stderr=False):
    proc = subprocess.run(
        rsync_argv(*args),
        capture_output=True, text=True,
    )
    out = proc.stdout
    if capture_stderr:
        out += proc.stderr
    print(f"--- {label} output:")
    print(out)
    if proc.returncode != 0 and not capture_stderr:
        test_fail(f"{label}: rsync exited {proc.returncode}\n{proc.stderr}")
    return out


# Module list via the lsh.sh stand-in.
rsync_path = f"{RSYNC_PEER}{(' ' + ' '.join(confopt)) if confopt else ''}"
out = run_and_check(
    ['-ve', SSH, f'--rsync-path={rsync_path}', 'localhost::'],
    "module list via lsh.sh",
)
if expected_modules not in out:
    test_fail("module list via lsh.sh did not contain the expected modules")
# test-hidden is `list = no`; it must NOT appear in the module listing.
if 'test-hidden' in out:
    print(out)
    test_fail("module list via lsh.sh leaked the `list = no` test-hidden module")
print('====')

# Same module list via the test daemon (pipe transport by default; real
# loopback rsyncd under --use-tcp).
daemon_url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')

out = run_and_check(['-v', f'{daemon_url}/'], "module list via daemon")
if expected_modules not in out:
    test_fail("module list via daemon did not contain the expected modules")
# test-hidden is `list = no`; it must NOT appear in the module listing.
if 'test-hidden' in out:
    print(out)
    test_fail("module list via daemon leaked the `list = no` test-hidden module")
print('====')

# test-hidden: a recursive listing of the whole module. Compare the exact set
# of listed paths so an unexpected/leaked extra path is caught, not only a
# missing one.
out = run_and_check(['-r', f'{daemon_url}/test-hidden'], "test-hidden listing")
got = listed_paths(out)
want = {'.', 'bar', 'bar/two', 'bar/baz', 'bar/baz/three', 'foo', 'foo/one'}
if got != want:
    print(out)
    test_fail(f"test-hidden listing paths {sorted(got)} != expected {sorted(want)}")

# test-from/f* glob: only the foo subtree, nothing from bar.
out = run_and_check(['-r', f'{daemon_url}/test-from/f*'], "test-from glob")
got = listed_paths(out)
want = {'foo', 'foo/one'}
if got != want:
    print(out)
    test_fail(f"test-from glob paths {sorted(got)} != expected {sorted(want)}")

# atimes-format variant -- only if rsync was built with atimes support.
vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' in vv.stdout:
    out = run_and_check(['-rU', f'{daemon_url}/test-from/f*'], "test-from glob with -U")
    got = listed_paths(out)
    if got != want:
        print(out)
        test_fail(f"-U glob paths {sorted(got)} != expected {sorted(want)}")
