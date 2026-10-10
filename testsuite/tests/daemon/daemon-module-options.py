#!/usr/bin/env python3

import subprocess

from harness.rsync import (
    SCRATCHDIR, FROMDIR,
    claim_ports, make_tree, makepath, require_tcp, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

require_tcp("motd/socket-options need a real socket")

PORT = 19884
claim_ports(PORT)

src = FROMDIR
dst_in = SCRATCHDIR / 'dest-modopt-in'
dst_out = SCRATCHDIR / 'dest-modopt-out'
dst_hidden = SCRATCHDIR / 'dest-modopt-hidden'
for d in (src, dst_in, dst_out, dst_hidden):
    rmtree(d)
make_tree(src, depth=1)
makepath(dst_in, dst_out, dst_hidden)
(dst_out / 'o0').write_text('out\n')
(dst_out / 'o0').chmod(0o644)

motd = SCRATCHDIR / 'motd.txt'
motd.write_text('** TEST MOTD BANNER **\n')

mods = [
    ('inmod', {
        'path': str(dst_in), 'read only': 'no',
        'incoming chmod': 'Fu+x,g-w,o=r',
        'dont compress': '*.gz *.bz2',
        'comment': 'incoming-chmod test module',
    }),
    ('outmod', {
        'path': str(dst_out), 'read only': 'yes',
        'outgoing chmod': 'F-x,o-rwx',
        'comment': 'outgoing-chmod test module',
    }),
    ('hidden', {
        'path': str(dst_hidden), 'read only': 'no',
        'list': 'no',
    }),
]
conf = write_daemon_conf(
    mods,
    globals={
        'motd file': str(motd),
        'socket options': 'SO_KEEPALIVE SO_RCVBUF=8192 SO_BROADCAST NOSUCHOPT',
    },
    name='module-options.conf',
)
url = start_test_daemon(conf, PORT)

r = subprocess.run(rsync_argv(f'{url}'), capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"module listing failed (rc={r.returncode}):\n{r.stderr}")
if 'TEST MOTD BANNER' not in r.stdout:
    test_fail(f"motd file content not echoed to client:\n{r.stdout!r}")
if 'hidden' in r.stdout:
    test_fail(f"list=no module 'hidden' leaked into listing:\n{r.stdout}")
if 'inmod' not in r.stdout or 'outmod' not in r.stdout:
    test_fail(f"expected modules missing from listing:\n{r.stdout}")
if 'incoming-chmod test module' not in r.stdout:
    test_fail(f"module comment missing from listing:\n{r.stdout!r}")

nomotd_dst = SCRATCHDIR / 'pull-no-motd'
makepath(nomotd_dst)
r = subprocess.run(rsync_argv('-r', '--no-motd', f'{url}outmod/',
                             f'{nomotd_dst}/'),
                   capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"module transfer with --no-motd failed (rc={r.returncode}):\n"
              f"{r.stderr}")
if 'TEST MOTD BANNER' in r.stdout + r.stderr:
    test_fail(f"--no-motd did not suppress the banner:\n{r.stdout!r}\n{r.stderr!r}")
nomotd_file = nomotd_dst / 'o0'
if not nomotd_file.is_file() or nomotd_file.read_text() != 'out\n':
    test_fail('--no-motd module transfer did not copy the payload')

r = subprocess.run(
    rsync_argv('-rzp', '--sockopts=SO_KEEPALIVE,SO_SNDBUF=8192',
               f'{src}/', f'{url}inmod/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"push to [inmod] failed (rc={r.returncode}):\n{r.stderr}")
for f in dst_in.rglob('*'):
    if f.is_file():
        m = f.stat().st_mode & 0o777
        if not (m & 0o100):
            test_fail(f"incoming chmod Fu+x not applied to {f.name}: {oct(m)}")
        if m & 0o020:
            test_fail(f"incoming chmod g-w not applied to {f.name}: {oct(m)}")
        if (m & 0o007) != 0o004:
            test_fail(f"incoming chmod o=r not applied to {f.name}: {oct(m)}")
        break
else:
    test_fail("push to [inmod] produced no regular files")

pull_dst = SCRATCHDIR / 'pull-outmod'
rmtree(pull_dst)
makepath(pull_dst)
r = subprocess.run(
    rsync_argv('-rp', f'{url}outmod/', f'{pull_dst}/'),
    capture_output=True, text=True,
)
if r.returncode != 0:
    test_fail(f"pull from [outmod] failed (rc={r.returncode}):\n{r.stderr}")
m = (pull_dst / 'o0').stat().st_mode & 0o777
if m & 0o111:
    test_fail(f"outgoing chmod F-x not applied: {oct(m)}")
if m & 0o007:
    test_fail(f"outgoing chmod o-rwx not applied: {oct(m)}")

r = subprocess.run(rsync_argv('-r', f'{src}/', f'{url}hidden/'),
                   capture_output=True, text=True)
if r.returncode != 0:
    test_fail(f"push to list=no [hidden] should still work by name "
              f"(rc={r.returncode}):\n{r.stderr}")

print("daemon-module-options: motd, socket options, --sockopts, "
      "incoming/outgoing chmod, dont compress, list=no, comment ok")
