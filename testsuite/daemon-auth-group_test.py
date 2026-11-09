#!/usr/bin/env python3
"""Daemon coverage: `auth users = @group` matching and `gid = *`.

daemon-auth_test.py covers plain username auth; this exercises the
`@group` branch in authenticate.c auth_server() (lines ~362-395), which
resolves the connecting username to a uid (user_to_uid), enumerates that
uid's groups via uidlist.c getallgroups() -> getgrouplist(), and
wildmatches each group name against the @pattern.  Also covers
clientserver.c want_all_groups() via a `gid = *` module, and
uidlist.c is_in_group() via a non-root receiver child.

Needs real passwd/group entries -- skips if `daemon` (or any non-root
user with a same-named primary group) isn't present.
"""

import grp
import os
import platform
import pwd
import subprocess

from rsyncfns import (
    FROMDIR, SCRATCHDIR,
    make_tree, makepath, owners_supported, rmtree, rsync_argv,
    start_test_daemon, test_fail, test_skipped, write_daemon_conf,
)

# This covers the @group / getgrouplist daemon-auth path, which depends on the
# group membership of system users (daemon/bin/nobody).  That membership and the
# group-name matching differ enough on the BSDs/Solaris/macOS that the test's
# fixed expectations don't hold there; it is a Linux daemon-coverage test.
if platform.system() != 'Linux':
    test_skipped("@group daemon-auth coverage is Linux-specific")

DAEMON_PORT = 12892
os.environ['RSYNC_PASSWORD'] = 'env-fallback-wrong'  # never prompt

# Find a non-root user whose PRIMARY group name we can wildmatch on -- on a
# minimal Debian/gcc image that's `daemon` (uid 1, gid 1, group "daemon").
# We need the user to exist in passwd (for user_to_uid + getgrouplist) and
# its primary gid to resolve to a name (for gid_to_group).
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
    test_skipped("no suitable non-root passwd entry for @group auth")

# A second user NOT in U's group, for the negative case.
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
# The non-root receiver child (uid=U) must be able to write its dest, which
# needs a cross-uid chown -- i.e. root.  Skip cleanly when we can't.
if not owners_supported():
    test_skipped("needs chown to set up the non-root-owned dest tree")
os.chown(dest_nonroot, U.pw_uid, U.pw_gid)

mods = [
    # @group auth: U matches its own primary group exactly; root matches the
    # wildcard `@ro*` (group "root") and gets :ro; anyone else is denied.
    ('gauth', {
        'path': str(dest_g), 'read only': 'no', 'use chroot': 'no',
        'auth users': f'@{G.gr_name}, @ro*:ro',
        'secrets file': str(secrets), 'strict modes': 'no',
    }),
    # gid = * -> clientserver.c want_all_groups() -> uidlist.c getallgroups().
    ('gidstar', {
        'path': str(dest_star), 'read only': 'no', 'use chroot': 'no',
        'uid': '0', 'gid': '*',
    }),
]
# Non-root receiver (am_root==0 in the child) -> recv_add_id() for gids calls
# uidlist.c is_in_group(). Only when we can chown the dest and setuid.
if owners_supported():
    mods.append(('nonroot', {
        'path': str(dest_nonroot), 'read only': 'no', 'use chroot': 'no',
        'uid': str(U.pw_uid), 'gid': str(U.pw_gid),
    }))

conf = write_daemon_conf(mods, name='auth-group.conf')
url = start_test_daemon(conf, DAEMON_PORT)


def pwfile(name, text):
    p = SCRATCHDIR / name
    p.write_text(text)
    p.chmod(0o600)
    return p


def push(user, pw, module, *extra):
    return subprocess.run(
        rsync_argv('-r', f'--password-file={pw}', *extra,
                   f'{src}/', url.replace('rsync://', f'rsync://{user}@', 1) + f'{module}/'),
        capture_output=True, text=True,
    )


# --- @group exact match: U is in group G -> allowed rw -----------------------
r = push(U.pw_name, pwfile('pw-u', 'upass'), 'gauth')
if r.returncode != 0:
    test_fail(f"@{G.gr_name} should allow {U.pw_name!r} (rc={r.returncode}):\n{r.stderr}")

# --- @group wildcard + :ro: root is in group "root" which matches @ro* ------
# The :ro suffix flips the module read-only for this user, so a push fails
# with "module is read only" -- proving the @ro* matched AND the suffix took.
r = push('root', pwfile('pw-r', 'rpass'), 'gauth')
if r.returncode == 0:
    test_fail(f"@ro*:ro should make [gauth] read-only for root, but push succeeded")
if 'read only' not in r.stderr:
    test_fail(f"expected 'read only' for root@gauth (got rc={r.returncode}):\n{r.stderr}")

# --- @group negative: outsider's primary group matches neither --------------
if OUT:
    r = push(OUT.pw_name, pwfile('pw-o', 'opass'), 'gauth')
    if r.returncode == 0:
        test_fail(f"{OUT.pw_name!r} (group {grp.getgrgid(OUT.pw_gid).gr_name!r}) "
                  f"should NOT match @{G.gr_name}/@ro*, but push succeeded")
    if 'auth failed' not in r.stderr:
        test_fail(f"expected 'auth failed' for {OUT.pw_name!r}:\n{r.stderr}")

# --- gid = * -> want_all_groups() -------------------------------------------
r = subprocess.run(rsync_argv('-r', f'{src}/', f'{url}gidstar/'),
                   capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"push to [gidstar] (gid=*) failed (rc={r.returncode}):\n{r.stderr}")

# --- non-root receiver -> is_in_group() -------------------------------------
if owners_supported():
    r = subprocess.run(rsync_argv('-rg', f'{src}/', f'{url}nonroot/'),
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"push to [nonroot] (uid={U.pw_uid}) failed (rc={r.returncode}):\n{r.stderr}")

print(f"daemon-auth-group: @{G.gr_name} allow, @ro*:ro, "
      f"{'@-deny, ' if OUT else ''}gid=*, "
      f"{'non-root is_in_group' if owners_supported() else 'is_in_group SKIPPED (non-root)'}")
