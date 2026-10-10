#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12902
USER = 'tuser'
CORRECT_PASSWORD = 'correctpass'

if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(the attacker simulation)", capability='cross_uid')

NOBODY_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name).pw_uid
        if u != 0 and u != os.geteuid():
            NOBODY_UID = u
            break
    except KeyError:
        continue
if NOBODY_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant", capability='cross_uid')

os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

authdir = SCRATCHDIR / 'authdest'
secrets = SCRATCHDIR / 'rsyncd.secrets'
secrets.write_text(f'{USER}:{CORRECT_PASSWORD}\n')
secrets.chmod(0o600)

conf = write_daemon_conf([
    ('auth', {'path': authdir, 'read only': 'no',
              'auth users': USER, 'secrets file': secrets}),
])
url = start_test_daemon(conf, DAEMON_PORT)
userurl = url.replace('rsync://', f'rsync://{USER}@', 1)

victim = SCRATCHDIR / 'victim_password'
victim.write_text(CORRECT_PASSWORD + '\n')
victim.chmod(0o600)

def push_with_pwfile(pwfile_path):
    rmtree(authdir)
    makepath(authdir)
    return subprocess.run(
        rsync_argv('-a', f'--password-file={pwfile_path}',
                   f'{src}/', f'{userurl}auth/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

real_pwfile = SCRATCHDIR / 'real_pwfile'
real_pwfile.write_text(CORRECT_PASSWORD + '\n')
real_pwfile.chmod(0o600)

sanity = push_with_pwfile(real_pwfile)
if sanity.returncode != 0:
    test_fail(
        "sanity check failed: a non-symlinked password file with the correct "
        f"password could not authenticate. stderr: {sanity.stderr!r}")

plants = SCRATCHDIR / 'plants'
rmtree(plants)
plants.mkdir()
leaf_plant = plants / 'pwfile_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

leaf = push_with_pwfile(leaf_plant)
if leaf.returncode == 0:
    test_fail(
        f'--password-file authenticated with {victim} through untrusted uid '
        f'{NOBODY_UID} symlink')

real_target = plants / 'parent_real_target'
real_target.mkdir()
(real_target / 'victim_in_target').write_text(CORRECT_PASSWORD + '\n')
os.chmod(real_target / 'victim_in_target', 0o600)

parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

parent = push_with_pwfile(parent_plant / 'victim_in_target')
if parent.returncode == 0:
    test_fail(
        f'--password-file authenticated through untrusted uid {NOBODY_UID} '
        f'parent symlink {parent_plant} -> {real_target}')
