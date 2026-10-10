#!/usr/bin/env python3

import os
import pwd
import subprocess

from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12903
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

base = SCRATCHDIR / 'secretsplant'
rmtree(base)
base.mkdir()

victim = base / 'victim_secrets'
victim.write_text(f'{USER}:{CORRECT_PASSWORD}\n')
os.chmod(victim, 0o600)
os.chown(victim, 0, 0)

leaf_plant = base / 'secrets_leaf'
os.symlink(victim, leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

parent_real = base / 'parent_real'
parent_real.mkdir()
victim_in_target = parent_real / 'victim_in_target'
victim_in_target.write_text(f'{USER}:{CORRECT_PASSWORD}\n')
os.chmod(victim_in_target, 0o600)
os.chown(victim_in_target, 0, 0)
parent_plant = base / 'parent_link'
os.symlink(parent_real, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

authreal = base / 'authreal'
authleaf = base / 'authleaf'
authparent = base / 'authparent'
makepath(authreal, authleaf, authparent)

conf = write_daemon_conf([
    ('authreal', {'path': authreal, 'read only': 'no',
                  'auth users': USER, 'secrets file': victim}),
    ('authleaf', {'path': authleaf, 'read only': 'no',
                  'auth users': USER, 'secrets file': leaf_plant}),
    ('authparent', {'path': authparent, 'read only': 'no',
                    'auth users': USER,
                    'secrets file': parent_plant / 'victim_in_target'}),
])
url = start_test_daemon(conf, DAEMON_PORT)
userurl = url.replace('rsync://', f'rsync://{USER}@', 1)

pwfile = SCRATCHDIR / 'pw.ok'
pwfile.write_text(CORRECT_PASSWORD + '\n')
pwfile.chmod(0o600)

def push(module, dest):
    rmtree(dest)
    makepath(dest)
    return subprocess.run(
        rsync_argv('-a', f'--password-file={pwfile}',
                   f'{src}/', f'{userurl}{module}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

sanity = push('authreal', authreal)
if sanity.returncode != 0:
    test_fail(f'direct secrets file did not authenticate: {sanity.stderr!r}')

leaf = push('authleaf', authleaf)
if leaf.returncode == 0:
    test_fail(f'daemon authenticated through untrusted uid {NOBODY_UID} secrets '
              f'file symlink: {leaf_plant} -> {victim}')

parent = push('authparent', authparent)
if parent.returncode == 0:
    test_fail(f'daemon authenticated through untrusted uid {NOBODY_UID} secrets '
              f'path symlink: {parent_plant} -> {parent_real}')

print("daemon-secrets-file-symlink: leaf + parent plants refused")
