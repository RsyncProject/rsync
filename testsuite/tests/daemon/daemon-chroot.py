#!/usr/bin/env python3

import ctypes
import os
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    FROMDIR, SCRATCHDIR, claim_ports, get_rootuid, get_testuid, make_tree, makepath,
    require_tcp, rmtree, rsync_argv, start_test_daemon, test_fail, test_skipped,
    under_valgrind,
)

if get_testuid() != get_rootuid():
    test_skipped("use chroot = yes needs root (CAP_SYS_CHROOT)", capability='root')
if under_valgrind():
    test_skipped("per-process Valgrind logs are outside the daemon chroot",
                 capability='chroot')

pid = os.fork()
if pid == 0:
    try:
        result = ctypes.CDLL(None, use_errno=True).chroot(b'/')
        os._exit(0 if result == 0 else 1)
    except OSError:
        os._exit(1)
_, st = os.waitpid(pid, 0)
if not (os.WIFEXITED(st) and os.WEXITSTATUS(st) == 0):
    test_skipped("chroot(2) not permitted in this environment", capability='chroot')

require_tcp("daemon chroot path needs the real start_daemon socket flow")

PORT = 19886
claim_ports(PORT)

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

root_chr = SCRATCHDIR / 'chroot-plain'
root_outer = SCRATCHDIR / 'chroot-outer'
root_inner = root_outer / 'inner'
root_tmp = SCRATCHDIR / 'chroot-tmp'
tmpd = root_tmp / 'tmpd'
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
        'path': f'{root_outer}/./inner',
        'use chroot': 'yes',
        'read only': 'no',
    }),
    ('chrtmp', {
        'path': str(root_tmp),
        'use chroot': 'yes',
        'read only': 'no',
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

pull_dst = SCRATCHDIR / 'pull-chr'
rmtree(pull_dst)
makepath(pull_dst)
r = subprocess.run(rsync_argv('-r', f'{url}chr/', f'{pull_dst}/'),
                   capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"pull from [chr] failed (rc={r.returncode}):\n{r.stderr}")
if not any(pull_dst.iterdir()):
    test_fail("pull from [chr] produced nothing")

print("daemon-chroot: use chroot=yes (plain + /./inner + temp dir) push+pull ok")
