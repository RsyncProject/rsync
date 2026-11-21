#!/usr/bin/env python3

import secrets

from harness import metadata
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_is_symlink, assert_same
from harness.results import test_fail
from harness.rsync import run_rsync

metadata(protocols={29, 30, 31, 32, 33}, transports={'pipe', 'tcp'})

base = SCRATCHDIR / 'delay-symlinks-daemon'
base.mkdir(mode=0o700)
source = base / 'source'
ordinary = base / 'ordinary'
restricted = base / 'restricted'
for path in (source, ordinary, restricted):
    path.mkdir()
(source / 'target').write_text('daemon transfer\n')
(source / 'link').symlink_to('target')

password = secrets.token_hex(24)
password_file = base / 'password'
secret_file = base / 'secrets'
password_file.write_text(password + '\n')
secret_file.write_text('rsynctest:' + password + '\n')
password_file.chmod(0o600)
secret_file.chmod(0o600)
settings = {'read only': 'no', 'munge symlinks': 'no',
            'auth users': 'rsynctest', 'secrets file': secret_file}
conf = write_daemon_conf([
    ('ordinary', {**settings, 'path': ordinary}),
    ('restricted', {**settings, 'path': restricted, 'refuse options': 'delay-symlinks'}),
])
url = start_test_daemon(conf, 13159).replace('rsync://', 'rsync://rsynctest@', 1)
args = ('-rl', f'--password-file={password_file}')

run_rsync(*args, '--delay-symlinks', f'{source}/', f'{url}ordinary/')
assert_is_symlink(ordinary / 'link', target='target')
assert_same(ordinary / 'target', source / 'target')

refusal = run_rsync(*args, '--delay-symlinks', f'{source}/', f'{url}restricted/',
                    check=False, capture_output=True)
if refusal.returncode == 0 or 'configured to refuse --delay-symlinks' not in refusal.stderr:
    test_fail(f'daemon did not refuse the option on a push: {refusal.stderr}')
if next(restricted.iterdir(), None) is not None:
    test_fail('the refused push changed the destination')

run_rsync(*args, f'{source}/', f'{url}restricted/')
pulled = base / 'pulled'
run_rsync(*args, '--delay-symlinks', f'{url}restricted/', str(pulled))
assert_is_symlink(pulled / 'link', target='target')
assert_same(pulled / 'target', source / 'target')
