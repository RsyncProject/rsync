#!/usr/bin/env python3
# Python rewrite of testsuite/acls.test.
#
# Test that rsync -A preserves POSIX ACLs across a transfer. Skips on
# binaries built without ACL support, on filesystems with ACLs disabled,
# and on hosts that lack both setfacl(1) and a chmod that understands "+a".

import os
import platform
import subprocess

from rsyncfns import FROMDIR, SCRATCHDIR, TODIR, makepath, run_rsync, test_fail, test_skipped


vv = run_rsync('-VV', check=True, capture_output=True)
if '"ACLs": true' not in vv.stdout:
    test_skipped("Rsync is configured without ACL support")

makepath(FROMDIR / 'foo')
(FROMDIR / 'file1').write_text("something\n")
(FROMDIR / 'file2').write_text("else\n")

files = ['foo', 'file1', 'file2']

# Decide which ACL command surface to use. Mirrors the shell test's
# branching on $setfacl_nodef (set by runtests.py).
setfacl_nodef = os.environ.get('setfacl_nodef', 'true')


def _chmod_plus_a_supported() -> bool:
    """macOS-style: chmod +a 'user allow ...'."""
    out = subprocess.run(['chmod', '--help'], capture_output=True, text=True)
    return '+a' in (out.stdout + out.stderr)


use_chmod_plus_a = setfacl_nodef == 'true' and _chmod_plus_a_supported()

if setfacl_nodef == 'true' and not use_chmod_plus_a:
    test_skipped("I don't know how to use setfacl or chmod for ACLs")


def _setfacl(*args) -> int:
    return subprocess.run(['setfacl', *args]).returncode


def _chmod_acl(*args) -> int:
    return subprocess.run(['chmod', *args]).returncode


if use_chmod_plus_a:
    if _chmod_acl('+a', 'root allow read,write,execute',
                  str(FROMDIR / 'foo')) != 0:
        test_skipped("Your filesystem has ACLs disabled")
    _chmod_acl('+a', 'root allow read,execute', str(FROMDIR / 'file1'))
    _chmod_acl('+a', 'admin allow read', str(FROMDIR / 'file1'))
    _chmod_acl('+a', 'daemon allow read,write', str(FROMDIR / 'file1'))
    _chmod_acl('+a', 'root allow read,execute', str(FROMDIR / 'file2'))

    def see_acls(paths):
        return subprocess.check_output(['ls', '-le', *paths], text=True)
else:
    if _setfacl('-m', 'u:0:7', str(FROMDIR / 'foo')) != 0:
        test_skipped("Your filesystem has ACLs disabled")
    _setfacl('-m', 'g:1:5', str(FROMDIR / 'foo'))
    _setfacl('-m', 'g:2:1', str(FROMDIR / 'foo'))
    _setfacl('-m', 'g:0:7', str(FROMDIR / 'foo'))
    _setfacl('-m', 'u:2:1', str(FROMDIR / 'foo'))
    _setfacl('-m', 'u:1:5', str(FROMDIR / 'foo'))

    _setfacl('-m', 'u:0:5', str(FROMDIR / 'file1'))
    _setfacl('-m', 'g:0:4', str(FROMDIR / 'file1'))
    _setfacl('-m', 'u:1:6', str(FROMDIR / 'file1'))

    _setfacl('-m', 'u:0:5', str(FROMDIR / 'file2'))

    def see_acls(paths):
        return subprocess.check_output(['getfacl', *paths], text=True)


os.chdir(FROMDIR)
run_rsync('-avvA', *files, f'{TODIR}/')

before = see_acls(files)
(SCRATCHDIR / 'acls.txt').write_text(before)

os.chdir(TODIR)
after = see_acls(files)
if before != after:
    print("--- expected (from) ---")
    print(before)
    print("--- got (to) ---")
    print(after)
    test_fail("ACL listing differs between source and destination")
