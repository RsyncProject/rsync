#!/usr/bin/env python3

import subprocess

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_argv, rsync_path_arg


@requires(features={'daemon', 'files-from', 'remote-shell'}, transports={'pipe', 'tcp'},
          mutates={'environment', 'filesystem', 'process', 'socket'}, tags={'transfer'})
def test(context: TestContext):
    source = context.source
    listed = source / 'listed'
    listed.mkdir(parents=True)
    (listed / 'keep').write_text('selected\n')
    (listed / 'omit').write_text('excluded\n')
    (source / 'unlisted').write_text('outside the list\n')
    entries = context.scratch / 'entries'
    entries.write_text('listed/\n')
    working = context.scratch / 'empty'
    working.mkdir()

    def listing(label, args, input_text=None):
        result = subprocess.run(rsync_argv('-r', '--exclude=omit', *args),
                                cwd=working, input=input_text,
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            test_fail(f'{label}: listing failed with exit {result.returncode}: {result.stderr}')
        files = [line.rsplit(maxsplit=1)[-1] for line in result.stdout.splitlines() if line.strip()]
        if set(files) != {'listed', 'listed/keep'} or len(files) != 2:
            test_fail(f'{label}: wrong listing: {result.stdout!r}')
        if any(working.iterdir()):
            test_fail(f'{label}: a listing created destination files')

    listing('local file', [f'--files-from={entries}', f'{source}/'])
    listing('stdin', ['--files-from=-', f'{source}/'], entries.read_text())
    listing('remote-shell list', ['-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}',
                                  f'--files-from=:{entries}', f'lh:{source}/'])

    config = write_daemon_conf([('files', {'path': str(source)})])
    url = start_test_daemon(config, 12982)
    listing('daemon source', [f'--files-from={entries}', f'{url}files/'])

    rejected = subprocess.run(
        rsync_argv(f'--files-from={entries}', str(source), str(source), str(working)),
        capture_output=True, text=True, timeout=15,
    )
    if rejected.returncode != 1 or any(working.iterdir()):
        test_fail('three positional arguments with --files-from must be rejected')


if __name__ == '__main__':
    run(test)
