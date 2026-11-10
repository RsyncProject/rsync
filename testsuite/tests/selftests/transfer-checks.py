#!/usr/bin/env python3

from contextlib import redirect_stderr
from io import StringIO
from shutil import copytree
import os
import shlex
import subprocess
import sys
from unittest.mock import patch

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

    (context.destination / 'file').write_text('changed\n')
    output = StringIO()
    with patch('harness.comparison.rsync_argv', return_value=command), redirect_stderr(output):
        try:
            checkit([], context.expected, context.destination, allowed_codes=(0, 23))
        except SystemExit as error:
            if error.code != Exit.FAIL or 'file-diff' not in output.getvalue():
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

if __name__ == '__main__':
    run(test)
