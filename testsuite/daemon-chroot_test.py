#!/usr/bin/env python3
"""Daemon coverage: `use chroot = yes` and the `path = /outer/./inner`
chroot-with-inner-subdir syntax.

Every other daemon test in the suite forces `use chroot = no` (via
write_daemon_conf()'s globals) so the chroot setup in clientserver.c
rsync_module() -- the use_chroot path-split (~866-887), the actual
chroot(2) call (~1003-1012), and the `temp dir` module option -- was
never reached.  Those lines are also where the per-connection child
loses the ability to write its .gcda counters (the build-tree paths
are outside the chroot), so a companion gcov_flush() lands just before
chroot() in clientserver.c; this test exercises that flush point and
everything before it.

Three modules:
  chr        use chroot = yes, plain path           -> 880-887, 1003-1012
  chrinner   use chroot = yes, path = /outer/./in   -> 868-877 split
  chrtmp     use chroot = yes, temp dir = /tmpd     -> 1093-1095 lp_temp_dir

Requires CAP_SYS_CHROOT (root in the test container); skips cleanly
without it.
"""

import ctypes
import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, FROMDIR,
    claim_ports, get_rootuid, get_testuid, make_tree, makepath, require_tcp,
    rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
    write_daemon_conf,
)

if get_testuid() != get_rootuid():
    test_skipped("use chroot = yes needs root (CAP_SYS_CHROOT)")

# Probe chroot() directly so a rootless/seccomp'd container that maps uid 0
# but withholds CAP_SYS_CHROOT skips cleanly instead of failing.
pid = os.fork()
if pid == 0:
    try:
        ctypes.CDLL(None).chroot(b"/")
        os._exit(0)
    except Exception:
        os._exit(1)
_, st = os.waitpid(pid, 0)
if not (os.WIFEXITED(st) and os.WEXITSTATUS(st) == 0):
    test_skipped("chroot(2) not permitted in this environment")

require_tcp("daemon chroot path needs the real start_daemon socket flow")

PORT = 19886
claim_ports(PORT)

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

# Module roots.  For the /./ form the OUTER path is what gets chroot()'d
# and the INNER path is chdir'd inside it.
root_chr = SCRATCHDIR / 'chroot-plain'
root_outer = SCRATCHDIR / 'chroot-outer'
root_inner = root_outer / 'inner'
root_tmp = SCRATCHDIR / 'chroot-tmp'
tmpd = root_tmp / 'tmpd'      # `temp dir` is interpreted INSIDE the chroot
for d in (root_chr, root_inner, root_tmp, tmpd):
    rmtree(d)
makepath(root_chr, root_inner, root_tmp, tmpd)

mods = [
    ('chr', {
        'path': str(root_chr),
        'use chroot': 'yes',
        'read only': 'no',
    }),
    ('chrinner', {
        # /outer/./inner: chroot to /outer, chdir to /inner inside.
        'path': f'{root_outer}/./inner',
        'use chroot': 'yes',
        'read only': 'no',
    }),
    ('chrtmp', {
        'path': str(root_tmp),
        'use chroot': 'yes',
        'read only': 'no',
        # Relative to the chroot root (= module path).
        'temp dir': '/tmpd',
    }),
]
conf = write_daemon_conf(mods, name='chroot.conf')
url = start_test_daemon(conf, PORT)


def push(module, dest):
    r = subprocess.run(rsync_argv('-r', f'{src}/', f'{url}{module}/'),
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"push to [{module}] (use chroot=yes) failed "
                  f"(rc={r.returncode}):\n{r.stderr}")
    if not any(dest.iterdir()):
        test_fail(f"push to [{module}] wrote nothing into {dest}")


push('chr', root_chr)
push('chrinner', root_inner)
push('chrtmp', root_tmp)

# Pull from a chrooted module too (sender side under chroot).
pull_dst = SCRATCHDIR / 'pull-chr'
rmtree(pull_dst); makepath(pull_dst)
r = subprocess.run(rsync_argv('-r', f'{url}chr/', f'{pull_dst}/'),
                   capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"pull from [chr] failed (rc={r.returncode}):\n{r.stderr}")
if not any(pull_dst.iterdir()):
    test_fail("pull from [chr] produced nothing")

print("daemon-chroot: use chroot=yes (plain + /./inner + temp dir) push+pull ok")
