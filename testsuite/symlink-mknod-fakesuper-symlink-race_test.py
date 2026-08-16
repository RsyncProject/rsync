#!/usr/bin/env python3
# Python rewrite of testsuite/symlink-mknod-fakesuper-symlink-race.test.
#
# Regression test for the fake-super branches of do_symlink_at() and
# do_mknod_at(). In --fake-super mode a symlink or device is stored as a
# regular placeholder file, so creating it resolves the final path component.
# The slashed-path branch always used openat(... O_NOFOLLOW), but a top-level
# (no-slash) path fell back to do_symlink()/do_mknod(), whose plain
# open(... O_CREAT|O_TRUNC) followed a pre-planted symlink at the basename and
# wrote outside the module.
#
# This is the same shape as the do_rename_at() mixed-parent bug: a no-slash
# path bypassed the safer branch. It is narrower -- exploiting it end-to-end
# needs a TOCTOU race to plant the basename symlink between the generator's
# check and the create -- but the openat(... O_NOFOLLOW) in the slashed branch
# exists precisely to win that race, and the bare branch silently lost it.
#
# The helper runs at the wrapper level (deterministic): --poc shows the pre-fix
# do_symlink()/do_mknod() fallback follows the planted basename symlink and
# clobbers a file outside the module; the regular run proves the fixed
# do_symlink_at()/do_mknod_at() refuse it.

import os
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, TOOLDIR, rmtree, test_fail, test_skipped


def run_helper(args, label):
    proc = subprocess.run([str(TOOLDIR / 't_symlink_secure'), *args])
    if proc.returncode == 77:
        test_skipped(f"t_symlink_secure {label} skipped")
    if proc.returncode != 0:
        test_fail(f"t_symlink_secure {label} reported failures (see stderr above)")


# Fake-super only stores symlinks as placeholder files -- the surface this PoC
# exploits -- where symlink xattrs are unavailable; elsewhere (e.g. FreeBSD)
# do_symlink() makes a real symlink and there is no escape to demonstrate.
# Mirror do_symlink()'s compile-time guard (and the helper's
# TEST_SYMLINK_PLACEHOLDER) so we set up and assert only what applies here. The
# device (do_mknod) placeholder is unconditional, so it is always exercised.
try:
    _config_h = (TOOLDIR / 'config.h').read_text()
except OSError:
    _config_h = ''
symlink_placeholders = ('#define NO_SYMLINK_XATTRS 1' in _config_h
                        or '#define NO_SYMLINK_USER_XATTRS 1' in _config_h)


# --- PoC tree: pre-fix fallback escapes -------------------------------------
poc = SCRATCHDIR / 'poc'
pmod = poc / 'module'
pout = poc / 'outside'
rmtree(poc)
pmod.mkdir(parents=True)
pout.mkdir(parents=True)
(pout / 'secret_nod').write_text("POC_VICTIM_NOD\n")
os.symlink('../outside/secret_nod', pmod / 'nodpath')
if symlink_placeholders:
    (pout / 'secret_sym').write_text("POC_VICTIM_SYM\n")
    os.symlink('../outside/secret_sym', pmod / 'sympath')

run_helper(['--poc', str(pmod)], '--poc')

# Independent confirmation the PoC really escaped the module.
if symlink_placeholders and (pout / 'secret_sym').read_text().strip() != "VULN_SYM_PAYLOAD":
    test_fail("PoC did not write through the symlink for do_symlink")
if (pout / 'secret_nod').stat().st_size != 0:
    test_fail("PoC did not truncate through the symlink for do_mknod")


# --- Regression tree: fixed wrappers refuse the escape ----------------------
fix = SCRATCHDIR / 'fix'
fmod = fix / 'module'
fout = fix / 'outside'
rmtree(fix)
(fmod / 'sub').mkdir(parents=True)
fout.mkdir(parents=True)
(fout / 'secret_nod').write_text("VICTIM_NOD\n")
(fout / 'secret_nod2').write_text("VICTIM_NOD2\n")
os.symlink('../outside/secret_nod', fmod / 'nodpath')
os.symlink('../../outside/secret_nod2', fmod / 'sub' / 'nodpath2')

checks = [
    ('secret_nod',  "VICTIM_NOD",  "fixed do_mknod_at escaped (bare)"),
    ('secret_nod2', "VICTIM_NOD2", "fixed do_mknod_at escaped (slashed)"),
]
if symlink_placeholders:
    (fout / 'secret_sym').write_text("VICTIM_SYM\n")
    (fout / 'secret_sym2').write_text("VICTIM_SYM2\n")
    os.symlink('../outside/secret_sym', fmod / 'sympath')
    os.symlink('../../outside/secret_sym2', fmod / 'sub' / 'sympath2')
    checks = [
        ('secret_sym',  "VICTIM_SYM",  "fixed do_symlink_at escaped (bare)"),
        ('secret_sym2', "VICTIM_SYM2", "fixed do_symlink_at escaped (slashed)"),
    ] + checks

run_helper([str(fmod)], 'regression run')

# Independent confirmation nothing outside the module was touched.
for name, want, what in checks:
    if (fout / name).read_text().strip() != want:
        test_fail(what)
