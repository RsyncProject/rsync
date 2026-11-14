#!/usr/bin/env python3

from contextlib import redirect_stderr
from io import StringIO
from shutil import copytree
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from fixtures.trees import hands_setup
from harness import Exit, TestContext, environment, requires, run
from harness.results import test_fail
from harness.comparison import checkit, rsync_ls_lR
from harness.environment import TLS_ARGS

@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    listing = context.scratch / 'listing'
    listing.mkdir()
    names = ['two words', "single'quote", 'double"quote', '*', 'line\nbreak']
    for name in names:
        (listing / name).touch()
    paths = ['.', *sorted(('./' + name for name in names), key=os.fsencode)]
    command = [str(context.tools / 'tls'), *shlex.split(TLS_ARGS), *paths]
    expected = subprocess.check_output(command, cwd=listing, encoding='utf-8', errors='backslashreplace')
    if rsync_ls_lR(listing) != expected:
        test_fail('directory listing interpreted filenames as shell syntax')
    previous = environment.TLS_ARGS
    try:
        environment.TLS_ARGS = '--atimes'
        expected = subprocess.check_output([str(context.tools / 'tls'), '--atimes', *paths],
                                           cwd=listing, encoding='utf-8', errors='backslashreplace')
        if rsync_ls_lR(listing) != expected:
            test_fail('directory listing ignored changed TLS arguments')
    finally:
        environment.TLS_ARGS = previous
    with patch('harness.comparison.subprocess.run') as run_listing:
        run_listing.return_value = SimpleNamespace(returncode=17, stdout='', stderr='probe failed')
        output = StringIO()
        try:
            with redirect_stderr(output):
                rsync_ls_lR(listing)
        except SystemExit as error:
            if error.code != Exit.FAIL or 'metadata listing exited 17' not in output.getvalue():
                test_fail(f'wrong metadata probe failure: {output.getvalue()}')
        else:
            test_fail('a failed metadata probe produced an empty listing')
    context.expected.mkdir()
    (context.expected / 'file').write_text('contents\n')
    cases = (
        (12, (0, 23), 'status=12'),
        (1, (0, 23), 'status=1'),
        (23, (0,), 'status=23'),
        (30, (0, 23), 'status=30'),
        (0, (0,), 'destination'),
        (23, (0, 23), 'destination'),
    )
    for code, allowed, message in cases:
        output = StringIO()
        command = [sys.executable, '-c', f'raise SystemExit({code})']
        with patch('harness.comparison.rsync_argv', return_value=command), redirect_stderr(output):
            try:
                checkit([], context.expected, context.destination, allowed_codes=allowed)
            except SystemExit as error:
                if error.code != Exit.FAIL or message not in output.getvalue():
                    test_fail(f'wrong transfer failure for {code}: {output.getvalue()}')
            else:
                test_fail(f'accepted a missing destination after exit {code}')

    copytree(context.expected, context.destination)
    for code in (0, 23):
        command = [sys.executable, '-c', f'raise SystemExit({code})']
        with patch('harness.comparison.rsync_argv', return_value=command):
            checkit([], context.expected, context.destination, allowed_codes=(0, 23))

    expected_stat = (context.expected / 'file').stat()
    changed = context.destination / 'file'
    changed.write_text('changed!\n')
    os.utime(changed, ns=(expected_stat.st_atime_ns, expected_stat.st_mtime_ns))
    output = StringIO()
    with patch('harness.comparison.rsync_argv', return_value=command), redirect_stderr(output):
        try:
            checkit([], context.expected, context.destination, allowed_codes=(0, 23))
        except SystemExit as error:
            if error.code != Exit.FAIL or 'file content differs' not in output.getvalue():
                test_fail(f'wrong content failure: {output.getvalue()}')
        else:
            test_fail('an allowed exit code bypassed content comparison')

    with patch('harness.comparison.rsync_argv', return_value=command):
        try:
            checkit([], context.scratch / 'missing-fixture', context.destination,
                    allowed_codes=(0, 23))
        except FileNotFoundError:
            pass
        else:
            test_fail('a missing source fixture was not reported as an error')

    events = []
    def listing(directory):
        events.append(('listing', str(directory)))
        return 'unchanged metadata\n'
    def command(argv):
        events.append(('diff' if argv[0] == 'diff' else 'transfer',))
        return SimpleNamespace(returncode=0)
    with patch.object(environment, 'TLS_ARGS', '--atimes'), \
            patch('harness.comparison.rsync_ls_lR', side_effect=listing), \
            patch('harness.comparison.subprocess.run', side_effect=command):
        checkit([], context.expected, context.destination)
    if events != [('listing', str(context.expected)), ('transfer',),
                  ('listing', str(context.destination)), ('diff',)]:
        test_fail('access-time comparison did not preserve the source snapshot before transfer')

    checkout = context.scratch / 'checkout'
    checkout.mkdir()
    core = checkout / 'core'
    core.write_bytes(b'transient source entry')
    (checkout / 'fixture.c').write_text('fixture text\n')
    run_command = subprocess.run
    def remove_shared_entry(*args, **kwargs):
        if kwargs.get('cwd') == checkout:
            core.unlink()
        return run_command(*args, **kwargs)

    with patch.object(environment, 'SRCDIR', checkout), \
            patch('harness.comparison.subprocess.run', side_effect=remove_shared_entry):
        hands_setup()
    if not core.exists():
        test_fail('transfer fixture enumerated the shared checkout')
    listing = (context.source / 'filelist').read_text()
    entries = {line.split()[-1] for line in listing.splitlines()}
    if not {'./empty', './emptydir'} <= entries or './core' in entries:
        test_fail('transfer fixture listing contains the wrong entries')
    if (context.source / 'text').read_text() != 'fixture text\n':
        test_fail('transfer fixture lost its source text')

if __name__ == '__main__':
    run(test)
