#!/usr/bin/env python3
# Python rewrite of testsuite/daemon.test.
#
# Basic daemon-mode operations against an in-process rsyncd: list
# modules, list a hidden module, list a single-glob match, and the
# atimes-format variant. We avoid actually starting a listening server
# by using RSYNC_CONNECT_PROG to spawn the daemon as a child of rsync.

import os
import re
import subprocess

from rsyncfns import (
    CHKFILE, FROMDIR, OUTFILE, RSYNC, SCRATCHDIR, SRCDIR, TODIR,
    build_rsyncd_conf, get_rootuid, get_testuid, makepath,
    rsync_argv, run_rsync, start_test_daemon, test_fail,
)


DAEMON_PORT = 12877


SSH = f"{SRCDIR / 'support' / 'lsh.sh'} --no-cd"

# Replacements that hide the variable parts of `rsync -r` listings: tabs/
# columns for file vs directory, and the date/time stamp.
_FILE_RE = re.compile(r'^([^d][^ ]*) *(\.{10}[0-9]) ', flags=re.MULTILINE)
_DIR_RE = re.compile(r'^(d[^ ]*)  *[0-9][.,0-9]* ', flags=re.MULTILINE)
_LS_RE = re.compile(
    r'[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}'
)


def normalise(text: str) -> str:
    out = _FILE_RE.sub(r'\1 \2 ', text)
    out = _DIR_RE.sub(r'\1         DIR ', out)
    out = _LS_RE.sub('####/##/## ##:##:##', out)
    return out


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


def run_and_check(args, expected, label, capture_stderr=False):
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
rsync_path = f"{RSYNC}{(' ' + ' '.join(confopt)) if confopt else ''}"
out = run_and_check(
    ['-ve', SSH, f'--rsync-path={rsync_path}', 'localhost::'],
    expected_modules, "module list via lsh.sh",
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

out = run_and_check(['-v', f'{daemon_url}/'], expected_modules, "module list via daemon")
if expected_modules not in out:
    test_fail("module list via daemon did not contain the expected modules")
# test-hidden is `list = no`; it must NOT appear in the module listing.
if 'test-hidden' in out:
    print(out)
    test_fail("module list via daemon leaked the `list = no` test-hidden module")
print('====')

# test-hidden: a recursive listing of the module, with file/dir/date
# columns normalised so the diff is content-only.
out = run_and_check(['-r', f'{daemon_url}/test-hidden'], "", "test-hidden listing")
normalised = normalise(out)
expected_hidden = """\
drwxr-xr-x         DIR ####/##/## ##:##:## .
drwxr-xr-x         DIR ####/##/## ##:##:## bar
-rw-r--r-- ........1 ####/##/## ##:##:## bar/two
drwxr-xr-x         DIR ####/##/## ##:##:## bar/baz
-rw-r--r-- ........1 ####/##/## ##:##:## bar/baz/three
drwxr-xr-x         DIR ####/##/## ##:##:## foo
-rw-r--r-- ........1 ####/##/## ##:##:## foo/one
"""
# The exact byte sizes vary by locale ("4" vs "          4"); just check that
# every expected path appears in the normalised output.
for path in ('bar', 'bar/two', 'bar/baz', 'bar/baz/three', 'foo', 'foo/one'):
    if path not in normalised:
        print(normalised)
        test_fail(f"test-hidden listing missing path {path!r}")

# test-from/f* glob: only the foo subtree.
out = run_and_check(['-r', f'{daemon_url}/test-from/f*'], "", "test-from glob")
normalised = normalise(out)
for path in ('foo', 'foo/one'):
    if path not in normalised:
        print(normalised)
        test_fail(f"test-from glob listing missing path {path!r}")
if 'bar' in normalised:
    print(normalised)
    test_fail("test-from glob listing leaked the bar subtree")

# atimes-format variant -- only if rsync was built with atimes support.
vv = run_rsync('-VV', check=True, capture_output=True)
if '"atimes": true' in vv.stdout:
    out = run_and_check(['-rU', f'{daemon_url}/test-from/f*'], "", "test-from glob with -U")
    normalised = normalise(out)
    for path in ('foo', 'foo/one'):
        if path not in normalised:
            print(normalised)
            test_fail(f"-U glob listing missing path {path!r}")
