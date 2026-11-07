#!/usr/bin/env python3

import grp
import os
import platform
import pwd
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, make_tree, makepath, owners_supported, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_text_file,
)

if platform.system() != 'Linux':
    test_skipped("@group daemon-auth coverage is Linux-specific", capability='group_auth')

DAEMON_PORT = 12892
os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'

def pick_user():
    for name in ('daemon', 'bin', 'nobody'):
        try:
            p = pwd.getpwnam(name)
            g = grp.getgrgid(p.pw_gid)
            if p.pw_uid != 0:
                return p, g
        except KeyError:
            continue
    return None, None

U, G = pick_user()
if U is None:
    test_skipped("no suitable non-root passwd entry for @group auth", capability='group_auth')

def pick_outsider():
    for p in pwd.getpwall():
        if p.pw_uid in (0, U.pw_uid):
            continue
        try:
            if G.gr_gid != p.pw_gid and U.pw_name not in grp.getgrgid(p.pw_gid).gr_mem:
                return p
        except KeyError:
            continue
    return None

OUT = pick_outsider()

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)

secrets = SCRATCHDIR / 'group.secrets'
lines = [f'{U.pw_name}:upass', 'root:rpass']
if OUT:
    lines.append(f'{OUT.pw_name}:opass')
secrets.write_text('\n'.join(lines) + '\n')
secrets.chmod(0o600)

dest_g = SCRATCHDIR / 'dest-gauth'
dest_star = SCRATCHDIR / 'dest-gidstar'
dest_nonroot = SCRATCHDIR / 'dest-nonroot'
makepath(dest_g, dest_star, dest_nonroot)
if not owners_supported():
    test_skipped("needs chown to set up the non-root-owned dest tree", capability='group_auth')
os.chown(dest_nonroot, U.pw_uid, U.pw_gid)

mods = [
    ('gauth', {
        'path': str(dest_g), 'read only': 'no', 'use chroot': 'no',
        'auth users': f'@{G.gr_name}, @ro*:ro',
        'secrets file': str(secrets), 'strict modes': 'no',
    }),
    ('gidstar', {
        'path': str(dest_star), 'read only': 'no', 'use chroot': 'no',
        'uid': '0', 'gid': '*',
    }),
]
if owners_supported():
    mods.append(('nonroot', {
        'path': str(dest_nonroot), 'read only': 'no', 'use chroot': 'no',
        'uid': str(U.pw_uid), 'gid': str(U.pw_gid),
    }))

conf = write_daemon_conf(mods, name='auth-group.conf')
url = start_test_daemon(conf, DAEMON_PORT)

def push(user, pw, module, *extra):
    return subprocess.run(
        rsync_argv('-r', f'--password-file={pw}', *extra,
                   f'{src}/', url.replace('rsync://', f'rsync://{user}@', 1) + f'{module}/'),
        capture_output=True, text=True,
    )

r = push(U.pw_name, write_text_file(SCRATCHDIR / 'pw-u', 'upass', 0o600), 'gauth')
if r.returncode != 0:
    test_fail(f"@{G.gr_name} should allow {U.pw_name!r} (rc={r.returncode}):\n{r.stderr}")

r = push('root', write_text_file(SCRATCHDIR / 'pw-r', 'rpass', 0o600), 'gauth')
if r.returncode == 0:
    test_fail(f"@ro*:ro should make [gauth] read-only for root, but push succeeded")
if 'read only' not in r.stderr:
    test_fail(f"expected 'read only' for root@gauth (got rc={r.returncode}):\n{r.stderr}")

if OUT:
    r = push(OUT.pw_name, write_text_file(SCRATCHDIR / 'pw-o', 'opass', 0o600), 'gauth')
    if r.returncode == 0:
        test_fail(f"{OUT.pw_name!r} (group {grp.getgrgid(OUT.pw_gid).gr_name!r}) "
                  f"should NOT match @{G.gr_name}/@ro*, but push succeeded")
    if 'auth failed' not in r.stderr:
        test_fail(f"expected 'auth failed' for {OUT.pw_name!r}:\n{r.stderr}")

r = subprocess.run(rsync_argv('-r', f'{src}/', f'{url}gidstar/'),
                   capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"push to [gidstar] (gid=*) failed (rc={r.returncode}):\n{r.stderr}")

if owners_supported():
    r = subprocess.run(rsync_argv('-rg', f'{src}/', f'{url}nonroot/'),
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"push to [nonroot] (uid={U.pw_uid}) failed (rc={r.returncode}):\n{r.stderr}")

print(f"daemon-auth-group: @{G.gr_name} allow, @ro*:ro, "
      f"{'@-deny, ' if OUT else ''}gid=*, "
      f"{'non-root is_in_group' if owners_supported() else 'is_in_group SKIPPED (non-root)'}")
