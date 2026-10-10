#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, TOOLDIR, rmtree, test_fail, test_skipped

def run_helper(args, label):
    proc = subprocess.run([str(TOOLDIR / 't_symlink_secure'), *args])
    if proc.returncode == 77:
        test_skipped(f"t_symlink_secure {label} skipped", capability='symlink_mknod')
    if proc.returncode != 0:
        test_fail(f"t_symlink_secure {label} reported failures (see stderr above)")

try:
    _config_h = (TOOLDIR / 'config.h').read_text()
except OSError:
    _config_h = ''
symlink_placeholders = ('#define NO_SYMLINK_XATTRS 1' in _config_h
                        or '#define NO_SYMLINK_USER_XATTRS 1' in _config_h)

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

if symlink_placeholders and (pout / 'secret_sym').read_text().strip() != "VULN_SYM_PAYLOAD":
    test_fail("PoC did not write through the symlink for do_symlink")
if (pout / 'secret_nod').stat().st_size != 0:
    test_fail("PoC did not truncate through the symlink for do_mknod")

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

for name, want, what in checks:
    if (fout / name).read_text().strip() != want:
        test_fail(what)
