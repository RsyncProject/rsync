#!/usr/bin/env python3

import subprocess

from harness.rsync import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

PORT = 12951

base = SCRATCHDIR / 'daemon-refuse-delete-alias'
rmtree(base)
src = base / 'src'
makepath(src)
(src / 'keep').write_text('KEEP\n')

mods = {}
for name, rule in (('refuses_canonical', 'delete-during'), ('refuses_alias', 'del')):
    d = base / name
    makepath(d)
    mods[name] = d

conf = write_daemon_conf([
    ('refuses_canonical', {'path': str(mods['refuses_canonical']), 'read only': 'no',
                           'use chroot': 'no', 'refuse options': 'delete-during'}),
    ('refuses_alias', {'path': str(mods['refuses_alias']), 'read only': 'no',
                       'use chroot': 'no', 'refuse options': 'del'}),
    ('open', {'path': str(base / 'open'), 'read only': 'no', 'use chroot': 'no'}),
], name='refuse-delete-alias.conf')
makepath(base / 'open')
url = start_test_daemon(conf, PORT)

def push(module, *opts):
    return subprocess.run(
        rsync_argv('-r', *opts, f'{src}/', f'{url}{module}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)

def refused(proc, spelling):
    return (proc.returncode != 0
            and f'configured to refuse --{spelling}' in proc.stdout)

for opt in ('-M--del', '-M--delete-during'):
    proc = push('open', '--delete', opt)
    if proc.returncode != 0 or not (base / 'open' / 'keep').is_file():
        test_fail(f'control failed: {opt} could not push to a module with no '
                  f'refuse rule (rc={proc.returncode}, '
                  f'output={proc.stdout.strip()[:300]!r})')

proc = push('refuses_canonical', '--delete', '-M--del')
if not refused(proc, 'del'):
    test_fail('"refuse options = delete-during" did not refuse --del, which '
              'sets the very same delete_during: the rule names a capability, '
              f'not one spelling of it (rc={proc.returncode}, '
              f'output={proc.stdout.strip()[:300]!r})')

proc = push('refuses_alias', '--delete', '-M--delete-during')
if not refused(proc, 'delete-during'):
    test_fail('"refuse options = del" did not refuse --delete-during '
              f'(rc={proc.returncode}, output={proc.stdout.strip()[:300]!r})')

proc = push('refuses_canonical', '--delete', '-M--delete-during')
if not refused(proc, 'delete-during'):
    test_fail('"refuse options = delete-during" did not even refuse '
              f'--delete-during (rc={proc.returncode}, '
              f'output={proc.stdout.strip()[:300]!r})')
proc = push('refuses_alias', '--delete', '-M--del')
if not refused(proc, 'del'):
    test_fail(f'"refuse options = del" did not even refuse --del '
              f'(rc={proc.returncode}, output={proc.stdout.strip()[:300]!r})')

print('a refuse rule for one spelling of --delete-during covers --del too')
